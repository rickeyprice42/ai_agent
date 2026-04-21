from __future__ import annotations

from typing import Protocol

from ai_agent.types import Message, ModelResponse


class BaseProvider(Protocol):
    def generate(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict],
    ) -> ModelResponse:
        ...
