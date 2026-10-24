"""行程规划 Agent — 汇总所有 Agent 数据，生成结构化行程"""

import json
from typing import List, Optional
from langchain_core.prompts import ChatPromptTemplate
from loguru import logger

from ..core.llm import get_llm
from ..models.schemas import POI, Weather, Hotel
from ..planning.weather import has_forecast
from ..planning.plan_postprocessor import (
    apply_route_skeleton,
    fix_coordinates,
    parse_planner_response,
)
from ..core.constants import (
    BUDGET_ESTIMATE_MARKER,
    PLANNER_MAX_HOTELS_DISPLAY,
    PLANNER_MAX_POIS_DISPLAY,
)
from ..prompts import PLANNER_MAIN_PROMPT


async def run_planner_agent(
    city: str,
    start_date: str,
    end_date: str,
    travel_days: int,
    transportation: str,
    accommodation: str,
    preferences: List[str],
    pois: List[POI],
    weather: List[Weather],
    hotels: List[Hotel],
    budget: Optional[List[int]],
    llm_provider: str,
    route_skeleton: List[dict],
    evaluator_suggestions: Optional[List[str]] = None,
    free_text_input: Optional[str] = None,
) -> dict:
    """
    行程规划 Agent — 汇总所有数据生成结构化行程

    Args:
        city: 目标城市
        start_date: 开始日期
        end_date: 结束日期
        travel_days: 旅行天数
        transportation: 交通方式
        accommodation: 住宿偏好
        preferences: 用户偏好
        pois: POI Agent 筛选后的景点列表
        weather: 天气工具获取的事实数据
        hotels: Hotel Agent 筛选后的酒店列表
        budget: 预算范围 [min, max]
        llm_provider: LLM 提供商
        route_skeleton: Route Optimizer 预生成的路线骨架
        evaluator_suggestions: 上一次评估失败后的改进建议
        free_text_input: 用户的自由文本额外要求

    Returns:
        行程规划字典
    """
    logger.info(f"[Planner Agent] 开始执行 | 汇总数据: {len(pois)} 个景点, {len(weather)} 天天气, {len(hotels)} 家酒店")

    # 结构化 JSON 输出需要稳定遵循骨架与字段约束，温度取低值
    llm = get_llm(llm_provider, temperature=0.3)

    # 准备上下文
    pois_info = _format_pois(pois, _skeleton_attraction_count(route_skeleton))
    weather_info = _format_weather(weather)
    hotels_info = _format_hotels(hotels)
    preferences_str = ", ".join(preferences) if preferences else "无特殊偏好"
    route_skeleton_info = _format_route_skeleton(route_skeleton)
    logger.info(f"[Planner Agent] 路线骨架生成完成，共 {len(route_skeleton)} 天")
    _log_route_skeleton(route_skeleton)

    budget_info = "无预算限制"
    if budget and len(budget) == 2:
        budget_info = f"{budget[0]} - {budget[1]} 元"
    extra_instructions = _format_evaluator_suggestions(evaluator_suggestions or [])

    # 调用 LLM 生成行程
    logger.info("[Planner Agent] 调用 LLM 生成行程...")
    prompt = ChatPromptTemplate.from_template(PLANNER_MAIN_PROMPT)
    chain = prompt | llm

    response = await chain.ainvoke({
        "city": city,
        "start_date": start_date,
        "end_date": end_date,
        "travel_days": travel_days,
        "transportation": transportation,
        "accommodation": accommodation,
        "preferences": preferences_str,
        "budget_info": budget_info,
        "free_text_input": (free_text_input or "").strip() or "无",
        "pois_info": pois_info,
        "weather_info": weather_info,
        "hotels_info": hotels_info,
        "route_skeleton_info": route_skeleton_info,
        "extra_instructions": extra_instructions,
    })

    # 解析响应
    trip_plan = parse_planner_response(
        response=response.content,
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

    logger.info(f"[Planner Agent] 行程生成完成，共 {len(trip_plan.get('days', []))} 天")

    # 坐标校验：确保景点使用真实坐标
    fix_coordinates(trip_plan, pois)
    apply_route_skeleton(trip_plan, route_skeleton)

    # Do not let the model invent source data when external collection failed.
    # A fabricated hotel/attraction is worse than an explicit empty field.
    if not route_skeleton:
        for day in trip_plan.get("days", []):
            day["attractions"] = []
            day["route_summary"] = {
                "estimated_distance_km": 0,
                "estimated_duration_min": None,
                "optimization_reason": "未获取到路线候选，景点与交通费用待核实",
            }
    if not hotels:
        for day in trip_plan.get("days", []):
            day["hotel"] = None
    else:
        hotel_by_name = {hotel.name.strip(): hotel for hotel in hotels if hotel.name.strip()}
        for day in trip_plan.get("days", []):
            hotel = day.get("hotel") or {}
            source = _match_recommended_hotel(str(hotel.get("name", "")).strip(), hotel_by_name, hotels)
            if source is None:
                day["hotel"] = None
            else:
                day["hotel"] = {
                    **hotel,
                    "name": source.name,
                    "estimated_cost": source.estimated_cost,
                    "price_range": source.price_range,
                    "rating": source.rating,
                    "address": source.address,
                    "location": source.location.model_dump() if source.location else None,
                }

    return trip_plan


def _match_recommended_hotel(name: str, by_name: dict, hotels: List[Hotel]) -> Optional[Hotel]:
    """LLM 常会略改酒店名(省略分店、括号等),精确匹配失败时按包含关系兜底,再退回首选推荐。"""
    if name in by_name:
        return by_name[name]
    if name:
        for candidate_name, candidate in by_name.items():
            if name in candidate_name or candidate_name in name:
                return candidate
    # 模型没给出可识别的酒店时,用第一家推荐酒店,避免整段住宿信息丢失
    return hotels[0]


def _skeleton_attraction_count(route_skeleton: List[dict]) -> int:
    return sum(len(day.get("attractions", [])) for day in route_skeleton)


def _format_pois(pois: List[POI], required_count: int = 0) -> str:
    """格式化 POI 信息；上限不低于路线骨架实际用到的景点数，避免长行程的景点被截掉。"""
    if not pois:
        return "暂无景点信息"

    limit = max(PLANNER_MAX_POIS_DISPLAY, required_count)
    lines = []
    for i, poi in enumerate(pois[:limit], 1):
        if not poi.location:
            continue
        lines.append(f"{i}. {poi.name}")
        lines.append(f"   地址: {poi.address}")
        lines.append(f"   坐标: longitude={poi.location.longitude}, latitude={poi.location.latitude}")
        if poi.rating:
            lines.append(f"   评分: {poi.rating}")
        if poi.category:
            lines.append(f"   类别: {poi.category}")
        if poi.ticket_price:
            lines.append(f"   门票参考(高德): {poi.ticket_price}元")
        elif poi.cost_reference:
            lines.append(f"   高德人均消费参考: {poi.cost_reference}元（不是门票价）")
        lines.append("")

    return "\n".join(lines)


def _format_weather(weather: List[Weather]) -> str:
    """格式化天气信息"""
    if not weather:
        return "暂无天气信息"

    lines = []
    for w in weather:
        if not has_forecast(w):
            lines.append(f"{w.date}: 暂无预报，不要推测天气")
            continue
        lines.append(f"{w.date}: {w.day_weather} {w.day_temp}°C / {w.night_weather} {w.night_temp}°C")

    return "\n".join(lines)


def _format_hotels(hotels: List[Hotel]) -> str:
    """格式化酒店信息"""
    if not hotels:
        return "暂无酒店信息"

    lines = []
    for i, h in enumerate(hotels[:PLANNER_MAX_HOTELS_DISPLAY], 1):
        lines.append(f"{i}. {h.name} - {h.address}")
        if h.rating:
            lines.append(f"   评分: {h.rating}")
        if BUDGET_ESTIMATE_MARKER in h.price_range:
            lines.append(f"   价格: 无真实报价（仅为按预算推算的占位价 ¥{h.estimated_cost}/晚，不是实际房价）")
        else:
            if h.price_range:
                lines.append(f"   价格: {h.price_range}")
            if h.estimated_cost:
                lines.append(f"   每晚参考价: ¥{h.estimated_cost}（高德参考，非实时房价）")
        if h.location:
            lines.append(f"   坐标: longitude={h.location.longitude}, latitude={h.location.latitude}")
        lines.append("")

    return "\n".join(lines)


def _format_route_skeleton(route_skeleton: List[dict]) -> str:
    """格式化路线骨架，约束 LLM 按算法结果生成行程"""
    if not route_skeleton:
        return "暂无路线骨架，请从可用景点中生成合理安排。"

    return json.dumps(route_skeleton, ensure_ascii=False, indent=2)


def _format_evaluator_suggestions(suggestions: List[str]) -> str:
    if not suggestions:
        return ""

    lines = ["", "## 上一次自动评估发现的问题，请本次生成时修正"]
    lines.extend(f"- {suggestion}" for suggestion in suggestions)
    return "\n".join(lines)


def _log_route_skeleton(route_skeleton: List[dict]):
    """输出每日路线摘要，便于在后端日志中确认路线优化结果"""
    for day in route_skeleton:
        names = " -> ".join(
            attraction.get("name", "")
            for attraction in day.get("attractions", [])
            if attraction.get("name")
        )
        logger.info(
            f"[Planner Agent] 路线优化 | 第{day.get('day_index', 0) + 1}天 | "
            f"{day.get('estimated_distance_km', 0)} km | {names}"
        )
