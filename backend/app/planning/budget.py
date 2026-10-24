"""Pure budget calculation helpers."""

from __future__ import annotations

import re

from .travel_time import transport_cost_per_km


def coerce_cost(value: object) -> int:
    """Coerce legacy numeric strings without letting one malformed field abort a save."""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value)
        if not match:
            return 0
        value = match.group(0)
    try:
        return max(0, int(round(float(value))))
    except (TypeError, ValueError):
        return 0


def estimate_transportation(trip_plan: dict) -> int:
    """Estimate route transport only when the plan has no explicit amount.

    Route summaries contain inter-attraction distance, so this deliberately
    excludes airport transfers and fares that cannot be inferred reliably.
    """
    total = 0.0
    for day in trip_plan.get("days", []):
        route_summary = day.get("route_summary")
        if not isinstance(route_summary, dict):
            continue
        distance_km = _coerce_distance(route_summary.get("estimated_distance_km"))
        transportation = str(day.get("transportation") or "公共交通")
        total += distance_km * transport_cost_per_km(transportation)
    return max(0, int(round(total)))


def _coerce_distance(value: object) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value)
        if not match:
            return 0.0
        value = match.group(0)
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def estimate_budget(trip_plan: dict) -> dict:
    """Sum attraction, meal, hotel, and route transport costs."""
    total_attractions = 0
    total_meals = 0
    total_hotels = 0

    for day in trip_plan.get("days", []):
        for attraction in day.get("attractions", []):
            total_attractions += coerce_cost(attraction.get("ticket_price"))
        for meal in day.get("meals", []):
            total_meals += coerce_cost(meal.get("estimated_cost"))
        hotel = day.get("hotel")
        if hotel:
            total_hotels += coerce_cost(hotel.get("estimated_cost"))

    previous_budget = trip_plan.get("budget") or {}
    explicit_transport = 0 if previous_budget.get("transportation_is_estimated") else coerce_cost(
        previous_budget.get("total_transportation")
    )
    total_transportation = explicit_transport or estimate_transportation(trip_plan)
    total = total_attractions + total_meals + total_hotels + total_transportation
    return {
        "total_attractions": total_attractions,
        "total_meals": total_meals,
        "total_hotels": total_hotels,
        "total_transportation": total_transportation,
        "transportation_is_estimated": not bool(explicit_transport),
        "total": total,
    }
