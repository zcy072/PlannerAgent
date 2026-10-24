"""Optional verification must preserve the local planning fallback."""

import asyncio
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.workflows.trip.graph import route_planning_node
from app.workflows.trip.graph import build_trip_graph, _initial_state
from app.models.schemas import TripRequest, POI, Location
from app.services.amap_mcp_service import RouteVerificationResult
from langchain_core.runnables import RunnableLambda
from langchain_core.messages import AIMessage
from langsmith import tracing_context


class OptionalRouteVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def run_node(self, enabled, verifier):
        self.local = {'route_skeleton': [{'estimated_distance_km': 2}],
                      'node_traces': [], 'pois': [], 'pois_pool': []}
        with patch('app.workflows.trip.graph.get_settings', return_value=SimpleNamespace(amap_mcp_enabled=enabled)), \
             patch('app.workflows.trip.graph.route_optimizer_node', new=AsyncMock(return_value=copy.deepcopy(self.local))), \
             patch('app.workflows.trip.graph.route_verification_node', new=verifier):
            return await route_planning_node({})

    async def test_disabled_never_calls_verifier(self):
        verifier = AsyncMock()
        result = await self.run_node(False, verifier)
        verifier.assert_not_awaited()
        self.assertEqual(result, self.local)

    async def test_enabled_passes_verified_skeleton_downstream(self):
        verified = [{'estimated_distance_km': 3, 'estimated_duration_min': 20}]
        verifier = AsyncMock(return_value={'route_skeleton': verified, 'node_traces': []})
        result = await self.run_node(True, verifier)
        self.assertEqual(result['route_skeleton'], verified)
        self.assertEqual(verifier.call_args.args[0]['route_skeleton'], self.local['route_skeleton'])

    async def test_failure_keeps_local_route(self):
        result = await self.run_node(True, AsyncMock(side_effect=RuntimeError('unavailable')))
        self.assertEqual(result['route_skeleton'], self.local['route_skeleton'])
        self.assertEqual(result['node_traces'][-1]['status'], 'failed')

    async def test_cancellation_is_not_swallowed(self):
        with self.assertRaises(asyncio.CancelledError):
            await self.run_node(True, AsyncMock(side_effect=asyncio.CancelledError()))

    async def run_compiled(self, fails=False):
        request = TripRequest(city='郑州', start_date='2026-10-09',
                              end_date='2026-10-09', travel_days=1)
        pois = [POI(name=f'景点{i}', address=f'地址{i}', description='测试景点',
                    location=Location(longitude=113.6 + i * .01, latitude=34.7))
                for i in range(2)]
        generated = dict(city=request.city, start_date=request.start_date,
                         end_date=request.end_date, days=[dict(
                             date=request.start_date, day_index=0, description='测试',
                             attractions=[p.model_dump() for p in pois], meals=[],
                             route_summary={'estimated_distance_km': 999})])
        prompts = []

        async def fake_llm(prompt):
            prompts.append(prompt.to_string())
            return AIMessage(content=json.dumps(generated, ensure_ascii=False))

        async def verify(skeleton, **kwargs):
            if fails:
                raise RuntimeError('unavailable')
            result = copy.deepcopy(skeleton)
            result[0].update(estimated_distance_km=8, estimated_duration_min=500,
                             optimization_reason='MCP verified')
            return result, RouteVerificationResult(True, 1, 1, 1, message='verified')

        with (
            tracing_context(enabled=False),
            patch('app.workflows.trip.graph.get_settings', return_value=SimpleNamespace(amap_mcp_enabled=True, open_meteo_enabled=False)),
            patch('app.workflows.trip.graph.get_amap_service', return_value=SimpleNamespace(get_weather=AsyncMock(return_value=[]))),
            patch('app.workflows.trip.graph.run_poi_agent', new=AsyncMock(return_value=(pois, []))),
            patch('app.workflows.trip.graph.run_hotel_agent', new=AsyncMock(return_value=([], []))),
            patch('app.workflows.trip.graph.verify_route_skeleton', new=AsyncMock(side_effect=verify)) as verifier,
            patch('app.agents.planner_agent.get_llm', return_value=RunnableLambda(fake_llm)),
        ):
            result = await build_trip_graph().ainvoke(_initial_state(request))
        verifier.assert_awaited_once()
        self.assertTrue(prompts)
        return result

    async def test_compiled_graph_preserves_verified_metrics_and_evaluates_duration(self):
        state = await self.run_compiled()
        summary = state['trip_plan']['days'][0]['route_summary']
        self.assertEqual(summary['estimated_distance_km'], 8)
        self.assertEqual(summary['estimated_duration_min'], 500)
        self.assertFalse(state['evaluator_report']['passed'])
        self.assertTrue(any('时长' in issue for issue in state['evaluator_report']['issues']))

    async def test_compiled_graph_finishes_when_verifier_fails(self):
        state = await self.run_compiled(fails=True)
        summary = state['trip_plan']['days'][0]['route_summary']
        self.assertLess(summary['estimated_distance_km'], 8)
        self.assertIsNone(summary['estimated_duration_min'])
        self.assertTrue(state['evaluator_report']['passed'])
        self.assertTrue(any(t['node'] == 'route_verification' and t['status'] == 'failed'
                            for t in state['node_traces']))
