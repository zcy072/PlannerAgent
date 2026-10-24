"""旅行规划 API 路由"""

import uuid
import json
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse
from loguru import logger

from ...models.schemas import (
    SESSION_ID_PATTERN,
    ChatRequest,
    ChatResponse,
    ItemModifyRequest,
    ItemModifyResponse,
    TripPlan,
    TripRequest,
)
from ...workflows.trip.graph import (
    PlanningSessionConflict,
    load_resume_request,
    resume_trip_graph_stream,
    run_trip_graph,
    run_trip_graph_stream,
)
from ...services.trip_chat_service import ChatError, handle_chat
from ...services.trip_modify_service import (
    TripModificationError,
    modify_persisted_trip,
)
from ...services.trip_result_service import trip_result_repository

router = APIRouter(prefix="/trip", tags=["旅行规划"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def _persist_complete_stream_event(
    event: str,
    session_id: str,
    request: TripRequest,
) -> str:
    """Persist a complete SSE event without preventing delivery of its itinerary."""
    if not event.startswith("data: "):
        return event

    try:
        event_data = json.loads(event[6:].strip())
    except (json.JSONDecodeError, TypeError):
        return event

    data = event_data.get("data") or {}
    itinerary = data.get("itinerary")
    if event_data.get("node") != "complete" or not itinerary:
        return event

    try:
        async with trip_result_repository.transaction(session_id):
            saved = await trip_result_repository.save(session_id, itinerary, request) is not None
    except Exception as exc:
        logger.error(f"[Trip Plan Stream] 保存结果时发生异常: {exc}", exc_info=True)
        saved = False

    data["persistence_saved"] = saved
    if not saved:
        warning = "行程已生成，但保存失败；当前可以查看，刷新页面后将无法恢复。"
        data["persistence_warning"] = warning
        event_data["message"] = warning
    event_data["data"] = data
    return f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"


@router.post("/plan")
async def create_trip_plan(request: TripRequest):
    """
    创建旅行计划 - 同步接口

    混合规划流程：
    1. Weather Tool: 获取天气预报
    2. Candidate Collection: 并行执行 POI / Hotel 搜索策略与候选筛选
    3. Route Optimizer: 生成确定性的路线骨架
    4. Planner Agent / Quality Gate: 生成结构化行程并按规则决定是否重试
    """
    session_id = request.session_id or str(uuid.uuid4())
    request.session_id = session_id

    logger.info(f"[Trip Plan] 开始处理 | Session: {session_id} | 城市: {request.city} | 日期: {request.start_date}~{request.end_date} | LLM: {request.llm_provider}")

    try:
        trip_plan = await run_trip_graph(request)

        async with trip_result_repository.transaction(session_id):
            saved = await trip_result_repository.save(session_id, trip_plan, request)
        if saved is None:
            raise RuntimeError("行程已生成，但保存结果失败")

        return {
            "success": True,
            "message": "旅行计划生成成功",
            "data": trip_plan,
            "status": "completed",
            "session_id": session_id
        }

    except PlanningSessionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Trip Plan] 发生错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="生成失败，请稍后重试")


@router.post("/plan/stream")
async def create_trip_plan_stream(request: TripRequest):
    """
    创建旅行计划 - 流式响应

    通过 SSE 实时推送混合工作流执行进度
    """
    session_id = request.session_id or str(uuid.uuid4())
    request.session_id = session_id

    logger.info(f"[Trip Plan Stream] 开始处理 | Session: {session_id} | 城市: {request.city}")

    async def event_generator():
        async for event in run_trip_graph_stream(request):
            # 完成事件发给前端前先保存，确保结果页刷新时可以立即恢复行程。
            yield await _persist_complete_stream_event(event, session_id, request)

    return FastAPIStreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/plan/resume/{session_id}")
async def resume_trip_plan_stream(session_id: str):
    """从失败节点继续规划，复用已完成节点的结果，不重复调用外部服务和 LLM。"""
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="会话ID格式无效")

    # 原始请求保存在 checkpoint 里；取不到说明没有可续跑的进度，流里会返回明确的错误事件。
    request = await load_resume_request(session_id)

    async def event_generator():
        async for event in resume_trip_graph_stream(session_id):
            if request is None:
                yield event
            else:
                yield await _persist_complete_stream_event(event, session_id, request)

    return FastAPIStreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/result/{session_id}")
async def get_saved_trip_plan(session_id: str):
    """按会话恢复已保存的行程，供结果页刷新后重新加载。"""
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise HTTPException(status_code=404, detail="未找到已保存的行程")
    trip_plan = await trip_result_repository.load(session_id)
    if trip_plan is None:
        raise HTTPException(status_code=404, detail="未找到已保存的行程")

    try:
        validated_plan = TripPlan.model_validate(trip_plan)
    except Exception as e:
        logger.error(f"[Trip Result] 已保存行程格式无效 | session={session_id}: {e}")
        raise HTTPException(status_code=500, detail="已保存的行程格式无效")

    return {
        "success": True,
        "data": validated_plan,
    }


@router.post("/items/modify", response_model=ItemModifyResponse)
async def modify_trip_item(request: ItemModifyRequest):
    """局部修改行程：删除或替换单个景点/酒店，重排路线，重算预算。"""
    try:
        validated_plan, persistence_saved, persistence_warning = (
            await modify_persisted_trip(request)
        )

        return ItemModifyResponse(
            success=True,
            message="修改成功" if persistence_saved else persistence_warning,
            trip_plan=validated_plan,
            changed_day_index=request.day_index,
            persistence_saved=persistence_saved,
            persistence_warning=persistence_warning,
        )
    except TripModificationError as exc:
        status_code = 404 if str(exc).startswith("未找到会话") else 400
        raise HTTPException(status_code=status_code, detail=str(exc))
    except Exception as e:
        logger.error(f"[Modify] 修改失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="修改失败，请稍后重试")


@router.post("/chat", response_model=ChatResponse)
async def chat_with_trip(request: ChatRequest):
    """多轮对话式行程修改：自然语言 → LLM 解析意图 → 复用 modify 流程。"""
    try:
        result = await handle_chat(
            session_id=request.session_id,
            message=request.message,
            llm_provider=request.llm_provider,
        )
    except ChatError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"[Chat] 处理失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="对话处理失败，请稍后重试")

    trip_plan_model: Optional[TripPlan] = None
    raw_plan = result.get("trip_plan")
    if raw_plan is not None:
        trip_plan_model = TripPlan.model_validate(raw_plan)

    return ChatResponse(
        success=True,
        reply=result.get("reply", ""),
        trip_plan=trip_plan_model,
        modified=bool(result.get("modified")),
        action=result.get("action", "answer"),
        persistence_saved=result.get("persistence_saved"),
        persistence_warning=result.get("persistence_warning"),
    )
