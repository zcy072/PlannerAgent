"""Weather response boundary regression tests."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.services.amap_service import AmapService


class WeatherResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def weather(self, payload):
        with patch('app.services.amap_service.get_settings',
                   return_value=SimpleNamespace(amap_api_key='test')):
            service = AmapService()
        service._throttled_request = AsyncMock(
            return_value=httpx.Response(200, content=json.dumps(payload)))
        result = await service.get_weather('test')
        service._throttled_request.assert_awaited_once()
        return result

    async def test_invalid_envelopes_degrade(self):
        for payload in (None, [], {'status': '1', 'forecasts': None},
                        {'status': '1', 'forecasts': [None]},
                        {'status': '1', 'forecasts': [{'casts': {}}]}):
            with self.subTest(payload=payload):
                self.assertEqual(await self.weather(payload), [])

    async def test_bad_records_do_not_discard_valid_forecasts(self):
        result = await self.weather({'status': '1', 'forecasts': [{'casts': [
            {'date': '2026-10-09', 'dayweather': 'rain', 'daytemp': '18'},
            None, [], {'date': []}, {'date': ' '},
            {'date': '2026-10-10', 'daytemp': {'invalid': True}},
            {'date': '2026-10-11', 'daytemp': 0, 'nighttemp': '-2'},
        ]}]})
        self.assertEqual([item.date for item in result], ['2026-10-09', '2026-10-11'])
        self.assertEqual(result[1].day_temp, 0)
        self.assertEqual(result[1].night_temp, '-2')

    async def test_missing_temperature_is_unknown(self):
        result = await self.weather({'status': '1', 'forecasts': [{'casts': [
            {'date': '2026-10-09', 'daytemp': None, 'nighttemp': ''},
            {'date': '2026-10-10'},
        ]}]})
        for item in result:
            self.assertEqual(item.day_temp, '未知')
            self.assertEqual(item.night_temp, '未知')
