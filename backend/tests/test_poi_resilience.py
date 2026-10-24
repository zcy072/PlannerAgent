"""Malformed external records must not discard valid POI candidates."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.services.amap_service import AmapService


class PoiResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def search(self, payload):
        with patch('app.services.amap_service.get_settings',
                   return_value=SimpleNamespace(amap_api_key='test')):
            service = AmapService()
        request = AsyncMock(return_value=httpx.Response(200, content=json.dumps(payload)))
        service._throttled_request = request
        result = await service.search_poi('museum', 'test')
        request.assert_awaited_once()
        return result

    async def test_mixed_records_preserve_valid_candidates(self):
        result = await self.search({'status': '1', 'pois': [
            {'name': 'first', 'location': '113.6,34.7'},
            None, 12, [], {'name': []}, {'name': '   '},
            {'name': 'broken', 'address': {'unexpected': True}},
            {'name': 'last', 'location': '113.7,34.8'},
        ]})
        self.assertEqual([poi.name for poi in result], ['first', 'last'])
        self.assertIsNotNone(result[0].location)

    async def test_invalid_envelopes_return_empty(self):
        for payload in ([], None, {'status': '1', 'pois': None},
                        {'status': '1', 'pois': {}}):
            with self.subTest(payload=payload):
                self.assertEqual(await self.search(payload), [])

    async def test_optional_malformed_fields_degrade(self):
        result = await self.search({'status': '1', 'pois': [
            {'name': 'bad coordinates', 'location': '999,34', 'biz_ext': ['bad']},
            {'name': 'bad rating', 'location': [], 'biz_ext': {'rating': 'NaN'}},
            {'name': 'bad cost', 'biz_ext': {'cost': '9' * 400}},
        ]})
        self.assertEqual(len(result), 3)
        for poi in result:
            self.assertIsNone(poi.location)
            self.assertIsNone(poi.rating)
            self.assertEqual(poi.ticket_price, 0)
