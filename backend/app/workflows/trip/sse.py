"""Map the five-node trip workflow to the public SSE event contract."""

from __future__ import annotations

import json
from typing import Iterable, Optional

from ...models.schemas import StreamingResponse
from .routing import evaluator_next_step


def initial_events(session_id: str) -> Iterable[str]:
    yield sse_event(session_id, 1, "init", "running", "正在初始化混合规划工作流...")
    yield sse_event(session_id, 2, "weather", "running", "天气工具正在获取预报...")
    yield sse_event(session_id, 3, "candidate_collection", "pending", "正在等待天气结果并收集景点和酒店...")


def events_for_update(session_id: str, node_name: str, fields: dict, stream_state: dict, max_evaluator_retry: int) -> Iterable[str]:
    emitter = _NODE_EMITTERS.get(node_name)
    if emitter is None:
        return ()
    return emitter(session_id, fields, stream_state, max_evaluator_retry)


def _emit_weather(session_id: str, fields: dict, _state: dict, _limit: int):
    weather = fields.get("weather", [])
    yield sse_event(session_id, 2, "weather", "completed", f"天气工具完成，获取到 {len(weather)} 天预报", {"weather_days": len(weather)})
    yield sse_event(session_id, 3, "candidate_collection", "running", "POI Agent 和 Hotel Agent 正在并发收集候选...")


def _emit_candidate_collection(session_id: str, fields: dict, _state: dict, _limit: int):
    yield sse_event(session_id, 3, "candidate_collection", "completed", "景点和酒店候选收集完成", {"pois_count": len(fields.get("pois", [])), "hotels_count": len(fields.get("hotels", []))})
    yield sse_event(session_id, 4, "route_optimizer", "running", "路线优化节点正在生成路线骨架...")


def _emit_route_optimizer(session_id: str, fields: dict, _state: dict, _limit: int):
    skeleton = fields.get("route_skeleton", [])
    yield sse_event(session_id, 4, "route_optimizer", "completed", f"路线优化完成，生成 {len(skeleton)} 天路线骨架", {"route_days": len(skeleton)})
    verification = next((trace for trace in reversed(fields.get("node_traces", []))
                         if trace.get("node") == "route_verification"), None)
    if verification is not None:
        yield sse_event(session_id, 4, "route_verification", "completed",
                        verification["summary"])
    yield sse_event(session_id, 5, "planner", "running", "Planner Agent 正在生成行程规划...")


def _emit_planner_agent(session_id: str, fields: dict, state: dict, _limit: int):
    state["latest_trip_plan"] = fields.get("trip_plan")
    days_count = len((state["latest_trip_plan"] or {}).get("days", []))
    yield sse_event(session_id, 5, "planner", "completed", f"Planner Agent 完成，共 {days_count} 天行程", {"plan_generated": True, "days_count": days_count, "retry_count": fields.get("retry_count", 0)})
    yield sse_event(session_id, 6, "quality_gate", "running", "质量门正在检查坐标、强度和预算...")


def _emit_quality_gate(session_id: str, fields: dict, state: dict, limit: int):
    report = fields.get("evaluator_report") or {}
    state["latest_trip_plan"] = fields.get("trip_plan") or state.get("latest_trip_plan")
    passed = bool(report.get("passed"))
    retry_count = fields.get("retry_count", 0)
    issues = report.get("issues", [])
    message = "质量检查通过" if passed else f"质量检查发现 {len(issues)} 个问题"
    yield sse_event(session_id, 6, "quality_gate", "completed", message, {"evaluator_report": report, "retry_count": retry_count})
    if evaluator_next_step(report, retry_count, limit) == "planner_agent":
        yield sse_event(session_id, 5, "planner", "running", "Planner Agent 正在根据评估建议重新生成行程...", {"retry_count": retry_count + 1, "evaluator_issues": issues})
        return
    final_plan = state.get("latest_trip_plan") or {}
    # quality_gate 的更新带有完整累计轨迹；同步接口会手动补上，流式路径也要保持一致
    traces = fields.get("node_traces")
    if traces:
        final_plan = {**final_plan, "node_traces": traces}
    warnings = final_plan.get("evaluator_warnings") or []
    complete_message = f"行程已生成，仍有 {len(warnings)} 项需要调整，正在跳转..." if warnings else "行程规划已生成，正在跳转..."
    yield sse_event(session_id, 7, "complete", "completed", complete_message, {"itinerary": final_plan})


_NODE_EMITTERS = {"weather": _emit_weather, "candidate_collection": _emit_candidate_collection, "route_optimizer": _emit_route_optimizer, "planner_agent": _emit_planner_agent, "quality_gate": _emit_quality_gate}


def sse_event(session_id: str, step: int, node: str, status: str, message: str, data: Optional[dict] = None) -> str:
    # Building through the schema keeps the public SSE contract (and its status enum) enforced.
    event = StreamingResponse(
        session_id=session_id, step=step, node=node, status=status,
        message=message, data=data or {},
    ).model_dump(mode="json")
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
