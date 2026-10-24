"""Fill route candidates from the unused POI pool and bounded Amap searches."""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from ...agents.utils.pool_manager import dedupe_key
from ...core.constants import POI_CANDIDATE_POOL_SIZE
from ...models.schemas import POI, TripRequest
from ...planning.location_utils import is_valid_coordinate
from ...services.amap_service import get_amap_service


@dataclass
class RouteSupply:
    pois: list[POI]
    pool: list[POI]
    promoted: int = 0
    fetched: int = 0
    search_attempts: int = 0
    failures: list[str] | None = None


def _usable(poi: POI) -> bool:
    location = poi.location
    return bool(location and is_valid_coordinate(location.longitude, location.latitude))


def _search_keywords(request: TripRequest) -> list[str]:
    keywords = []
    for preference in request.preferences[:1]:
        value = preference.strip()
        if value:
            keywords.append(value if "景点" in value else f"{value}景点")
    keywords.extend(("景点", "博物馆", "公园"))
    return list(dict.fromkeys(keywords))[:3]


async def supply_route_candidates(
    selected: list[POI],
    remaining_pool: list[POI],
    request: TripRequest,
) -> RouteSupply:
    """Ensure one distinct, coordinate-valid candidate per requested day when possible."""
    seen = set()
    route_pois = []
    pool = []
    for source, destination in ((selected, route_pois), (remaining_pool, pool)):
        for poi in source:
            key = dedupe_key(poi)
            if _usable(poi) and key not in seen:
                destination.append(poi)
                seen.add(key)

    promoted = 0
    while len(route_pois) < request.travel_days and pool:
        route_pois.append(pool.pop(0))
        promoted += 1

    supply = RouteSupply(route_pois, pool, promoted=promoted, failures=[])
    if len(route_pois) >= request.travel_days:
        return supply

    try:
        amap = get_amap_service()
    except Exception as exc:
        logger.warning("[RouteSupply] 高德补搜不可用 | error_type={}", type(exc).__name__)
        return supply

    for keyword in _search_keywords(request):
        supply.search_attempts += 1
        try:
            results = await amap.search_poi(keyword, request.city, citylimit=True)
        except Exception as exc:
            supply.failures.append(f"{keyword}: {type(exc).__name__}")
            logger.warning(
                "[RouteSupply] POI 补搜失败 | keyword={} error_type={}",
                keyword, type(exc).__name__,
            )
            continue
        if not results:
            supply.failures.append(f"{keyword}: empty")
        for poi in results:
            key = dedupe_key(poi)
            if not _usable(poi) or key in seen:
                continue
            seen.add(key)
            if len(route_pois) < request.travel_days:
                route_pois.append(poi)
                supply.fetched += 1
            elif len(pool) < POI_CANDIDATE_POOL_SIZE:
                pool.append(poi)
        if len(route_pois) >= request.travel_days:
            break

    if not route_pois and supply.search_attempts:
        logger.warning(
            "[RouteSupply] 补搜完成但没有可用 POI | attempts={} failures={}",
            supply.search_attempts, supply.failures,
        )

    return supply


def partition_route_candidates(
    pois: list[POI], pool: list[POI], route_skeleton: list[dict],
) -> tuple[list[POI], list[POI]]:
    """Keep route POIs in skeleton order and all unused POIs in the replacement pool."""
    used_order = [
        f"{attraction.get('name', '')}|{attraction.get('address', '')}"
        for day in route_skeleton
        for attraction in day.get("attractions", [])
    ]
    used_keys = set(used_order)
    by_key = {dedupe_key(poi): poi for poi in pois}
    route_pois = [by_key[key] for key in used_order if key in by_key]
    remaining_pool = [
        poi for poi in pois + pool
        if dedupe_key(poi) not in used_keys
    ]
    return route_pois, remaining_pool
