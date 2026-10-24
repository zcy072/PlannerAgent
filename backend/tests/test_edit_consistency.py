"""Regression coverage for planning ownership and editing stale trip data."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.routes.trip import create_trip_plan
from app.models.schemas import ItemModifyRequest, TripPlan
from app.planning.budget import estimate_budget
from app.services.trip_chat_service import handle_chat
from app.services.trip_modify_service import apply_modification
from app.workflows.trip import graph
from test_resume_and_quality import _good_plan, make_request


class BudgetRecalculationTests(unittest.IsolatedAsyncioTestCase):
    async def test_estimated_transport_is_recalculated_after_route_edit_and_round_trip(self):
        plan = _good_plan()
        day = plan["days"][0]
        day["transportation"] = "公共交通"
        day["route_summary"]["estimated_distance_km"] = 50
        day["attractions"].append({**deepcopy(day["attractions"][0]), "name": "景点B"})
        plan["budget"] = estimate_budget(plan)
        plan = TripPlan.model_validate(plan).model_dump()
        self.assertGreater(plan["budget"]["total_transportation"], 0)
        req = ItemModifyRequest(session_id="edit-1", day_index=0,
                                item_type="attraction", item_index=1, action="delete")
        with patch("app.services.trip_modify_service._rewrite_day_description", new=AsyncMock(return_value="新描述")):
            updated = await apply_modification(plan, req)
        self.assertEqual(updated["budget"]["total_transportation"], 0)
        self.assertTrue(updated["budget"]["transportation_is_estimated"])

    def test_explicit_legacy_transport_amount_is_preserved(self):
        plan = _good_plan()
        plan["budget"] = {"total_transportation": 80}
        recalculated = estimate_budget(plan)
        self.assertEqual(recalculated["total_transportation"], 80)
        self.assertFalse(recalculated["transportation_is_estimated"])


class PlanningOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_rejects_session_owned_by_stream_without_touching_checkpoint(self):
        graph._active_sessions.add("resume-1")
        self.addCleanup(graph._active_sessions.clear)
        compiled = SimpleNamespace(checkpointer=None, ainvoke=AsyncMock(return_value={"trip_plan": {}}))
        with (
            patch.object(graph, "discard_checkpoint", new=AsyncMock()) as discard,
            patch.object(graph, "get_trip_graph", new=AsyncMock(return_value=compiled)),
            patch("app.api.routes.trip.trip_result_repository.save", new=AsyncMock(return_value="saved.json")),
        ):
            with self.assertRaises(HTTPException) as caught:
                await create_trip_plan(make_request())
        self.assertEqual(caught.exception.status_code, 409)
        discard.assert_not_awaited()
        self.assertIn("resume-1", graph._active_sessions)

    async def test_sync_owns_session_until_completion_and_releases_after_cancel(self):
        started = asyncio.Event()

        async def invoke(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        compiled = SimpleNamespace(checkpointer=None, ainvoke=invoke)
        with patch.object(graph, "get_trip_graph", new=AsyncMock(return_value=compiled)):
            task = asyncio.create_task(graph.run_trip_graph(make_request()))
            try:
                await asyncio.wait_for(started.wait(), 2)
                events = [event async for event in graph.run_trip_graph_stream(make_request())]
                self.assertEqual(len(events), 1)
                self.assertIn("正在规划中", events[0])
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertNotIn("resume-1", graph._active_sessions)


class ChatConcurrentEditTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_does_not_delete_another_attraction_after_concurrent_edit(self):
        original = _good_plan()
        original["days"][0]["attractions"].append({
            **deepcopy(original["days"][0]["attractions"][0]), "name": "景点B",
        })
        changed = deepcopy(original)
        changed["days"][0]["attractions"].reverse()
        intent = {"action": "delete", "day_index": 0, "item_type": "attraction",
                  "item_index": 0, "target_name": "景点A"}
        with (
            patch("app.services.trip_chat_service.trip_result_repository.load",
                  new=AsyncMock(side_effect=[original, changed])),
            patch("app.services.trip_chat_service.run_chat_router", new=AsyncMock(return_value=intent)),
            patch("app.services.trip_modify_service.trip_result_repository.save", new=AsyncMock()) as save,
            patch("app.services.trip_modify_service._rewrite_day_description", new=AsyncMock(return_value="新描述")),
        ):
            result = await handle_chat("edit-race", "删除景点A")
        self.assertFalse(result["modified"])
        self.assertIn("变化", result["reply"])
        save.assert_not_awaited()
