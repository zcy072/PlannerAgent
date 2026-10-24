"""POI 搜索 Agent — LLM 决策 + 工具调用 + LLM 筛选"""

import asyncio
from typing import Awaitable, Callable, List, Optional, Tuple
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate
from loguru import logger

from ..core.llm import get_llm
from ..services.amap_service import get_amap_service
from ..models.schemas import Location, POI, Weather
from ..planning.location_utils import extract_coordinates
from ..planning.weather import has_forecast
from .tools.amap_tools import search_poi_tool
from ..utils.llm_json import parse_keywords
from .utils.formatters import format_pois_for_filter
from .utils.pool_manager import build_balanced_candidates, select_candidates
from ..core.constants import (
    POI_MAX_KEYWORDS,
    POI_CANDIDATE_POOL_SIZE,
    POI_PER_KEYWORD_LIMIT,
    POI_FILTER_TARGET,
)
from ..prompts import (
    KEYWORD_GENERATION_PROMPT,
    POI_FILTER_PROMPT,
    POI_TOOL_CALLING_PROMPT,
    POI_TOOL_FILTER_PROMPT,
    WEATHER_FILTER_HINT,
)


async def run_poi_agent(
    city: str,
    preferences: List[str],
    free_text_input: Optional[str],
    llm_provider: str,
    weather: Optional[List[Weather]] = None,
    travel_days: int = 3,
) -> Tuple[List[POI], List[POI]]:
    """
    POI 搜索 Agent

    流程：LLM 生成关键词 → 调用 Amap 搜索 → LLM 筛选排序

    Args:
        city: 目标城市
        preferences: 用户偏好列表
        free_text_input: 用户额外输入
        llm_provider: LLM 提供商
        weather: 天气工具获取的预报，用于影响关键词生成和候选筛选
        travel_days: 旅行天数，用于按天数确定筛选目标景点数

    Returns:
        (筛选后的 POI 列表, 候选池剩余 POI 列表)
    """
    logger.info(f"[POI Agent] 开始执行 | 城市: {city}, 偏好: {preferences}")

    llm = get_llm(llm_provider, temperature=0.3)
    preferences_str = ", ".join(preferences) if preferences else "景点"
    free_text = free_text_input or "无"
    weather_context = _format_weather_context(weather or [])
    target = _filter_target(travel_days)

    try:
        result = await _run_poi_agent_with_tool_calling(
            city=city,
            preferences_str=preferences_str,
            free_text=free_text,
            weather_context=weather_context,
            llm=llm,
            target=target,
        )
        if result is not None:
            logger.info(f"[POI Agent] tool calling 路径完成，选出 {len(result[0])} 个景点，候选池剩余 {len(result[1])} 个")
            return result
    except Exception as e:
        logger.warning(f"[POI Agent] tool calling 失败，降级到原有流程: {e}")

    logger.info("[POI Agent] 使用 fallback 三步流程")
    return await _run_poi_agent_fallback(
        city, preferences_str, free_text, weather_context, llm, preferences, target=target,
    )


def _filter_target(travel_days: int) -> int:
    """按天数确定 LLM 应选出的景点数：每天约 3 个，不低于默认值，不超过候选池容量的 4/5。"""
    return min(max(POI_FILTER_TARGET, travel_days * 3 + 1), POI_CANDIDATE_POOL_SIZE * 4 // 5)


def _target_range(target: int) -> str:
    return f"{max(1, target - 3)}-{target}"


async def _run_poi_agent_with_tool_calling(
    city: str,
    preferences_str: str,
    free_text: str,
    weather_context: str,
    llm,
    target: int = POI_FILTER_TARGET,
) -> Optional[Tuple[List[POI], List[POI]]]:
    """Try LLM tool calling before falling back to the original keyword flow."""
    if not hasattr(llm, "bind_tools"):
        logger.warning("[POI Agent] 当前 LLM 不支持 bind_tools，触发 fallback")
        return None

    llm_with_tools = llm.bind_tools([search_poi_tool])
    messages = [HumanMessage(content=POI_TOOL_CALLING_PROMPT.format(
        city=city,
        preferences=preferences_str,
        free_text=free_text,
        weather_context=weather_context,
    ))]

    response = await llm_with_tools.ainvoke(messages)
    tool_calls = getattr(response, "tool_calls", None)
    if not tool_calls:
        logger.warning("[POI Agent] LLM 未返回 tool_calls，触发 fallback")
        return None

    messages.append(response)
    pois_by_keyword = []

    for tool_call in tool_calls:
        args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
        keywords = args.get("keywords", "")
        call_city = city  # 城市以用户请求为准，不允许 LLM 工具调用改写
        tool_call_id = tool_call.get("id", "") if isinstance(tool_call, dict) else ""

        if not keywords:
            logger.warning("[POI Agent] tool call 缺少 keywords，跳过")
            if tool_call_id:
                messages.append(ToolMessage(
                    content="搜索失败：缺少 keywords 参数",
                    tool_call_id=tool_call_id,
                ))
            continue

        try:
            tool_result = await search_poi_tool.ainvoke({
                "keywords": keywords,
                "city": call_city,
            })
            pois = [_poi_from_dict(item) for item in tool_result.get("pois", [])]
            pois_by_keyword.append((keywords, pois))
            logger.debug(f"[POI Agent] tool call '{keywords}' 返回 {len(pois)} 个结果")

            if tool_call_id:
                messages.append(ToolMessage(
                    content=f"搜索'{keywords}'返回 {len(pois)} 个景点",
                    tool_call_id=tool_call_id,
                ))
        except Exception as e:
            logger.warning(f"[POI Agent] tool call '{keywords}' 执行失败: {e}")
            if tool_call_id:
                messages.append(ToolMessage(
                    content=f"搜索失败: {e}",
                    tool_call_id=tool_call_id,
                ))

    if not pois_by_keyword:
        return None

    async def invoke_filter(pois_info: str, count: int) -> str:
        messages.append(HumanMessage(content=POI_TOOL_FILTER_PROMPT.format(
            count=count,
            pois_info=pois_info,
            preferences=preferences_str,
            free_text=free_text,
            weather_context=weather_context,
            weather_hint=WEATHER_FILTER_HINT,
            target_range=_target_range(target),
        )))
        filter_response = await llm.ainvoke(messages)
        return filter_response.content

    return await _filter_and_select(pois_by_keyword, invoke_filter, target)


async def _run_poi_agent_fallback(
    city: str,
    preferences_str: str,
    free_text: str,
    weather_context: str,
    llm,
    fallback_preferences: Optional[List[str]] = None,
    target: int = POI_FILTER_TARGET,
) -> Tuple[List[POI], List[POI]]:
    """Original three-step flow: keyword generation, Amap search, LLM filtering."""
    amap = get_amap_service()

    # Step 1: LLM 生成搜索关键词
    logger.info("[POI Agent] Step 1: LLM 生成搜索关键词...")

    keyword_chain = ChatPromptTemplate.from_template(KEYWORD_GENERATION_PROMPT) | llm
    response = await keyword_chain.ainvoke({
        "city": city,
        "preferences": preferences_str,
        "free_text_input": free_text,
        "weather_context": weather_context,
    })

    keywords = parse_keywords(
        response.content,
        fallback=fallback_preferences or [preferences_str],
        max_items=POI_MAX_KEYWORDS
    )
    logger.info(f"[POI Agent] 生成的搜索关键词: {keywords}")

    # Step 2: 调用 Amap 搜索
    logger.info("[POI Agent] Step 2: 并发调用高德地图搜索...")
    search_keywords = keywords
    search_results = await asyncio.gather(
        *[amap.search_poi(keyword, city, citylimit=True) for keyword in search_keywords],
        return_exceptions=True,
    )

    pois_by_keyword = []
    for keyword, result in zip(search_keywords, search_results):
        if isinstance(result, Exception):
            logger.warning(f"[POI Agent] 搜索 '{keyword}' 失败: {result}")
            continue
        pois_by_keyword.append((keyword, result))
        logger.debug(f"[POI Agent] 关键词 '{keyword}' 找到 {len(result)} 个结果")

    if not pois_by_keyword:
        return [], []

    # Step 3: LLM 筛选排序
    logger.info("[POI Agent] Step 3: LLM 筛选和排序景点...")

    async def invoke_filter(pois_info: str, count: int) -> str:
        filter_chain = ChatPromptTemplate.from_template(POI_FILTER_PROMPT) | llm
        filter_response = await filter_chain.ainvoke({
            "city": city,
            "preferences": preferences_str,
            "free_text_input": free_text,
            "weather_context": weather_context,
            "pois_info": pois_info,
            "weather_hint": WEATHER_FILTER_HINT,
            "target_range": _target_range(target),
        })
        return filter_response.content

    result = await _filter_and_select(pois_by_keyword, invoke_filter, target)
    if result is None:
        return [], []

    selected_pois, remaining_pool = result
    logger.info(f"[POI Agent] 最终选出 {len(selected_pois)} 个景点")
    return selected_pois, remaining_pool


async def _filter_and_select(
    pois_by_keyword: List[Tuple[str, List[POI]]],
    invoke_filter: Callable[[str, int], Awaitable[str]],
    target: int = POI_FILTER_TARGET,
) -> Optional[Tuple[List[POI], List[POI]]]:
    """Build balanced pool, ask LLM to pick indices, return (selected, remaining)."""
    # 转换为字典格式供 pool_manager 使用
    items_dict = {keyword: pois for keyword, pois in pois_by_keyword}
    filter_candidates = build_balanced_candidates(
        items_dict,
        max_total=POI_CANDIDATE_POOL_SIZE,
        per_keyword=POI_PER_KEYWORD_LIMIT
    )
    if not filter_candidates:
        return None

    pois_info = format_pois_for_filter(filter_candidates)
    response = await invoke_filter(pois_info, len(filter_candidates))

    return select_candidates(filter_candidates, response, target)


def _poi_from_dict(data: dict) -> POI:
    location = None
    coordinates = extract_coordinates(data.get("location"))
    if coordinates is not None:
        location = Location(longitude=coordinates[0], latitude=coordinates[1])
    return POI(
        id=data.get("id", ""),
        name=data.get("name", ""),
        address=data.get("address", ""),
        location=location,
        category=data.get("category"),
        rating=data.get("rating"),
        tel=data.get("tel"),
        ticket_price=data.get("ticket_price", 0) or 0,
        cost_reference=data.get("cost_reference"),
        visit_duration=data.get("visit_duration", 120) or 120,
        description=data.get("description"),
        image_url=data.get("image_url"),
    )


def _format_weather_context(weather: List[Weather]) -> str:
    """Summarize weather so POI selection can adapt indoor/outdoor balance."""
    weather = [item for item in weather if has_forecast(item)]
    if not weather:
        return "暂无天气数据，按常规旅行场景选择室内外均衡景点"

    lines = []
    risky_keywords = ("雨", "雪", "风", "霾", "沙", "雷", "雾", "高温", "低温")
    has_risky_weather = False
    for item in weather[:5]:
        day_weather = item.day_weather or ""
        night_weather = item.night_weather or ""
        if any(keyword in f"{day_weather}{night_weather}" for keyword in risky_keywords):
            has_risky_weather = True
        lines.append(
            f"{item.date}: 白天{day_weather or '未知'} {item.day_temp}°C，"
            f"夜间{night_weather or '未知'} {item.night_temp}°C"
        )

    suggestion = (
        "检测到可能不利户外活动的天气，筛选时应提高室内、短时、低体力景点比例"
        if has_risky_weather
        else "天气整体可按室内外均衡安排，保留适量户外景点"
    )
    return "\n".join(lines + [suggestion])
