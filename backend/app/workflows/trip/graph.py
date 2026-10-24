"""LangGraph workflow for trip planning."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import AsyncGenerator

from langgraph.graph import END, StateGraph
from langgraph.config import get_stream_writer
from loguru import logger

from ...agents.hotel_agent import run_hotel_agent
from ...agents.planner_agent import run_planner_agent
from ...agents.poi_agent import run_poi_agent
from ...core.config import get_settings
from ...core.llm import resolve_provider
from ...models.schemas import NodeTraceModel, TripRequest
from ...planning.itinerary_optimizer import optimize_poi_order
from ...planning.rule_evaluator import evaluate_trip_plan
from ...planning.weather import merge_supplemental_weather, match_weather_to_trip_dates, uncovered_dates
from ...services.open_meteo_service import fetch_forecast as fetch_open_meteo_forecast
from ...services.amap_service import get_amap_service
from ...services.amap_mcp_service import verify_route_skeleton
from ...services.trip_modify_service import recompute_budget
from .checkpoint import close_checkpointer, discard_checkpoint, get_checkpointer
from .routing import evaluator_next_step
from .route_supply import RouteSupply, partition_route_candidates, supply_route_candidates
from .state import TripPlanningState
from .sse import events_for_update, initial_events, sse_event


MAX_EVALUATOR_RETRY = 1


class PlanningSessionConflict(Exception):
    """A synchronous or streamed run already owns this session."""


def _llm_provider(request: TripRequest) -> str:
    return resolve_provider(request.llm_provider)


def _append_trace(state: TripPlanningState, trace: NodeTraceModel) -> list[dict]:
    return state.get("node_traces", []) + [trace.model_dump()]


def _collection_progress(node: str, status: str, message: str, data: dict | None = None) -> None:
    """Emit real subtask progress when the graph is streamed."""
    try:
        writer = get_stream_writer()
    except (KeyError, RuntimeError):
        return
    writer({
        "kind": "collection_progress",
        "node": node,
        "status": status,
        "message": message,
        "data": data or {},
    })


async def _supplement_weather(weather: list, city: str) -> list:
    """Fill dates the Amap forecast does not cover; any failure degrades to no supplement."""
    if not get_settings().open_meteo_enabled:
        return []
    missing = uncovered_dates(weather)
    if not missing:
        return []
    try:
        location = await get_amap_service().geocode(city)
        if location is None:
            return []
        return await fetch_open_meteo_forecast(location.longitude, location.latitude, missing)
    except Exception as exc:
        logger.warning("[TripGraph] 天气补充降级 | error_type={}", type(exc).__name__)
        return []


async def weather_node(state: TripPlanningState) -> dict:
    """Fetch weather before candidate collection."""
    request = state["request"]
    started_at = time.perf_counter()
    try:
        raw_weather = await get_amap_service().get_weather(request.city)
    except Exception as e:
        raw_weather = []
        logger.warning(f"[TripGraph] Weather Tool 失败，降级为空天气列表: {e}")
    weather = match_weather_to_trip_dates(raw_weather, request)
    supplemented = await _supplement_weather(weather, request.city)
    if supplemented:
        weather = merge_supplemental_weather(weather, supplemented)
    known_dates = [item.date for item in raw_weather if item.day_weather and item.day_weather != "未知"]
    trace = NodeTraceModel(
        node="weather",
        status="completed",
        duration_ms=int((time.perf_counter() - started_at) * 1000),
        summary=(
            f"获取天气 {len(weather)} 天"
            + (f"；高德返回日期 {known_dates[0]}~{known_dates[-1]}" if known_dates else "；高德未返回可用预报")
            + (f"；Open-Meteo 补充 {len(supplemented)} 天" if supplemented else "")
        ),
    )
    return {"weather": weather, "node_traces": _append_trace(state, trace)}


async def candidate_collection_node(state: TripPlanningState) -> dict:
    """Collect weather-aware POIs and hotels concurrently."""
    request = state["request"]
    provider = _llm_provider(request)
    weather = state.get("weather", [])
    started_at = time.perf_counter()
    warnings = []

    logger.info("[TripGraph] candidate_collection 开始")
    _collection_progress("poi_search", "running", "POI Agent 正在结合天气筛选景点...")
    _collection_progress("hotel", "running", "Hotel Agent 正在搜索酒店...")

    async def collect_pois():
        try:
            result = await run_poi_agent(
                request.city,
                request.preferences,
                request.free_text_input,
                provider,
                weather=weather,
                travel_days=request.travel_days,
            )
            pois, pois_pool = result
        except Exception as exc:
            logger.warning(f"[TripGraph] POI Agent 失败，降级为空景点列表: {exc}")
            warnings.append("POI Agent 失败，使用空景点列表")
            pois, pois_pool = [], []
        try:
            supply = await supply_route_candidates(pois, pois_pool, request)
        except Exception as exc:
            logger.warning("[TripGraph] 路线候选补足失败 | error_type={}", type(exc).__name__)
            warnings.append("路线候选补足失败，使用原景点列表")
            supply = RouteSupply(pois, pois_pool)
        _collection_progress(
            "poi_search", "completed",
            f"景点召回完成，得到 {len(supply.pois)} 个路线候选景点",
            {"pois_count": len(supply.pois)},
        )
        return supply

    async def collect_hotels():
        try:
            result = await run_hotel_agent(
                request.city, request.accommodation, request.budget, provider,
                travel_days=request.travel_days,
                free_text_input=request.free_text_input,
            )
            hotels, hotels_pool = result
        except Exception as exc:
            logger.warning(f"[TripGraph] Hotel Agent 失败，降级为空酒店列表: {exc}")
            warnings.append("Hotel Agent 失败，使用空酒店列表")
            hotels, hotels_pool = [], []
        _collection_progress(
            "hotel", "completed", f"Hotel Agent 完成，推荐 {len(hotels)} 家酒店",
            {"hotels_count": len(hotels)},
        )
        return hotels, hotels_pool

    supply, (hotels, hotels_pool) = await asyncio.gather(collect_pois(), collect_hotels())
    pois, pois_pool = supply.pois, supply.pool

    trace = NodeTraceModel(
        node="candidate_collection",
        status="completed",
        duration_ms=int((time.perf_counter() - started_at) * 1000),
        summary=(
            f"景点 {len(pois)} 个，POI候选 {len(pois_pool)} 个，"
            f"天气 {len(weather)} 天，酒店 {len(hotels)} 家，酒店候选 {len(hotels_pool)} 家"
            f"；候选池补入 {supply.promoted} 个，高德补搜 {supply.search_attempts} 次、补入 {supply.fetched} 个"
            + (f"；补搜失败: {', '.join(supply.failures)}" if supply.failures else "")
            + (f"；降级: {'；'.join(warnings)}" if warnings else "")
        ),
    )
    logger.info(f"[TripGraph] candidate_collection 完成 | {trace.summary}")

    return {
        "pois": pois,
        "pois_pool": pois_pool,
        "weather": weather,
        "hotels": hotels,
        "hotels_pool": hotels_pool,
        "node_traces": _append_trace(state, trace),
    }


async def route_optimizer_node(state: TripPlanningState) -> dict:
    """Generate the route skeleton before planner prompting."""
    started_at = time.perf_counter()
    request = state["request"]

    logger.info("[TripGraph] route_optimizer 开始")
    route_skeleton = optimize_poi_order(
        state["pois"], request.travel_days, request.transportation,
        weather=state.get("weather"),
    )
    route_pois, remaining_pool = partition_route_candidates(
        state["pois"], state.get("pois_pool", []), route_skeleton,
    )

    trace = NodeTraceModel(
        node="route_optimizer",
        status="completed",
        duration_ms=int((time.perf_counter() - started_at) * 1000),
        summary=f"生成 {len(route_skeleton)} 天路线骨架，使用 {len(route_pois)} 个景点",
    )
    logger.info(f"[TripGraph] route_optimizer 完成 | {trace.summary}")

    return {
        "pois": route_pois,
        "pois_pool": remaining_pool,
        "route_skeleton": route_skeleton,
        "node_traces": _append_trace(state, trace),
    }


async def route_verification_node(state: TripPlanningState) -> dict:
    """Verify the optimized skeleton without changing attraction grouping or order."""
    started_at = time.perf_counter()
    request = state["request"]
    route_skeleton, verification = await verify_route_skeleton(
        state.get("route_skeleton", []), city=request.city, transportation=request.transportation,
    )
    summary = verification.message
    if verification.tool_name:
        summary += f"（工具: {verification.tool_name}）"
    return {
        "route_skeleton": route_skeleton,
        "mcp_route_verification": verification.to_dict(),
        "node_traces": _append_trace(state, NodeTraceModel(
            node="route_verification", status="completed",
            duration_ms=int((time.perf_counter() - started_at) * 1000), summary=summary,
        )),
    }


async def route_planning_node(state: TripPlanningState) -> dict:
    """Keep local optimization deterministic; optionally verify its output."""
    update = await route_optimizer_node(state)
    if not get_settings().amap_mcp_enabled:
        return update
    try:
        verification = await route_verification_node({**state, **update})
    except Exception as exc:
        logger.warning("[TripGraph] 路线验证降级 | error_type={}", type(exc).__name__)
        update["node_traces"] = _append_trace(update, NodeTraceModel(
            node="route_verification", status="failed", duration_ms=0,
            summary="路线验证失败，保留坐标估算",
        ))
        return update
    update["route_skeleton"] = verification["route_skeleton"]
    update["node_traces"] = verification["node_traces"]
    return update


async def planner_agent_node(state: TripPlanningState) -> dict:
    """Generate the final trip plan from collected context."""
    request = state["request"]
    started_at = time.perf_counter()
    previous_report = state.get("evaluator_report")
    evaluator_suggestions = (previous_report or {}).get("suggestions", [])
    retry_count = state.get("retry_count", 0)
    is_retry = bool(previous_report and not previous_report.get("passed", True))
    if is_retry:
        retry_count += 1

    logger.info(f"[TripGraph] planner_agent 开始 | retry_count={retry_count}")
    trip_plan = await run_planner_agent(
        city=request.city,
        start_date=request.start_date,
        end_date=request.end_date,
        travel_days=request.travel_days,
        transportation=request.transportation,
        accommodation=request.accommodation,
        preferences=request.preferences,
        pois=state["pois"],
        weather=state["weather"],
        hotels=state["hotels"],
        budget=request.budget,
        llm_provider=_llm_provider(request),
        route_skeleton=state["route_skeleton"],
        evaluator_suggestions=evaluator_suggestions,
        free_text_input=request.free_text_input,
    )

    trace = NodeTraceModel(
        node="planner_agent",
        status="completed",
        duration_ms=int((time.perf_counter() - started_at) * 1000),
        summary=f"生成 {len(trip_plan.get('days', []))} 天行程"
                + (f"（第 {retry_count} 次重试）" if is_retry else ""),
    )
    logger.info(f"[TripGraph] planner_agent 完成 | {trace.summary}")

    trip_plan["pois_pool"] = [poi.model_dump() for poi in state.get("pois_pool", [])]
    trip_plan["hotels_pool"] = [hotel.model_dump() for hotel in state.get("hotels_pool", [])]

    return {
        "trip_plan": trip_plan,
        "retry_count": retry_count,
        "evaluator_report": None,
        "node_traces": _append_trace(state, trace),
    }


async def quality_gate_node(state: TripPlanningState) -> dict:
    """Evaluate, annotate, and finalize the generated trip plan."""
    started_at = time.perf_counter()

    logger.info("[TripGraph] quality_gate 开始")
    trip_plan = dict(state.get("trip_plan") or {})
    trip_plan["budget"] = recompute_budget(trip_plan)
    report = evaluate_trip_plan(
        trip_plan, state["request"], state.get("route_skeleton"), state.get("hotels"),
    )

    if report.get("issues"):
        trip_plan["evaluator_warnings"] = report["issues"]
    trip_plan["evaluator_report"] = report

    trace = NodeTraceModel(
        node="quality_gate",
        status="completed",
        duration_ms=int((time.perf_counter() - started_at) * 1000),
        summary=f"规则检查通过={report['passed']} 问题={len(report['issues'])}",
    )
    logger.info(f"[TripGraph] quality_gate 完成 | {trace.summary}")
    if report["issues"]:
        logger.info(f"[TripGraph] quality_gate issues | {report['issues']}")

    return {
        "trip_plan": trip_plan,
        "evaluator_report": report,
        "retry_count": state.get("retry_count", 0),
        "node_traces": _append_trace(state, trace),
    }


async def evaluator_router(state: TripPlanningState) -> str:
    """Route back to planner when evaluation fails and retry budget remains."""
    return evaluator_next_step(state.get("evaluator_report") or {}, state.get("retry_count", 0), MAX_EVALUATOR_RETRY)


def build_trip_graph(checkpointer=None):
    """Build and compile the trip planning graph."""
    graph = StateGraph(TripPlanningState)

    graph.add_node("weather", weather_node)
    graph.add_node("candidate_collection", candidate_collection_node)
    graph.add_node("route_optimizer", route_planning_node)
    graph.add_node("planner_agent", planner_agent_node)
    graph.add_node("quality_gate", quality_gate_node)

    graph.set_entry_point("weather")
    graph.add_edge("weather", "candidate_collection")
    graph.add_edge("candidate_collection", "route_optimizer")
    graph.add_edge("route_optimizer", "planner_agent")
    graph.add_edge("planner_agent", "quality_gate")
    graph.add_conditional_edges(
        "quality_gate",
        evaluator_router,
        {
            "planner_agent": "planner_agent",
            "finalize": END,
        },
    )

    return graph.compile(checkpointer=checkpointer) if checkpointer else graph.compile()


_trip_graph = None


async def get_trip_graph():
    """Return a module-level compiled graph instance with checkpointer attached."""
    global _trip_graph
    if _trip_graph is None:
        checkpointer = await get_checkpointer()
        _trip_graph = build_trip_graph(checkpointer=checkpointer)
    return _trip_graph


async def close_trip_graph_resources() -> None:
    """Close graph-level resources opened for checkpoint persistence."""
    global _trip_graph
    await close_checkpointer()
    _trip_graph = None


def _initial_state(request: TripRequest) -> TripPlanningState:
    return {
        "request": request,
        "pois": [],
        "weather": [],
        "hotels": [],
        "pois_pool": [],
        "hotels_pool": [],
        "route_skeleton": [],
        "trip_plan": None,
        "evaluator_report": None,
        "retry_count": 0,
        "node_traces": [],
    }


def _thread_config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


async def run_trip_graph(request: TripRequest) -> dict:
    """Run the graph and return the final trip plan."""
    session_id = request.session_id or str(uuid.uuid4())
    request.session_id = session_id
    if session_id in _active_sessions:
        raise PlanningSessionConflict("该会话正在规划中，请稍候")
    _active_sessions.add(session_id)
    try:
        graph = await get_trip_graph()
        await discard_checkpoint(graph.checkpointer, session_id)
        final_state = await graph.ainvoke(
            _initial_state(request),
            config=_thread_config(session_id),
        )
        await discard_checkpoint(graph.checkpointer, session_id)
    finally:
        # Failed checkpoints remain available to the resume endpoint.
        _active_sessions.discard(session_id)
    trip_plan = final_state["trip_plan"] or {}
    trip_plan["node_traces"] = final_state.get("node_traces", [])
    return trip_plan


# Sessions with a stream in flight; a second concurrent run would execute nodes twice.
_active_sessions: set[str] = set()

# Only these nodes report subtask progress through the custom stream.
_COLLECTION_STEPS = {"poi_search": 3, "hotel": 3}


async def _pending_nodes(graph, session_id: str) -> tuple[str, ...]:
    """Nodes still waiting to run for this session; empty when nothing can resume."""
    try:
        snapshot = await graph.aget_state(_thread_config(session_id))
    except Exception:
        return ()
    return tuple(snapshot.next or ())


async def load_resume_request(session_id: str) -> TripRequest | None:
    """Original request of a resumable session, or None when nothing can resume."""
    graph = await get_trip_graph()
    try:
        snapshot = await graph.aget_state(_thread_config(session_id))
    except Exception as exc:
        logger.warning("[TripGraph] 读取 checkpoint 失败 | error_type={}", type(exc).__name__)
        return None
    if not snapshot.next:
        return None
    request = (snapshot.values or {}).get("request")
    return request if isinstance(request, TripRequest) else None


def _error_event(session_id: str, message: str, resumable: bool = False) -> str:
    return sse_event(session_id, 0, "error", "failed", message, {"resumable": resumable})


async def _stream_trip(
    session_id: str, request: TripRequest | None, resume: bool,
) -> AsyncGenerator[str, None]:
    """Stream one fresh or resumed run using the public SSE contract."""
    graph = await get_trip_graph()
    if session_id in _active_sessions:
        yield _error_event(session_id, "该会话正在规划中，请稍候")
        return

    _active_sessions.add(session_id)
    try:
        stream_state: dict = {"latest_trip_plan": None}
        if resume:
            pending = await _pending_nodes(graph, session_id)
            if not pending:
                yield _error_event(session_id, "没有可继续的规划进度，请重新开始规划")
                return
            snapshot = await graph.aget_state(_thread_config(session_id))
            stream_state["latest_trip_plan"] = snapshot.values.get("trip_plan")
            graph_input = None
            yield sse_event(
                session_id, 1, "init", "running", f"正在从 {pending[0]} 阶段继续规划...",
            )
        else:
            # A reused session id must not inherit state from an earlier run.
            await discard_checkpoint(graph.checkpointer, session_id)
            graph_input = _initial_state(request)
            for event in initial_events(session_id):
                yield event

        async for mode, chunk in graph.astream(
            graph_input,
            stream_mode=["updates", "custom"],
            config=_thread_config(session_id),
        ):
            if mode == "custom":
                if isinstance(chunk, dict) and chunk.get("kind") == "collection_progress":
                    yield sse_event(
                        session_id, _COLLECTION_STEPS[chunk["node"]], chunk["node"],
                        chunk["status"], chunk["message"], chunk["data"],
                    )
                continue
            for node_name, updated_fields in chunk.items():
                for event in events_for_update(
                    session_id,
                    node_name,
                    updated_fields,
                    stream_state,
                    MAX_EVALUATOR_RETRY,
                ):
                    yield event
        await discard_checkpoint(graph.checkpointer, session_id)
    except Exception as exc:
        # Details stay in the log; the client only learns where it can resume from.
        logger.error(f"[TripGraph] 流式执行失败: {exc}", exc_info=True)
        pending = await _pending_nodes(graph, session_id)
        if pending:
            yield _error_event(session_id, f"规划在 {pending[0]} 阶段失败，可从该阶段继续", True)
        else:
            yield _error_event(session_id, "规划执行失败，请稍后重新开始")
    finally:
        _active_sessions.discard(session_id)


async def run_trip_graph_stream(request: TripRequest) -> AsyncGenerator[str, None]:
    """Run the graph and map graph updates to the existing SSE contract."""
    session_id = request.session_id or str(uuid.uuid4())
    request.session_id = session_id
    async for event in _stream_trip(session_id, request, resume=False):
        yield event


async def resume_trip_graph_stream(session_id: str) -> AsyncGenerator[str, None]:
    """Continue a failed run from its last checkpoint instead of starting over."""
    async for event in _stream_trip(session_id, None, resume=True):
        yield event
