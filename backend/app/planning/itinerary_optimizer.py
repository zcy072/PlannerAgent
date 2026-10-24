"""Lightweight route ordering for itinerary planning."""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
import re
from typing import Callable, List, Sequence, TypeVar

from ..core.constants import MAX_DAILY_MINUTES
from ..models.schemas import POI, Weather
from .location_utils import extract_coordinates, is_valid_coordinate
from .travel_time import route_speed_kmh
from .weather import is_unfavorable_for_outdoor


T = TypeVar("T")


_INDOOR_KEYWORDS = (
    "博物馆", "博物院", "美术馆", "艺术馆", "展览馆", "展馆", "科技馆", "图书馆", "纪念馆",
    "水族馆", "海洋馆", "剧院", "剧场", "影城", "商场", "购物", "文化宫", "体验馆", "科教文化",
)


def _is_indoor(poi: POI) -> bool:
    text = f"{poi.name}{poi.category or ''}"
    return any(keyword in text for keyword in _INDOOR_KEYWORDS)


def _assign_groups_by_weather(
    groups: List[List[POI]], weather: Sequence[Weather],
) -> List[int] | None:
    """Reorder day groups in place so the most indoor groups land on unfavorable-weather days.

    Only whole days move, so grouping and in-day order are untouched. Returns the old
    group index now sitting at each day, or None when nothing moved.
    """
    day_count = len(groups)
    bad_days = [
        index for index, item in enumerate(weather[:day_count])
        if is_unfavorable_for_outdoor(item)
    ]
    if not bad_days or len(bad_days) == day_count:
        return None

    def indoor_ratio(group: List[POI]) -> float:
        return sum(_is_indoor(poi) for poi in group) / len(group) if group else 0.0

    # Stable sorts keep the original order whenever ratios tie.
    bad_set = set(bad_days)
    day_order = sorted(range(day_count), key=lambda index: index not in bad_set)
    group_order = sorted(range(day_count), key=lambda index: -indoor_ratio(groups[index]))
    source_by_day = [0] * day_count
    for day_index, group_index in zip(day_order, group_order):
        source_by_day[day_index] = group_index
    if source_by_day == list(range(day_count)):
        return None
    groups[:] = [groups[group_index] for group_index in source_by_day]
    return source_by_day


def optimize_poi_order(
    pois: List[POI], travel_days: int, transportation: str = "公共交通",
    weather: Sequence[Weather] | None = None,
) -> List[dict]:
    """
    Build a route skeleton by grouping POIs by day and ordering each day by distance.

    This is intentionally lightweight: it uses coordinates already returned by Amap,
    filters invalid points, assigns 2-3 attractions per day, then applies a nearest
    neighbor ordering inside each day. When a forecast is given, whole-day groups
    with more indoor attractions are moved onto rainy/extreme-temperature days.
    """
    valid_pois = [poi for poi in pois if _has_valid_location(poi)]
    if travel_days <= 0 or not valid_pois:
        return []

    selected_pois = _select_diverse_pois(
        valid_pois, travel_days * 3, min_total=travel_days,
    )
    daily_groups = _split_by_day(selected_pois, travel_days)
    adjusted_days = _fit_daily_groups(daily_groups, transportation)
    moved_from = _assign_groups_by_weather(daily_groups, weather) if weather else None
    if moved_from is not None:
        adjusted_days = {day for day, old in enumerate(moved_from) if old in adjusted_days}
    weather_sorted_days = (
        {day for day, old in enumerate(moved_from) if old != day} if moved_from else set()
    )
    route_days = []

    for day_index, day_pois in enumerate(daily_groups):
        ordered_pois = _nearest_neighbor_order(day_pois)
        estimated_distance = _route_distance_km(ordered_pois)

        route_days.append({
            "day_index": day_index,
            "attractions": [_poi_to_attraction(poi) for poi in ordered_pois],
            "estimated_distance_km": round(estimated_distance, 2),
            "optimization_reason": (
                "基于景点坐标先做空间聚类分组，再使用最近邻策略排序，减少跨区域往返。"
                + ("已根据单日游玩强度调整分组。" if day_index in adjusted_days else "")
                + ("已按天气调整日期顺序，室内景点较多的一天排在不利户外的日期。" if day_index in weather_sorted_days else "")
            ),
        })

    return route_days


def order_attractions_by_distance(attractions: List[dict]) -> tuple[List[dict], float]:
    """Order original attraction dictionaries without dropping their extra fields."""
    valid_attractions = [
        attraction
        for attraction in attractions
        if _attraction_coordinates(attraction) is not None
    ]
    invalid_attractions = [
        attraction
        for attraction in attractions
        if _attraction_coordinates(attraction) is None
    ]
    ordered = _best_nearest_neighbor_order(
        valid_attractions,
        _distance_between_attractions,
    )
    distance = _route_distance(ordered, _distance_between_attractions)
    return ordered + invalid_attractions, round(distance, 2)


def _select_diverse_pois(
    pois: List[POI], max_total: int, min_total: int = 0,
) -> List[POI]:
    """Select route candidates while avoiding one large scenic area dominating the trip."""
    if max_total <= 0:
        return []

    selected = []
    seen_ids = set()
    cluster_counts = {}

    # First pass: keep broad diversity by limiting each name cluster to one POI.
    for poi in pois:
        cluster_key = _poi_cluster_key(poi)
        if cluster_counts.get(cluster_key, 0) >= 1:
            continue

        selected.append(poi)
        seen_ids.add(id(poi))
        cluster_counts[cluster_key] = cluster_counts.get(cluster_key, 0) + 1

        if len(selected) >= max_total:
            return selected

    # Second pass: allow one additional sub-attraction per cluster if seats remain.
    for poi in pois:
        if id(poi) in seen_ids:
            continue

        cluster_key = _poi_cluster_key(poi)
        if cluster_counts.get(cluster_key, 0) >= 2:
            continue

        selected.append(poi)
        seen_ids.add(id(poi))
        cluster_counts[cluster_key] = cluster_counts.get(cluster_key, 0) + 1

        if len(selected) >= max_total:
            return selected

    # Preserve one usable attraction per day if strict diversity would leave gaps.
    for poi in pois:
        if len(selected) >= min_total or len(selected) >= max_total:
            break
        if id(poi) not in seen_ids:
            selected.append(poi)
            seen_ids.add(id(poi))

    return selected


def _split_by_day(selected: List[POI], travel_days: int) -> List[List[POI]]:
    day_count = min(travel_days, len(selected))
    if day_count <= 1:
        return [selected]

    centers = _choose_spread_centers(selected, day_count)
    groups = [[center] for center in centers]
    center_ids = {id(center) for center in centers}

    remaining = [poi for poi in selected if id(poi) not in center_ids]
    for poi in remaining:
        group_index = _nearest_available_group_index(poi, groups)
        groups[group_index].append(poi)

    _rebalance_groups(groups)
    return groups


def _poi_cluster_key(poi: POI) -> str:
    name = poi.name.strip()
    if not name:
        return poi.address.strip()

    parts = re.split(r"[-—–－(（]", name, maxsplit=1)
    return parts[0].strip() or name


def _choose_spread_centers(pois: List[POI], day_count: int) -> List[POI]:
    centers = [pois[0]]

    while len(centers) < day_count:
        center_ids = {id(center) for center in centers}
        next_center = max(
            (poi for poi in pois if id(poi) not in center_ids),
            key=lambda poi: min(_distance_between_pois(poi, center) for center in centers)
        )
        centers.append(next_center)

    return centers


def _nearest_available_group_index(poi: POI, groups: List[List[POI]]) -> int:
    available_indexes = [
        index for index, group in enumerate(groups)
        if len(group) < 3
    ]
    if not available_indexes:
        available_indexes = list(range(len(groups)))

    return min(
        available_indexes,
        key=lambda index: min(_distance_between_pois(poi, group_poi) for group_poi in groups[index])
    )


def _rebalance_groups(groups: List[List[POI]]):
    """Prefer at least two POIs per day when there are enough selected attractions."""
    if sum(len(group) for group in groups) < len(groups) * 2:
        return

    small_groups = [group for group in groups if len(group) < 2]
    for small_group in small_groups:
        donor = max(groups, key=len)
        if len(donor) <= 2:
            return
        small_group.append(donor.pop())


def _group_minutes(pois: List[POI], transportation: str) -> float:
    ordered = _nearest_neighbor_order(pois)
    visit_minutes = sum(poi.visit_duration or 120 for poi in ordered)
    route_minutes = _route_distance_km(ordered) / route_speed_kmh(transportation) * 60
    return visit_minutes + route_minutes


def _fit_daily_groups(groups: List[List[POI]], transportation: str) -> set[int]:
    """Move overlong-day POIs into a feasible day, or leave them as spare candidates."""
    adjusted = set()
    for source_index, source in enumerate(groups):
        while len(source) > 1 and _group_minutes(source, transportation) > MAX_DAILY_MINUTES:
            moves = []
            for poi_index, poi in enumerate(source):
                remaining = source[:poi_index] + source[poi_index + 1:]
                source_minutes = _group_minutes(remaining, transportation)
                for target_index, target in enumerate(groups):
                    if target_index == source_index or len(target) >= 3:
                        continue
                    target_minutes = _group_minutes(target + [poi], transportation)
                    if target_minutes <= MAX_DAILY_MINUTES:
                        moves.append((
                            max(0, source_minutes - MAX_DAILY_MINUTES),
                            source_minutes + target_minutes,
                            poi_index,
                            target_index,
                        ))

            if moves:
                _, _, poi_index, target_index = min(moves)
                groups[target_index].append(source.pop(poi_index))
                adjusted.update((source_index, target_index))
                continue

            # The fixed skeleton cannot use this POI without exceeding the day limit.
            drop_index = min(
                range(len(source)),
                key=lambda index: _group_minutes(
                    source[:index] + source[index + 1:], transportation,
                ),
            )
            source.pop(drop_index)
            adjusted.add(source_index)

    return adjusted


def _nearest_neighbor_order(pois: List[POI]) -> List[POI]:
    return _best_nearest_neighbor_order(pois, _distance_between_pois)


def _best_nearest_neighbor_order(
    items: Sequence[T],
    distance: Callable[[T, T], float],
) -> List[T]:
    """Try nearest-neighbor from every start and keep the shortest route."""
    if len(items) <= 2:
        return list(items)

    best_order: List[T] = []
    best_distance = float("inf")
    for start_index in range(len(items)):
        remaining = list(items)
        ordered = [remaining.pop(start_index)]
        while remaining:
            current = ordered[-1]
            nearest_index = min(
                range(len(remaining)),
                key=lambda index: distance(current, remaining[index]),
            )
            ordered.append(remaining.pop(nearest_index))

        route_distance = _route_distance(ordered, distance)
        if route_distance < best_distance:
            best_order = ordered
            best_distance = route_distance

    return best_order


def _route_distance(items: Sequence[T], distance: Callable[[T, T], float]) -> float:
    return sum(
        distance(items[index], items[index + 1])
        for index in range(len(items) - 1)
    )


def _route_distance_km(pois: List[POI]) -> float:
    return _route_distance(pois, _distance_between_pois)


def _distance_between_pois(first: POI, second: POI) -> float:
    return _haversine_km(
        first.location.longitude,
        first.location.latitude,
        second.location.longitude,
        second.location.latitude,
    )


def _attraction_coordinates(attraction: dict) -> tuple[float, float] | None:
    return extract_coordinates(attraction.get("location"))


def _distance_between_attractions(first: dict, second: dict) -> float:
    first_coordinates = _attraction_coordinates(first)
    second_coordinates = _attraction_coordinates(second)
    if first_coordinates is None or second_coordinates is None:
        raise ValueError("景点缺少有效坐标，无法计算距离")
    first_lon, first_lat = first_coordinates
    second_lon, second_lat = second_coordinates
    return _haversine_km(first_lon, first_lat, second_lon, second_lat)


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    radius_km = 6371.0
    dlon = radians(lon2 - lon1)
    dlat = radians(lat2 - lat1)
    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)

    value = (
        sin(dlat / 2) ** 2
        + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2) ** 2
    )
    return 2 * radius_km * asin(sqrt(value))


def _has_valid_location(poi: POI) -> bool:
    if not poi.location:
        return False

    lon = poi.location.longitude
    lat = poi.location.latitude
    return is_valid_coordinate(lon, lat)


def _poi_to_attraction(poi: POI) -> dict:
    return {
        "name": poi.name,
        "address": poi.address,
        "location": {
            "longitude": poi.location.longitude,
            "latitude": poi.location.latitude,
        },
        "visit_duration": poi.visit_duration or 120,
        "description": poi.description or "",
        "category": poi.category or "景点",
        "ticket_price": poi.ticket_price or 0,
    }
