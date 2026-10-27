from __future__ import annotations

from collections.abc import Iterator
import logging

from ai_agent.llm.base import BaseProvider, ProviderDescriptor, ProviderError
from ai_agent.types import Message, ModelResponse, ModelStreamChunk


class FailoverProvider:
    """Uses the next configured provider only when the current provider fails safely."""

    def __init__(self, providers: list[BaseProvider]) -> None:
        if not providers:
            raise ValueError("FailoverProvider requires at least one provider.")
        self.providers = providers
        self.logger = logging.getLogger("avelin.providers")

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self.providers[0].descriptor

    def generate(
        self, system_prompt: str, messages: list[Message], tools: list[dict]
    ) -> ModelResponse:
        last_error: ProviderError | None = None
        for index, provider in enumerate(self.providers):
            try:
                return provider.generate(system_prompt, messages, tools)
            except ProviderError as exc:
                last_error = exc
                self._record(index, provider, exc)
        raise last_error or ProviderError("Все LLM-провайдеры недоступны.", "unavailable")

    def stream(
        self, system_prompt: str, messages: list[Message], tools: list[dict]
    ) -> Iterator[ModelStreamChunk]:
        last_error: ProviderError | None = None
        for index, provider in enumerate(self.providers):
            iterator = iter(provider.stream(system_prompt, messages, tools))
            emitted = False
            try:
                for chunk in iterator:
                    emitted = emitted or bool(chunk.delta or chunk.response is not None)
                    yield chunk
                return
            except ProviderError as exc:
                last_error = exc
                self._record(index, provider, exc)
                if emitted:
                    raise
            finally:
                close = getattr(iterator, "close", None)
                if close is not None:
                    close()
        raise last_error or ProviderError("Все LLM-провайдеры недоступны.", "unavailable")

    def _record(self, index: int, provider: BaseProvider, exc: ProviderError) -> None:
        self.logger.warning(
            "provider failed; fallback=%s provider=%s model=%s code=%s",
            index + 1 < len(self.providers),
            provider.descriptor.name,
            provider.descriptor.model,
            exc.code,
        )
