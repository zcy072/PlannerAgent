"""LangGraph checkpoint lifecycle helpers.

Checkpoints exist so a failed run can resume from the node that failed. A run that
finishes is deleted at once (its itinerary lives in the result JSON), and stale
unfinished runs are pruned at startup, so the database does not grow without bound.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from loguru import logger

from ...models.schemas import Hotel, LLMProvider, Location, POI, TripRequest, Weather


CHECKPOINT_DB_PATH = Path(__file__).resolve().parents[2] / "saved_results" / "checkpoints.sqlite"

# Domain models stored in graph state; listing them lets checkpoints deserialize
# without LangGraph's "unregistered type" warning and keeps strict mode usable.
_STATE_MODELS = (TripRequest, POI, Location, Weather, Hotel, LLMProvider)

_checkpointer: Optional[AsyncSqliteSaver] = None
_checkpointer_conn: Optional[aiosqlite.Connection] = None
_checkpointer_lock = asyncio.Lock()


def _serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        allowed_msgpack_modules=[(model.__module__, model.__name__) for model in _STATE_MODELS],
    )


async def get_checkpointer() -> AsyncSqliteSaver:
    """Return the shared AsyncSqliteSaver used by the trip graph."""
    global _checkpointer, _checkpointer_conn
    if _checkpointer is not None:
        return _checkpointer
    async with _checkpointer_lock:
        if _checkpointer is None:
            CHECKPOINT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = await aiosqlite.connect(str(CHECKPOINT_DB_PATH))
            saver = AsyncSqliteSaver(conn, serde=_serializer())
            await saver.setup()
            _checkpointer_conn = conn
            _checkpointer = saver
            logger.info(f"[TripGraph] AsyncSqliteSaver 已就绪 | {CHECKPOINT_DB_PATH}")
    return _checkpointer


async def discard_checkpoint(checkpointer, session_id: str) -> None:
    """Drop one session's checkpoints; a cleanup failure must never fail the request."""
    delete_thread = getattr(checkpointer, "adelete_thread", None)
    if delete_thread is None:
        return
    try:
        await delete_thread(session_id)
    except Exception as exc:
        logger.warning("[TripGraph] 清理 checkpoint 失败 | error_type={}", type(exc).__name__)


async def prune_stale_checkpoints(retention_days: int) -> int:
    """Delete sessions whose newest checkpoint is older than the retention window."""
    if retention_days <= 0:
        return 0
    saver = await get_checkpointer()
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    newest: dict[str, datetime] = {}
    async for item in saver.alist(None):
        thread_id = item.config["configurable"]["thread_id"]
        try:
            created = datetime.fromisoformat(item.checkpoint["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if thread_id not in newest or created > newest[thread_id]:
            newest[thread_id] = created

    stale = [thread_id for thread_id, created in newest.items() if created < cutoff]
    for thread_id in stale:
        await discard_checkpoint(saver, thread_id)
    if stale:
        logger.info("[TripGraph] 已清理过期 checkpoint 会话 {} 个", len(stale))
    return len(stale)


async def close_checkpointer() -> None:
    """Close checkpoint resources opened for graph persistence."""
    global _checkpointer, _checkpointer_conn
    if _checkpointer_conn is not None:
        await _checkpointer_conn.close()
        logger.info("[TripGraph] AsyncSqliteSaver 连接已关闭")
    _checkpointer = None
    _checkpointer_conn = None
