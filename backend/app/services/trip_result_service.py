"""Repository for persisted trip results."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import glob
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from typing import AsyncIterator, Optional
from weakref import WeakValueDictionary

from loguru import logger

from ..models.schemas import TripRequest


RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "saved_results")
os.makedirs(RESULTS_DIR, exist_ok=True)
MODIFICATION_PERSISTENCE_WARNING = "行程已修改，但保存失败；当前可以查看，刷新后修改会丢失。"


def _session_filename_token(session_id: str) -> str:
    """Return a collision-resistant, path-safe token for one full session id."""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _legacy_session_filename_token(session_id: str) -> str:
    """Return the token used by result files created before the repository migration."""
    token = re.sub(r"[^A-Za-z0-9_-]", "_", session_id[:8])
    return token or "session"


def _safe_result_filepath(city: str, start_date: str, session_id: str) -> str:
    """Build a result path and enforce that it remains inside RESULTS_DIR."""
    city_token = city.replace(" ", "_")
    filename = f"{city_token}_{start_date}_{_session_filename_token(session_id)}.json"
    results_root = os.path.realpath(RESULTS_DIR)
    filepath = os.path.realpath(os.path.join(results_root, filename))
    if os.path.commonpath((results_root, filepath)) != results_root:
        raise ValueError("结果文件路径超出保存目录")
    return filepath


def _candidate_result_files(session_id: str) -> list[str]:
    tokens = (_session_filename_token(session_id), _legacy_session_filename_token(session_id))
    matches: set[str] = set()
    for token in tokens:
        matches.update(glob.glob(os.path.join(RESULTS_DIR, f"*_{token}.json")))
    return sorted(matches, key=os.path.getmtime, reverse=True)


def _find_filepath_by_session(session_id: str) -> Optional[str]:
    """Find a new or legacy result file and verify its full session id."""
    if not session_id:
        return None

    for filepath in _candidate_result_files(session_id):
        try:
            with open(filepath, "r", encoding="utf-8") as file:
                data = json.load(file)
            if data.get("session_id") == session_id:
                return filepath
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning(f"[Trip Plan] 跳过无法读取的结果文件 {filepath}: {exc}")
    return None


def _atomic_write_json(filepath: str, data: dict) -> None:
    """Write JSON durably, then atomically replace the destination file."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=os.path.dirname(filepath),
            prefix=".trip-result-",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name
            json.dump(data, temp_file, ensure_ascii=False, indent=2)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, filepath)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


class TripResultRepository:
    """File-backed result repository with async adapters and per-session locks."""

    def __init__(self) -> None:
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._locks_guard = asyncio.Lock()

    async def _lock_for(self, session_id: str) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[session_id] = lock
            return lock

    @asynccontextmanager
    async def transaction(self, session_id: str) -> AsyncIterator[None]:
        """Serialize a complete read-modify-write operation for one session."""
        lock = await self._lock_for(session_id)
        async with lock:
            yield

    async def load(self, session_id: str) -> Optional[dict]:
        return await asyncio.to_thread(load_trip_result, session_id)

    async def save(
        self,
        session_id: str,
        trip_plan: dict,
        request: Optional[TripRequest] = None,
    ) -> Optional[str]:
        return await asyncio.to_thread(save_trip_result, session_id, trip_plan, request)


def save_trip_result(
    session_id: str,
    trip_plan: dict,
    request: Optional[TripRequest] = None,
) -> Optional[str]:
    """Synchronously save a result; async callers should use the repository."""
    try:
        if request is not None:
            save_data = {
                "session_id": session_id,
                "created_at": datetime.now().isoformat(),
                "request": {
                    "city": request.city,
                    "start_date": request.start_date,
                    "end_date": request.end_date,
                    "travel_days": request.travel_days,
                    "transportation": request.transportation,
                    "accommodation": request.accommodation,
                    "preferences": request.preferences,
                    "free_text_input": request.free_text_input,
                    "budget": request.budget,
                },
                "result": trip_plan,
            }
            filepath = _safe_result_filepath(request.city, request.start_date, session_id)
        else:
            filepath = _find_filepath_by_session(session_id)
            if filepath is None:
                logger.warning(f"[Trip Plan] 未找到 session {session_id} 的旧文件，无法仅更新行程")
                return None
            try:
                with open(filepath, "r", encoding="utf-8") as file:
                    save_data = json.load(file)
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                # Rewriting would drop the stored request; leave the file untouched.
                logger.error(f"[Trip Plan] 结果文件损坏，放弃更新 {filepath}: {exc}")
                return None
            save_data["result"] = trip_plan
            save_data["updated_at"] = datetime.now().isoformat()

        previous_files = _candidate_result_files(session_id) if request is not None else []
        _atomic_write_json(filepath, save_data)
        _remove_superseded_files(session_id, filepath, previous_files)
        logger.info(f"[Trip Plan] 结果已保存到: {filepath}")
        return filepath
    except (OSError, TypeError, ValueError) as exc:
        logger.error(f"[Trip Plan] 保存结果失败: {exc}")
        return None


def _remove_superseded_files(session_id: str, keep: str, candidates: list[str]) -> None:
    """A new plan for the same session (different city/date) must not leave an orphan."""
    keep = os.path.realpath(keep)
    for path in candidates:
        if os.path.realpath(path) == keep:
            continue
        try:
            with open(path, "r", encoding="utf-8") as file:
                if json.load(file).get("session_id") != session_id:
                    continue
            os.unlink(path)
        except (OSError, json.JSONDecodeError, TypeError, AttributeError) as exc:
            logger.warning(f"[Trip Plan] 清理被取代的结果文件失败 {path}: {exc}")


def load_trip_result(session_id: str) -> Optional[dict]:
    """Synchronously load one result; async callers should use the repository."""
    filepath = _find_filepath_by_session(session_id)
    if filepath is None:
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data.get("result")
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.error(f"[Trip Plan] 加载 session {session_id} 失败: {exc}")
        return None


def _prune_expired_results(retention_days: int) -> int:
    """Delete result files untouched for longer than the retention window."""
    if retention_days <= 0:
        return 0
    cutoff = datetime.now().timestamp() - retention_days * 86400
    removed = 0
    for filepath in glob.glob(os.path.join(RESULTS_DIR, "*.json")):
        try:
            if os.path.getmtime(filepath) < cutoff:
                os.unlink(filepath)
                removed += 1
        except OSError as exc:
            logger.warning(f"[Trip Plan] 清理过期结果失败 {filepath}: {exc}")
    # Orphaned atomic-write temp files (crash between create and replace).
    for filepath in glob.glob(os.path.join(RESULTS_DIR, ".trip-result-*.tmp")):
        try:
            if os.path.getmtime(filepath) < cutoff:
                os.unlink(filepath)
        except OSError as exc:
            logger.warning(f"[Trip Plan] 清理临时文件失败 {filepath}: {exc}")
    return removed


async def prune_expired_results(retention_days: int) -> int:
    return await asyncio.to_thread(_prune_expired_results, retention_days)


trip_result_repository = TripResultRepository()
