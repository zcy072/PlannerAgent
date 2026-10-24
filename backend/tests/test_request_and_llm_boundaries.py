"""Request-boundary validation and LLM factory cache-key tests."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.llm import LLMFactory, resolve_provider
from app.models.schemas import ChatRequest, ChatResponse, ItemModifyRequest, LLMProvider, TripRequest
from app.services import trip_result_service
from app.services.trip_modify_service import validate_request


def _build(start: str, end: str, days: int) -> TripRequest:
    return TripRequest(city="郑州", start_date=start, end_date=end, travel_days=days)


class RequestDateFormatTests(unittest.TestCase):
    def test_non_iso_dates_are_rejected_at_the_boundary(self):
        with self.assertRaises(ValidationError):
            _build("2026-1-5", "2026-1-5", 1)

    def test_iso_dates_are_accepted(self):
        self.assertEqual(_build("2026-01-05", "2026-01-06", 2).travel_days, 2)

    def test_free_text_and_preferences_are_length_capped(self):
        base = dict(city="郑州", start_date="2026-01-05", end_date="2026-01-06", travel_days=2)
        with self.assertRaises(ValidationError):
            TripRequest(**base, free_text_input="x" * 501)
        with self.assertRaises(ValidationError):
            TripRequest(**base, preferences=["a"] * 21)
        TripRequest(**base, free_text_input="x" * 500, preferences=["a"] * 20)


class LLMFactoryCacheKeyTests(unittest.TestCase):
    def setUp(self):
        LLMFactory._instances.clear()
        self.addCleanup(LLMFactory._instances.clear)

    def test_extra_kwargs_produce_distinct_instances(self):
        with patch.object(LLMFactory, "_create_llm", side_effect=lambda *a, **k: object()):
            a = LLMFactory.get_llm("deepseek", temperature=0.3, timeout=5)
            b = LLMFactory.get_llm("deepseek", temperature=0.3, timeout=30)
            c = LLMFactory.get_llm("deepseek", temperature=0.3, timeout=5)
        self.assertIsNot(a, b)
        self.assertIs(a, c)

    def test_default_timeout_and_retries_are_applied(self):
        seen = {}

        def fake_create(provider, settings, temperature, max_tokens, **kwargs):
            seen.update(kwargs)
            return object()

        with patch.object(LLMFactory, "_create_llm", side_effect=fake_create):
            LLMFactory.get_llm("deepseek")
        self.assertEqual(seen["timeout"], get_settings().llm_timeout_seconds)
        self.assertEqual(seen["max_retries"], get_settings().llm_max_retries)


class ResolveProviderTests(unittest.TestCase):
    def test_enum_string_and_default(self):
        self.assertEqual(resolve_provider(LLMProvider.ALIYUN), "aliyun")
        self.assertEqual(resolve_provider("openai"), "openai")
        with patch("app.core.llm.get_settings") as settings:
            settings.return_value.llm_provider = "aliyun"
            self.assertEqual(resolve_provider(None), "aliyun")


class ReplacementValidationTests(unittest.TestCase):
    PLAN = {"days": [{"attractions": [
        {"name": "A", "address": "a", "location": {"longitude": 113.6, "latitude": 34.7}},
        {"name": "B", "address": "b", "location": {"longitude": 113.7, "latitude": 34.8}},
    ]}]}

    def _validate(self, replacement, item_type="attraction"):
        req = ItemModifyRequest(
            session_id="s1", day_index=0, item_type=item_type,
            item_index=0 if item_type == "attraction" else None,
            action="replace", replacement=replacement,
        )
        return validate_request(self.PLAN, req)

    def test_non_dict_location_is_a_validation_error_not_a_crash(self):
        self.assertIn("坐标", self._validate({"name": "C", "location": "bad"}))

    def test_non_numeric_coordinates_are_rejected(self):
        self.assertIn("坐标", self._validate(
            {"name": "C", "location": {"longitude": "x", "latitude": "y"}}))

    def test_malformed_field_types_are_rejected(self):
        error = self._validate({
            "name": "C", "address": "c", "visit_duration": "abc",
            "location": {"longitude": 113.9, "latitude": 34.9},
        })
        self.assertIn("格式无效", error)

    def test_valid_replacement_passes(self):
        self.assertIsNone(self._validate({
            "name": "C", "address": "c", "location": {"longitude": 113.9, "latitude": 34.9},
        }))


class ProviderSchemaTests(unittest.TestCase):
    def test_chat_request_rejects_unknown_provider_and_long_message(self):
        with self.assertRaises(ValidationError):
            ChatRequest(session_id="s1", message="hi", llm_provider="unknown")
        with self.assertRaises(ValidationError):
            ChatRequest(session_id="s1", message="x" * 501)

    def test_chat_response_action_is_limited_to_known_intents(self):
        self.assertEqual(ChatResponse().action, "answer")
        for action in ("delete", "replace", "answer", "clarify"):
            self.assertEqual(ChatResponse(action=action).action, action)
        with self.assertRaises(ValidationError):
            ChatResponse(action="unknown")


class ResultSaveConsistencyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = patch.object(trip_result_service, "RESULTS_DIR", self._tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _request(self, city: str, start: str, end: str) -> TripRequest:
        return TripRequest(city=city, start_date=start, end_date=end, travel_days=2)

    def _json_files(self) -> list[str]:
        return sorted(Path(self._tmp.name).glob("*.json"))

    def test_resaving_a_session_with_new_city_replaces_the_old_file(self):
        save = trip_result_service.save_trip_result
        save("sess-1", {"v": 1}, self._request("郑州", "2026-01-05", "2026-01-06"))
        save("sess-1", {"v": 2}, self._request("洛阳", "2026-02-05", "2026-02-06"))

        files = self._json_files()
        self.assertEqual(len(files), 1)
        self.assertEqual(trip_result_service.load_trip_result("sess-1"), {"v": 2})

    def test_resaving_does_not_touch_other_sessions(self):
        save = trip_result_service.save_trip_result
        save("sess-a", {"v": "a"}, self._request("郑州", "2026-01-05", "2026-01-06"))
        save("sess-b", {"v": "b"}, self._request("郑州", "2026-01-05", "2026-01-06"))
        save("sess-a", {"v": "a2"}, self._request("洛阳", "2026-02-05", "2026-02-06"))

        self.assertEqual(len(self._json_files()), 2)
        self.assertEqual(trip_result_service.load_trip_result("sess-b"), {"v": "b"})

    def test_update_refuses_to_overwrite_a_corrupt_file(self):
        path = trip_result_service.save_trip_result(
            "sess-1", {"v": 1}, self._request("郑州", "2026-01-05", "2026-01-06"),
        )
        # _find_filepath_by_session skips unreadable files, so simulate the race
        # where the file is damaged between lookup and read.
        Path(path).write_text("{broken", encoding="utf-8")
        with patch.object(trip_result_service, "_find_filepath_by_session", return_value=path):
            self.assertIsNone(trip_result_service.save_trip_result("sess-1", {"v": 2}, None))
        self.assertEqual(Path(path).read_text(encoding="utf-8"), "{broken")

    def test_prune_removes_only_stale_orphan_temp_files(self):
        import os
        old = Path(self._tmp.name) / ".trip-result-old.tmp"
        fresh = Path(self._tmp.name) / ".trip-result-fresh.tmp"
        for item in (old, fresh):
            item.write_text("{}", encoding="utf-8")
        stale = old.stat().st_mtime - 10 * 86400
        os.utime(old, (stale, stale))

        trip_result_service._prune_expired_results(retention_days=7)

        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())


class SyncPlanConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_run_rejects_a_session_that_is_already_running(self):
        from app.workflows.trip import graph as trip_graph

        request = TripRequest(
            city="郑州", start_date="2026-01-05", end_date="2026-01-06",
            travel_days=2, session_id="busy-session",
        )
        trip_graph._active_sessions.add("busy-session")
        self.addCleanup(trip_graph._active_sessions.discard, "busy-session")
        with self.assertRaises(trip_graph.PlanningSessionConflict):
            await trip_graph.run_trip_graph(request)
        # The rejected call must not release the other run's claim on the session.
        self.assertIn("busy-session", trip_graph._active_sessions)


class ResumeRequestLoadTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkpoint_read_failure_means_nothing_to_resume(self):
        from unittest.mock import AsyncMock, MagicMock
        from app.workflows.trip import graph as trip_graph

        broken = MagicMock()
        broken.aget_state = AsyncMock(side_effect=RuntimeError("db locked"))
        with patch.object(trip_graph, "get_trip_graph", AsyncMock(return_value=broken)):
            self.assertIsNone(await trip_graph.load_resume_request("sess-1"))


class StaticMapRouteTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, markers, payload=b"\x89PNG....", city="郑州"):
        from unittest.mock import AsyncMock, MagicMock
        from app.api.routes import map as map_routes

        service = MagicMock()
        service.get_static_map = AsyncMock(return_value=payload)
        with patch.object(map_routes, "get_amap_service", return_value=service):
            return await map_routes.get_static_map(city=city, markers=markers)

    async def test_valid_markers_return_image(self):
        response = await self._call("113.6,34.7|113.7,34.8")
        self.assertEqual(response.media_type, "image/png")

    async def test_malformed_markers_are_rejected(self):
        from fastapi import HTTPException
        for bad in ("abc", "113.6,34.7&key=x", "113.6"):
            with self.subTest(markers=bad), self.assertRaises(HTTPException) as ctx:
                await self._call(bad)
            self.assertEqual(ctx.exception.status_code, 422)

    async def test_amap_error_body_is_not_forwarded_as_image(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            await self._call(None, payload=b'{"status":"0","info":"INVALID_USER_KEY"}')
        self.assertEqual(ctx.exception.status_code, 502)


class ExtractCoordinatesTests(unittest.TestCase):
    def test_canonical_alias_and_invalid_inputs(self):
        from app.planning.location_utils import extract_coordinates

        self.assertEqual(extract_coordinates({"longitude": 113.6, "latitude": 34.7}), (113.6, 34.7))
        self.assertEqual(extract_coordinates({"lng": "113.6", "lat": "34.7"}), (113.6, 34.7))
        # 0 is a real value and must not fall through to the alias.
        self.assertIsNone(extract_coordinates({"longitude": 0, "latitude": 0, "lng": 113, "lat": 34}))
        for bad in (None, "x", [], {}, {"longitude": 200, "latitude": 10}):
            with self.subTest(value=bad):
                self.assertIsNone(extract_coordinates(bad))


class PoiFromDictTests(unittest.TestCase):
    def test_malformed_location_becomes_none_instead_of_raising(self):
        from app.agents.poi_agent import _poi_from_dict

        for bad in ("113,34", [113, 34], {"longitude": "x", "latitude": 1}, {"longitude": 999, "latitude": 1}):
            with self.subTest(location=bad):
                self.assertIsNone(_poi_from_dict({"name": "A", "location": bad}).location)
        good = _poi_from_dict({"name": "A", "location": {"longitude": "113.6", "latitude": 34.7}})
        self.assertEqual((good.location.longitude, good.location.latitude), (113.6, 34.7))


class ChatHistoryConsistencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_router_failure_leaves_no_orphan_user_turn(self):
        from unittest.mock import AsyncMock
        from app.services import chat_history, trip_chat_service

        chat_history._chat_history.clear()
        self.addCleanup(chat_history._chat_history.clear)
        repo = AsyncMock()
        repo.load.return_value = {"days": []}
        with patch.object(trip_chat_service, "trip_result_repository", repo), \
             patch.object(trip_chat_service, "run_chat_router", AsyncMock(side_effect=RuntimeError("llm down"))):
            with self.assertRaises(RuntimeError):
                await trip_chat_service.handle_chat("sess-1", "删掉第一个景点", "deepseek")
        self.assertEqual(list(chat_history.history_for_session("sess-1")), [])

    def test_stored_messages_are_truncated(self):
        from app.core.constants import CHAT_MESSAGE_MAX_CHARS
        from app.services import chat_history

        chat_history._chat_history.clear()
        self.addCleanup(chat_history._chat_history.clear)
        chat_history.record_message("sess-2", "assistant", "x" * (CHAT_MESSAGE_MAX_CHARS + 500))
        stored = list(chat_history.history_for_session("sess-2"))[0]["content"]
        self.assertEqual(len(stored), CHAT_MESSAGE_MAX_CHARS)


if __name__ == "__main__":
    unittest.main()
