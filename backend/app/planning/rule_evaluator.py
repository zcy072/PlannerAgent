"""Evaluate generated trip plans before finalizing."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from ..core.constants import (
    BUDGET_LOWER_TOLERANCE,
    BUDGET_UPPER_TOLERANCE,
    MAX_DAILY_MINUTES,
)
from ..models.schemas import EvaluatorReportModel, TripRequest
from .location_utils import extract_coordinates
from .travel_time import route_speed_kmh

def evaluate_trip_plan(
    trip_plan: Dict[str, Any],
    request: TripRequest,
    route_skeleton: Sequence[dict] | None = None,
    candidate_hotels: Sequence[Any] | None = None,
) -> dict:
    """Run deterministic quality checks on the generated trip plan.

    ``candidate_hotels`` are the hotels the Hotel Agent recommended; when given and
    non-empty, every hotel in the plan must come from that list.
    """
    issues: List[str] = []
    suggestions: List[str] = []
    day_issues: Dict[int, List[str]] = {}
    global_issues: List[str] = []
    non_retryable_issues: List[str] = []

    _check_coverage(trip_plan, request, route_skeleton, issues, global_issues, non_retryable_issues)
    _check_coordinates(trip_plan, issues, suggestions, day_issues)
    _check_attraction_sources(trip_plan, route_skeleton, issues, suggestions, day_issues)
    _check_hotel_sources(trip_plan, candidate_hotels, issues, suggestions, day_issues)
    _check_daily_intensity(trip_plan, request, issues, suggestions, day_issues, non_retryable_issues)
    _check_budget(trip_plan, request.budget, issues, suggestions, global_issues)

    return EvaluatorReportModel(
        passed=len(issues) == 0,
        issues=issues,
        suggestions=_dedupe_keep_order(suggestions),
        day_issues={
            day_index: _dedupe_keep_order(day_items)
            for day_index, day_items in day_issues.items()
        },
        global_issues=_dedupe_keep_order(global_issues),
        non_retryable_issues=_dedupe_keep_order(non_retryable_issues),
    ).model_dump()


def _check_coverage(
    trip_plan: Dict[str, Any],
    request: TripRequest,
    route_skeleton: Sequence[dict] | None,
    issues: List[str],
    global_issues: List[str],
    non_retryable_issues: List[str],
) -> None:
    days = trip_plan.get("days", [])
    if len(days) != request.travel_days:
        issue = f"行程只有 {len(days)} 天，与请求的 {request.travel_days} 天不符"
        issues.append(issue)
        global_issues.append(issue)
        non_retryable_issues.append(issue)

    if route_skeleton is not None:
        covered_indices = {
            day.get("day_index") for day in route_skeleton if day.get("attractions")
        }
        uncovered = [index + 1 for index in range(request.travel_days) if index not in covered_indices]
        if uncovered:
            issue = "第 " + "、".join(map(str, uncovered)) + " 天缺少可用的景点路线骨架"
            issues.append(issue)
            global_issues.append(issue)
            non_retryable_issues.append(issue)

    empty_days = [
        _day_index(day, index) + 1
        for index, day in enumerate(days)
        if not day.get("attractions")
    ]
    if empty_days:
        issue = "第 " + "、".join(map(str, empty_days)) + " 天没有可用景点"
        issues.append(issue)
        global_issues.append(issue)
        non_retryable_issues.append(issue)


def _check_coordinates(
    trip_plan: Dict[str, Any],
    issues: List[str],
    suggestions: List[str],
    day_issues: Dict[int, List[str]],
) -> None:
    for fallback_index, day in enumerate(trip_plan.get("days", [])):
        day_index = _day_index(day, fallback_index)
        day_label = _day_label(day)
        for attraction in day.get("attractions", []):
            name = attraction.get("name") or "未命名景点"
            if extract_coordinates(attraction.get("location")) is None:
                issue = f"{day_label} '{name}' 缺少有效坐标"
                issues.append(issue)
                day_issues.setdefault(day_index, []).append(issue)
                suggestions.append("所有景点必须使用可用景点或路线骨架中的真实经纬度")


def _attraction_key(attraction: Dict[str, Any]) -> tuple[str, str]:
    return (attraction.get("name") or "").strip(), (attraction.get("address") or "").strip()


def _check_attraction_sources(
    trip_plan: Dict[str, Any],
    route_skeleton: Sequence[dict] | None,
    issues: List[str],
    suggestions: List[str],
    day_issues: Dict[int, List[str]],
) -> None:
    """Attractions must come from the route skeleton and appear on one day only."""
    allowed_names = {
        (attraction.get("name") or "").strip()
        for skeleton_day in (route_skeleton or [])
        for attraction in skeleton_day.get("attractions", [])
    }
    first_day_of: Dict[tuple[str, str], str] = {}
    for fallback_index, day in enumerate(trip_plan.get("days", [])):
        day_index = _day_index(day, fallback_index)
        day_label = _day_label(day)
        for attraction in day.get("attractions", []):
            name, _ = key = _attraction_key(attraction)
            if not name:
                continue
            if allowed_names and name not in allowed_names:
                issue = f"{day_label} '{name}' 不在候选景点和路线骨架中"
                issues.append(issue)
                day_issues.setdefault(day_index, []).append(issue)
                suggestions.append("只能安排路线骨架中提供的景点，不要自行添加景点")
            elif key in first_day_of:
                if first_day_of[key] != day_label:
                    issue = f"'{name}' 同时出现在{first_day_of[key]}和{day_label}"
                else:
                    issue = f"{day_label} '{name}' 重复出现"
                issues.append(issue)
                day_issues.setdefault(day_index, []).append(issue)
                suggestions.append("同一景点只能安排在一天，不要重复")
            else:
                first_day_of.setdefault(key, day_label)


def _check_hotel_sources(
    trip_plan: Dict[str, Any],
    candidate_hotels: Sequence[Any] | None,
    issues: List[str],
    suggestions: List[str],
    day_issues: Dict[int, List[str]],
) -> None:
    """A planned hotel must be one the Hotel Agent recommended, not an invented one."""
    allowed_names = {
        (getattr(hotel, "name", None) or (hotel.get("name") if isinstance(hotel, dict) else "") or "").strip()
        for hotel in (candidate_hotels or [])
    } - {""}
    if not allowed_names:
        return
    for fallback_index, day in enumerate(trip_plan.get("days", [])):
        hotel = day.get("hotel") or {}
        name = (hotel.get("name") or "").strip()
        if name and name not in allowed_names:
            day_index = _day_index(day, fallback_index)
            issue = f"{_day_label(day)} 酒店 '{name}' 不在推荐酒店列表中"
            issues.append(issue)
            day_issues.setdefault(day_index, []).append(issue)
            suggestions.append("酒店必须从推荐酒店列表中选择，不要自行编造")


def _check_daily_intensity(
    trip_plan: Dict[str, Any],
    request: TripRequest,
    issues: List[str],
    suggestions: List[str],
    day_issues: Dict[int, List[str]],
    non_retryable_issues: List[str],
) -> None:
    for fallback_index, day in enumerate(trip_plan.get("days", [])):
        day_index = _day_index(day, fallback_index)
        attractions = day.get("attractions", [])
        visit_minutes = sum(
            _safe_number(attraction.get("visit_duration"), 120)
            for attraction in attractions
        )
        route_summary = day.get("route_summary") or {}
        verified_minutes = _safe_number(route_summary.get("estimated_duration_min"), -1)
        if verified_minutes >= 0:
            route_minutes = verified_minutes
        else:
            distance_km = _safe_number(route_summary.get("estimated_distance_km"), 0)
            transportation = day.get("transportation") or request.transportation
            route_minutes = distance_km / route_speed_kmh(transportation) * 60
        total_minutes = visit_minutes + route_minutes

        if total_minutes > MAX_DAILY_MINUTES:
            day_label = _day_label(day)
            issue = f"{day_label} 总时长约 {total_minutes:.0f} 分钟，超过 10 小时"
            issues.append(issue)
            day_issues.setdefault(day_index, []).append(issue)
            if route_minutes >= MAX_DAILY_MINUTES:
                non_retryable_issues.append(issue)
                suggestions.append(f"{day_label}路线通行时间已达上限，需要重新选择或分组景点")
            else:
                suggestions.append(f"缩短{day_label}景点停留时间；如仍超时，需要重新选择路线")


def _check_budget(
    trip_plan: Dict[str, Any],
    user_budget: List[int] | None,
    issues: List[str],
    suggestions: List[str],
    global_issues: List[str],
) -> None:
    if not user_budget or len(user_budget) != 2:
        return

    budget = trip_plan.get("budget") or {}
    total = _safe_number(budget.get("total"), 0)
    min_budget, max_budget = user_budget
    lower_bound = min_budget * (1 - BUDGET_LOWER_TOLERANCE)
    upper_bound = max_budget * (1 + BUDGET_UPPER_TOLERANCE)

    if total < lower_bound or total > upper_bound:
        issue = (
            f"预算 {total:.0f} 元偏离用户区间 {min_budget}-{max_budget} 元"
            f"（允许范围 {lower_bound:.0f}-{upper_bound:.0f} 元）"
        )
        issues.append(issue)
        global_issues.append(issue)
        suggestions.append("调整酒店、餐饮、交通或景点门票估算，使总预算贴近用户预算区间")


def _safe_number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _day_label(day: Dict[str, Any]) -> str:
    day_index = day.get("day_index", 0)
    try:
        return f"第{int(day_index) + 1}天"
    except (TypeError, ValueError):
        return "当天"


def _day_index(day: Dict[str, Any], fallback: int) -> int:
    try:
        return int(day.get("day_index", fallback))
    except (TypeError, ValueError):
        return fallback


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
