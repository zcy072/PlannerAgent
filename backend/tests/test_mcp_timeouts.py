"""Exercise MCP deadlines without network calls."""

import asyncio
import copy
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.amap_mcp_service import verify_route_skeleton


class McpTimeoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original = [{
            "attractions": [
                {"location": {"longitude": 113.6, "latitude": 34.7}},
                {"location": {"longitude": 113.7, "latitude": 34.8}},
            ],
            "estimated_distance_km": 12,
        }]
        self.settings = SimpleNamespace(
            amap_mcp_api_key="test-key", amap_mcp_url="https://example.invalid/mcp",
            amap_mcp_max_route_legs=12, amap_mcp_timeout_seconds=0.1,
            amap_mcp_call_timeout_seconds=0.02,
        )
        self.session = AsyncMock()
        self.session.list_tools.return_value = SimpleNamespace(tools=[
            SimpleNamespace(name="maps_direction_walking", description="walking route")
        ])
        self.session.call_tool.return_value = SimpleNamespace(
            isError=False, structuredContent={"distance": 2000, "duration": 600}, content=[],
        )
        self.http = AsyncMock()
        self.transport = AsyncMock()
        self.transport.__aenter__.return_value = (None, None, None)
        self.context = AsyncMock()
        self.context.__aenter__.return_value = self.session
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        module = "app.services.amap_mcp_service."
        for name, value in (("get_settings", self.settings),
                            ("httpx.AsyncClient", self.http),
                            ("streamable_http_client", self.transport),
                            ("ClientSession", self.context)):
            self.stack.enter_context(patch(module + name, return_value=value))

    async def verify(self):
        return await verify_route_skeleton(self.original, city="test", transportation="步行")

    async def hang(self, *args, **kwargs):
        await asyncio.Event().wait()

    async def test_initialization_timeout_returns_original_and_closes_resources(self):
        self.session.initialize.side_effect = self.hang
        result, summary = await asyncio.wait_for(self.verify(), 1)
        self.assertEqual(result, self.original)
        self.assertEqual(summary.verified_days, 0)
        self.session.call_tool.assert_not_called()
        self.context.__aexit__.assert_awaited_once()
        self.transport.__aexit__.assert_awaited_once()
        self.http.__aexit__.assert_awaited_once()

    async def test_single_leg_timeout_cancels_call_and_retains_estimate(self):
        cancelled = asyncio.Event()

        async def slow_call(*args, **kwargs):
            try:
                await self.hang()
            finally:
                cancelled.set()

        self.session.call_tool.side_effect = slow_call
        result, summary = await asyncio.wait_for(self.verify(), 1)
        self.assertEqual(result, self.original)
        self.assertEqual(summary.verified_legs, 0)
        self.assertTrue(cancelled.is_set())

    async def test_total_timeout_discards_previously_verified_days(self):
        self.original.append(copy.deepcopy(self.original[0]))
        snapshot = copy.deepcopy(self.original)
        self.settings.amap_mcp_call_timeout_seconds = 1
        calls = 0

        async def call(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return self.session.call_tool.return_value
            await self.hang()

        self.session.call_tool.side_effect = call
        result, summary = await asyncio.wait_for(self.verify(), 1)
        self.assertEqual(calls, 2)
        self.assertEqual(result, snapshot)
        self.assertEqual(self.original, snapshot)
        self.assertEqual(summary.verified_days, 0)

    async def test_caller_cancellation_propagates(self):
        started = asyncio.Event()

        async def initialize():
            started.set()
            await self.hang()

        self.session.initialize.side_effect = initialize
        task = asyncio.create_task(self.verify())
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.context.__aexit__.assert_awaited_once()

    async def test_success_returns_verified_copy(self):
        snapshot = copy.deepcopy(self.original)
        result, summary = await self.verify()
        self.assertEqual(result[0]["estimated_distance_km"], 2)
        self.assertEqual(result[0]["estimated_duration_min"], 10)
        self.assertEqual(summary.verified_days, 1)
        self.assertEqual(self.original, snapshot)

    async def test_cleanup_failure_discards_verified_metrics(self):
        self.context.__aexit__.side_effect = RuntimeError("cleanup failed")
        result, summary = await self.verify()
        self.assertEqual(result, self.original)
        self.assertEqual(summary.verified_days, 0)
        self.http.__aexit__.assert_awaited_once()
