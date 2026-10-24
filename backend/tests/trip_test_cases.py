"""Shared test cases grouped by domain discovery modules."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError
from langgraph.checkpoint.memory import InMemorySaver

from app.models.schemas import Hotel, ItemModifyRequest, Location, POI, StreamingResponse, TripPlan, TripRequest, Weather
from app.workflows.trip.graph import (
    MAX_EVALUATOR_RETRY,
    build_trip_graph,
    evaluator_router,
    route_verification_node,
    run_trip_graph,
    run_trip_graph_stream,
)
from app.planning.rule_evaluator import evaluate_trip_plan
from app.agents.hotel_agent import run_hotel_agent
from app.agents.planner_agent import _format_pois
from app.agents.poi_agent import (
    _filter_and_select,
    _run_poi_agent_fallback,
    _run_poi_agent_with_tool_calling,
)
from app.utils.llm_json import parse_json_object
from app.agents.utils.pool_manager import build_balanced_candidates, select_candidates
from app.planning.itinerary_optimizer import (
    _choose_spread_centers,
    _nearest_neighbor_order,
    _poi_cluster_key,
    _rebalance_groups,
    optimize_poi_order,
)
from app.planning.plan_postprocessor import (
    apply_route_skeleton,
    fix_coordinates,
    normalize_days,
    parse_planner_response,
)
from app.api.routes.trip import _persist_complete_stream_event, get_saved_trip_plan, modify_trip_item
from app.services.amap_mcp_service import (
    RouteVerificationResult,
    _extract_route_metric,
    _select_route_tool,
    _verify_route_days,
    verify_route_skeleton,
    _positive_timeout,
)
from app.services.amap_service import AmapService, _parse_cost_to_int
from app.services import trip_result_service
from app.services.chat_history import _chat_history
from app.services.trip_chat_service import (
    _history_for_session,
    _resolve_replacement,
    handle_chat,
)
from app.services.trip_modify_service import (
    _reorder_day_route,
    _rewrite_day_description,
    apply_modification,
    validate_request,
)


class TripResultServiceTests(unittest.TestCase):
    def test_find_filepath_requires_full_session_id_match(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session_id = "abcdefgh-real-session"
            colliding_session_id = "abcdefgh-other-session"
            result_path = Path(temp_dir) / "郑州_2026-05-29_abcdefgh.json"
            result_path.write_text(
                json.dumps(
                    {
                        "session_id": colliding_session_id,
                        "result": {"city": "郑州"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.object(trip_result_service, "RESULTS_DIR", temp_dir):
                self.assertIsNone(trip_result_service._find_filepath_by_session(session_id))

    def test_safe_result_filepath_rejects_directory_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            trip_result_service,
            "RESULTS_DIR",
            temp_dir,
        ):
            with self.assertRaises(ValueError):
                trip_result_service._safe_result_filepath(
                    "../../outside",
                    "2026-05-29",
                    "session-1",
                )

    def test_full_session_hash_prevents_prefix_collision(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            trip_result_service, "RESULTS_DIR", temp_dir
        ):
            first = trip_result_service._safe_result_filepath(
                "郑州", "2026-05-29", "abcdefgh-first"
            )
            second = trip_result_service._safe_result_filepath(
                "郑州", "2026-05-29", "abcdefgh-second"
            )
        self.assertNotEqual(first, second)

    def test_atomic_write_leaves_valid_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = str(Path(temp_dir) / "result.json")
            trip_result_service._atomic_write_json(path, {"value": 1})
            trip_result_service._atomic_write_json(path, {"value": 2})
            self.assertEqual(json.loads(Path(path).read_text(encoding="utf-8")), {"value": 2})


class TripModifyValidationTests(unittest.TestCase):
    def setUp(self):
        self.trip_plan = {
            "days": [
                {
                    "attractions": [
                        {
                            "name": "景点A",
                            "location": {"longitude": 113.6, "latitude": 34.7},
                        },
                        {
                            "name": "景点B",
                            "location": {"longitude": 113.7, "latitude": 34.8},
                        },
                    ],
                    "hotel": {
                        "name": "酒店A",
                        "location": {"longitude": 113.65, "latitude": 34.75},
                    },
                }
            ]
        }

    def test_rejects_negative_attraction_index(self):
        with self.assertRaises(ValidationError):
            ItemModifyRequest(
                session_id="session-1",
                day_index=0,
                item_type="attraction",
                item_index=-1,
                action="delete",
            )

    def test_rejects_hotel_replacement_without_location(self):
        req = ItemModifyRequest(
            session_id="session-1",
            day_index=0,
            item_type="hotel",
            action="replace",
            replacement={"name": "新酒店"},
        )

        self.assertEqual(validate_request(self.trip_plan, req), "replacement 缺少有效坐标")

    def test_rejects_hotel_item_index(self):
        req = ItemModifyRequest(
            session_id="session-1",
            day_index=0,
            item_type="hotel",
            item_index=0,
            action="replace",
            replacement={
                "name": "新酒店",
                "location": {"longitude": 113.6, "latitude": 34.7},
            },
        )

        self.assertEqual(validate_request(self.trip_plan, req), "hotel 修改不应提供 item_index")

    def test_rejects_out_of_range_replacement_location(self):
        req = ItemModifyRequest(
            session_id="session-1",
            day_index=0,
            item_type="attraction",
            item_index=0,
            action="replace",
            replacement={
                "name": "新景点",
                "location": {"longitude": 999, "latitude": 34.7},
            },
        )

        self.assertEqual(validate_request(self.trip_plan, req), "replacement 坐标格式无效")

    def test_rejects_replacing_with_attraction_already_on_another_day(self):
        self.trip_plan["days"].append({"attractions": [{
            "name": "景点C", "address": "郑州", "location": {"longitude": 113.8, "latitude": 34.9},
        }]})
        req = ItemModifyRequest(
            session_id="session-1",
            day_index=0,
            item_type="attraction",
            item_index=0,
            action="replace",
            replacement={
                "name": "景点C", "address": "郑州",
                "location": {"longitude": 113.8, "latitude": 34.9},
            },
        )

        self.assertEqual(
            validate_request(self.trip_plan, req),
            "replacement 景点已经出现在行程中，请选择其他景点",
        )

    def test_accepts_coordinate_on_prime_meridian(self):
        req = ItemModifyRequest(
            session_id="session-1",
            day_index=0,
            item_type="attraction",
            item_index=0,
            action="replace",
            replacement={
                "name": "新景点",
                "location": {"longitude": 0, "latitude": 34.7},
            },
        )

        self.assertIsNone(validate_request(self.trip_plan, req))


class TripModifyRewriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_rewrite_failure_uses_current_attractions_in_fallback(self):
        day = {
            "description": "旧描述提到已删除景点",
            "attractions": [{"name": "当前景点A"}, {"name": "当前景点B"}],
        }
        with patch("app.services.trip_modify_service.get_llm", side_effect=RuntimeError("offline")):
            description = await _rewrite_day_description(day, "deepseek")

        self.assertEqual(description, "当天安排：当前景点A、当前景点B。")

    async def test_rewrite_day_description_uses_modify_prompt(self):
        class FakeResponse:
            content = "上午串联景点A和景点B，节奏轻松。"

        class FakeLLM:
            async def ainvoke(self, messages):
                prompt = messages[0].content
                self.prompt = prompt
                return FakeResponse()

        fake_llm = FakeLLM()
        day = {
            "date": "2026-05-29",
            "day_index": 0,
            "description": "旧描述",
            "attractions": [
                {"name": "景点A"},
                {"name": "景点B"},
            ],
        }

        with patch("app.services.trip_modify_service.get_llm", return_value=fake_llm):
            description = await _rewrite_day_description(day, "deepseek")

        self.assertEqual(description, "上午串联景点A和景点B，节奏轻松。")
        self.assertIn("景点A、景点B", fake_llm.prompt)

    async def test_apply_modification_uses_requested_llm_provider(self):
        class FakeLLM:
            async def ainvoke(self, messages):
                return SimpleNamespace(content="新描述")

        trip_plan = {
            "city": "郑州",
            "weather_info": [],
            "days": [{
                "date": "2026-05-29",
                "day_index": 0,
                "description": "旧描述",
                "attractions": [
                    {"name": "景点A", "location": {"longitude": 113.60, "latitude": 34.70}},
                    {"name": "景点B", "location": {"longitude": 113.61, "latitude": 34.71}},
                ],
            }],
        }
        for provider, expected in ((None, "deepseek"), ("aliyun", "aliyun")):
            req = ItemModifyRequest(
                session_id="s", day_index=0, item_type="attraction",
                item_index=1, action="delete", llm_provider=provider,
            )
            with patch(
                "app.services.trip_modify_service.get_llm", return_value=FakeLLM(),
            ) as get_llm:
                await apply_modification(trip_plan, req)
            self.assertEqual(get_llm.call_args.args[0], expected)


class TripModifyFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_replacement_updates_route_budget_and_description(self):
        trip_plan = {
            "city": "郑州",
            "start_date": "2026-05-29",
            "end_date": "2026-05-29",
            "weather_info": [],
            "days": [
                {
                    "date": "2026-05-29",
                    "day_index": 0,
                    "description": "旧描述",
                    "transportation": "公共交通",
                    "accommodation": "经济型酒店",
                    "attractions": [
                        {
                            "name": "景点A",
                            "address": "地址A",
                            "location": {"longitude": 113.60, "latitude": 34.70},
                            "visit_duration": 90,
                            "ticket_price": 20,
                        },
                        {
                            "name": "景点B",
                            "address": "地址B",
                            "location": {"longitude": 113.61, "latitude": 34.71},
                            "visit_duration": 120,
                            "ticket_price": 30,
                        },
                        {
                            "name": "景点C",
                            "address": "地址C",
                            "location": {"longitude": 113.62, "latitude": 34.72},
                            "visit_duration": 120,
                            "ticket_price": 40,
                        },
                    ],
                    "meals": [
                        {"type": "lunch", "name": "午餐", "estimated_cost": 30},
                    ],
                    "hotel": {
                        "name": "酒店A",
                        "address": "酒店地址",
                        "location": {"longitude": 113.60, "latitude": 34.70},
                        "estimated_cost": 100,
                    },
                }
            ],
            "budget": {"total_transportation": 50},
            "evaluator_report": {
                "passed": False,
                "issues": ["旧路线超时"],
                "suggestions": [],
                "day_issues": {0: ["旧路线超时"]},
                "global_issues": [],
                "non_retryable_issues": [],
            },
            "evaluator_warnings": ["旧路线超时"],
        }
        request = ItemModifyRequest(
            session_id="session-1",
            day_index=0,
            item_type="attraction",
            item_index=1,
            action="replace",
            replacement={
                "name": "新景点",
                "address": "新地址",
                "location": {"longitude": 113.63, "latitude": 34.73},
                "visit_duration": 150,
                "ticket_price": 80,
            },
        )

        with patch(
            "app.services.trip_modify_service._rewrite_day_description",
            new=AsyncMock(return_value="替换后的当天描述"),
        ) as rewrite_day:
            updated = await apply_modification(trip_plan, request)

        names = {item["name"] for item in updated["days"][0]["attractions"]}
        self.assertEqual(len(updated["days"][0]["attractions"]), 3)
        self.assertIn("新景点", names)
        self.assertNotIn("景点B", names)
        self.assertEqual(updated["days"][0]["description"], "替换后的当天描述")
        self.assertIn("route_summary", updated["days"][0])
        self.assertEqual(updated["budget"]["total_attractions"], 140)
        self.assertEqual(updated["budget"]["total"], 320)
        self.assertIsNone(updated["evaluator_report"])
        self.assertEqual(updated["evaluator_warnings"], [])
        rewrite_day.assert_awaited_once()

    def test_reorder_preserves_duplicate_names_and_clears_stale_duration(self):
        day = {
            "attractions": [
                {
                    "name": "同名景点",
                    "address": "地址A",
                    "location": {"longitude": 113.60, "latitude": 34.70},
                },
                {
                    "name": "同名景点",
                    "address": "地址B",
                    "location": {"longitude": 113.61, "latitude": 34.71},
                },
            ],
            "route_summary": {
                "estimated_distance_km": 10,
                "estimated_duration_min": 50,
            },
        }

        _reorder_day_route(day)

        self.assertEqual(len(day["attractions"]), 2)
        self.assertEqual(
            {attraction["address"] for attraction in day["attractions"]},
            {"地址A", "地址B"},
        )
        self.assertIsNone(day["route_summary"]["estimated_duration_min"])


class TripModifyPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_modify_response_warns_when_save_fails(self):
        plan = {
            "city": "郑州",
            "start_date": "2026-05-29",
            "end_date": "2026-05-29",
            "days": [{
                "date": "2026-05-29",
                "day_index": 0,
                "description": "当天",
                "transportation": "公共交通",
                "accommodation": "经济型酒店",
                "attractions": [{
                    "name": "景点A",
                    "address": "地址A",
                    "location": {"longitude": 113.6, "latitude": 34.7},
                    "visit_duration": 120,
                    "ticket_price": 0,
                }],
                "meals": [],
            }],
            "weather_info": [],
        }
        request = ItemModifyRequest(
            session_id="session-1",
            day_index=0,
            item_type="attraction",
            item_index=0,
            action="replace",
            replacement={
                "name": "景点B",
                "address": "地址B",
                "location": {"longitude": 113.7, "latitude": 34.8},
            },
        )

        with (
            patch(
                "app.api.routes.trip.modify_persisted_trip",
                new=AsyncMock(return_value=(TripPlan.model_validate(plan), False, "行程已修改，但保存失败；当前可以查看，刷新后修改会丢失。")),
            ),
        ):
            response = await modify_trip_item(request)

        self.assertTrue(response.success)
        self.assertFalse(response.persistence_saved)
        self.assertIn("刷新后修改会丢失", response.persistence_warning)


class TripResultRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_saved_trip_plan_returns_validated_plan(self):
        saved_plan = {
            "city": "郑州",
            "start_date": "2026-05-29",
            "end_date": "2026-05-29",
            "days": [],
            "weather_info": [],
        }

        with patch(
            "app.api.routes.trip.trip_result_repository.load",
            new=AsyncMock(return_value=saved_plan),
        ):
            response = await get_saved_trip_plan("session-1")

        self.assertTrue(response["success"])
        self.assertEqual(response["data"].city, "郑州")


class TripResultRepositoryConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_session_transactions_are_serialized(self):
        repository = trip_result_service.TripResultRepository()
        order = []

        async def worker(name: str):
            async with repository.transaction("same-session"):
                order.append(f"{name}:enter")
                await asyncio.sleep(0)
                order.append(f"{name}:exit")

        await asyncio.gather(worker("first"), worker("second"))

        self.assertEqual(
            order,
            ["first:enter", "first:exit", "second:enter", "second:exit"],
        )


class TripStreamPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_event_keeps_itinerary_when_persistence_fails(self):
        request = TripRequest(
            city="郑州",
            start_date="2026-05-29",
            end_date="2026-05-29",
            travel_days=1,
        )
        event = "data: " + json.dumps({
            "session_id": "session-1",
            "step": 9,
            "node": "complete",
            "status": "completed",
            "message": "行程规划已生成",
            "data": {"itinerary": {"city": "郑州"}},
        }, ensure_ascii=False) + "\n\n"

        with patch(
            "app.api.routes.trip.trip_result_repository.save",
            new=AsyncMock(return_value=None),
        ):
            result = await _persist_complete_stream_event(event, "session-1", request)

        payload = json.loads(result[6:].strip())
        self.assertEqual(payload["data"]["itinerary"], {"city": "郑州"})
        self.assertFalse(payload["data"]["persistence_saved"])
        self.assertIn("刷新页面后将无法恢复", payload["data"]["persistence_warning"])

    async def test_non_complete_event_is_not_rewritten(self):
        request = TripRequest(
            city="郑州",
            start_date="2026-05-29",
            end_date="2026-05-29",
            travel_days=1,
        )
        event = 'data: {"node":"planner","data":{}}\n\n'
        self.assertEqual(
            await _persist_complete_stream_event(event, "session-1", request),
            event,
        )


class TripRequestSchemaTests(unittest.TestCase):
    def test_rejects_city_path_traversal(self):
        with self.assertRaises(ValidationError):
            TripRequest(
                city="../../tmp/pwned",
                start_date="2026-05-29",
                end_date="2026-05-29",
                travel_days=1,
            )

    def test_rejects_mismatched_travel_days(self):
        with self.assertRaises(ValidationError):
            TripRequest(
                city="郑州",
                start_date="2026-05-29",
                end_date="2026-05-30",
                travel_days=3,
            )

    def test_rejects_invalid_budget_range(self):
        with self.assertRaises(ValidationError):
            TripRequest(
                city="郑州",
                start_date="2026-05-29",
                end_date="2026-05-30",
                travel_days=2,
                budget=[3000, 1000],
            )

    def test_rejects_unknown_llm_provider(self):
        with self.assertRaises(ValidationError):
            TripRequest(
                city="郑州",
                start_date="2026-05-29",
                end_date="2026-05-30",
                travel_days=2,
                llm_provider="unknown",
            )


class PlannerPostprocessorTests(unittest.TestCase):
    def test_falls_back_when_planner_returns_json_array(self):
        plan = parse_planner_response(
            response="[]",
            city="郑州",
            start_date="2026-05-29",
            end_date="2026-05-29",
            travel_days=1,
            transportation="公共交通",
            accommodation="经济型酒店",
            pois=[],
            weather=[],
        )

        self.assertEqual(plan["city"], "郑州")
        self.assertEqual(len(plan["days"]), 1)
        TripPlan.model_validate(plan)

    def test_falls_back_when_planner_returns_invalid_day_shape(self):
        plan = parse_planner_response(
            response='{"days": [{"day_index": 0, "attractions": "invalid"}]}',
            city="郑州",
            start_date="2026-05-29",
            end_date="2026-05-29",
            travel_days=1,
            transportation="公共交通",
            accommodation="经济型酒店",
            pois=[],
            weather=[],
        )

        self.assertIsInstance(plan["days"][0]["attractions"], list)
        TripPlan.model_validate(plan)

    def test_normalize_days_backfills_missing_days(self):
        pois = [
            POI(
                name="景点A",
                address="地址A",
                location=Location(longitude=113.6, latitude=34.7),
            ),
            POI(
                name="景点B",
                address="地址B",
                location=Location(longitude=113.7, latitude=34.8),
            ),
        ]
        generated_days = [
            {
                "date": "2099-01-01",
                "day_index": 0,
                "description": "已有第一天",
                "attractions": [],
                "meals": [],
            }
        ]
        route_skeleton = [
            {
                "day_index": 0,
                "attractions": [
                    {
                        "name": "景点A",
                        "address": "地址A",
                        "location": {"longitude": 113.6, "latitude": 34.7},
                    }
                ],
                "estimated_distance_km": 0,
                "estimated_duration_min": 12,
                "optimization_reason": "测试路线骨架",
            },
            {
                "day_index": 1,
                "attractions": [
                    {
                        "name": "景点B",
                        "address": "地址B",
                        "location": {"longitude": 113.7, "latitude": 34.8},
                    }
                ],
                "estimated_distance_km": 0,
                "estimated_duration_min": 15,
                "optimization_reason": "测试路线骨架",
            },
        ]

        days = normalize_days(
            generated_days,
            city="郑州",
            start_date="2026-05-29",
            travel_days=2,
            transportation="公共交通",
            accommodation="经济型酒店",
            pois=pois,
            route_skeleton=route_skeleton,
        )

        self.assertEqual(len(days), 2)
        self.assertEqual([day["day_index"] for day in days], [0, 1])
        self.assertEqual([day["date"] for day in days], ["2026-05-29", "2026-05-30"])
        self.assertEqual(days[0]["description"], "已有第一天")
        self.assertTrue(days[1]["attractions"])
        self.assertEqual(days[1]["route_summary"]["estimated_duration_min"], 15)

    def test_normalize_days_accepts_one_based_indices(self):
        generated_days = [
            {
                "day_index": 1,
                "description": "LLM第一天",
                "attractions": [],
                "meals": [],
            },
            {
                "day_index": 2,
                "description": "LLM第二天",
                "attractions": [],
                "meals": [],
            },
        ]

        days = normalize_days(
            generated_days,
            city="武汉",
            start_date="2026-01-01",
            travel_days=2,
            transportation="公共交通",
            accommodation="经济型酒店",
            pois=[],
        )

        self.assertEqual(
            [day["description"] for day in days],
            ["LLM第一天", "LLM第二天"],
        )
        self.assertEqual([day["day_index"] for day in days], [0, 1])

    def test_fix_coordinates_skips_empty_attraction_name(self):
        trip_plan = {
            "days": [{
                "attractions": [{
                    "name": "",
                    "location": {"longitude": 0, "latitude": 0},
                }],
            }],
        }
        pois = [POI(
            name="景点A",
            address="地址A",
            location=Location(longitude=113.6, latitude=34.7),
        )]

        fix_coordinates(trip_plan, pois)

        self.assertEqual(
            trip_plan["days"][0]["attractions"][0]["location"],
            {"longitude": 0, "latitude": 0},
        )

    def test_fix_coordinates_preserves_valid_equator_coordinate(self):
        trip_plan = {
            "days": [{
                "attractions": [{
                    "name": "赤道景点",
                    "location": {"longitude": 116.0, "latitude": 0.0},
                }],
            }],
        }
        pois = [POI(
            name="赤道景点",
            address="其他地址",
            location=Location(longitude=116.0, latitude=39.0),
        )]

        fix_coordinates(trip_plan, pois)

        self.assertEqual(
            trip_plan["days"][0]["attractions"][0]["location"],
            {"longitude": 116.0, "latitude": 0.0},
        )

    def test_planner_poi_display_covers_every_skeleton_attraction(self):
        pois = [
            POI(name=f"景点{i}", address=f"地址{i}", location=Location(longitude=113.0 + i / 100, latitude=34.0))
            for i in range(20)
        ]

        default_info = _format_pois(pois)
        full_info = _format_pois(pois, required_count=20)

        self.assertNotIn("景点19", default_info)
        self.assertIn("景点19", full_info)

    def test_fix_coordinates_prefers_exact_name_over_substring(self):
        trip_plan = {"days": [{"attractions": [{"name": "西湖", "location": None}]}]}
        pois = [
            POI(name="西湖文化广场", address="甲", location=Location(longitude=1.0, latitude=1.0)),
            POI(name="西湖", address="乙", location=Location(longitude=120.1, latitude=30.2)),
        ]

        fix_coordinates(trip_plan, pois)

        self.assertEqual(
            trip_plan["days"][0]["attractions"][0]["location"],
            {"longitude": 120.1, "latitude": 30.2},
        )

    def test_fix_coordinates_uses_address_to_pick_between_same_names(self):
        trip_plan = {"days": [{"attractions": [
            {"name": "西湖", "address": "南段", "location": None},
        ]}]}
        pois = [
            POI(name="西湖", address="北段", location=Location(longitude=1.0, latitude=1.0)),
            POI(name="西湖", address="南段", location=Location(longitude=120.1, latitude=30.2)),
        ]

        fix_coordinates(trip_plan, pois)

        self.assertEqual(
            trip_plan["days"][0]["attractions"][0]["location"],
            {"longitude": 120.1, "latitude": 30.2},
        )

    def test_fix_coordinates_skips_ambiguous_substring_match(self):
        trip_plan = {"days": [{"attractions": [{"name": "公园", "location": None}]}]}
        pois = [
            POI(name="人民公园", address="甲", location=Location(longitude=1.0, latitude=1.0)),
            POI(name="中央公园", address="乙", location=Location(longitude=2.0, latitude=2.0)),
        ]

        fix_coordinates(trip_plan, pois)

        self.assertIsNone(trip_plan["days"][0]["attractions"][0]["location"])

    def test_route_skeleton_consumes_duplicate_names_in_order(self):
        trip_plan = {
            "days": [{
                "day_index": 0,
                "attractions": [
                    {"name": "西湖", "address": "北段", "description": "第一段"},
                    {"name": "西湖", "address": "南段", "description": "第二段"},
                ],
            }],
        }
        route_skeleton = [{
            "day_index": 0,
            "attractions": [
                {"name": "西湖", "address": "北段"},
                {"name": "西湖", "address": "南段"},
            ],
        }]

        apply_route_skeleton(trip_plan, route_skeleton)

        self.assertEqual(
            [item["description"] for item in trip_plan["days"][0]["attractions"]],
            ["第一段", "第二段"],
        )

class ItineraryOptimizerTests(unittest.TestCase):
    @staticmethod
    def _poi(name, longitude=113.6, latitude=34.7, address="地址"):
        location = None
        if longitude is not None and latitude is not None:
            location = Location(longitude=longitude, latitude=latitude)
        return POI(name=name, address=address, location=location)

    def test_returns_empty_for_invalid_inputs(self):
        valid = self._poi("景点A")
        invalid = self._poi("无坐标", None, None)

        self.assertEqual(optimize_poi_order([valid], 0), [])
        self.assertEqual(optimize_poi_order([], 2), [])
        self.assertEqual(optimize_poi_order([invalid], 2), [])

    def test_limits_days_to_valid_poi_count(self):
        routes = optimize_poi_order([
            self._poi("景点A", 113.60, 34.70),
            self._poi("景点B", 113.70, 34.80),
        ], 5)

        self.assertEqual(len(routes), 2)
        self.assertTrue(all(len(day["attractions"]) == 1 for day in routes))

    def test_handles_identical_coordinates(self):
        routes = optimize_poi_order([
            self._poi("景点A"),
            self._poi("景点B"),
            self._poi("景点C"),
        ], 1)

        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["estimated_distance_km"], 0)
        self.assertEqual(len(routes[0]["attractions"]), 3)

    def test_spread_centers_keeps_distinct_equal_poi_instances(self):
        first = self._poi("重复景点")
        second = self._poi("重复景点")

        centers = _choose_spread_centers([first, second], 2)

        self.assertIs(centers[0], first)
        self.assertIs(centers[1], second)

    def test_cluster_key_groups_scenic_sub_attractions(self):
        expected = "嵩山"
        for name in ("嵩山-少林寺", "嵩山—三皇寨", "嵩山（主峰）", "嵩山(索道)"):
            self.assertEqual(_poi_cluster_key(self._poi(name)), expected)

    def test_nearest_neighbor_keeps_zero_one_two_item_order(self):
        pois = [self._poi("A"), self._poi("B")]
        self.assertEqual(_nearest_neighbor_order([]), [])
        self.assertEqual(_nearest_neighbor_order(pois[:1]), pois[:1])
        self.assertEqual(_nearest_neighbor_order(pois), pois)

    def test_nearest_neighbor_chooses_shorter_start(self):
        middle = self._poi("middle", 1, 10)
        left = self._poi("left", 0, 10)
        right = self._poi("right", 2, 10)

        ordered = _nearest_neighbor_order([middle, left, right])

        self.assertEqual([poi.name for poi in ordered], ["left", "middle", "right"])

    def test_rebalance_does_not_split_two_item_donor(self):
        groups = [[self._poi("A"), self._poi("B")], [self._poi("C")]]
        _rebalance_groups(groups)
        self.assertEqual([len(group) for group in groups], [2, 1])


class PoolManagerTests(unittest.TestCase):
    def test_second_pass_fills_candidates_round_robin(self):
        items_by_keyword = {
            keyword: [
                POI(name=f"{keyword}{index}", address=f"{keyword}地址{index}")
                for index in range(10)
            ]
            for keyword in ("A", "B", "C")
        }

        candidates = build_balanced_candidates(
            items_by_keyword,
            max_total=15,
            per_keyword=3,
        )

        counts = {
            keyword: sum(item.name.startswith(keyword) for item in candidates)
            for keyword in items_by_keyword
        }
        self.assertEqual(counts, {"A": 5, "B": 5, "C": 5})


    def test_select_candidates_follows_llm_indices_and_returns_rest(self):
        pois = [POI(name=f"P{i}", address=f"A{i}") for i in range(5)]

        selected, remaining = select_candidates(pois, "[2, 4]", fallback_limit=3)

        self.assertEqual([p.name for p in selected], ["P1", "P3"])
        self.assertEqual([p.name for p in remaining], ["P0", "P2", "P4"])

    def test_select_candidates_falls_back_on_unusable_response(self):
        pois = [POI(name=f"P{i}", address=f"A{i}") for i in range(5)]

        for response in ("not json", "[99, 0, -1]", "[]"):
            selected, remaining = select_candidates(pois, response, fallback_limit=2)
            self.assertEqual([p.name for p in selected], ["P0", "P1"], response)
            self.assertEqual(len(remaining), 3, response)

class PoiAgentPathTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _poi(name):
        return POI(
            name=name,
            address=f"{name}地址",
            location=Location(longitude=113.6, latitude=34.7),
        )

    async def test_filter_and_select_returns_selected_and_remaining_pool(self):
        pois = [("历史", [self._poi("A"), self._poi("B")])]
        invoke_filter = AsyncMock(return_value="[2]")

        selected, remaining = await _filter_and_select(pois, invoke_filter)

        self.assertEqual([poi.name for poi in selected], ["B"])
        self.assertEqual([poi.name for poi in remaining], ["A"])
        invoke_filter.assert_awaited_once()

    async def test_tool_calling_path_executes_tool_and_plain_llm_filter(self):
        tool_response = SimpleNamespace(tool_calls=[{
            "id": "call-1",
            "args": {"keywords": "博物馆", "city": "郑州"},
        }])
        bound_llm = SimpleNamespace(ainvoke=AsyncMock(return_value=tool_response))

        class FakeLLM:
            def __init__(self):
                self.ainvoke = AsyncMock(return_value=SimpleNamespace(content="[1]"))

            def bind_tools(self, _tools):
                return bound_llm

        llm = FakeLLM()
        tool_result = {
            "pois": [{
                "name": "郑州博物馆",
                "address": "文翰街",
                "location": {"longitude": 113.6, "latitude": 34.7},
            }]
        }
        fake_tool = SimpleNamespace(ainvoke=AsyncMock(return_value=tool_result))
        with patch("app.agents.poi_agent.search_poi_tool", fake_tool):
            selected, remaining = await _run_poi_agent_with_tool_calling(
                city="郑州",
                preferences_str="历史",
                free_text="无",
                weather_context="晴",
                llm=llm,
            )

        self.assertEqual([poi.name for poi in selected], ["郑州博物馆"])
        self.assertEqual(remaining, [])
        fake_tool.ainvoke.assert_awaited_once_with({"keywords": "博物馆", "city": "郑州"})
        llm.ainvoke.assert_awaited_once()

    async def test_tool_calling_returns_none_without_bind_tools(self):
        result = await _run_poi_agent_with_tool_calling(
            city="郑州",
            preferences_str="历史",
            free_text="无",
            weather_context="晴",
            llm=SimpleNamespace(),
        )
        self.assertIsNone(result)

    async def test_fallback_searches_generated_keywords_and_filters(self):
        class FakePrompt:
            def __init__(self, response):
                self.response = response

            def __or__(self, _llm):
                response = self.response

                class Chain:
                    async def ainvoke(self, _payload):
                        return SimpleNamespace(content=response)

                return Chain()

        amap = SimpleNamespace(search_poi=AsyncMock(side_effect=[
            [self._poi("博物馆")],
            [self._poi("美术馆")],
        ]))
        prompts = [FakePrompt('["博物馆", "美术馆"]'), FakePrompt("[1,2]")]
        with patch("app.agents.poi_agent.get_amap_service", return_value=amap), patch(
            "app.agents.poi_agent.ChatPromptTemplate.from_template",
            side_effect=prompts,
        ):
            selected, remaining = await _run_poi_agent_fallback(
                city="郑州",
                preferences_str="历史, 艺术",
                free_text="无",
                weather_context="晴",
                llm=SimpleNamespace(),
            )

        self.assertEqual([poi.name for poi in selected], ["博物馆", "美术馆"])
        self.assertEqual(remaining, [])
        self.assertEqual(amap.search_poi.await_count, 2)


class HotelAgentTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _hotel(name, address=None):
        return Hotel(name=name, address=address or f"{name}地址")

    @staticmethod
    def _prompt(response_content):
        class FakePrompt:
            def __or__(self, _llm):
                return SimpleNamespace(
                    ainvoke=AsyncMock(
                        return_value=SimpleNamespace(content=response_content)
                    )
                )

        return FakePrompt()

    async def test_falls_back_when_all_keyword_searches_fail(self):
        amap = SimpleNamespace(search_hotels=AsyncMock(side_effect=[
            RuntimeError("first failed"),
            RuntimeError("second failed"),
            RuntimeError("third failed"),
            [self._hotel("fallback hotel")],
        ]))
        prompts = [self._prompt('["one", "two", "three"]'), self._prompt("[1]")]

        with (
            patch("app.agents.hotel_agent.get_llm", return_value=SimpleNamespace()),
            patch("app.agents.hotel_agent.get_amap_service", return_value=amap),
            patch(
                "app.agents.hotel_agent.ChatPromptTemplate.from_template",
                side_effect=prompts,
            ),
        ):
            selected, remaining = await run_hotel_agent(
                "Zhengzhou", "budget", None, "deepseek"
            )

        self.assertEqual([hotel.name for hotel in selected], ["fallback hotel"])
        self.assertEqual(remaining, [])
        self.assertEqual(amap.search_hotels.await_count, 4)
        amap.search_hotels.assert_awaited_with("Zhengzhou", "budget")

    async def test_balances_candidates_across_search_keywords(self):
        first_group = [self._hotel(f"A{index}") for index in range(10)]
        second_group = [self._hotel("B0")]
        third_group = [self._hotel("C0")]
        amap = SimpleNamespace(search_hotels=AsyncMock(side_effect=[
            first_group,
            second_group,
            third_group,
        ]))
        prompts = [self._prompt('["one", "two", "three"]'), self._prompt("[4]")]

        with (
            patch("app.agents.hotel_agent.get_llm", return_value=SimpleNamespace()),
            patch("app.agents.hotel_agent.get_amap_service", return_value=amap),
            patch(
                "app.agents.hotel_agent.ChatPromptTemplate.from_template",
                side_effect=prompts,
            ),
        ):
            selected, remaining = await run_hotel_agent(
                "Zhengzhou", "budget", None, "deepseek"
            )

        self.assertEqual([hotel.name for hotel in selected], ["B0"])
        self.assertIn("C0", [hotel.name for hotel in remaining])


class LlmJsonParsingTests(unittest.TestCase):
    def test_parse_json_object_ignores_trailing_prose_with_brace(self):
        response = '```json\n{"action": "answer"}\n``` trailing text }'

        self.assertEqual(parse_json_object(response), {"action": "answer"})


class TripChatServiceTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        _chat_history.clear()

    async def test_replacement_skips_names_already_used_in_trip(self):
        trip_plan = {
            "city": "郑州",
            "days": [{
                "attractions": [{"name": "景点A"}],
            }],
            "pois_pool": [
                {"name": "景点A", "address": "地址A"},
                {"name": "景点B", "address": "地址B"},
            ],
        }
        intent = {"item_type": "attraction", "replacement_query": "景点"}

        replacement = await _resolve_replacement(intent, trip_plan)

        self.assertEqual(replacement["name"], "景点B")

    async def test_chat_reports_persistence_failure(self):
        trip_plan = {
            "city": "郑州",
            "days": [{
                "attractions": [
                    {"name": "景点A"},
                    {"name": "景点B"},
                ],
            }],
        }
        intent = {
            "action": "delete",
            "day_index": 0,
            "item_type": "attraction",
            "item_index": 0,
            "target_name": "景点A",
        }

        with (
            patch("app.services.trip_chat_service.trip_result_repository.load", new=AsyncMock(return_value=trip_plan)),
            patch("app.services.trip_chat_service.run_chat_router", new=AsyncMock(return_value=intent)),
            patch(
                "app.services.trip_chat_service.modify_persisted_trip",
                new=AsyncMock(return_value=(TripPlan.model_validate({**trip_plan, "start_date": "2026-05-29", "end_date": "2026-05-29", "weather_info": [], "days": []}), False, "行程已修改，但保存失败；当前可以查看，刷新后修改会丢失。")),
            ),
        ):
            result = await handle_chat("session-1", "删除景点A")

        self.assertTrue(result["modified"])
        self.assertFalse(result["persistence_saved"])
        self.assertIn("刷新后修改会丢失", result["persistence_warning"])

    def test_chat_history_evicts_least_recently_used_session(self):
        with patch("app.services.chat_history.CHAT_SESSION_HISTORY_LIMIT", 2):
            _history_for_session("session-1").append({"role": "user", "content": "1"})
            _history_for_session("session-2").append({"role": "user", "content": "2"})
            _history_for_session("session-1")
            _history_for_session("session-3").append({"role": "user", "content": "3"})

        self.assertEqual(list(_chat_history), ["session-1", "session-3"])


class AmapMcpRouteServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_timeout_configuration_is_bounded_to_safe_positive_values(self):
        self.assertEqual(_positive_timeout(5, 15), 5.0)
        self.assertEqual(_positive_timeout(0, 15), 15)
        self.assertEqual(_positive_timeout(-1, 15), 15)
        self.assertEqual(_positive_timeout("invalid", 15), 15)

    def _skeleton(self):
        return [
            {
                "day_index": 0,
                "attractions": [
                    {"name": "景点A", "location": {"longitude": 113.60, "latitude": 34.70}},
                    {"name": "景点B", "location": {"longitude": 113.61, "latitude": 34.71}},
                    {"name": "景点C", "location": {"longitude": 113.62, "latitude": 34.72}},
                ],
                "estimated_distance_km": 2.1,
                "optimization_reason": "坐标排序",
            }
        ]

    def test_selects_matching_public_transit_tool(self):
        tools = [
            SimpleNamespace(name="maps_direction_driving", title="", description="驾车路径规划"),
            SimpleNamespace(name="maps_direction_transit_integrated", title="", description="公交路径规划"),
            SimpleNamespace(name="maps_direction_walking", title="", description="步行路径规划"),
        ]

        tool = _select_route_tool(tools, "公共交通")

        self.assertIsNotNone(tool)
        self.assertEqual(tool.name, "maps_direction_transit_integrated")

    def test_extracts_metric_from_text_result(self):
        result = SimpleNamespace(
            isError=False,
            structuredContent=None,
            content=[
                SimpleNamespace(
                    text='{"route": {"paths": [{"distance": "3.2 km", "duration": "8 min"}]}}'
                )
            ],
        )

        metric = _extract_route_metric(result)

        self.assertIsNotNone(metric)
        self.assertEqual(metric.distance_km, 3.2)
        self.assertEqual(metric.duration_min, 8)

    async def test_applies_mcp_metrics_only_after_full_day_verification(self):
        calls = []
        route_tool = SimpleNamespace(
            name="maps_direction_transit_integrated",
            title="",
            description="公交路径规划",
        )

        async def call_tool(name, arguments):
            calls.append((name, arguments))
            return SimpleNamespace(
                isError=False,
                structuredContent={
                    "route": {"paths": [{"distance": "1200", "duration": "600"}]}
                },
                content=[],
            )

        skeleton, summary = await _verify_route_days(
            self._skeleton(),
            city="郑州",
            transportation="公共交通",
            route_tool=route_tool,
            call_tool=call_tool,
            max_route_legs=12,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1]["city"], "郑州")
        self.assertEqual(skeleton[0]["estimated_distance_km"], 2.4)
        self.assertEqual(skeleton[0]["estimated_duration_min"], 20)
        self.assertIn("高德 MCP", skeleton[0]["optimization_reason"])
        self.assertEqual(summary.verified_days, 1)
        self.assertEqual(summary.verified_legs, 2)

    async def test_skips_mcp_without_mcp_key(self):
        original_skeleton = self._skeleton()
        with patch(
            "app.services.amap_mcp_service.get_settings",
            return_value=SimpleNamespace(amap_mcp_api_key=""),
        ):
            skeleton, summary = await verify_route_skeleton(
                original_skeleton,
                city="郑州",
                transportation="公共交通",
            )

        self.assertEqual(skeleton, original_skeleton)
        self.assertFalse(summary.configured)
        self.assertEqual(summary.verified_days, 0)

    async def test_graph_node_keeps_mcp_summary_for_streaming(self):
        verified_skeleton = self._skeleton()
        verification = RouteVerificationResult(
            configured=True,
            total_legs=2,
            verified_days=1,
            verified_legs=2,
            tool_name="maps_direction_transit_integrated",
            message="高德 MCP 已校验 1 天、2 段真实路径",
        )
        state = {
            "request": TripRequest(
                city="郑州",
                start_date="2026-05-29",
                end_date="2026-05-29",
                travel_days=1,
            ),
            "route_skeleton": self._skeleton(),
            "node_traces": [],
        }

        with patch(
            "app.workflows.trip.graph.verify_route_skeleton",
            new=AsyncMock(return_value=(verified_skeleton, verification)),
        ):
            updated = await route_verification_node(state)

        self.assertEqual(updated["route_skeleton"], verified_skeleton)
        self.assertEqual(updated["mcp_route_verification"]["verified_legs"], 2)
        self.assertIn("maps_direction_transit_integrated", updated["node_traces"][-1]["summary"])


class TripGraphIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Exercise every compiled graph node and the evaluator retry edge."""

    async def test_full_graph_retries_planner_and_re_evaluates(self):
        request = TripRequest(
            session_id="workflow-integration",
            city="郑州",
            start_date="2026-05-29",
            end_date="2026-05-29",
            travel_days=1,
        )
        weather = [Weather(date="2026-05-29", day_weather="晴")]
        poi = POI(
            id="poi-1",
            name="景点A",
            address="A地址",
            location=Location(longitude=113.6, latitude=34.7),
            visit_duration=120,
        )
        hotel = Hotel(
            name="酒店A",
            address="酒店地址",
            location=Location(longitude=113.61, latitude=34.71),
        )
        initial_day = {
            "date": "2026-05-29",
            "day_index": 0,
            "description": "需要规则修复的行程",
            "transportation": "公共交通",
            "accommodation": "经济型酒店",
            "attractions": [{
                "name": "景点A",
                "address": "A地址",
                "location": {"longitude": 113.6, "latitude": 34.7},
                "visit_duration": 900,
                "description": "停留时间过长",
            }],
            "meals": [],
            "route_summary": {"estimated_distance_km": 0},
        }
        initial_plan = {
            "city": "郑州",
            "start_date": "2026-05-29",
            "end_date": "2026-05-29",
            "days": [initial_day],
            "weather_info": [item.model_dump() for item in weather],
            "overall_suggestions": "",
            "budget": None,
        }
        repaired_day = {
            **initial_day,
            "description": "已缩短停留时间",
            "attractions": [{**initial_day["attractions"][0], "visit_duration": 120}],
        }
        verification = RouteVerificationResult(
            configured=False,
            total_legs=0,
            message="测试中跳过外部 MCP",
        )
        compiled_graph = build_trip_graph(checkpointer=InMemorySaver())

        with (
            patch("app.workflows.trip.graph.get_trip_graph", new=AsyncMock(return_value=compiled_graph)),
            patch("app.workflows.trip.graph.get_amap_service", return_value=SimpleNamespace(get_weather=AsyncMock(return_value=weather))),
            patch("app.workflows.trip.graph.run_poi_agent", new=AsyncMock(return_value=([poi], []))) as poi_agent,
            patch("app.workflows.trip.graph.run_hotel_agent", new=AsyncMock(return_value=([hotel], []))),
            patch(
                "app.workflows.trip.graph.verify_route_skeleton",
                new=AsyncMock(side_effect=lambda skeleton, **_: (skeleton, verification)),
            ),
            patch("app.workflows.trip.graph.run_planner_agent", new=AsyncMock(side_effect=[initial_plan, {**initial_plan, "days": [repaired_day]}])) as planner,
        ):
            result = await run_trip_graph(request)

        self.assertTrue(result["evaluator_report"]["passed"])
        self.assertEqual(result["days"][0]["description"], "已缩短停留时间")
        self.assertEqual(result["days"][0]["attractions"][0]["visit_duration"], 120)
        self.assertEqual([trace["node"] for trace in result["node_traces"]], [
            "weather", "candidate_collection", "route_optimizer",
            "planner_agent", "quality_gate", "planner_agent", "quality_gate",
        ])
        self.assertEqual(poi_agent.await_args.kwargs["weather"], weather)
        self.assertEqual(planner.await_count, 2)


class RuleEvaluatorTests(unittest.TestCase):
    def _evaluate(self, trip_plan, budget=None):
        request = TripRequest(
                city="郑州",
                start_date="2026-05-29",
                end_date="2026-05-29",
                travel_days=1,
                budget=budget,
            )
        return evaluate_trip_plan(trip_plan, request)

    def test_passes_valid_trip_plan(self):
        report = self._evaluate({
            "days": [
                {
                    "day_index": 0,
                    "attractions": [
                        {
                            "name": "景点A",
                            "location": {"longitude": 113.6, "latitude": 34.7},
                            "visit_duration": 120,
                        },
                    ],
                    "route_summary": {"estimated_distance_km": 5},
                }
            ],
            "budget": {"total": 1000},
        }, budget=[800, 1200])

        self.assertTrue(report["passed"])
        self.assertEqual(report["issues"], [])

    def test_flags_invalid_coordinate_intensity_and_budget(self):
        report = self._evaluate({
            "days": [
                {
                    "day_index": 0,
                    "attractions": [
                        {
                            "name": "景点A",
                            "location": {"longitude": 0, "latitude": 0},
                            "visit_duration": 700,
                        },
                    ],
                    "route_summary": {"estimated_distance_km": 30},
                }
            ],
            "budget": {"total": 5000},
        }, budget=[800, 1200])

        self.assertFalse(report["passed"])
        self.assertEqual(len(report["issues"]), 3)
        self.assertEqual(len(report["day_issues"][0]), 2)
        self.assertEqual(len(report["global_issues"]), 1)
        self.assertNotIn("score", report)
        self.assertTrue(report["suggestions"])

    def test_allows_conservative_budget_estimate(self):
        report = self._evaluate({
            "days": [
                {
                    "day_index": 0,
                    "attractions": [
                        {
                            "name": "景点A",
                            "location": {"longitude": 113.6, "latitude": 34.7},
                            "visit_duration": 120,
                        },
                    ],
                    "route_summary": {"estimated_distance_km": 5},
                }
            ],
            "budget": {"total": 600},
        }, budget=[1000, 3000])

        self.assertTrue(report["passed"])

    def test_flags_duplicate_attraction_within_one_day(self):
        attraction = {
            "name": "景点A",
            "address": "地址A",
            "location": {"longitude": 113.6, "latitude": 34.7},
            "visit_duration": 60,
        }
        report = self._evaluate({
            "days": [{
                "day_index": 0,
                "attractions": [dict(attraction), dict(attraction)],
                "route_summary": {"estimated_distance_km": 1},
            }],
            "budget": {"total": 1000},
        }, budget=[800, 1200])

        self.assertFalse(report["passed"])
        self.assertEqual(len(report["day_issues"][0]), 1)
        self.assertIn("重复", report["day_issues"][0][0])


class EvaluatorRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_to_finalize_when_passed(self):
        state = {"evaluator_report": {"passed": True}, "retry_count": 0}
        self.assertEqual(await evaluator_router(state), "finalize")

    async def test_routes_to_planner_when_failed_and_retry_remains(self):
        state = {
            "evaluator_report": {"passed": False, "global_issues": ["预算问题"]},
            "retry_count": 0,
        }
        self.assertEqual(await evaluator_router(state), "planner_agent")

    async def test_routes_to_day_retry_for_structured_day_issues(self):
        state = {
            "evaluator_report": {"passed": False, "day_issues": {1: ["第2天过载"]}},
            "retry_count": 0,
        }
        self.assertEqual(await evaluator_router(state), "planner_agent")

    async def test_routes_to_finalize_when_retry_limit_reached(self):
        state = {"evaluator_report": {"passed": False}, "retry_count": MAX_EVALUATOR_RETRY}
        self.assertEqual(await evaluator_router(state), "finalize")


class AmapServiceParsingTests(unittest.TestCase):
    def test_parse_cost_to_int(self):
        self.assertEqual(_parse_cost_to_int("88.5"), 88)
        self.assertEqual(_parse_cost_to_int("¥80/人"), 80)
        self.assertEqual(_parse_cost_to_int(None), 0)
        self.assertEqual(_parse_cost_to_int("暂无"), 0)

    def test_poi_cost_is_per_capita_reference_not_ticket_price(self):
        from app.services.amap_service import _parse_poi_records

        [poi] = _parse_poi_records([{
            "name": "餐馆A",
            "type": "餐饮服务;中餐厅;中餐厅",
            "location": "0,34.5",
            "biz_ext": {"cost": "88元/人", "rating": "4.5"},
        }])

        self.assertEqual(poi.cost_reference, 88)
        self.assertEqual(poi.ticket_price, 0)
        self.assertEqual(poi.location.longitude, 0)

    def test_scenic_poi_uses_amap_cost_as_ticket_estimate_only_when_available(self):
        from app.services.amap_service import _parse_poi_records

        with_data, without_data = _parse_poi_records([
            {
                "name": "景点A", "type": "风景名胜;公园广场;公园",
                "location": "113.6,34.7", "biz_ext": {"cost": "40"},
            },
            {
                "name": "景点B", "type": "风景名胜;风景名胜;国家级景点",
                "location": "113.7,34.8", "biz_ext": {"cost": []},
            },
        ])

        self.assertEqual(with_data.ticket_price, 40)
        self.assertEqual(without_data.ticket_price, 0)
        self.assertIsNone(without_data.cost_reference)


class AmapHotelSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_nearby_restaurant_search_uses_coordinate_and_distance_sort(self):
        service = AmapService.__new__(AmapService)
        service.api_key = "test-key"
        service.base_url = "https://restapi.amap.com/v3"
        service._throttled_request = AsyncMock(return_value=SimpleNamespace(json=lambda: {
            "status": "1",
            "pois": [{
                "name": "餐馆A", "location": "113.6,34.7", "distance": "250",
                "biz_ext": {"cost": "60", "rating": "4.6"},
            }],
        }))

        [restaurant] = await service.search_nearby_restaurants(113.6, 34.7)

        self.assertEqual(restaurant.name, "餐馆A")
        self.assertEqual(restaurant.distance_m, 250)
        params = service._throttled_request.await_args.kwargs["params"]
        self.assertEqual(params["location"], "113.600000,34.700000")
        self.assertEqual(params["sortrule"], "distance")

    async def test_search_hotels_does_not_duplicate_hotel_suffix(self):
        service = AmapService.__new__(AmapService)
        service.search_poi = AsyncMock(return_value=[])

        await service.search_hotels("郑州", "经济型酒店")
        await service.search_hotels("郑州", "经济型")

        self.assertEqual(
            [call.args[0] for call in service.search_poi.await_args_list],
            ["经济型酒店", "经济型酒店"],
        )


class StreamingResponseSchemaTests(unittest.TestCase):
    def test_accepts_running_status_used_by_sse_events(self):
        event = StreamingResponse(
            session_id="session-1",
            step=1,
            node="init",
            status="running",
            message="正在初始化",
        )

        self.assertEqual(event.status, "running")

    def test_accepts_pending_status_used_for_weather_dependent_agents(self):
        event = StreamingResponse(
            session_id="session-1",
            step=3,
            node="poi_search",
            status="pending",
            message="等待天气上下文",
        )

        self.assertEqual(event.status, "pending")


class TripGraphStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_progress_matches_weather_first_execution_order(self):
        class EmptyGraph:
            checkpointer = None

            async def astream(self, *_args, **_kwargs):
                if False:
                    yield {}

        request = TripRequest(
            session_id="stream-order",
            city="郑州",
            start_date="2026-05-29",
            end_date="2026-05-29",
            travel_days=1,
        )
        with patch(
            "app.workflows.trip.graph.get_trip_graph",
            new=AsyncMock(return_value=EmptyGraph()),
        ):
            events = [
                json.loads(raw.removeprefix("data: "))
                async for raw in run_trip_graph_stream(request)
            ]

        self.assertEqual(
            [(event["node"], event["status"]) for event in events],
            [
                ("init", "running"),
                ("weather", "running"),
                ("candidate_collection", "pending"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
