"""行程局部修改服务：增量更新单日景点/酒店，重排路线，重算预算，重写描述。"""

from __future__ import annotations

from copy import deepcopy
from typing import Optional

from langchain_core.messages import HumanMessage
from loguru import logger

from ..core.llm import get_llm, resolve_provider
from ..models.schemas import Attraction, Hotel, ItemModifyRequest, Location, TripPlan
from ..planning.budget import estimate_budget
from ..planning.itinerary_optimizer import order_attractions_by_distance
from ..planning.location_utils import is_valid_coordinate
from .trip_result_service import (
    MODIFICATION_PERSISTENCE_WARNING,
    trip_result_repository,
)
from ..prompts import TRIP_MODIFY_REWRITE_PROMPT


class TripModificationError(Exception):
    """A user-correctable persisted-trip modification error."""


async def modify_persisted_trip(
    req: ItemModifyRequest,
    *,
    expected_item: Optional[dict] = None,
) -> tuple[TripPlan, bool, Optional[str]]:
    """Atomically load, validate, modify, validate again, and persist one trip."""
    async with trip_result_repository.transaction(req.session_id):
        trip_plan = await trip_result_repository.load(req.session_id)
        if trip_plan is None:
            raise TripModificationError(f"未找到会话 {req.session_id} 的行程")

        error = validate_request(trip_plan, req)
        if error:
            raise TripModificationError(error)

        if expected_item is not None:
            day = trip_plan["days"][req.day_index]
            current_item = (
                day["attractions"][req.item_index]
                if req.item_type == "attraction" else day.get("hotel")
            )
            if current_item != expected_item:
                raise TripModificationError("行程已发生变化，请重新发送修改指令。")

        updated = await apply_modification(trip_plan, req)
        validated = TripPlan.model_validate(updated)
        normalized = validated.model_dump()
        persistence_saved = (
            await trip_result_repository.save(req.session_id, normalized, None) is not None
        )
        warning = None if persistence_saved else MODIFICATION_PERSISTENCE_WARNING
        return validated, persistence_saved, warning


def validate_request(trip_plan: dict, req: ItemModifyRequest) -> Optional[str]:
    """返回 None 表示合法，否则返回错误消息。"""
    days = trip_plan.get("days", [])
    if req.day_index >= len(days):
        return f"day_index {req.day_index} 超出范围（共 {len(days)} 天）"

    day = days[req.day_index]

    if req.item_type == "attraction":
        if req.action == "delete" and req.replacement is not None:
            return "delete 动作不应提供 replacement"
        if req.item_index is None:
            return "attraction 修改必须提供 item_index"
        if req.item_index < 0:
            return "item_index 不能为负数"
        attractions = day.get("attractions", [])
        if req.item_index >= len(attractions):
            return f"item_index {req.item_index} 超出范围"
        if req.action == "delete" and len(attractions) <= 1:
            return "当天只剩 1 个景点，不允许删除"

    elif req.item_type == "hotel":
        if req.item_index is not None:
            return "hotel 修改不应提供 item_index"
        if req.action == "delete":
            return "酒店不允许删除，只能替换"

    else:
        return f"不支持的 item_type: {req.item_type}"

    if req.action not in ("delete", "replace"):
        return f"不支持的 action: {req.action}"

    if req.action == "replace":
        if not req.replacement:
            return "replace 动作必须提供 replacement"
        if req.item_type in ("attraction", "hotel"):
            location = req.replacement.get("location")
            if not isinstance(location, dict):
                return "replacement 缺少有效坐标"
            longitude = location.get("longitude")
            latitude = location.get("latitude")
            if longitude is None or latitude is None:
                return "replacement 缺少有效坐标"
            try:
                normalized_location = Location(**location)
            except Exception:
                return "replacement 坐标格式无效"
            if not is_valid_coordinate(
                normalized_location.longitude,
                normalized_location.latitude,
            ):
                return "replacement 缺少有效坐标"
            try:
                if req.item_type == "attraction":
                    _normalize_to_attraction(req.replacement)
                else:
                    _normalize_to_hotel(req.replacement)
            except Exception:
                return "replacement 字段格式无效"

            # 景点跨天重复会破坏路线覆盖检查；当前被替换的项目本身可以保留原名。
            if req.item_type == "attraction":
                replacement_key = (
                    str(req.replacement.get("name", "")).strip(),
                    str(req.replacement.get("address", "")).strip(),
                )
                for day_index, candidate_day in enumerate(days):
                    for item_index, attraction in enumerate(candidate_day.get("attractions", [])):
                        if day_index == req.day_index and item_index == req.item_index:
                            continue
                        candidate_key = (
                            str(attraction.get("name", "")).strip(),
                            str(attraction.get("address", "")).strip(),
                        )
                        if replacement_key == candidate_key:
                            return "replacement 景点已经出现在行程中，请选择其他景点"

    return None


async def apply_modification(trip_plan: dict, req: ItemModifyRequest) -> dict:
    """对 trip_plan 做局部修改，返回新的 trip_plan dict。"""
    trip_plan = deepcopy(trip_plan)
    _sanitize_weather_info(trip_plan)
    day = trip_plan["days"][req.day_index]

    if req.item_type == "attraction":
        if req.action == "delete":
            removed = day["attractions"].pop(req.item_index)
            logger.info(f"[Modify] 删除第{req.day_index + 1}天景点: {removed.get('name')}")
        else:  # replace
            new_attr = _normalize_to_attraction(req.replacement or {})
            old = day["attractions"][req.item_index]
            day["attractions"][req.item_index] = new_attr
            logger.info(
                f"[Modify] 替换第{req.day_index + 1}天景点: "
                f"{old.get('name')} → {new_attr.get('name')}"
            )

        _reorder_day_route(day)
        day["description"] = await _rewrite_day_description(
            day, resolve_provider(req.llm_provider)
        )

    elif req.item_type == "hotel":
        new_hotel = _normalize_to_hotel(req.replacement or {})
        old = day.get("hotel") or {}
        day["hotel"] = new_hotel
        logger.info(
            f"[Modify] 替换第{req.day_index + 1}天酒店: "
            f"{old.get('name', '无')} → {new_hotel.get('name')}"
        )

    trip_plan["budget"] = recompute_budget(trip_plan)
    # The route and cost changed, so any evaluation report from the old plan is stale.
    trip_plan["evaluator_report"] = None
    trip_plan["evaluator_warnings"] = []
    return trip_plan


def _sanitize_weather_info(trip_plan: dict) -> None:
    """老数据 weather_info 可能是字符串列表，丢弃以通过 TripPlan 校验。"""
    weather_info = trip_plan.get("weather_info") or []
    if any(not isinstance(w, dict) for w in weather_info):
        trip_plan["weather_info"] = []


def _normalize_to_attraction(data: dict) -> dict:
    """把 POI dict 或 Attraction dict 统一成 Attraction dict。"""
    location = data.get("location") or {}
    return Attraction(
        name=data.get("name", ""),
        address=data.get("address", ""),
        location=Location(
            longitude=location.get("longitude", 0),
            latitude=location.get("latitude", 0),
        ),
        visit_duration=data.get("visit_duration") or 120,
        description=data.get("description") or "",
        category=data.get("category"),
        ticket_price=data.get("ticket_price") or 0,
    ).model_dump()


def _normalize_to_hotel(data: dict) -> dict:
    location = data.get("location") or None
    return Hotel(
        name=data.get("name", ""),
        address=data.get("address", ""),
        location=Location(**location) if location else None,
        price_range=data.get("price_range") or "",
        rating=data.get("rating"),
        type=data.get("type") or "",
        estimated_cost=data.get("estimated_cost") or 0,
    ).model_dump()


def _reorder_day_route(day: dict) -> None:
    """按坐标直接重排原始景点字典，保留所有业务字段。"""
    attractions = day.get("attractions", [])
    if not attractions:
        day["route_summary"] = {
            "estimated_distance_km": 0,
            "optimization_reason": "当天无景点",
        }
        return

    reordered, estimated_distance = order_attractions_by_distance(attractions)
    day["attractions"] = reordered

    day["route_summary"] = {
        "estimated_distance_km": estimated_distance,
        # The previous MCP duration belongs to the old route and must not be reused.
        "estimated_duration_min": None,
        "optimization_reason": "用户局部修改后，基于最近邻策略重排单日路线",
    }


def recompute_budget(trip_plan: dict) -> dict:
    """汇总各项费用；无明确交通金额时按新路线距离估算。"""
    return estimate_budget(trip_plan)


async def _rewrite_day_description(day: dict, llm_provider: str) -> str:
    attractions = day.get("attractions", [])
    attractions_str = "、".join(
        attraction.get("name", "") for attraction in attractions if attraction.get("name")
    )
    fallback = f"当天安排：{attractions_str}。" if attractions_str else "当天暂无景点安排。"
    try:
        if not attractions_str:
            return fallback

        llm = get_llm(llm_provider, temperature=0.5)
        prompt = TRIP_MODIFY_REWRITE_PROMPT.format(
            day_index_human=(day.get("day_index", 0) or 0) + 1,
            date=day.get("date", ""),
            attractions_str=attractions_str,
        )
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        text = (response.content or "").strip()
        text = text.split("\n")[0].strip()
        return text[:120] if text else fallback
    except Exception as e:
        logger.warning(f"[Modify] 重写描述失败: {e}，使用当前景点生成基础描述")
        return fallback
