"""Shared location validation helpers."""

from __future__ import annotations

from typing import Any


def is_valid_coordinate(longitude: Any, latitude: Any) -> bool:
    """Return whether longitude/latitude are in range and not the null-island pair."""
    try:
        lon = float(longitude)
        lat = float(latitude)
    except (TypeError, ValueError):
        return False

    return not (lon == 0 and lat == 0) and -180 <= lon <= 180 and -90 <= lat <= 90


def extract_coordinates(location: Any) -> tuple[float, float] | None:
    """Return a valid (longitude, latitude) from a location dict, or None.

    Accepts LLM-style ``lng`` / ``lat`` aliases when the canonical keys are missing.
    """
    if not isinstance(location, dict):
        return None
    longitude = location.get("longitude")
    latitude = location.get("latitude")
    if longitude is None:
        longitude = location.get("lng")
    if latitude is None:
        latitude = location.get("lat")
    if not is_valid_coordinate(longitude, latitude):
        return None
    return float(longitude), float(latitude)
