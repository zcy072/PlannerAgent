"""Shared coordinate-distance travel-time estimates for route planning and evaluation."""

from ..core.constants import (
    AVERAGE_ROUTE_SPEED_KMH,
    CYCLING_ROUTE_SPEED_KMH,
    TRANSIT_ROUTE_SPEED_KMH,
    WALKING_ROUTE_SPEED_KMH,
    WALKING_COST_PER_KM,
    CYCLING_COST_PER_KM,
    TRANSIT_COST_PER_KM,
    DRIVING_COST_PER_KM,
)


def route_speed_kmh(transportation: str) -> float:
    if "步行" in transportation:
        return WALKING_ROUTE_SPEED_KMH
    if "骑行" in transportation:
        return CYCLING_ROUTE_SPEED_KMH
    if "公共交通" in transportation or "公交" in transportation or "地铁" in transportation:
        return TRANSIT_ROUTE_SPEED_KMH
    return AVERAGE_ROUTE_SPEED_KMH


def transport_cost_per_km(transportation: str) -> float:
    """Return a conservative CNY/km estimate for route-only transport."""
    if "步行" in transportation:
        return WALKING_COST_PER_KM
    if "骑行" in transportation:
        return CYCLING_COST_PER_KM
    if "公共交通" in transportation or "公交" in transportation or "地铁" in transportation:
        return TRANSIT_COST_PER_KM
    return DRIVING_COST_PER_KM
