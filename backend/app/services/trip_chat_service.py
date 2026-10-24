"""Trip Chat Service: 多轮对话式行程修改的编排层。

把 ChatRouter 解析出的指令分流到既有 trip_modify_service。
对话历史保存在内存里，不持久化（前端 Pinia 已经维护展示用历史）。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from ..core.constants import (
    CHAT_SUGGESTION_LIMIT,
)
from ..core.llm import get_llm, resolve_provider
from ..models.schemas import ItemModifyRequest
from ..services.amap_service import get_amap_service
from ..services.trip_modify_service import (
    TripModificationError,
    modify_persisted_trip,
)
from ..services.trip_result_service import trip_result_repository
from ..prompts import CHAT_CLARIFY_PROMPT
from ..agents.chat_router_agent import run_chat_router
from .chat_history import (
    history_for_session as _history_for_session,
    record_message as _record,
)

_VAGUE_QUERIES = {"别的", "其他", "其它", "换一个", "换个", "随便", "都行", "随意"}


class ChatError(Exception):
    """业务错误：返回给用户的可读信息，不是 500。"""


def _reply(
    session_id: str,
    reply: str,
    *,
    action: str = "answer",
    trip_plan: Optional[Dict[str, Any]] = None,
    modified: bool = False,
    persistence_saved: Optional[bool] = None,
    persistence_warning: Optional[str] = None,
) -> Dict[str, Any]:
    """统一记录助手回复并组装返回结构，避免每个分支手抄一遍。"""
    _record(session_id, "assistant", reply)
    return {
        "reply": reply,
        "trip_plan": trip_plan,
        "modified": modified,
        "action": action,
        "persistence_saved": persistence_saved,
        "persistence_warning": persistence_warning,
    }


def _get_day_item(
    trip_plan: Dict[str, Any],
    day_index: Optional[int],
    item_type: str,
    item_index: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """按 day_index/item_index 取当天景点或酒店，越界一律返回 None。"""
    days = trip_plan.get("days", []) or []
    if day_index is None or not (0 <= day_index < len(days)):
        return None
    day = days[day_index]
    if item_type == "attraction":
        attractions = day.get("attractions", []) or []
        if not isinstance(item_index, int) or not (0 <= item_index < len(attractions)):
            return None
        return attractions[item_index]
    if item_type == "hotel":
        return day.get("hotel") or None
    return None


def _is_vague(query: str) -> bool:
    stripped = (query or "").strip()
    return not stripped or stripped in _VAGUE_QUERIES


def _collect_used_names(trip_plan: Dict[str, Any], item_type: str) -> set:
    """收集当前行程里已用到的景点 / 酒店名，建议候选时要剔除。"""
    used: set = set()
    days = trip_plan.get("days", []) or []
    if item_type == "attraction":
        for day in days:
            for attr in day.get("attractions", []) or []:
                name = (attr.get("name") or "").strip()
                if name:
                    used.add(name)
    elif item_type == "hotel":
        for day in days:
            hotel = day.get("hotel") or {}
            name = (hotel.get("name") or "").strip()
            if name:
                used.add(name)
    return used


def _suggest_from_pool(
    trip_plan: Dict[str, Any],
    item_type: str,
    limit: int = CHAT_SUGGESTION_LIMIT,
) -> List[Dict[str, Any]]:
    """从候选池挑出未被当前行程使用的几个备选。"""
    pool_key = "pois_pool" if item_type == "attraction" else "hotels_pool"
    pool = trip_plan.get(pool_key) or []
    used = _collect_used_names(trip_plan, item_type)
    suggestions: List[Dict[str, Any]] = []
    for item in pool:
        name = (item.get("name") or "").strip()
        if not name or name in used:
            continue
        suggestions.append(item)
        if len(suggestions) >= limit:
            break
    return suggestions




async def _phrase_clarify_with_suggestions(
    item_type: str,
    target_name: str,
    suggestions: List[Dict[str, Any]],
    llm_provider: str,
) -> str:
    """让 LLM 把"反问 + 候选"包装成一句自然的话；失败时回落到模板。"""
    noun = "景点" if item_type == "attraction" else "酒店"
    target = (target_name or f"这个{noun}").strip() or f"这个{noun}"
    names = [s.get("name", "").strip() for s in suggestions if s.get("name")]
    if not names:
        return f"想把「{target}」换成什么{noun}呢？告诉我具体名字就行。"

    fallback = (
        f"想把「{target}」换成什么呢？我从候选池挑了几个："
        + "、".join(f"「{n}」" for n in names)
        + "，告诉我具体名字。"
    )

    try:
        llm = get_llm(llm_provider, temperature=0.4)
        prompt = CHAT_CLARIFY_PROMPT.format(
            noun=noun, target=target, candidates="、".join(names)
        )
        response = await llm.ainvoke([
            SystemMessage(content="你是简洁友好的行程助手，回复要口语化、不要 Markdown。"),
            HumanMessage(content=prompt),
        ])
        text = (response.content or "").strip().split("\n")[0].strip()
        if text:
            return text[:120]
    except Exception as e:
        logger.warning(f"[ChatService] LLM 反问润色失败: {e}，使用模板")
    return fallback


def _find_in_pool(
    query: str,
    pool: List[Dict[str, Any]],
    excluded_names: Optional[set] = None,
) -> Optional[Dict[str, Any]]:
    """在候选池里做关键词模糊匹配。"""
    if not query or not pool:
        return None
    q = query.strip()
    excluded_names = excluded_names or set()
    for item in pool:
        name = (item.get("name") or "").strip()
        if name not in excluded_names and q == name:
            return item
    for item in pool:
        name = (item.get("name") or "").strip()
        if name not in excluded_names and q in name:
            return item
    return None


async def _resolve_replacement(
    intent: Dict[str, Any],
    trip_plan: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """先查池，落空再调高德。返回 normalized POI/Hotel dict 或 None。"""
    query = (intent.get("replacement_query") or "").strip()
    if not query:
        return None

    item_type = intent.get("item_type")
    used_names = _collect_used_names(trip_plan, item_type)
    if item_type == "attraction":
        pool = trip_plan.get("pois_pool") or []
        hit = _find_in_pool(query, pool, used_names)
        if hit:
            logger.info(f"[ChatService] replacement 命中 pois_pool | {hit.get('name')}")
            return hit
        chosen = await _search_amap_replacement(query, trip_plan, used_names)
        if chosen:
            logger.info(f"[ChatService] replacement 通过 amap 拿到 | {chosen.get('name')}")
        return chosen

    if item_type == "hotel":
        pool = trip_plan.get("hotels_pool") or []
        hit = _find_in_pool(query, pool, used_names)
        if hit:
            return hit
        # 用 search_poi 直接按关键词找酒店，避免 search_hotels 要走 type 分类
        poi = await _search_amap_replacement(query, trip_plan, used_names)
        if poi:
            return {
                "name": poi.get("name", ""),
                "address": poi.get("address", ""),
                "location": poi.get("location"),
                "price_range": "",
                "rating": "",
                "type": "",
                "estimated_cost": 0,
            }
        return None

    return None


async def _search_amap_replacement(
    query: str,
    trip_plan: Dict[str, Any],
    used_names: set,
) -> Optional[Dict[str, Any]]:
    """候选池落空后的高德回退：取第一个尚未出现在行程里的结果。"""
    pois = await get_amap_service().search_poi(query, trip_plan.get("city", ""), page_size=3)
    chosen = next((poi for poi in pois if poi.name.strip() not in used_names), None)
    return chosen.model_dump() if chosen else None


def _resolve_attraction_index(
    trip_plan: Dict[str, Any],
    day_index: int,
    item_index: Optional[int],
    target_name: str,
) -> Optional[int]:
    """优先按 target_name 在当天 attractions 里查实际下标，命中则覆盖 LLM 给的 item_index。"""
    days = trip_plan.get("days", []) or []
    if day_index is None or day_index < 0 or day_index >= len(days):
        return item_index
    attractions = days[day_index].get("attractions", []) or []
    if not attractions:
        return item_index

    name = (target_name or "").strip()
    if name:
        for idx, attr in enumerate(attractions):
            if (attr.get("name") or "").strip() == name:
                if item_index != idx:
                    logger.info(
                        f"[ChatService] item_index 校正 | LLM={item_index} → 名字命中={idx} ({name})"
                    )
                return idx
        for idx, attr in enumerate(attractions):
            if name in (attr.get("name") or ""):
                logger.info(
                    f"[ChatService] item_index 模糊校正 | LLM={item_index} → 模糊命中={idx} ({attr.get('name')})"
                )
                return idx

    return item_index


async def handle_chat(
    session_id: str,
    message: str,
    llm_provider: Optional[str] = None,
) -> Dict[str, Any]:
    """处理一条用户聊天消息，返回 {reply, trip_plan, modified, action}。"""
    llm_provider = resolve_provider(llm_provider)
    trip_plan = await trip_result_repository.load(session_id)
    if trip_plan is None:
        raise ChatError(f"未找到会话 {session_id} 的行程，请先生成行程")

    # 先取历史快照；路由成功后才记录本轮用户消息，失败不会留下没有回复的轮次。
    history_for_router = list(_history_for_session(session_id))
    intent = await run_chat_router(
        message,
        trip_plan,
        llm_provider,
        history=history_for_router,
    )
    _record(session_id, "user", message)
    action = intent.get("action")

    if action in ("answer", "clarify"):
        reply = (intent.get("answer_text") or "").strip() or "请问还有什么需要调整？"
        return _reply(session_id, reply, action=action)

    # delete / replace 路径：构造 ItemModifyRequest 复用既有服务
    day_index = intent.get("day_index")
    item_type = intent.get("item_type")
    item_index = intent.get("item_index")
    target_name = (intent.get("target_name") or "").strip()

    if day_index is None or item_type not in ("attraction", "hotel"):
        return _reply(
            session_id,
            "请告诉我具体哪一天、哪个景点或酒店。",
            action="clarify",
        )

    if item_type == "attraction":
        item_index = _resolve_attraction_index(trip_plan, day_index, item_index, target_name)
    else:
        # hotel 不允许 item_index，schema 明确禁止
        item_index = None

    expected_item = deepcopy(_get_day_item(trip_plan, day_index, item_type, item_index))
    if expected_item is None:
        return _reply(session_id, "未找到要修改的项目，请确认具体景点或酒店。", action="clarify")

    # delete 前抓一下被操作景点的名字，方便回复里告诉用户
    deleted_name = ""
    if action == "delete" and item_type == "attraction":
        item = _get_day_item(trip_plan, day_index, "attraction", item_index)
        deleted_name = (item or {}).get("name", "").strip()

    replacement: Optional[Dict[str, Any]] = None
    if action == "replace":
        query = (intent.get("replacement_query") or "").strip()
        if _is_vague(query):
            suggestions = _suggest_from_pool(trip_plan, item_type)
            # 用校正后的 item_index 反查 target_name，确保锚点准确
            anchor_item = _get_day_item(trip_plan, day_index, item_type, item_index)
            anchor_name = (anchor_item or {}).get("name") or target_name
            reply = await _phrase_clarify_with_suggestions(
                item_type, anchor_name, suggestions, llm_provider
            )
            return _reply(session_id, reply, action="clarify")

        replacement = await _resolve_replacement(intent, trip_plan)
        if replacement is None:
            reply = (
                f"没找到「{intent.get('replacement_query', '')}」，"
                "可以换个名字或在右侧候选池里手动选。"
            )
            return _reply(session_id, reply, action="answer")

    try:
        req = ItemModifyRequest(
            session_id=session_id,
            day_index=day_index,
            item_type=item_type,
            item_index=item_index,
            action=action,
            replacement=replacement,
            llm_provider=llm_provider,
        )
    except Exception as e:
        logger.warning(f"[ChatService] ItemModifyRequest 校验失败: {e}")
        return _reply(
            session_id,
            "指令不完整，请补充第几天 / 哪个景点 / 要换成什么。",
            action="clarify",
        )

    try:
        validated_plan, persistence_saved, persistence_warning = (
            await modify_persisted_trip(req, expected_item=expected_item)
        )
    except TripModificationError as exc:
        return _reply(session_id, str(exc), action="answer")
    new_trip_plan = validated_plan.model_dump()

    if action == "delete":
        if deleted_name:
            reply = f"好的，已从第 {day_index + 1} 天行程里删除「{deleted_name}」。"
        else:
            reply = f"好的，已删除第 {day_index + 1} 天的景点。"
    else:
        name = (replacement or {}).get("name", "新景点")
        anchor = target_name or "原项目"
        reply = f"好的，已把第 {day_index + 1} 天的「{anchor}」换成「{name}」。"

    return _reply(
        session_id,
        reply,
        action=action,
        trip_plan=new_trip_plan,
        modified=True,
        persistence_saved=persistence_saved,
        persistence_warning=persistence_warning,
    )
