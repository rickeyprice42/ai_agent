from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Protocol

from ai_agent.types import Message, ModelResponse, ModelStreamChunk


class ProviderError(RuntimeError):
    def __init__(self, message: str, code: str = "provider_error") -> None:
        super().__init__(message)
        self.user_message = message
        self.code = code


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    streaming: bool = True
    tool_calling: bool = True
    structured_output: bool = True
    cancellation: bool = True
    vision: bool = False


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    name: str
    model: str
    capabilities: ProviderCapabilities


class BaseProvider(Protocol):
    @property
    def descriptor(self) -> ProviderDescriptor:
        ...

    def generate(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict],
    ) -> ModelResponse:
        ...

    def stream(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict],
    ) -> Iterator[ModelStreamChunk]:
        ...


ProviderFactory = Callable[[], BaseProvider]


class ProviderRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}

    def register(self, name: str, factory: ProviderFactory) -> None:
        normalized = name.strip().lower()
        if not normalized:
            raise ValueError("Provider name must not be empty.")
        self._factories[normalized] = factory

    def create(self, name: str) -> BaseProvider:
        normalized = name.strip().lower()
        try:
            factory = self._factories[normalized]
        except KeyError as exc:
            supported = ", ".join(sorted(self._factories)) or "<none>"
            raise ValueError(
                f"Неизвестный провайдер: {name}. Поддерживаются: {supported}."
            ) from exc
        return factory()


class ProviderRuntime:
    """Normalizes retries, streaming cleanup, and provider metadata."""

    RETRYABLE_CODES = {"timeout", "unavailable", "rate_limited"}

    def __init__(self, provider: BaseProvider, max_retries: int = 1) -> None:
        self.provider = provider
        self.max_retries = max(0, int(max_retries))

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self.provider.descriptor

    def generate(self, system_prompt: str, messages: list[Message], tools: list[dict]) -> ModelResponse:
        for attempt in range(self.max_retries + 1):
            try:
                return self.provider.generate(system_prompt, messages, tools)
            except ProviderError as exc:
                if attempt >= self.max_retries or exc.code not in self.RETRYABLE_CODES:
                    raise
        raise ProviderError("Провайдер не вернул ответ.", "provider_error")

    def stream(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict],
    ) -> Iterator[ModelStreamChunk]:
        for attempt in range(self.max_retries + 1):
            iterator = iter(self.provider.stream(system_prompt, messages, tools))
            emitted = False
            try:
                for chunk in iterator:
                    emitted = emitted or bool(chunk.delta or chunk.response is not None)
                    yield chunk
                return
            except ProviderError as exc:
                if emitted or attempt >= self.max_retries or exc.code not in self.RETRYABLE_CODES:
                    raise
            finally:
                close = getattr(iterator, "close", None)
                if close is not None:
                    close()
