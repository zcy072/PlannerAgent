"""Prompts 模块 - 集中管理所有 LLM Prompt 模板。

将 Prompt 从业务逻辑中分离，便于：
1. 版本管理和对比
2. A/B 测试
3. Prompt 工程优化
4. 多语言支持

版本: 2.0.0
更新: 2026-06-30
"""

from .poi_prompts import (
    KEYWORD_GENERATION_PROMPT,
    POI_FILTER_PROMPT,
    POI_TOOL_CALLING_PROMPT,
    POI_TOOL_FILTER_PROMPT,
    WEATHER_FILTER_HINT,
)

from .hotel_prompts import (
    HOTEL_STRATEGY_PROMPT,
    HOTEL_FILTER_PROMPT,
)

from .planner_prompts import (
    PLANNER_MAIN_PROMPT,
)

from .chat_prompts import (
    CHAT_ROUTER_SYSTEM_PROMPT,
    CHAT_ROUTER_USER_PROMPT,
    CHAT_CLARIFY_PROMPT,
    TRIP_MODIFY_REWRITE_PROMPT,
)

__all__ = [
    # POI
    "KEYWORD_GENERATION_PROMPT",
    "POI_FILTER_PROMPT",
    "POI_TOOL_CALLING_PROMPT",
    "POI_TOOL_FILTER_PROMPT",
    "WEATHER_FILTER_HINT",
    # Hotel
    "HOTEL_STRATEGY_PROMPT",
    "HOTEL_FILTER_PROMPT",
    # Planner
    "PLANNER_MAIN_PROMPT",
    # Chat
    "CHAT_ROUTER_SYSTEM_PROMPT",
    "CHAT_ROUTER_USER_PROMPT",
    "CHAT_CLARIFY_PROMPT",
    "TRIP_MODIFY_REWRITE_PROMPT",
]
