"""Parse natural-language trip edits into structured actions."""

from __future__ import annotations

import time
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from ..core.llm import get_llm
from ..prompts import CHAT_ROUTER_SYSTEM_PROMPT, CHAT_ROUTER_USER_PROMPT
from ..utils.llm_json import parse_json_object


def _build_trip_summary(trip_plan: Dict[str, Any]) -> str:
    """Build a compact trip summary for the router prompt."""
    days = trip_plan.get("days", []) or []
    lines: List[str] = [f"目的地: {trip_plan.get('city', '')}"]
    for day in days:
        day_index = day.get("day_index", 0)
        date = day.get("date", "")
        attractions = day.get("attractions", []) or []
        attr_parts = [f"[{i}] {a.get('name', '')}" for i, a in enumerate(attractions)]
        hotel = (day.get("hotel") or {}).get("name") or "无"
        lines.append(
            f"第{day_index + 1}天 (day_index={day_index}, {date}) 景点: "
            + ("、".join(attr_parts) if attr_parts else "无")
            + f"；酒店: {hotel}"
        )
    return "\n".join(lines)


async def run_chat_router(
    message: str,
    trip_plan: Dict[str, Any],
    llm_provider: str | None = None,
    history: List[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Parse one user message into a structured trip action."""
    started_at = time.perf_counter()
    summary = _build_trip_summary(trip_plan)

    history_block = ""
    if history:
        recent = history[-6:]
        lines = []
        for turn in recent:
            role = "用户" if turn.get("role") == "user" else "助手"
            content = (turn.get("content") or "").strip()
            if content:
                lines.append(f"{role}: {content}")
        if lines:
            history_block = "最近对话:\n" + "\n".join(lines) + "\n\n"

    user_prompt = CHAT_ROUTER_USER_PROMPT.format(
        history_block=history_block,
        summary=summary,
        message=message,
    )

    llm = get_llm(llm_provider, temperature=0.1)
    response = await llm.ainvoke([
        SystemMessage(content=CHAT_ROUTER_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ])
    raw = (response.content or "").strip()

    parsed = parse_json_object(raw)
    duration_ms = int((time.perf_counter() - started_at) * 1000)

    if not parsed or "action" not in parsed:
        logger.warning(f"[ChatRouter] 解析失败 | raw={raw[:200]} | 耗时={duration_ms}ms")
        return {
            "action": "answer",
            "answer_text": "抱歉，我没听明白这条指令。你可以说：第二天黄鹤楼换成东湖。",
            "_raw": raw,
        }

    action = parsed.get("action")
    if action not in {"delete", "replace", "answer", "clarify"}:
        logger.warning(f"[ChatRouter] 未知 action={action} | parsed={parsed}")
        return {
            "action": "answer",
            "answer_text": "抱歉，我暂不支持这个操作。",
            "_raw": raw,
        }

    logger.info(
        f"[ChatRouter] 解析完成 | action={action} | "
        f"day_index={parsed.get('day_index')} | item_type={parsed.get('item_type')} | "
        f"耗时={duration_ms}ms"
    )
    return parsed
