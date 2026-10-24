"""高德地图 API 服务封装"""

import asyncio
import copy
import math
import time
import httpx
import re
from pydantic import ValidationError
from typing import List, Any, Optional
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_fixed
from loguru import logger

from ..core.config import get_settings
from ..models.schemas import POI, Weather, Hotel, Location
from ..planning.location_utils import is_valid_coordinate


def _is_transient_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code == 429 or 500 <= exc.response.status_code < 600
    )


def _parse_cost_to_int(value: Any) -> int:
    """Parse Amap biz_ext.cost, which represents a per-capita reference amount."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(round(value)))

    text = str(value).strip()
    if not text:
        return 0

    matches = re.findall(r"\d+(?:\.\d+)?", text)
    if not matches:
        return 0
    # Amap may return ranges such as "80-120 元/人". Use the midpoint so
    # estimates are not systematically biased toward the lower bound.
    values = [float(item) for item in matches[:2]]
    amount = sum(values) / len(values) if len(values) == 2 and re.search(r"[-~至]", text) else values[0]
    return max(0, int(round(amount)))


def _parse_optional_distance(value: Any) -> Optional[int]:
    """Parse Amap distance while treating its empty-array sentinel as missing."""
    if value in (None, "", [], {}):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0, int(round(number)))


def _parse_poi_records(pois: list[Any]) -> List[POI]:
    result = []
    for poi in pois:
        if not isinstance(poi, dict) or not isinstance(poi.get("name"), str) or not poi["name"].strip():
            logger.warning("[Amap] 跳过缺少有效名称的 POI")
            continue

        location_obj = None
        location_str = poi.get("location", "")
        if isinstance(location_str, str) and "," in location_str:
            try:
                longitude, latitude = (float(part) for part in location_str.split(",", 1))
                if is_valid_coordinate(longitude, latitude):
                    location_obj = Location(longitude=longitude, latitude=latitude)
            except (ValueError, TypeError):
                pass

        tel_value = poi.get("tel", "")
        if isinstance(tel_value, list):
            tel_value = "; ".join(str(item) for item in tel_value if item)

        rating_value = None
        cost_reference = None
        biz_ext = poi.get("biz_ext", {})
        if not isinstance(biz_ext, dict):
            biz_ext = {}
        try:
            if biz_ext.get("rating"):
                rating_value = float(biz_ext["rating"])
                if not math.isfinite(rating_value):
                    rating_value = None
            raw_cost = biz_ext.get("cost")
            if raw_cost not in (None, "", [], {}):
                parsed_cost = _parse_cost_to_int(raw_cost)
                cost_reference = parsed_cost if parsed_cost > 0 else None
        except (ValueError, TypeError, OverflowError):
            rating_value = None

        # 景点类 POI 有高德费用数据时作为门票估算；餐饮/住宿的费用不是门票，没有数据则保持 0
        category = poi.get("type", "") or ""
        is_dining_or_lodging = "餐饮服务" in category or "住宿服务" in category
        ticket_price = cost_reference if cost_reference and not is_dining_or_lodging else 0

        try:
            distance_m = _parse_optional_distance(poi.get("distance"))
            result.append(POI(
                id=poi.get("id", ""),
                name=poi["name"].strip(),
                address=poi.get("address", "") or f"{poi.get('pname', '')}{poi.get('cityname', '')}{poi.get('adname', '')}",
                location=location_obj,
                category=poi.get("type", ""),
                rating=rating_value,
                tel=tel_value,
                ticket_price=ticket_price,
                cost_reference=cost_reference,
                distance_m=distance_m,
            ))
        except (ValidationError, ValueError, TypeError, OverflowError):
            logger.warning("[Amap] 跳过字段校验失败的 POI")
    return result


def _amap_response_ok(data: Any) -> bool:
    """Accept both documented status=1 and valid infocode=10000 responses."""
    if not isinstance(data, dict):
        return False
    status = data.get("status")
    if str(status) == "1":
        return True
    # Some Amap gateways omit ``status`` while still returning a successful
    # response with the standard success infocode and payload.
    return status in (None, "") and str(data.get("infocode", "")) == "10000"


# 同一城市连续规划、反复替换景点时会重复请求相同内容；短时缓存可以减少触发高德 QPS 限制
POI_CACHE_TTL_SECONDS = 600
WEATHER_CACHE_TTL_SECONDS = 1800
GEOCODE_CACHE_TTL_SECONDS = 3600
CACHE_MAX_ENTRIES = 256


class AmapService:
    """高德地图服务"""

    def _cache_get(self, key: tuple) -> Any:
        """命中返回缓存值的深拷贝，调用方修改结果不会污染缓存。"""
        cache = getattr(self, "_cache", None)
        entry = cache.get(key) if cache else None
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            cache.pop(key, None)
            return None
        return copy.deepcopy(value)

    def _cache_put(self, key: tuple, value: Any, ttl: float) -> None:
        """只缓存成功且非空的结果，避免把一次失败或空响应固定下来。"""
        if not value:
            return
        cache = self.__dict__.setdefault("_cache", {})
        if len(cache) >= CACHE_MAX_ENTRIES:
            cache.pop(min(cache, key=lambda k: cache[k][0]), None)
        cache[key] = (time.monotonic() + ttl, copy.deepcopy(value))

    def __init__(self):
        settings = get_settings()
        self.api_key = settings.amap_api_key
        self.base_url = "https://restapi.amap.com/v3"
        self._semaphore = asyncio.Semaphore(2)
        self._request_interval = 0.4
        self._client: Optional[httpx.AsyncClient] = None

        if not self.api_key:
            raise ValueError("高德地图 API Key 未配置")

        logger.info("[Amap] 高德地图服务初始化完成")

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15)
        return self._client

    async def close(self) -> None:
        """Close the shared HTTP client used for Amap requests."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            logger.info("[Amap] HTTP 客户端已关闭")
        self._client = None

    async def _throttled_request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """限流请求：通过 semaphore 限制并发数，请求完成后等待固定间隔"""
        async with self._semaphore:
            response = await self._get_client().request(method, url, **kwargs)
            response.raise_for_status()
            await asyncio.sleep(self._request_interval)
            return response

    @retry(stop=stop_after_attempt(2), wait=wait_fixed(1), retry=retry_if_exception(_is_transient_error), reraise=True)
    async def search_poi(
        self,
        keywords: str,
        city: str,
        citylimit: bool = True,
        page_size: int = 20
    ) -> List[POI]:
        """搜索 POI"""
        logger.debug(f"[Amap] search_poi | 关键词: {keywords}, 城市: {city}")

        cache_key = ("poi", keywords, city, citylimit, page_size)
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("[Amap] search_poi 命中缓存")
            return cached

        params = {
            "key": self.api_key,
            "keywords": keywords,
            "city": city,
            "citylimit": "true" if citylimit else "false",
            "offset": page_size,
            "extensions": "all",
            "output": "json"
        }

        response = await self._throttled_request("GET", f"{self.base_url}/place/text", params=params)
        data = response.json()

        if not isinstance(data, dict):
            logger.warning("[Amap] POI 响应根节点不是对象")
            return []
        if not _amap_response_ok(data):
            logger.warning(
                "[Amap] API 返回错误 | info={} infocode={}",
                data.get("info"), data.get("infocode"),
            )
            return []

        pois = data.get("pois", [])
        if not isinstance(pois, list):
            logger.warning("[Amap] POI 响应列表格式无效")
            return []
        logger.debug(f"[Amap] search_poi 返回 {len(pois)} 个结果")
        result = _parse_poi_records(pois)

        logger.debug(f"[Amap] search_poi 完成，返回 {len(result)} 个有效POI")
        self._cache_put(cache_key, result, POI_CACHE_TTL_SECONDS)
        return result

    @retry(stop=stop_after_attempt(2), wait=wait_fixed(1), retry=retry_if_exception(_is_transient_error), reraise=True)
    async def search_nearby_restaurants(
        self,
        longitude: float,
        latitude: float,
        radius: int = 3000,
        page_size: int = 10,
    ) -> List[POI]:
        """Search nearby restaurants and return Amap's per-capita price reference."""
        if not is_valid_coordinate(longitude, latitude):
            return []
        params = {
            "key": self.api_key,
            "location": f"{longitude:.6f},{latitude:.6f}",
            "types": "050000",  # 高德餐饮服务大类,比关键词匹配更稳定
            "radius": min(max(radius, 500), 5000),
            "sortrule": "distance",
            "offset": min(max(page_size, 1), 20),
            "extensions": "all",
            "output": "json",
        }
        response = await self._throttled_request(
            "GET", f"{self.base_url}/place/around", params=params,
        )
        data = response.json()
        if not _amap_response_ok(data):
            logger.warning("[Amap] 周边餐饮搜索返回无效响应")
            return []
        pois = data.get("pois", [])
        if not isinstance(pois, list):
            return []
        return _parse_poi_records(pois)

    @retry(stop=stop_after_attempt(2), wait=wait_fixed(1), retry=retry_if_exception(_is_transient_error), reraise=True)
    async def get_weather(self, city: str) -> List[Weather]:
        """获取天气预报"""
        logger.debug(f"[Amap] get_weather | 城市: {city}")

        cache_key = ("weather", city)
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("[Amap] get_weather 命中缓存")
            return cached

        params = {
            "key": self.api_key,
            "city": city,
            "extensions": "all",
            "output": "json"
        }

        response = await self._throttled_request("GET", f"{self.base_url}/weather/weatherInfo", params=params)
        data = response.json()

        if not isinstance(data, dict):
            logger.warning("[Amap] 天气响应根节点不是对象")
            return []
        if not _amap_response_ok(data):
            logger.warning(f"[Amap] API 返回错误: {data.get('info')}")
            return []

        forecasts = data.get("forecasts", [])
        if not isinstance(forecasts, list) or not forecasts:
            return []
        if not isinstance(forecasts[0], dict):
            logger.warning("[Amap] 天气预报结构无效")
            return []

        casts = forecasts[0].get("casts", [])
        if not isinstance(casts, list):
            logger.warning("[Amap] 天气预报列表格式无效")
            return []

        result = []
        for cast in casts:
            if not isinstance(cast, dict) or not isinstance(cast.get("date"), str) or not cast["date"].strip():
                logger.warning("[Amap] 跳过缺少日期的天气条目")
                continue
            try:
                weather = Weather(
                    date=cast["date"],
                    day_weather=cast.get("dayweather") or "",
                    night_weather=cast.get("nightweather") or "",
                    day_temp=cast.get("daytemp") if cast.get("daytemp") not in (None, "") else "未知",
                    night_temp=cast.get("nighttemp") if cast.get("nighttemp") not in (None, "") else "未知",
                    wind_direction=cast.get("daywind") or "",
                    wind_power=cast.get("daypower") or ""
                )
            except ValidationError:
                logger.warning("[Amap] 跳过字段校验失败的天气条目")
                continue
            result.append(weather)

        logger.debug(f"[Amap] get_weather 完成，返回 {len(result)} 天天气数据")
        self._cache_put(cache_key, result, WEATHER_CACHE_TTL_SECONDS)
        return result

    async def search_hotels(
        self,
        city: str,
        hotel_type: str = "酒店",
        page_size: int = 10
    ) -> List[Hotel]:
        """搜索酒店"""
        logger.debug(f"[Amap] search_hotels | 城市: {city}, 类型: {hotel_type}")

        keywords = hotel_type if "酒店" in hotel_type else f"{hotel_type}酒店"
        pois = await self.search_poi(keywords, city, citylimit=True, page_size=page_size)

        hotels = []
        for poi in pois:
            hotel = Hotel(
                name=poi.name,
                address=poi.address,
                location=poi.location,
                rating=str(poi.rating) if poi.rating else "",
                type=poi.category or "酒店",
                # Amap's cost is a per-capita reference. For hotels it is the
                # closest available nightly estimate; never present it as a
                # guaranteed booking price.
                estimated_cost=poi.cost_reference or 0,
                price_range=(f"约 ¥{poi.cost_reference}/晚" if poi.cost_reference else ""),
            )
            hotels.append(hotel)

        logger.debug(f"[Amap] search_hotels 完成，返回 {len(hotels)} 家酒店")
        return hotels

    @retry(stop=stop_after_attempt(2), wait=wait_fixed(1), retry=retry_if_exception(_is_transient_error), reraise=True)
    async def geocode(self, address: str, city: Optional[str] = None) -> Optional[Location]:
        """地理编码"""
        logger.debug(f"[Amap] geocode | 地址: {address}, 城市: {city}")

        cache_key = ("geocode", address, city)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        params = {
            "key": self.api_key,
            "address": address,
            "output": "json"
        }
        if city:
            params["city"] = city

        response = await self._throttled_request("GET", f"{self.base_url}/geocode/geo", params=params)
        data = response.json()

        if not _amap_response_ok(data):
            return None

        geocodes = data.get("geocodes", [])
        if not isinstance(geocodes, list) or not geocodes or not isinstance(geocodes[0], dict):
            return None

        location_str = geocodes[0].get("location", "")
        if not isinstance(location_str, str):
            return None

        parts = location_str.split(",")
        if len(parts) != 2 or not is_valid_coordinate(parts[0], parts[1]):
            logger.warning("[Amap] 地理编码返回无效坐标")
            return None
        location = Location(longitude=float(parts[0]), latitude=float(parts[1]))
        logger.debug(f"[Amap] geocode 坐标: ({location.longitude}, {location.latitude})")

        self._cache_put(cache_key, location, GEOCODE_CACHE_TTL_SECONDS)
        return location

    async def get_static_map(
        self,
        city: str,
        markers: Optional[str] = None,
        width: int = 800,
        height: int = 500
    ) -> bytes:
        """获取静态地图图片"""
        params = {
            "key": self.api_key,
            "city": city,
            "zoom": 11,
            "size": f"{width}*{height}",
            "scale": 2,
            "traffic": 0
        }

        if markers:
            params["markers"] = f"mid,0x008000,A:{markers}"

        response = await self._throttled_request(
            "GET",
            "https://restapi.amap.com/v3/staticmap",
            params=params
        )
        return response.content


# 单例
_amap_service: Optional[AmapService] = None


def get_amap_service() -> AmapService:
    """获取高德地图服务实例"""
    global _amap_service
    if _amap_service is None:
        _amap_service = AmapService()
    return _amap_service


async def close_amap_service() -> None:
    """Close resources owned by the singleton Amap service."""
    global _amap_service
    if _amap_service is not None:
        await _amap_service.close()
        _amap_service = None
