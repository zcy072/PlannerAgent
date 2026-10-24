"""FastAPI 主应用"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from loguru import logger

from ..core.config import get_settings
from ..core.llm import setup_langsmith
from ..workflows.trip.checkpoint import prune_stale_checkpoints
from ..workflows.trip.graph import close_trip_graph_resources
from ..services.amap_service import close_amap_service
from ..services.trip_result_service import prune_expired_results
from .routes import trip, map, config


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    settings = get_settings()

    # 启动时
    setup_langsmith()
    logger.info(f"{settings.app_name} v{settings.app_version} 启动")
    logger.info(f"LLM Provider: {settings.llm_provider}")
    logger.info(f"DeepSeek API Key: {'已配置' if settings.deepseek_api_key else '未配置'}")
    logger.info(f"阿里云 API Key: {'已配置' if settings.aliyun_dashscope_api_key else '未配置'}")
    logger.info(f"高德地图 API Key: {'已配置' if settings.amap_api_key else '未配置'}")
    logger.info(f"高德 MCP 路线校验: {'已配置' if settings.amap_mcp_api_key else '未配置（使用坐标估算）'}")
    logger.info(f"LangSmith 追踪: {'已启用' if settings.langchain_tracing_v2 else '未启用'}")

    # 清理失败只记录日志，不能阻止服务启动。
    try:
        await prune_stale_checkpoints(settings.checkpoint_retention_days)
        removed = await prune_expired_results(settings.result_retention_days)
        if removed:
            logger.info(f"已清理过期行程文件 {removed} 个")
    except Exception as exc:
        logger.warning("启动清理失败 | error_type={}", type(exc).__name__)

    try:
        yield
    finally:
        # 关闭时
        logger.info("应用正在关闭...")
        # 各资源独立关闭，前一个失败不能让后一个泄漏。
        for name, close in (
            ("行程图", close_trip_graph_resources),
            ("高德服务", close_amap_service),
        ):
            try:
                await close()
            except Exception as exc:
                logger.warning("关闭{}失败 | error_type={}", name, type(exc).__name__)


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="""
## 智能旅行助手

基于 LangGraph 构建的混合式智能旅行规划系统。

### 核心特性
- 混合工作流：POI、Hotel、Planner 等 LLM Agent 与天气工具、路线算法、规则评估节点协作
- LLM 负责语义决策，确定性节点负责事实获取、路线约束和结果检查
- 可选高德 MCP 路径校验：在本地路线排序后补充真实道路距离和通行时间，失败时自动回退坐标估算
- SSE 流式进度：实时推送工作流执行步骤
- Agent 可观测性：集成 LangSmith

### 混合规划流程
```
Weather Tool → POI Agent / Hotel Agent → Route Optimizer → MCP 路径校验（可选）
                                                           → Planner Agent → Rule Evaluator
```
        """,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc"
    )

    # CORS 来源由环境变量配置，便于本地端口调整时保持前后端一致。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.get_cors_origins_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    app.include_router(trip.router, prefix="/api")
    app.include_router(map.router, prefix="/api")
    app.include_router(config.router, prefix="/api")

    # 根路由
    @app.get("/")
    async def root():
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "status": "running",
            "docs": "/docs"
        }

    # 健康检查
    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    return app


app = create_app()
