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

        read_file_match = None
        for candidate in normalized:
            read_file_match = re.search(
                r"(?:прочитай|открой|покажи)\s+файл[:\s]+(.+)",
                candidate,
                re.IGNORECASE,
            )
            if read_file_match:
                break
        if read_file_match:
            path = read_file_match.group(1).strip().strip("'\"`")
            return ModelResponse(tool_calls=[ToolCall(name="read_file", arguments={"path": path})])

        write_file_match = None
        for candidate in normalized:
            write_file_match = re.search(
                r"(?:запиши|создай)\s+файл[:\s]+(.+?)\s+(?:с текстом|текст|content)[:\s]+(.+)",
                candidate,
                re.IGNORECASE,
            )
            if write_file_match:
                break
        if write_file_match:
            path = write_file_match.group(1).strip().strip("'\"`")
            content = write_file_match.group(2).strip().strip("'\"`")
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        name="write_file",
                        arguments={"path": path, "content": content, "overwrite": False},
                    )
                ]
            )

        plan_match = None
        for candidate in normalized:
            plan_match = re.search(
                r"(?:спланируй|составь план|запланируй|разбей на шаги)[:\s]+(.+)",
                candidate,
                re.IGNORECASE,
            )
            if plan_match:
                break
        if plan_match:
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        name="plan_task",
                        arguments={
                            "goal": plan_match.group(1).strip(),
                            "priority": 3,
                        },
                    )
                ]
            )

        task_match = None
        for candidate in normalized:
            task_match = re.search(
                r"(?:создай|добавь|заведи)\s+(?:задачу|таск)[:\s]+(.+)",
                candidate,
                re.IGNORECASE,
            )
            if task_match:
                break
        if task_match:
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        name="create_task",
                        arguments={
                            "description": task_match.group(1).strip(),
                            "priority": 3,
                            "steps": [],
                        },
                    )
                ]
            )

        if any(
            phrase in candidate
            for candidate in normalized
            for phrase in ("покажи задачи", "список задач", "очередь задач", "какие задачи")
        ):
            return ModelResponse(tool_calls=[ToolCall(name="list_tasks", arguments={"limit": 20})])

        if any(
            phrase in candidate
            for candidate in normalized
            for phrase in ("запусти следующую задачу", "начни следующую задачу", "выполняй следующую задачу")
        ):
            return ModelResponse(tool_calls=[ToolCall(name="start_next_task")])

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
