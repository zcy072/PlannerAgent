"""酒店推荐 Agent - LLM 搜索策略 + Amap 搜索 + LLM 筛选。"""

import asyncio
from typing import List, Optional, Tuple
from langchain_core.prompts import ChatPromptTemplate
from loguru import logger

from ..core.llm import get_llm
from ..models.schemas import Hotel
from ..services.amap_service import get_amap_service
from .utils.formatters import format_hotels_for_filter
from ..utils.llm_json import parse_keywords
from .utils.pool_manager import build_balanced_candidates, select_candidates
from ..core.constants import (
    BUDGET_ESTIMATE_MARKER,
    HOTEL_MAX_KEYWORDS,
    HOTEL_CANDIDATE_POOL_SIZE,
    HOTEL_PER_KEYWORD_LIMIT,
    HOTEL_FILTER_TARGET,
)
from ..prompts import HOTEL_STRATEGY_PROMPT, HOTEL_FILTER_PROMPT


def _apply_budget_estimate(hotels: List[Hotel], budget: Optional[List[int]], accommodation: str, travel_days: int) -> None:
    """Fill missing prices with a transparent budget-based nightly estimate."""
    if not budget or len(budget) != 2:
        return
    # Lodging commonly consumes roughly 30-40% of a trip budget. Use the
    # midpoint and spread it across the requested trip only at display time;
    # this is explicitly a planning estimate, never an Amap quote.
    nightly = round((budget[1] * 0.35) / max(1, travel_days) / 10) * 10
    if "豪华" in accommodation:
        nightly = round(nightly * 1.6 / 10) * 10
    elif "舒适" in accommodation:
        nightly = round(nightly * 1.2 / 10) * 10
    nightly = max(80, min(1200, nightly))
    for hotel in hotels:
        if hotel.estimated_cost:
            continue
        hotel.estimated_cost = nightly
        hotel.price_range = f"{BUDGET_ESTIMATE_MARKER}约 ¥{nightly}/晚（非实时房价）"


async def run_hotel_agent(
    city: str,
    accommodation: str,
    budget: Optional[List[int]],
    llm_provider: str,
    travel_days: int = 3,
    free_text_input: Optional[str] = None,
) -> Tuple[List[Hotel], List[Hotel]]:
    """
    酒店推荐 Agent

    流程：LLM 生成搜索策略 → 调用 Amap 搜索 → LLM 筛选推荐

    Args:
        city: 目标城市
        accommodation: 住宿偏好
        budget: 预算范围 [min, max]
        llm_provider: LLM 提供商
        travel_days: 旅行天数，用于推算每晚预算
        free_text_input: 用户的自由文本额外要求

    Returns:
        (筛选后的酒店列表, 候选池剩余酒店列表)
    """
    logger.info(f"[Hotel Agent] 开始执行 | 城市: {city}, 住宿偏好: {accommodation}")

    llm = get_llm(llm_provider, temperature=0.3)
    amap = get_amap_service()

    budget_info = "无预算限制"
    if budget and len(budget) == 2:
        nights = max(1, travel_days)
        low = round(budget[0] * 0.3 / nights / 10) * 10
        high = round(budget[1] * 0.4 / nights / 10) * 10
        budget_info = (
            f"总预算 {budget[0]} - {budget[1]} 元，住宿约占 30-40%，"
            f"折合每晚约 {low} - {high} 元"
        )
    free_text = (free_text_input or "").strip() or "无"

    # Step 1: LLM 生成搜索策略
    logger.info("[Hotel Agent] Step 1: LLM 生成搜索策略...")
    prompt = ChatPromptTemplate.from_template(HOTEL_STRATEGY_PROMPT)
    chain = prompt | llm

    response = await chain.ainvoke({
        "city": city,
        "accommodation": accommodation,
        "budget_info": budget_info,
        "free_text_input": free_text,
    })

    keywords = parse_keywords(
        response.content,
        fallback=[accommodation or "酒店"],
        max_items=HOTEL_MAX_KEYWORDS
    )
    logger.info(f"[Hotel Agent] 生成的搜索关键词: {keywords}")

    # Step 2: 调用 Amap 搜索
    logger.info("[Hotel Agent] Step 2: 并发调用高德地图搜索...")
    hotels_by_keyword = {}
    search_keywords = keywords
    search_tasks = [
        amap.search_hotels(city, keyword)
        for keyword in search_keywords
    ]
    search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    for keyword, result in zip(search_keywords, search_results):
        if isinstance(result, Exception):
            logger.warning(f"[Hotel Agent] 搜索 '{keyword}' 失败: {result}")
            continue

        hotels_by_keyword[keyword] = result
        logger.debug(f"[Hotel Agent] 关键词 '{keyword}' 找到 {len(result)} 家酒店")

    if not any(hotels_by_keyword.values()):
        try:
            fallback_keyword = accommodation or "酒店"
            hotels_by_keyword = {
                fallback_keyword: await amap.search_hotels(city, fallback_keyword)
            }
        except Exception as e:
            logger.warning(f"[Hotel Agent] fallback 搜索失败: {e}")

    filter_candidates = build_balanced_candidates(
        hotels_by_keyword,
        max_total=HOTEL_CANDIDATE_POOL_SIZE,
        per_keyword=HOTEL_PER_KEYWORD_LIMIT,
    )
    logger.info(f"[Hotel Agent] 均衡去重后共 {len(filter_candidates)} 家候选酒店")

    if not filter_candidates:
        return [], []

    # 先补价再交给 LLM 筛选，候选池酒店同样带价，替换时不会出现"价格待核实"
    _apply_budget_estimate(filter_candidates, budget, accommodation, travel_days)

    # Step 3: LLM 筛选推荐
    logger.info("[Hotel Agent] Step 3: LLM 筛选推荐酒店...")
    hotels_info = format_hotels_for_filter(filter_candidates)

    filter_prompt = ChatPromptTemplate.from_template(HOTEL_FILTER_PROMPT)
    filter_chain = filter_prompt | llm

    filter_response = await filter_chain.ainvoke({
        "city": city,
        "accommodation": accommodation,
        "budget_info": budget_info,
        "free_text_input": free_text,
        "hotels_info": hotels_info,
    })

    selected_hotels, remaining_pool = select_candidates(
        filter_candidates, filter_response.content, HOTEL_FILTER_TARGET,
    )

    logger.info(f"[Hotel Agent] 最终推荐 {len(selected_hotels)} 家酒店")
    return selected_hotels, remaining_pool
