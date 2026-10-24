"""Geocoding must reject malformed coordinates without breaking callers."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.services.amap_service import AmapService


class GeocodeResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def geocode(self, payload):
        with patch('app.services.amap_service.get_settings',
                   return_value=SimpleNamespace(amap_api_key='test')):
            service = AmapService()
        service._throttled_request = AsyncMock(
            return_value=httpx.Response(200, content=json.dumps(payload)))
        result = await service.geocode('test address', 'test city')
        service._throttled_request.assert_awaited_once()
        return result

    async def test_invalid_envelopes_return_none(self):
        for payload in (None, [], {'status': '0'},
                        {'status': '1', 'geocodes': None},
                        {'status': '1', 'geocodes': {}},
                        {'status': '1', 'geocodes': [None]}):
            with self.subTest(payload=payload):
                self.assertIsNone(await self.geocode(payload))

    async def test_invalid_coordinates_return_none(self):
        for coordinates in (None, [], {}, '', '113', '113,34,5',
                            'bad,34', '181,34', '113,91', 'NaN,34',
                            '113,Infinity', '0,0'):
            with self.subTest(coordinates=coordinates):
                self.assertIsNone(await self.geocode({
                    'status': '1', 'geocodes': [{'location': coordinates}]}))

    async def test_valid_coordinates_preserved(self):
        for longitude, latitude in ((113.6, 34.7), (0, 51), (30, 0)):
            with self.subTest(longitude=longitude, latitude=latitude):
                result = await self.geocode({'status': '1', 'geocodes': [
                    {'location': f'{longitude},{latitude}'}]})
                self.assertEqual(result.longitude, longitude)
                self.assertEqual(result.latitude, latitude)

    async def test_invalid_first_match_does_not_select_another_address(self):
        self.assertIsNone(await self.geocode({'status': '1', 'geocodes': [
            {'location': 'bad'}, {'location': '113.6,34.7'}]}))
