"""Post-processing helpers for LLM generated trip plans."""

import json
from datetime import datetime, timedelta
from typing import List, Optional

from loguru import logger
from pydantic import ValidationError

from ..models.schemas import POI, TripPlan, Weather
from ..utils.llm_json import extract_json_object
from .location_utils import extract_coordinates


def parse_planner_response(
    response: str,
    city: str,
    start_date: str,
    end_date: str,
    travel_days: int,
    transportation: str,
    accommodation: str,
    pois: List[POI],
    weather: List[Weather],
    route_skeleton: Optional[List[dict]] = None
) -> dict:
    """Parse Planner LLM response and provide a fallback trip plan."""
    json_str = extract_json_object(response)

    try:
        data = json.loads(json_str)
        if not isinstance(data, dict):
            raise ValueError("Planner 返回的 JSON 根节点不是对象")

        if "city" not in data:
            data["city"] = city
        if "start_date" not in data:
            data["start_date"] = start_date
        if "end_date" not in data:
            data["end_date"] = end_date
        if "days" not in data or not data["days"]:
            data["days"] = create_default_days(
                city, start_date, travel_days, transportation, accommodation, pois, route_skeleton
            )
        else:
            data["days"] = normalize_days(
                data["days"],
                city,
                start_date,
                travel_days,
                transportation,
                accommodation,
                pois,
                route_skeleton,
            )

        data["weather_info"] = [w.model_dump() for w in weather] if weather else []

        return TripPlan.model_validate(data).model_dump()

    except (json.JSONDecodeError, TypeError, AttributeError, ValueError, ValidationError) as e:
        logger.warning(f"[Planner Agent] 响应无法恢复，使用默认行程: {e}")
        return _create_fallback_plan(
            city=city,
            start_date=start_date,
            end_date=end_date,
            travel_days=travel_days,
            transportation=transportation,
            accommodation=accommodation,
            pois=pois,
            weather=weather,
            route_skeleton=route_skeleton,
        )


def _create_fallback_plan(
    city: str,
    start_date: str,
    end_date: str,
    travel_days: int,
    transportation: str,
    accommodation: str,
    pois: List[POI],
    weather: List[Weather],
    route_skeleton: Optional[List[dict]],
) -> dict:
    """Return a schema-valid itinerary when the Planner response is unusable."""
    fallback_days = create_default_days(
        city, start_date, travel_days, transportation, accommodation, pois, route_skeleton
    )
    fallback_attractions = sum(
        int(item.get("ticket_price") or 0)
        for day in fallback_days
        for item in day.get("attractions", [])
    )
    fallback = {
        "city": city,
        "start_date": start_date,
        "end_date": end_date,
        "days": fallback_days,
        "weather_info": [w.model_dump() for w in weather] if weather else [],
        "overall_suggestions": f"这是{city}的{travel_days}天旅行计划。",
        "budget": {
            "total_attractions": fallback_attractions,
            "total_hotels": 0,
            "total_meals": travel_days * 160,
            "total_transportation": 0,
            "total": travel_days * 160,
        },
    }
    return TripPlan.model_validate(fallback).model_dump()


def _match_poi_for_coordinates(attr: dict, pois: List[POI]) -> Optional[POI]:
    """Pick the source POI for an attraction; ambiguous fuzzy matches return None."""
    name = (attr.get("name") or "").strip()
    address = (attr.get("address") or "").strip()
    located = [poi for poi in pois if poi.location]

    exact = [poi for poi in located if poi.name == name]
    if address:
        same_address = [poi for poi in exact if (poi.address or "").strip() == address]
        if same_address:
            return same_address[0]
    if exact:
        return exact[0]

    # Substring matching can attach another POI's coordinates, so only accept
    # it when exactly one candidate qualifies.
    fuzzy = [poi for poi in located if poi.name and (name in poi.name or poi.name in name)]
    return fuzzy[0] if len(fuzzy) == 1 else None


def fix_coordinates(trip_plan: dict, pois: List[POI]):
    """Ensure generated attractions use real coordinates from source POIs."""
    for day in trip_plan.get("days", []):
        for attr in day.get("attractions", []):
            if not attr.get("name"):
                continue
            if extract_coordinates(attr.get("location")) is None:
                poi = _match_poi_for_coordinates(attr, pois)
                if poi is not None:
                    attr["location"] = {
                        "longitude": poi.location.longitude,
                        "latitude": poi.location.latitude
                    }


def apply_route_skeleton(trip_plan: dict, route_skeleton: List[dict]):
    """Restore optimizer grouping, order, coordinates, and route summary after LLM generation."""
    if not route_skeleton:
        # A Planner response cannot create a trustworthy route or price source
        # when candidate collection produced no skeleton. Keep its content for
        # display, but remove fabricated route metrics so budget/evaluator do
        # not treat model prose as verified data.
        for day in trip_plan.get("days", []):
            summary = day.get("route_summary")
            if isinstance(summary, dict):
                summary["estimated_distance_km"] = 0
                summary["estimated_duration_min"] = None
                summary["optimization_reason"] = "未获取到路线候选，距离与交通费用待核实"
        return

    days = trip_plan.get("days", [])
    day_by_index = {
        day.get("day_index", index): day
        for index, day in enumerate(days)
    }

    for skeleton_day in route_skeleton:
        day_index = skeleton_day.get("day_index")
        day = day_by_index.get(day_index)
        if not day:
            continue

        generated_attractions = list(day.get("attractions", []))

        merged_attractions = []
        for skeleton_attr in skeleton_day.get("attractions", []):
            skeleton_name = skeleton_attr.get("name") or ""
            skeleton_address = skeleton_attr.get("address") or ""
            match_index = next(
                (
                    index
                    for index, attraction in enumerate(generated_attractions)
                    if (attraction.get("name") or "") == skeleton_name
                    and (attraction.get("address") or "") == skeleton_address
                ),
                None,
            )
            if match_index is None:
                match_index = next(
                    (
                        index
                        for index, attraction in enumerate(generated_attractions)
                        if (attraction.get("name") or "") == skeleton_name
                    ),
                    None,
                )
            generated_attr = (
                generated_attractions.pop(match_index)
                if match_index is not None
                else {}
            )
            merged_attr = {
                **generated_attr,
                **skeleton_attr,
                "description": generated_attr.get("description") or skeleton_attr.get("description", "")
            }
            merged_attractions.append(merged_attr)

        day["attractions"] = merged_attractions
        day["route_summary"] = {
            "estimated_distance_km": skeleton_day.get("estimated_distance_km", 0),
            "estimated_duration_min": skeleton_day.get("estimated_duration_min"),
            "optimization_reason": skeleton_day.get("optimization_reason", "")
        }


def normalize_days(
    days: list,
    city: str,
    start_date: str,
    travel_days: int,
    transportation: str,
    accommodation: str,
    pois: List[POI],
    route_skeleton: Optional[List[dict]] = None,
) -> list:
    """Ensure planner output has exactly travel_days continuous day entries."""
    fallback_days = create_default_days(
        city,
        start_date,
        travel_days,
        transportation,
        accommodation,
        pois,
        route_skeleton,
    )
    generated_days = [day for day in days if isinstance(day, dict)]
    raw_indices = []
    for day in generated_days:
        try:
            raw_indices.append(int(day.get("day_index")))
        except (TypeError, ValueError):
            raw_indices.append(None)
    one_based = (
        len(generated_days) == travel_days
        and raw_indices == list(range(1, travel_days + 1))
    )

    generated_by_index = {}
    for fallback_index, day in enumerate(generated_days):
        raw_index = day.get("day_index", fallback_index)
        try:
            normalized_index = int(raw_index) - (1 if one_based else 0)
        except (TypeError, ValueError):
            normalized_index = fallback_index
        generated_by_index[normalized_index] = day

    normalized = []
    for index, fallback_day in enumerate(fallback_days):
        day = generated_by_index.get(index, fallback_day)
        day["day_index"] = index
        day["date"] = fallback_day["date"]
        day.setdefault("transportation", transportation)
        day.setdefault("accommodation", accommodation)
        day.setdefault("description", fallback_day["description"])
        day.setdefault("attractions", fallback_day.get("attractions", []))
        day.setdefault("meals", fallback_day.get("meals", []))
        if "route_summary" not in day and fallback_day.get("route_summary"):
            day["route_summary"] = fallback_day["route_summary"]
        normalized.append(day)

    return normalized


def create_default_days(
    city: str,
    start_date: str,
    travel_days: int,
    transportation: str,
    accommodation: str,
    pois: List[POI],
    route_skeleton: Optional[List[dict]] = None
) -> list:
    """Create a fallback itinerary when the LLM does not return usable days."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    days = []
    poi_index = 0

    for i in range(travel_days):
        current_date = start + timedelta(days=i)

        skeleton_day = route_skeleton[i] if route_skeleton and i < len(route_skeleton) else None
        if skeleton_day:
            day_attractions = skeleton_day.get("attractions", [])
            route_summary = {
                "estimated_distance_km": skeleton_day.get("estimated_distance_km", 0),
                "estimated_duration_min": skeleton_day.get("estimated_duration_min"),
                "optimization_reason": skeleton_day.get("optimization_reason", "")
            }
        else:
            day_attractions = []
            route_summary = None
            for _ in range(3):
                while poi_index < len(pois) and not pois[poi_index].location:
                    poi_index += 1

                if poi_index < len(pois):
                    poi = pois[poi_index]
                    day_attractions.append({
                        "name": poi.name,
                        "address": poi.address,
                        "location": {
                            "longitude": poi.location.longitude,
                            "latitude": poi.location.latitude
                        },
                        "visit_duration": 120,
                        "description": f"{city}推荐景点",
                        "category": poi.category or "景点",
                        # Unknown ticket prices must stay unknown (0); do not
                        # turn a fallback value into a false charge.
                        "ticket_price": poi.ticket_price or 0
                    })
                    poi_index += 1

        day_plan = {
            "date": current_date.strftime("%Y-%m-%d"),
            "day_index": i,
            "description": f"第{i + 1}天 - {city}游览",
            "transportation": transportation,
            "accommodation": accommodation,
            "attractions": day_attractions,
            "meals": [
                {"type": "breakfast", "name": "早餐", "description": "当地特色早餐", "estimated_cost": 30},
                {"type": "lunch", "name": "午餐", "description": "当地特色午餐", "estimated_cost": 50},
                {"type": "dinner", "name": "晚餐", "description": "当地特色晚餐", "estimated_cost": 80}
            ]
        }
        if route_summary:
            day_plan["route_summary"] = route_summary

        days.append(day_plan)

    return days
