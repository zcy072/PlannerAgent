"""核心配置模块"""

from typing import List
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseSettings):
    """应用配置"""

    # 应用信息
    app_name: str = "LangGraph 混合式智能旅行规划系统"
    app_version: str = "2.0.0"
    debug: bool = False

    # 服务器配置
    host: str = "0.0.0.0"
    port: int = 8100

    # CORS 配置
    cors_origins: str = "http://localhost:5180,http://127.0.0.1:5180,http://localhost:3000,http://127.0.0.1:3000"

    # LLM 配置
    llm_provider: str = "deepseek"  # deepseek / aliyun / openai
    # 单次请求超时与重试，避免卡住的上游长时间占用会话锁
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 1

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # 阿里云百炼
    aliyun_dashscope_api_key: str = ""
    aliyun_model: str = "qwen-plus"

    # OpenAI
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4-turbo-preview"

    # LangSmith
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "trip-planner-agent"

    # 高德地图
    amap_api_key: str = ""
    amap_mcp_api_key: str = ""
    amap_mcp_enabled: bool = False
    amap_mcp_url: str = "https://mcp.amap.com/mcp"
    amap_mcp_max_route_legs: int = 12
    # MCP is an optional verification layer; strict time limits keep it from
    # delaying the core itinerary workflow.
    amap_mcp_timeout_seconds: float = 15.0
    amap_mcp_call_timeout_seconds: float = 8.0

    # 高德预报覆盖不到的日期用 Open-Meteo（免费、无需 key）补充；失败只降级，不影响规划
    open_meteo_enabled: bool = True

    # 数据保留：checkpoint 只服务于失败续跑，默认保留 7 天；
    # 已保存行程 JSON 是用户数据，默认永久保留（0 表示不自动清理）。
    checkpoint_retention_days: int = 7
    result_retention_days: int = 0

    # 日志
    log_level: str = "INFO"

    model_config = {
        "env_file": ".env",
        "case_sensitive": False,
        "extra": "ignore"
    }

    def get_cors_origins_list(self) -> List[str]:
        """获取 CORS origins 列表"""
        return [origin.strip() for origin in self.cors_origins.split(",")]


settings = Settings()


def get_settings() -> Settings:
    """获取配置实例"""
    return settings
