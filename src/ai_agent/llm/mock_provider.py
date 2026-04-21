from __future__ import annotations

import re

from ai_agent.types import Message, ModelResponse, ToolCall


class MockProvider:
    def generate(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict],
    ) -> ModelResponse:
        _ = system_prompt, tools
        if not messages:
            return ModelResponse(text="Чем помочь?")

        last_message = messages[-1].content.strip()
        normalized = _normalized_variants(last_message)

        remember_match = None
        for candidate in normalized:
            remember_match = re.search(r"запомни[:\s]+(.+)", candidate, re.IGNORECASE)
            if remember_match:
                break
        if remember_match:
            return ModelResponse(
                tool_calls=[ToolCall(name="remember_note", arguments={"note": remember_match.group(1)})]
            )

        if any(
            phrase in candidate
            for candidate in normalized
            for phrase in ("что ты помнишь", "что у тебя в памяти", "напомни")
        ):
            return ModelResponse(tool_calls=[ToolCall(name="recall_notes")])

        if any(
            phrase in candidate
            for candidate in normalized
            for phrase in ("время", "дата", "сейчас")
        ):
            return ModelResponse(tool_calls=[ToolCall(name="get_time")])

        if messages[-1].role == "tool":
            return ModelResponse(text=f"Готово. {last_message}")

        return ModelResponse(
            text=(
                "Я готов как основа личного AI-агента. Уже умею хранить заметки, "
                "смотреть время и поддерживать структуру для будущего подключения настоящей модели."
            )
        )


def _normalized_variants(text: str) -> list[str]:
    variants = {text.lower()}
    repaired = _repair_mojibake(text)
    if repaired:
        variants.add(repaired.lower())
    return list(variants)


def _repair_mojibake(text: str) -> str | None:
    encodings = ("cp1251", "cp866", "latin-1")
    for encoding in encodings:
        try:
            repaired = text.encode(encoding).decode("utf-8")
        except UnicodeError:
            continue
        if repaired and repaired != text:
            return repaired
    return None
