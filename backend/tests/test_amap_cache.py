import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import amap_service
from app.services.amap_service import AmapService


def _service(payload: dict) -> AmapService:
    service = AmapService.__new__(AmapService)
    service.api_key = "test-key"
    service.base_url = "https://restapi.amap.com/v3"
    service._throttled_request = AsyncMock(
        return_value=SimpleNamespace(json=lambda: payload)
    )
    return service


POI_PAYLOAD = {
    "status": "1",
    "pois": [{"name": "景点A", "type": "风景名胜", "location": "113.6,34.7"}],
}


class AmapCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_poi_search_hits_cache(self):
        service = _service(POI_PAYLOAD)

        first = await service.search_poi("景点", "郑州")
        second = await service.search_poi("景点", "郑州")

        self.assertEqual(service._throttled_request.await_count, 1)
        self.assertEqual([p.name for p in first], [p.name for p in second])

    async def test_different_arguments_do_not_share_entries(self):
        service = _service(POI_PAYLOAD)

        await service.search_poi("景点", "郑州")
        await service.search_poi("博物馆", "郑州")
        await service.search_poi("景点", "开封")

        self.assertEqual(service._throttled_request.await_count, 3)

    async def test_mutating_cached_result_does_not_pollute_cache(self):
        service = _service(POI_PAYLOAD)

        first = await service.search_poi("景点", "郑州")
        first[0].name = "被改掉了"
        second = await service.search_poi("景点", "郑州")

        self.assertEqual(second[0].name, "景点A")

    async def test_empty_results_are_not_cached(self):
        service = _service({"status": "1", "pois": []})

        await service.search_poi("景点", "郑州")
        await service.search_poi("景点", "郑州")

        self.assertEqual(service._throttled_request.await_count, 2)

    async def test_entries_expire_after_ttl(self):
        service = _service(POI_PAYLOAD)
        clock = {"now": 1000.0}
        with patch.object(amap_service.time, "monotonic", side_effect=lambda: clock["now"]):
            await service.search_poi("景点", "郑州")
            clock["now"] += amap_service.POI_CACHE_TTL_SECONDS + 1
            await service.search_poi("景点", "郑州")

        self.assertEqual(service._throttled_request.await_count, 2)

    async def test_weather_and_geocode_are_cached(self):
        weather = _service({
            "status": "1",
            "forecasts": [{"casts": [{"date": "2026-06-10", "dayweather": "晴"}]}],
        })
        await weather.get_weather("郑州")
        await weather.get_weather("郑州")
        self.assertEqual(weather._throttled_request.await_count, 1)

        geocode = _service({"status": "1", "geocodes": [{"location": "113.6,34.7"}]})
        await geocode.geocode("郑州")
        await geocode.geocode("郑州")
        self.assertEqual(geocode._throttled_request.await_count, 1)

    async def test_cache_is_bounded(self):
        service = _service(POI_PAYLOAD)
        with patch.object(amap_service, "CACHE_MAX_ENTRIES", 3):
            for index in range(6):
                await service.search_poi(f"关键词{index}", "郑州")

        self.assertLessEqual(len(service._cache), 3)


if __name__ == "__main__":
    unittest.main()
