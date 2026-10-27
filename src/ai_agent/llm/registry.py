from __future__ import annotations

from ai_agent.config import Settings
from ai_agent.llm.base import BaseProvider, ProviderRegistry, ProviderRuntime
from ai_agent.llm.mock_provider import MockProvider
from ai_agent.llm.openai_provider import OpenAIProvider
from ai_agent.llm.ollama_provider import OllamaProvider
from ai_agent.llm.failover import FailoverProvider


def build_provider_registry(settings: Settings) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register("mock", lambda: MockProvider(model=settings.model_name))
    registry.register(
        "ollama",
        lambda: OllamaProvider(
            model=settings.model_name,
            base_url=settings.ollama_url,
            timeout_seconds=settings.llm_timeout_seconds,
        ),
    )
    registry.register(
        "ollama_cloud",
        lambda: OllamaProvider(
            model=settings.model_name,
            base_url=settings.ollama_cloud_url,
            api_key=settings.ollama_api_key,
            timeout_seconds=settings.llm_timeout_seconds,
            provider_name="ollama_cloud",
        ),
    )
    registry.register(
        "openai",
        lambda: OpenAIProvider(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
        ),
    )
    return registry


def build_provider(settings: Settings) -> BaseProvider:
    registry = build_provider_registry(settings)
    provider = registry.create(settings.model_provider)
    if (
        settings.reasoning_fallback_enabled
        and settings.openai_api_key
        and settings.model_provider in {"ollama", "ollama_cloud"}
    ):
        provider = FailoverProvider([provider, registry.create("openai")])
    return ProviderRuntime(provider, max_retries=settings.llm_max_retries)
