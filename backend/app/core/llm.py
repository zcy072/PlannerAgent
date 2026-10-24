"""LLM 工厂模块 - 支持多模型切换"""

import os
from typing import Optional
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from loguru import logger

from .config import get_settings


def setup_langsmith():
    """设置 LangSmith 追踪"""
    settings = get_settings()

    if settings.langchain_tracing_v2 and settings.langchain_api_key:
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key
        os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project
        logger.info(f"[LangSmith] 已启用追踪，项目: {settings.langchain_project}")
    else:
        logger.info("[LangSmith] 未启用追踪")


def resolve_provider(provider: object = None) -> str:
    """Normalize a requested provider (str or enum); fall back to LLM_PROVIDER."""
    value = getattr(provider, "value", provider)
    return str(value) if value else get_settings().llm_provider


class LLMFactory:
    """LLM 工厂类，支持动态切换不同的 LLM 提供商"""

    _instances: dict = {}

    @classmethod
    def get_llm(
        cls,
        provider: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> BaseChatModel:
        """获取 LLM 实例"""
        settings = get_settings()
        provider = resolve_provider(provider)
        kwargs.setdefault("timeout", settings.llm_timeout_seconds)
        kwargs.setdefault("max_retries", settings.llm_max_retries)

        logger.info(f"[LLM] Provider: {provider}, Temperature: {temperature}, MaxTokens: {max_tokens}")

        extra = ",".join(f"{k}={v!r}" for k, v in sorted(kwargs.items()))
        cache_key = f"{provider}_{temperature}_{max_tokens}_{extra}"
        if cache_key in cls._instances:
            logger.debug("[LLM] 使用缓存的实例")
            return cls._instances[cache_key]

        llm = cls._create_llm(provider, settings, temperature, max_tokens, **kwargs)
        cls._instances[cache_key] = llm

        logger.info("[LLM] LLM 实例创建成功")
        return llm

    @classmethod
    def _create_llm(
        cls,
        provider: str,
        settings,
        temperature: float,
        max_tokens: int,
        **kwargs
    ) -> BaseChatModel:
        """创建 LLM 实例"""

        if provider == "deepseek":
            api_key = settings.deepseek_api_key
            if not api_key:
                raise ValueError("DeepSeek API Key 未配置，请检查 .env 文件中的 DEEPSEEK_API_KEY")

            logger.debug(f"[LLM] DeepSeek Base URL: {settings.deepseek_base_url}, Model: {settings.deepseek_model}")

            return ChatOpenAI(
                model=settings.deepseek_model,
                api_key=api_key,
                base_url=settings.deepseek_base_url,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )

        elif provider == "aliyun":
            api_key = settings.aliyun_dashscope_api_key
            if not api_key:
                raise ValueError("阿里云百炼 API Key 未配置，请检查 .env 文件中的 ALIYUN_DASHSCOPE_API_KEY")

            logger.debug(f"[LLM] 阿里云百炼 Model: {settings.aliyun_model}")

            return ChatOpenAI(
                model=settings.aliyun_model,
                api_key=api_key,
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )

        elif provider == "openai":
            api_key = settings.openai_api_key
            if not api_key:
                raise ValueError("OpenAI API Key 未配置，请检查 .env 文件中的 OPENAI_API_KEY")

            logger.debug(f"[LLM] OpenAI Base URL: {settings.openai_base_url}, Model: {settings.openai_model}")

            return ChatOpenAI(
                model=settings.openai_model,
                api_key=api_key,
                base_url=settings.openai_base_url,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )

        else:
            raise ValueError(f"不支持的 LLM 提供商: {provider}")

    @classmethod
    def get_available_providers(cls) -> list:
        """获取可用的 LLM 提供商列表"""
        settings = get_settings()
        providers = []

        if settings.deepseek_api_key:
            providers.append({"name": "deepseek", "model": settings.deepseek_model})
        if settings.aliyun_dashscope_api_key:
            providers.append({"name": "aliyun", "model": settings.aliyun_model})
        if settings.openai_api_key:
            providers.append({"name": "openai", "model": settings.openai_model})

        return providers


def get_llm(provider: Optional[str] = None, **kwargs) -> BaseChatModel:
    """获取 LLM 实例的便捷函数"""
    return LLMFactory.get_llm(provider, **kwargs)
