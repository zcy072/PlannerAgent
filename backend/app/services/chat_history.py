"""Bounded in-memory history for trip chat sessions."""

from __future__ import annotations

from collections import OrderedDict, deque
from typing import Deque, Dict

from ..core.constants import (
    CHAT_HISTORY_LIMIT,
    CHAT_MESSAGE_MAX_CHARS,
    CHAT_SESSION_HISTORY_LIMIT,
)


_chat_history: OrderedDict[str, Deque[Dict[str, str]]] = OrderedDict()


def history_for_session(session_id: str) -> Deque[Dict[str, str]]:
    """Return one history and evict the least recently used session."""
    history = _chat_history.pop(session_id, None)
    if history is None:
        if len(_chat_history) >= CHAT_SESSION_HISTORY_LIMIT:
            _chat_history.popitem(last=False)
        history = deque(maxlen=CHAT_HISTORY_LIMIT)
    _chat_history[session_id] = history
    return history


def record_message(session_id: str, role: str, content: str) -> None:
    # LLM 回复长度不受控，截断后再保存，避免历史无限撑大后续提示词。
    history_for_session(session_id).append({"role": role, "content": content[:CHAT_MESSAGE_MAX_CHARS]})
