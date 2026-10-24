"""Regression tests for workflow quality checks and live collection progress."""

import asyncio
from datetime import date, timedelta
import json
import unittest
from unittest.mock import AsyncMock, patch

from langgraph.graph import END, StateGraph

from app.models.schemas import Location, POI, TripRequest, Weather
from app.planning.itinerary_optimizer import optimize_poi_order
from app.planning.budget import estimate_budget
from app.planning.rule_evaluator import evaluate_trip_plan
from app.workflows.trip.graph import (
    candidate_collection_node,
    evaluator_router,
    quality_gate_node,
    route_optimizer_node,
    run_trip_graph_stream,
    weather_node,
)
from app.workflows.trip.state import TripPlanningState
from app.workflows.trip.sse import events_for_update
from app.workflows.trip.route_supply import supply_route_candidates


async def data_collection_node(state):
    """Run weather then candidate collection as one step, like the compiled graph does."""
    weather_update = await weather_node(state)
    return await candidate_collection_node({**state, **weather_update})


def request(**overrides):
    return TripRequest(
        city="郑州",
        start_date="2026-05-29",
        end_date="2026-05-29",
        travel_days=1,
        **overrides,
    )


def plan(*, distance=5, duration=None, transportation="公共交通", visit=120):
    return {
        "days": [{
            "day_index": 0,
            "transportation": transportation,
            "attractions": [{
                "name": "景点A",
                "location": {"longitude": 113.6, "latitude": 34.7},
                "visit_duration": visit,
                "ticket_price": 100,
            }],
            "meals": [{"estimated_cost": 50}],
            "hotel": {"estimated_cost": 200},
            "route_summary": {
                "estimated_distance_km": distance,
                "estimated_duration_min": duration,
            },
        }],
        "budget": {"total_transportation": 30, "total": 1000},
    }


class WorkflowQualityTests(unittest.IsolatedAsyncioTestCase):
    def test_budget_accepts_legacy_numeric_strings(self):
        budget = estimate_budget({
            "days": [{
                "attractions": [{"ticket_price": "50"}],
                "meals": [{"estimated_cost": "30元"}],
                "hotel": {"estimated_cost": "120.4"},
            }],
        })
        self.assertEqual(budget["total_attractions"], 50)
        self.assertEqual(budget["total_meals"], 30)
        self.assertEqual(budget["total_hotels"], 120)
        self.assertEqual(budget["total"], 200)

    def test_uses_verified_duration_and_transport_mode(self):
        fast_route = evaluate_trip_plan(plan(distance=50), request())
        self.assertTrue(fast_route["passed"])

        verified_slow_route = evaluate_trip_plan(plan(distance=50, duration=510), request())
        self.assertFalse(verified_slow_route["passed"])
        self.assertIn(0, verified_slow_route["day_issues"])

        walking_route = evaluate_trip_plan(plan(distance=50, transportation="步行"), request())
        self.assertFalse(walking_route["passed"])
        self.assertTrue(walking_route["non_retryable_issues"])

    async def test_missing_skeleton_is_reported_without_planner_retry(self):
        report = evaluate_trip_plan(plan(), request(), route_skeleton=[])
        self.assertFalse(report["passed"])
        self.assertIn("路线骨架", report["non_retryable_issues"][0])
        self.assertEqual(
            await evaluator_router({"evaluator_report": report, "retry_count": 0}),
            "finalize",
        )

    async def test_retryable_global_issue_precedes_non_retryable_route_warning(self):
        report = {
            "passed": False,
            "global_issues": ["预算偏高", "第 1 天缺少可用的景点路线骨架"],
            "non_retryable_issues": ["第 1 天缺少可用的景点路线骨架"],
        }
        self.assertEqual(
            await evaluator_router({"evaluator_report": report, "retry_count": 0}),
            "planner_agent",
        )

    async def test_budget_and_day_issues_use_full_planner_retry(self):
        report = {"passed": False, "day_issues": {0: ["超时"]}, "global_issues": ["预算"]}
        self.assertEqual(
            await evaluator_router({"evaluator_report": report, "retry_count": 0}),
            "planner_agent",
        )
        events = [
            json.loads(raw.removeprefix("data: "))
            for raw in events_for_update(
                "session", "quality_gate", {"evaluator_report": report, "retry_count": 0, "trip_plan": {}}, {}, 1,
            )
        ]
        self.assertEqual(events[-1]["node"], "planner")

    async def test_evaluator_recomputes_budget_before_checking(self):
        state = {
            "request": request(budget=[300, 500]),
            "trip_plan": plan(),
            "route_skeleton": [{"day_index": 0, "attractions": [{"name": "景点A"}]}],
            "node_traces": [],
        }
        result = await quality_gate_node(state)
        self.assertEqual(result["trip_plan"]["budget"]["total"], 380)
        self.assertTrue(result["evaluator_report"]["passed"])

    def test_non_retryable_issue_emits_warning_without_retry_progress(self):
        report = {"passed": False, "issues": ["路线不足"], "non_retryable_issues": ["路线不足"]}
        events = list(events_for_update(
            "session", "quality_gate", {"evaluator_report": report, "retry_count": 1, "trip_plan": {"evaluator_warnings": ["路线不足"]}}, {}, 1,
        ))
        self.assertEqual(len(events), 2)

        complete = list(events_for_update(
            "session", "quality_gate", {"trip_plan": {"evaluator_warnings": ["路线不足"]}, "evaluator_report": report, "retry_count": 1}, {}, 1,
        ))[-1]
        self.assertIn("仍有 1 项", json.loads(complete.removeprefix("data: "))["message"])


class CollectionProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_reports_subtasks_as_each_finishes(self):
        poi_release = asyncio.Event()
        weather = [Weather(date="2026-05-29", day_weather="晴")]
        poi = POI(
            name="景点A",
            address="地址A",
            location=Location(longitude=113.6, latitude=34.7),
        )

        async def collect_pois(*_args, **_kwargs):
            await poi_release.wait()
            return [poi], []

        graph = StateGraph(TripPlanningState)
        graph.add_node("data_collection", data_collection_node)
        graph.set_entry_point("data_collection")
        graph.add_edge("data_collection", END)
        compiled = graph.compile()

        async def read_events():
            events = []
            async for raw in run_trip_graph_stream(request()):
                event = json.loads(raw.removeprefix("data: "))
                events.append((event["node"], event["status"]))
                if event["node"] == "hotel" and event["status"] == "completed":
                    poi_release.set()
            return events

        with (
            patch("app.workflows.trip.graph.get_trip_graph", new=AsyncMock(return_value=compiled)),
            patch("app.workflows.trip.graph.get_amap_service", return_value=type("Amap", (), {"get_weather": AsyncMock(return_value=weather)})()),
            patch("app.workflows.trip.graph.run_poi_agent", side_effect=collect_pois),
            patch("app.workflows.trip.graph.run_hotel_agent", new=AsyncMock(return_value=([], []))),
        ):
            events = await asyncio.wait_for(read_events(), timeout=3)

        self.assertIn(("poi_search", "completed"), events)
        self.assertIn(("hotel", "completed"), events)


class RouteSupplyTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def poi(name: str, longitude: float, *, visit_duration: int = 120) -> POI:
        return POI(
            name=name,
            address=f"{name}地址",
            location=Location(longitude=longitude, latitude=10),
            visit_duration=visit_duration,
        )

    @staticmethod
    def multi_day_request(days: int, transportation: str = "公共交通") -> TripRequest:
        end_date = date(2026, 5, 29) + timedelta(days=days - 1)
        return TripRequest(
            city="郑州",
            start_date="2026-05-29",
            end_date=end_date.isoformat(),
            travel_days=days,
            transportation=transportation,
        )

    async def test_route_uses_pool_then_one_bounded_search(self):
        first = self.poi("A", 113.0)
        second = self.poi("B", 113.1)
        third = self.poi("C", 113.2)
        fourth = self.poi("D", 113.3)
        spare = self.poi("E", 113.4)
        amap = type("Amap", (), {
            "search_poi": AsyncMock(return_value=[first, fourth, spare]),
        })()
        trip_request = self.multi_day_request(4)

        with patch("app.workflows.trip.route_supply.get_amap_service", return_value=amap):
            supply = await supply_route_candidates([first], [second, third], trip_request)
        result = await route_optimizer_node({
            "request": trip_request,
            "pois": supply.pois,
            "pois_pool": supply.pool,
            "node_traces": [],
        })

        self.assertEqual(len(result["route_skeleton"]), 4)
        self.assertEqual({poi.name for poi in result["pois"]}, {"A", "B", "C", "D"})
        self.assertEqual([poi.name for poi in result["pois_pool"]], ["E"])
        self.assertEqual(amap.search_poi.await_count, 1)
        self.assertEqual((supply.promoted, supply.fetched), (2, 1))

    async def test_pool_coverage_avoids_external_search(self):
        first = self.poi("A", 113.0)
        second = self.poi("B", 113.1)
        trip_request = self.multi_day_request(2)
        with patch("app.workflows.trip.route_supply.get_amap_service") as get_amap:
            supply = await supply_route_candidates([first], [second], trip_request)
        result = await route_optimizer_node({
            "request": trip_request,
            "pois": supply.pois,
            "pois_pool": supply.pool,
            "node_traces": [],
        })
        get_amap.assert_not_called()
        self.assertEqual(len(result["route_skeleton"]), 2)

    async def test_collection_promotes_pool_before_route_optimization(self):
        first = self.poi("A", 113.0)
        second = self.poi("B", 113.1)
        amap = type("Amap", (), {
            "get_weather": AsyncMock(return_value=[]),
            "search_poi": AsyncMock(return_value=[]),
        })()
        with (
            patch("app.workflows.trip.graph.get_amap_service", return_value=amap),
            patch("app.workflows.trip.route_supply.get_amap_service", return_value=amap),
            patch("app.workflows.trip.graph.run_poi_agent", new=AsyncMock(return_value=([first], [second]))),
            patch("app.workflows.trip.graph.run_hotel_agent", new=AsyncMock(return_value=([], []))),
        ):
            result = await data_collection_node({
                "request": self.multi_day_request(2),
                "node_traces": [],
            })
        self.assertEqual([poi.name for poi in result["pois"]], ["A", "B"])
        amap.search_poi.assert_not_awaited()

    async def test_external_recall_is_bounded_when_no_pois_exist(self):
        amap = type("Amap", (), {"search_poi": AsyncMock(return_value=[])})()
        with patch("app.workflows.trip.route_supply.get_amap_service", return_value=amap):
            supply = await supply_route_candidates([], [], self.multi_day_request(30))
        self.assertEqual(supply.search_attempts, 3)
        self.assertEqual(amap.search_poi.await_count, 3)
        self.assertEqual(supply.pois, [])

    async def test_external_recall_caps_replacement_pool(self):
        results = [self.poi(f"POI-{index}", 113 + index * 0.001) for index in range(40)]
        amap = type("Amap", (), {"search_poi": AsyncMock(return_value=results)})()
        with patch("app.workflows.trip.route_supply.get_amap_service", return_value=amap):
            supply = await supply_route_candidates([], [], self.multi_day_request(1))
        self.assertEqual(len(supply.pois), 1)
        self.assertEqual(len(supply.pool), 25)

    def test_same_name_cluster_can_fill_every_day(self):
        pois = [self.poi(f"嵩山-{name}", 113 + index * 0.01) for index, name in enumerate("甲乙丙")]
        routes = optimize_poi_order(pois, 3)
        self.assertEqual(len(routes), 3)
        self.assertEqual(sum(len(day["attractions"]) for day in routes), 3)

    def test_overlong_day_moves_poi_to_available_day(self):
        pois = [
            self.poi("A", 0, visit_duration=220),
            self.poi("B", 0.1, visit_duration=220),
            self.poi("C", 0.2, visit_duration=220),
            self.poi("D", 0.6),
            self.poi("E", 0.61),
        ]
        routes = optimize_poi_order(pois, 2, "自驾")
        self.assertEqual(sorted(len(day["attractions"]) for day in routes), [2, 3])
        self.assertEqual(sum(len(day["attractions"]) for day in routes), 5)
        self.assertTrue(any("调整分组" in day["optimization_reason"] for day in routes))
        report = evaluate_trip_plan({
            "days": [
                {"day_index": day["day_index"], "attractions": day["attractions"],
                 "route_summary": {"estimated_distance_km": day["estimated_distance_km"]}}
                for day in routes
            ],
        }, self.multi_day_request(2, "自驾"), routes)
        self.assertTrue(report["passed"])

    async def test_unplaceable_poi_returns_to_replacement_pool(self):
        first = self.poi("A", 113.0)
        distant = self.poi("B", 114.0)
        result = await route_optimizer_node({
            "request": self.multi_day_request(1, "步行"),
            "pois": [first, distant],
            "pois_pool": [],
            "node_traces": [],
        })
        self.assertEqual(len(result["route_skeleton"][0]["attractions"]), 1)
        self.assertEqual(len(result["pois"]), 1)
        self.assertEqual(len(result["pois_pool"]), 1)
        self.assertNotEqual(result["pois"][0].name, result["pois_pool"][0].name)


if __name__ == "__main__":
    unittest.main()
