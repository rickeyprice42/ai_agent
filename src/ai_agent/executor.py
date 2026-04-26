from __future__ import annotations

from dataclasses import dataclass, field
import re

from ai_agent.action_log import ActionLogStore
from ai_agent.tasks import TaskManager
from ai_agent.tools.base import ToolRegistry
from ai_agent.types import Task, TaskStep


@dataclass(slots=True)
class ExecutionDecision:
    tool_name: str | None
    arguments: dict = field(default_factory=dict)
    reason: str = ""


class ExecutionEngine:
    def __init__(
        self,
        tasks: TaskManager,
        tools: ToolRegistry,
        action_log: ActionLogStore,
    ) -> None:
        self.tasks = tasks
        self.tools = tools
        self.action_log = action_log

    def execute_next_step(self) -> str:
        task = self.tasks.ensure_running_task()
        if task is None:
            return "В очереди нет задач для выполнения."

        step = self.tasks.running_step(task)
        if step is None:
            completed = self.tasks.update_task(task.id, "completed", "Задача не содержит шагов.")
            return _format_task_state("Задача завершена без отдельных шагов.", completed)

        decision = decide_step_action(step)
        if decision.tool_name is None:
            result = f"Шаг обработан без инструмента: {decision.reason or step.description}"
            updated = self.tasks.complete_running_step(result)
            return _format_task_state(result, updated)

        if not self.tools.has(decision.tool_name):
            result = f"Инструмент недоступен для шага: {decision.tool_name}"
            self.action_log.record(decision.tool_name, "not_found", decision.arguments, result)
            updated = self.tasks.fail_running_step(result)
            return _format_task_state(result, updated)

        arguments, validation_error = self.tools.validate_arguments(decision.tool_name, decision.arguments)
        if validation_error:
            self.action_log.record(decision.tool_name, "validation_error", decision.arguments, validation_error)
            updated = self.tasks.fail_running_step(validation_error)
            return _format_task_state(validation_error, updated)

        tool_result = self.tools.execute(decision.tool_name, arguments)
        status = _tool_status(tool_result)
        self.action_log.record(decision.tool_name, status, arguments, tool_result)

        if status == "failed":
            updated = self.tasks.fail_running_step(tool_result)
            return _format_task_state(f"Шаг завершился ошибкой.\n{tool_result}", updated)

        updated = self.tasks.complete_running_step(tool_result)
        return _format_task_state(
            f"Шаг выполнен через {decision.tool_name}.\n{tool_result}",
            updated,
        )


def decide_step_action(step: TaskStep) -> ExecutionDecision:
    text = step.description.strip()
    lowered = text.lower()

    read_path = _extract_file_path(text, ("прочитай", "открой", "покажи", "read"))
    if read_path:
        return ExecutionDecision("read_file", {"path": read_path}, "read file requested")

    write_target = _extract_write_file(text)
    if write_target is not None:
        path, content, overwrite = write_target
        return ExecutionDecision(
            "write_file",
            {"path": path, "content": content, "overwrite": overwrite},
            "write file requested",
        )

    command = _extract_shell_command(text)
    if command:
        return ExecutionDecision("run_shell", {"command": command}, "shell verification requested")

    url = _extract_url(text)
    if url:
        method = "HEAD" if "head" in lowered else "GET"
        return ExecutionDecision("http_request", {"url": url, "method": method}, "http request requested")

    return ExecutionDecision(None, reason="нет подходящего безопасного инструмента; шаг отмечен как выполненный")


def _extract_file_path(text: str, verbs: tuple[str, ...]) -> str | None:
    pattern = r"(?:{})\s+(?:файл\s*)?:?\s*([^\n\r]+)".format("|".join(re.escape(verb) for verb in verbs))
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip().strip("'\"`")


def _extract_write_file(text: str) -> tuple[str, str, bool] | None:
    match = re.search(
        r"(?:запиши|создай)\s+файл\s+(.+?)\s+(?:с текстом|текст|content)\s*[:=]\s*(.+)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    path = match.group(1).strip().strip("'\"`")
    content = match.group(2).strip().strip("'\"`")
    overwrite = bool(re.search(r"\b(?:overwrite|перезапиши|замени)\b", text, flags=re.IGNORECASE))
    return path, content, overwrite


def _extract_shell_command(text: str) -> str | None:
    match = re.search(
        r"(?:запусти|выполни|проверь)\s+(?:команду|shell)\s*:?\s*(.+)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).strip().strip("'\"`")


def _extract_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s)>\]\"']+", text)
    if not match:
        return None
    return match.group(0).strip()


def _tool_status(tool_result: str) -> str:
    if "не смог выполнить действие" in tool_result:
        return "failed"
    if "Код возврата: -" in tool_result:
        return "failed"
    if "Код возврата: " in tool_result and "Код возврата: 0" not in tool_result:
        return "failed"
    return "completed"


def _format_task_state(message: str, task: Task) -> str:
    lines = [
        message,
        "",
        f"Задача {task.id}",
        f"Статус: {task.status}",
        f"Описание: {task.description}",
    ]
    if task.result:
        lines.append(f"Результат: {task.result}")
    if task.steps:
        lines.append("Шаги:")
        for step in task.steps:
            result = f" -> {step.result}" if step.result else ""
            lines.append(f"{step.position}. [{step.status}] {step.description}{result}")
    return "\n".join(lines)
