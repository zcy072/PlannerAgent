"""Shared state models for the trip planning graph."""

from __future__ import annotations

from typing import List, Optional, TypedDict

from ...models.schemas import Hotel, POI, TripRequest, Weather


class TripPlanningState(TypedDict):
    """LangGraph state passed between trip planning nodes."""

    request: TripRequest
    pois: List[POI]
    weather: List[Weather]
    hotels: List[Hotel]
    pois_pool: List[POI]
    hotels_pool: List[Hotel]
    route_skeleton: List[dict]
    trip_plan: Optional[dict]
    evaluator_report: Optional[dict]
    retry_count: int
    node_traces: List[dict]
