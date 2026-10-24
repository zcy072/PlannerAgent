"""Verify REST retry budgets at the HTTP boundary without external services."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.services.amap_service import AmapService


class AmapRetryTests(unittest.IsolatedAsyncioTestCase):
    async def run_search(self, outcomes, *, hotels=False):
        calls = []

        async def handler(request):
            calls.append(request)
            outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
            if isinstance(outcome, BaseException):
                raise outcome
            if outcome == 'malformed':
                return httpx.Response(200, text='invalid json')
            return httpx.Response(outcome, json={'status': '1', 'pois': []})

        with patch('app.services.amap_service.get_settings',
                   return_value=SimpleNamespace(amap_api_key='test')):
            service = AmapService()
        service._request_interval = 0
        self.calls = calls
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service._client = client
            if hotels:
                return await service.search_hotels('test')
            return await service.search_poi('museum', 'test')

    async def test_hotel_timeout_uses_only_two_http_attempts(self):
        with self.assertRaises(httpx.ReadTimeout):
            await self.run_search([httpx.ReadTimeout('timeout')], hotels=True)
        self.assertEqual(len(self.calls), 2)

    async def test_transient_http_errors_can_recover(self):
        for status in (429, 503):
            with self.subTest(status=status):
                self.assertEqual(await self.run_search([status, 200]), [])
                self.assertEqual(len(self.calls), 2)

    async def test_permanent_http_errors_are_not_retried(self):
        for status in (400, 401, 403):
            with self.subTest(status=status):
                with self.assertRaises(httpx.HTTPStatusError):
                    await self.run_search([status], hotels=True)
                self.assertEqual(len(self.calls), 1)

    async def test_malformed_json_is_not_retried(self):
        with self.assertRaises(ValueError):
            await self.run_search(['malformed'])
        self.assertEqual(len(self.calls), 1)

    async def test_cancellation_propagates_without_retry(self):
        with self.assertRaises(asyncio.CancelledError):
            await self.run_search([asyncio.CancelledError()], hotels=True)
        self.assertEqual(len(self.calls), 1)
