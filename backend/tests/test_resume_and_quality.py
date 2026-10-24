"""Resume-after-failure, checkpoint retention, evaluator source checks and input hardening."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from app.models.schemas import ChatRequest, Hotel, Location, POI, TripRequest, Weather
from app.planning.rule_evaluator import evaluate_trip_plan
from app.services import trip_result_service
from app.services.amap_mcp_service import RouteVerificationResult
from app.workflows.trip import checkpoint as checkpoint_module
from app.workflows.trip import graph as graph_module
from app.workflows.trip.graph import (
    build_trip_graph,
    resume_trip_graph_stream,
    run_trip_graph_stream,
)
from app.workflows.trip.routing import evaluator_next_step


def make_request(**overrides) -> TripRequest:
    values = dict(
        session_id="resume-1", city="郑州", start_date="2026-05-29",
        end_date="2026-05-29", travel_days=1,
    )
    values.update(overrides)
    return TripRequest(**values)


def make_plan(attractions=None, hotel=None, days=1) -> dict:
    attractions = attractions or [{"name": "景点A", "address": "A地址",
                                   "location": {"longitude": 113.6, "latitude": 34.7},
                                   "visit_duration": 120}]
    return {"days": [
        {"day_index": index, "attractions": attractions, "hotel": hotel,
         "route_summary": {"estimated_distance_km": 1}}
        for index in range(days)
    ], "budget": {"total": 500}}


SKELETON = [{"day_index": 0, "attractions": [{"name": "景点A"}, {"name": "景点B"}]}]


class RetryRoutingTests(unittest.TestCase):
    def test_day_issue_is_retried_even_when_another_issue_is_not_retryable(self):
        report = {
            "passed": False,
            "day_issues": {0: ["景点缺少有效坐标"]},
            "global_issues": ["第 2 天缺少可用的景点路线骨架"],
            "non_retryable_issues": ["第 2 天缺少可用的景点路线骨架"],
        }
        self.assertEqual(evaluator_next_step(report, 0, 1), "planner_agent")

    def test_only_non_retryable_issues_finalize(self):
        report = {
            "passed": False,
            "day_issues": {0: ["第1天 总时长约 700 分钟，超过 10 小时"]},
            "non_retryable_issues": ["第1天 总时长约 700 分钟，超过 10 小时"],
        }
        self.assertEqual(evaluator_next_step(report, 0, 1), "finalize")

    def test_retry_limit_finalizes(self):
        report = {"passed": False, "global_issues": ["预算偏高"]}
        self.assertEqual(evaluator_next_step(report, 1, 1), "finalize")


class EvaluatorSourceTests(unittest.TestCase):
    def test_attraction_outside_skeleton_is_flagged_for_that_day(self):
        plan = make_plan([{"name": "编造景点", "location": {"longitude": 113.6, "latitude": 34.7}}])
        report = evaluate_trip_plan(plan, make_request(), SKELETON)
        self.assertFalse(report["passed"])
        self.assertTrue(any("编造景点" in issue for issue in report["day_issues"][0]))

    def test_attraction_repeated_on_two_days_is_flagged_once(self):
        request = make_request(end_date="2026-05-30", travel_days=2)
        skeleton = [
            {"day_index": 0, "attractions": [{"name": "景点A"}]},
            {"day_index": 1, "attractions": [{"name": "景点A"}]},
        ]
        report = evaluate_trip_plan(make_plan(days=2), request, skeleton)
        duplicates = [issue for issue in report["issues"] if "同时出现在" in issue]
        self.assertEqual(len(duplicates), 1)
        self.assertIn(1, report["day_issues"])

    def test_skeleton_attractions_pass(self):
        report = evaluate_trip_plan(make_plan(), make_request(), SKELETON)
        self.assertTrue(report["passed"], report["issues"])

    def test_hotel_must_come_from_candidates(self):
        candidates = [Hotel(name="酒店A")]
        invented = evaluate_trip_plan(make_plan(hotel={"name": "不存在酒店"}), make_request(), SKELETON, candidates)
        self.assertTrue(any("不存在酒店" in issue for issue in invented["issues"]))

        valid = evaluate_trip_plan(make_plan(hotel={"name": "酒店A"}), make_request(), SKELETON, candidates)
        self.assertTrue(valid["passed"], valid["issues"])

    def test_hotel_check_skipped_without_candidates(self):
        report = evaluate_trip_plan(make_plan(hotel={"name": "任意酒店"}), make_request(), SKELETON, [])
        self.assertTrue(report["passed"], report["issues"])


class SessionIdValidationTests(unittest.TestCase):
    def test_rejects_unsafe_session_ids(self):
        for bad in ("", "a/b", "../x", "x" * 65, "中文", "a b"):
            with self.subTest(session_id=bad), self.assertRaises(ValidationError):
                ChatRequest(session_id=bad, message="hi")

    def test_accepts_generated_session_ids(self):
        for good in ("3f2b8c1e-7a41-4c1e-9d11-0a5b6c7d8e9f", "session_1760000000000_abc123"):
            self.assertEqual(ChatRequest(session_id=good, message="hi").session_id, good)

    def test_trip_request_allows_blank_for_server_generation(self):
        self.assertEqual(make_request(session_id="").session_id, "")
        with self.assertRaises(ValidationError):
            make_request(session_id="../etc")


def _agent_patches(planner):
    weather = [Weather(date="2026-05-29", day_weather="晴")]
    poi = POI(id="p1", name="景点A", address="A地址",
              location=Location(longitude=113.6, latitude=34.7), visit_duration=120)
    hotel = Hotel(name="酒店A", address="酒店地址", location=Location(longitude=113.61, latitude=34.71))
    verification = RouteVerificationResult(configured=False, total_legs=0, message="跳过")
    counters = {
        "weather": AsyncMock(return_value=weather),
        "poi": AsyncMock(return_value=([poi], [])),
        "hotel": AsyncMock(return_value=([hotel], [])),
    }
    patches = [
        patch("app.workflows.trip.graph.get_amap_service",
              return_value=SimpleNamespace(get_weather=counters["weather"])),
        patch("app.workflows.trip.graph.run_poi_agent", new=counters["poi"]),
        patch("app.workflows.trip.graph.run_hotel_agent", new=counters["hotel"]),
        patch("app.workflows.trip.graph.verify_route_skeleton",
              new=AsyncMock(side_effect=lambda skeleton, **_: (skeleton, verification))),
        patch("app.workflows.trip.graph.run_planner_agent", new=planner),
    ]
    return patches, counters


def _good_plan() -> dict:
    return {
        "city": "郑州", "start_date": "2026-05-29", "end_date": "2026-05-29",
        "days": [{
            "date": "2026-05-29", "day_index": 0, "description": "一日游",
            "attractions": [{"name": "景点A", "address": "A地址",
                             "location": {"longitude": 113.6, "latitude": 34.7},
                             "visit_duration": 120}],
            "meals": [], "route_summary": {"estimated_distance_km": 0},
        }],
        "weather_info": [], "overall_suggestions": "", "budget": None,
    }


async def _collect(stream) -> list[dict]:
    return [json.loads(raw.removeprefix("data: ")) async for raw in stream]


class ResumeAfterFailureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saver = InMemorySaver()
        self.compiled = build_trip_graph(checkpointer=self.saver)
        graph_module._active_sessions.clear()

    async def test_failed_planner_resumes_without_repeating_data_collection(self):
        planner = AsyncMock(side_effect=[RuntimeError("secret upstream detail"), _good_plan()])
        patches, counters = _agent_patches(planner)
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

        with patch("app.workflows.trip.graph.get_trip_graph", new=AsyncMock(return_value=self.compiled)):
            first = await _collect(run_trip_graph_stream(make_request()))
            error = first[-1]
            self.assertEqual((error["node"], error["status"]), ("error", "failed"))
            self.assertTrue(error["data"]["resumable"])
            self.assertIn("planner_agent", error["message"])
            self.assertNotIn("secret upstream detail", json.dumps(first, ensure_ascii=False))

            resumed = await _collect(resume_trip_graph_stream("resume-1"))

        self.assertEqual(resumed[-1]["node"], "complete")
        self.assertEqual(counters["weather"].await_count, 1)
        self.assertEqual(counters["poi"].await_count, 1)
        self.assertEqual(counters["hotel"].await_count, 1)
        self.assertEqual(planner.await_count, 2)
        self.assertIsNone((await self.compiled.aget_state({"configurable": {"thread_id": "resume-1"}})).created_at)

    async def test_successful_run_leaves_no_checkpoint(self):
        planner = AsyncMock(return_value=_good_plan())
        patches, _ = _agent_patches(planner)
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        with patch("app.workflows.trip.graph.get_trip_graph", new=AsyncMock(return_value=self.compiled)):
            events = await _collect(run_trip_graph_stream(make_request()))
        self.assertEqual(events[-1]["node"], "complete")
        remaining = [item async for item in self.saver.alist({"configurable": {"thread_id": "resume-1"}})]
        self.assertEqual(remaining, [])

    async def test_resume_without_progress_reports_clear_error(self):
        with patch("app.workflows.trip.graph.get_trip_graph", new=AsyncMock(return_value=self.compiled)):
            events = await _collect(resume_trip_graph_stream("never-ran"))
        self.assertEqual([(e["node"], e["status"]) for e in events], [("error", "failed")])
        self.assertFalse(events[0]["data"]["resumable"])

    async def test_concurrent_run_for_same_session_is_rejected(self):
        graph_module._active_sessions.add("resume-1")
        self.addCleanup(graph_module._active_sessions.clear)
        with patch("app.workflows.trip.graph.get_trip_graph", new=AsyncMock(return_value=self.compiled)):
            events = await _collect(run_trip_graph_stream(make_request()))
        self.assertEqual(len(events), 1)
        self.assertIn("正在规划中", events[0]["message"])
        self.assertIn("resume-1", graph_module._active_sessions)

    async def test_fresh_run_ignores_leftover_state_from_same_session(self):
        failing = AsyncMock(side_effect=RuntimeError("boom"))
        patches, counters = _agent_patches(failing)
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        with patch("app.workflows.trip.graph.get_trip_graph", new=AsyncMock(return_value=self.compiled)):
            await _collect(run_trip_graph_stream(make_request()))
            await _collect(run_trip_graph_stream(make_request()))
        self.assertEqual(counters["weather"].await_count, 2)


class CheckpointRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db_patch = patch.object(checkpoint_module, "CHECKPOINT_DB_PATH", Path(self.tmp.name) / "c.sqlite")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        await checkpoint_module.close_checkpointer()
        self.addAsyncCleanup(checkpoint_module.close_checkpointer)

    async def _seed(self, saver, thread_id: str, age_days: int):
        ts = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        checkpoint = {"v": 4, "id": f"1ef-{thread_id}", "ts": ts, "channel_values": {},
                      "channel_versions": {}, "versions_seen": {}, "updated_channels": []}
        await saver.aput(config, checkpoint, {"source": "loop", "step": 0}, {})

    async def _threads(self, saver) -> set[str]:
        return {item.config["configurable"]["thread_id"] async for item in saver.alist(None)}

    async def test_prunes_only_sessions_older_than_retention(self):
        saver = await checkpoint_module.get_checkpointer()
        await self._seed(saver, "old", 30)
        await self._seed(saver, "recent", 1)

        removed = await checkpoint_module.prune_stale_checkpoints(7)

        self.assertEqual(removed, 1)
        self.assertEqual(await self._threads(saver), {"recent"})

    async def test_zero_retention_disables_pruning(self):
        saver = await checkpoint_module.get_checkpointer()
        await self._seed(saver, "old", 400)
        self.assertEqual(await checkpoint_module.prune_stale_checkpoints(0), 0)
        self.assertEqual(await self._threads(saver), {"old"})

    async def test_domain_models_round_trip_through_sqlite_checkpoint(self):
        saver = await checkpoint_module.get_checkpointer()
        compiled = build_trip_graph(checkpointer=saver)
        planner = AsyncMock(side_effect=[RuntimeError("boom"), _good_plan()])
        patches, _ = _agent_patches(planner)
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        with patch("app.workflows.trip.graph.get_trip_graph", new=AsyncMock(return_value=compiled)):
            await _collect(run_trip_graph_stream(make_request()))
            snapshot = await compiled.aget_state({"configurable": {"thread_id": "resume-1"}})
            self.assertIsInstance(snapshot.values["request"], TripRequest)
            self.assertIsInstance(snapshot.values["pois"][0], POI)
            resumed = await _collect(resume_trip_graph_stream("resume-1"))
        self.assertEqual(resumed[-1]["node"], "complete")


class ResultRetentionTests(unittest.TestCase):
    def test_removes_only_expired_result_files(self):
        with tempfile.TemporaryDirectory() as directory:
            old, fresh = Path(directory, "old.json"), Path(directory, "fresh.json")
            old.write_text("{}"), fresh.write_text("{}")
            ancient = (datetime.now() - timedelta(days=60)).timestamp()
            os.utime(old, (ancient, ancient))
            with patch.object(trip_result_service, "RESULTS_DIR", directory):
                self.assertEqual(trip_result_service._prune_expired_results(30), 1)
                self.assertEqual(trip_result_service._prune_expired_results(0), 0)
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())


if __name__ == "__main__":
    unittest.main()
