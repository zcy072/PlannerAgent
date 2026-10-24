"""Route verification through the official Amap MCP Server.

The local optimizer decides the attraction order first.  This module only
verifies that completed day-level routes against Amap's MCP route tools, so an
unavailable external service never prevents itinerary generation.
"""

from __future__ import annotations

import copy
import asyncio
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Optional
from urllib.parse import urlencode

import httpx
from loguru import logger
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from ..core.config import get_settings
from ..planning.location_utils import is_valid_coordinate


DEFAULT_AMAP_MCP_URL = "https://mcp.amap.com/mcp"
DEFAULT_MCP_TIMEOUT_SECONDS = 15.0
DEFAULT_MCP_CALL_TIMEOUT_SECONDS = 8.0
ROUTE_HINTS = ("route", "direction", "path", "路线", "路径", "规划")
MODE_HINTS = {
    "transit": ("transit", "transfer", "public", "公交", "公共交通", "地铁"),
    "driving": ("driving", "drive", "驾车", "自驾"),
    "walking": ("walking", "walk", "步行"),
    "cycling": ("bicycling", "bicycle", "cycling", "骑行"),
}

RouteToolCaller = Callable[[str, dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class RouteMetric:
    """One route leg returned by the external MCP tool."""

    distance_km: float
    duration_min: Optional[int]


@dataclass(frozen=True)
class RouteVerificationResult:
    """A serializable summary used by the graph trace and SSE progress."""

    configured: bool
    total_legs: int
    verified_days: int = 0
    verified_legs: int = 0
    tool_name: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "total_legs": self.total_legs,
            "verified_days": self.verified_days,
            "verified_legs": self.verified_legs,
            "tool_name": self.tool_name,
            "message": self.message,
        }


async def verify_route_skeleton(
    route_skeleton: list[dict],
    *,
    city: str,
    transportation: str,
) -> tuple[list[dict], RouteVerificationResult]:
    """Use Amap MCP to replace Haversine estimates for fully verified days.

    The Web Service key used by MCP is deliberately optional. If it is absent, tool
    discovery fails, or a route call cannot be parsed, the original coordinate
    estimates remain intact.
    """
    verified_skeleton = copy.deepcopy(route_skeleton)
    total_legs = _count_route_legs(verified_skeleton)
    settings = get_settings()
    api_key = settings.amap_mcp_api_key.strip()

    if not verified_skeleton:
        return verified_skeleton, RouteVerificationResult(
            configured=bool(api_key),
            total_legs=0,
            message="没有可校验的路线骨架",
        )

    if not api_key:
        return verified_skeleton, RouteVerificationResult(
            configured=False,
            total_legs=total_legs,
            message="未配置 AMAP_MCP_API_KEY（应填高德 Web 服务 Key），保留坐标距离估算",
        )

    if total_legs == 0:
        return verified_skeleton, RouteVerificationResult(
            configured=True,
            total_legs=0,
            message="每日景点不足两处，暂无需调用路线工具",
        )

    max_route_legs = max(0, int(settings.amap_mcp_max_route_legs))
    if max_route_legs == 0:
        return verified_skeleton, RouteVerificationResult(
            configured=True,
            total_legs=total_legs,
            message="AMAP_MCP_MAX_ROUTE_LEGS 为 0，已跳过路线校验",
        )

    mcp_url = _build_mcp_url(settings.amap_mcp_url, api_key)
    total_timeout = _positive_timeout(
        getattr(settings, "amap_mcp_timeout_seconds", DEFAULT_MCP_TIMEOUT_SECONDS),
        DEFAULT_MCP_TIMEOUT_SECONDS,
    )
    call_timeout = _positive_timeout(
        getattr(settings, "amap_mcp_call_timeout_seconds", DEFAULT_MCP_CALL_TIMEOUT_SECONDS),
        DEFAULT_MCP_CALL_TIMEOUT_SECONDS,
    )
    try:
        async with asyncio.timeout(total_timeout):
            async with httpx.AsyncClient(timeout=total_timeout) as http_client:
                async with streamable_http_client(
                    mcp_url,
                    http_client=http_client,
                ) as (read_stream, write_stream, _get_session_id):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        tools = (await session.list_tools()).tools
                        route_tool = _select_route_tool(tools, transportation)
                        if route_tool is None:
                            return verified_skeleton, RouteVerificationResult(
                                configured=True,
                                total_legs=total_legs,
                                message="高德 MCP 未提供匹配当前交通方式的路径规划工具",
                            )

                        async def call_route_tool(name: str, arguments: dict[str, Any]) -> Any:
                            return await asyncio.wait_for(
                                session.call_tool(name, arguments=arguments),
                                timeout=call_timeout,
                            )

                        return await _verify_route_days(
                            verified_skeleton,
                            city=city,
                            transportation=transportation,
                            route_tool=route_tool,
                            call_tool=call_route_tool,
                            max_route_legs=max_route_legs,
                        )
    except Exception as exc:
        # Exceptions from HTTP clients can embed the query string, which contains
        # the MCP key.  Keep logs useful without ever logging the exception text.
        logger.warning(
            "[Amap MCP] 路线校验不可用，保留坐标距离估算 | error_type={}",
            type(exc).__name__,
        )
        return copy.deepcopy(route_skeleton), RouteVerificationResult(
            configured=True,
            total_legs=total_legs,
            message="高德 MCP 调用失败，保留坐标距离估算",
        )


async def _verify_route_days(
    route_skeleton: list[dict],
    *,
    city: str,
    transportation: str,
    route_tool: Any,
    call_tool: RouteToolCaller,
    max_route_legs: int,
) -> tuple[list[dict], RouteVerificationResult]:
    """Apply external metrics only when every leg of a day succeeds."""
    total_legs = _count_route_legs(route_skeleton)
    verified_days = 0
    verified_legs = 0
    attempted_legs = 0
    tool_name = str(getattr(route_tool, "name", ""))

    for day in route_skeleton:
        legs, expected_leg_count = _day_route_legs(day)
        if expected_leg_count == 0 or len(legs) != expected_leg_count:
            continue
        if attempted_legs + len(legs) > max_route_legs:
            continue

        metrics: list[RouteMetric] = []
        day_succeeded = True
        for origin, destination in legs:
            attempted_legs += 1
            try:
                result = await call_tool(
                    tool_name,
                    _build_route_arguments(
                        origin=origin,
                        destination=destination,
                        city=city,
                        route_tool=route_tool,
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "[Amap MCP] 路径工具调用失败 | tool={} error_type={}",
                    tool_name,
                    type(exc).__name__,
                )
                day_succeeded = False
                break

            metric = _extract_route_metric(result)
            if metric is None:
                logger.warning("[Amap MCP] 路径工具未返回可解析距离 | tool={}", tool_name)
                day_succeeded = False
                break
            metrics.append(metric)

        if not day_succeeded or len(metrics) != len(legs):
            continue

        day["estimated_distance_km"] = round(sum(metric.distance_km for metric in metrics), 2)
        if all(metric.duration_min is not None for metric in metrics):
            day["estimated_duration_min"] = max(
                0,
                int(round(sum(metric.duration_min or 0 for metric in metrics))),
            )
        _append_mcp_reason(day)
        verified_days += 1
        verified_legs += len(metrics)

    if verified_days:
        message = f"高德 MCP 已校验 {verified_days} 天、{verified_legs} 段真实路径"
        if attempted_legs < total_legs:
            message += f"（按 {max_route_legs} 段上限执行）"
    else:
        message = "高德 MCP 未返回完整日程的可用路径数据，保留坐标距离估算"

    return route_skeleton, RouteVerificationResult(
        configured=True,
        total_legs=total_legs,
        verified_days=verified_days,
        verified_legs=verified_legs,
        tool_name=tool_name,
        message=message,
    )


def _positive_timeout(value: Any, default: float) -> float:
    """Normalize timeout configuration and avoid disabling safety limits."""
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(timeout) or timeout <= 0:
        return default
    return timeout


def _build_mcp_url(base_url: str, api_key: str) -> str:
    """Append the key without exposing it in any log output."""
    url = base_url.strip() or DEFAULT_AMAP_MCP_URL
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode({'key': api_key})}"


def _select_route_tool(tools: Iterable[Any], transportation: str) -> Optional[Any]:
    """Select a route-planning tool from the MCP server's live tool list."""
    route_tools = [tool for tool in tools if _is_route_tool(tool)]
    if not route_tools:
        return None

    for mode_hints in _transport_mode_priorities(transportation):
        matching_tools = [
            tool for tool in route_tools
            if any(hint in _tool_text(tool) for hint in mode_hints)
        ]
        if matching_tools:
            return max(matching_tools, key=lambda tool: _tool_score(tool, mode_hints))

    return None


def _transport_mode_priorities(transportation: str) -> tuple[tuple[str, ...], ...]:
    if "步行" in transportation:
        return (MODE_HINTS["walking"],)
    if "自驾" in transportation or "驾车" in transportation:
        return (MODE_HINTS["driving"],)
    if "骑行" in transportation:
        return (MODE_HINTS["cycling"],)
    if "混合" in transportation:
        return (
            MODE_HINTS["transit"],
            MODE_HINTS["driving"],
            MODE_HINTS["walking"],
        )
    return (MODE_HINTS["transit"],)


def _is_route_tool(tool: Any) -> bool:
    return any(hint in _tool_text(tool) for hint in ROUTE_HINTS)


def _tool_score(tool: Any, mode_hints: tuple[str, ...]) -> int:
    name = str(getattr(tool, "name", "") or "").lower()
    text = _tool_text(tool)
    score = sum(20 for hint in ROUTE_HINTS if hint in name)
    score += sum(5 for hint in ROUTE_HINTS if hint in text)
    score += sum(100 for hint in mode_hints if hint in name)
    score += sum(25 for hint in mode_hints if hint in text)
    return score


def _tool_text(tool: Any) -> str:
    fields = (
        getattr(tool, "name", ""),
        getattr(tool, "title", ""),
        getattr(tool, "description", ""),
    )
    return " ".join(str(value or "") for value in fields).lower()


def _build_route_arguments(
    *,
    origin: str,
    destination: str,
    city: str,
    route_tool: Any,
) -> dict[str, str]:
    arguments = {"origin": origin, "destination": destination}
    if any(hint in _tool_text(route_tool) for hint in MODE_HINTS["transit"]):
        arguments["city"] = city
        arguments["cityd"] = city
    return arguments


def _count_route_legs(route_skeleton: Iterable[dict]) -> int:
    return sum(len(_day_route_legs(day)[0]) for day in route_skeleton)


def _day_route_legs(day: dict) -> tuple[list[tuple[str, str]], int]:
    attractions = day.get("attractions", [])
    if not isinstance(attractions, list):
        return [], 0

    expected_leg_count = max(0, len(attractions) - 1)
    legs = []
    for first, second in zip(attractions, attractions[1:]):
        origin = _coordinate_text(first)
        destination = _coordinate_text(second)
        if origin is not None and destination is not None:
            legs.append((origin, destination))
    return legs, expected_leg_count


def _coordinate_text(attraction: Any) -> Optional[str]:
    if not isinstance(attraction, dict):
        return None
    location = attraction.get("location")
    if not isinstance(location, dict):
        return None

    longitude = location.get("longitude", location.get("lng"))
    latitude = location.get("latitude", location.get("lat"))
    if not is_valid_coordinate(longitude, latitude):
        return None
    return f"{float(longitude):.6f},{float(latitude):.6f}"


def _extract_route_metric(result: Any) -> Optional[RouteMetric]:
    if getattr(result, "isError", False):
        return None

    for payload in _tool_result_payloads(result):
        for mapping in _route_candidates(payload):
            distance_km = _distance_to_km(mapping.get("distance"))
            if distance_km is None:
                continue

            duration_min = _duration_to_min(mapping.get("duration"))
            if duration_min is None:
                cost = mapping.get("cost")
                if isinstance(cost, dict):
                    duration_min = _duration_to_min(cost.get("duration"))
            return RouteMetric(distance_km=distance_km, duration_min=duration_min)
    return None


def _tool_result_payloads(result: Any) -> Iterable[Any]:
    structured_content = getattr(result, "structuredContent", None)
    if structured_content is None:
        structured_content = getattr(result, "structured_content", None)
    if isinstance(structured_content, (dict, list)):
        yield structured_content

    for content in getattr(result, "content", []) or []:
        text = content.get("text") if isinstance(content, dict) else getattr(content, "text", None)
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, (dict, list)):
            yield payload


def _route_candidates(value: Any, depth: int = 0) -> Iterable[dict[str, Any]]:
    """Read explicit route summaries, never steps, segments or walking legs."""
    if not isinstance(value, dict) or depth > 4:
        return
    if "status" in value and str(value["status"]) != "1":
        return
    for key in ("paths", "transits"):
        candidates = value.get(key)
        if isinstance(candidates, list):
            for candidate in candidates:
                if isinstance(candidate, dict):
                    yield candidate
            return
    for key in ("route", "data", "result"):
        if isinstance(value.get(key), dict):
            yield from _route_candidates(value[key], depth + 1)
            return
    if "distance" in value:
        yield value


def _distance_to_km(value: Any) -> Optional[float]:
    number = _coerce_number(value)
    if number is None:
        return None

    text = str(value).lower()
    if "km" in text or "公里" in text:
        return number
    return number / 1000


def _duration_to_min(value: Any) -> Optional[int]:
    number = _coerce_number(value)
    if number is None:
        return None

    text = str(value).lower()
    if "小时" in text or "hour" in text or text.endswith("h"):
        return max(0, int(round(number * 60)))
    if "分钟" in text or "min" in text:
        return max(0, int(round(number)))
    return max(0, int(round(number / 60)))


def _coerce_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = re.search(r"-?\d+(?:\.\d+)?", str(value))
        if not match:
            return None
        number = float(match.group(0))

    if not math.isfinite(number) or number < 0:
        return None
    return number


def _append_mcp_reason(day: dict) -> None:
    suffix = "已通过高德 MCP 路径规划校验。"
    reason = str(day.get("optimization_reason", "")).strip()
    if suffix in reason:
        return
    day["optimization_reason"] = (
        f"{reason.rstrip('。； ')}；{suffix}" if reason else suffix
    )
