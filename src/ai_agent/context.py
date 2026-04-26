from __future__ import annotations

from dataclasses import dataclass

from ai_agent.action_log import ActionLogStore
from ai_agent.memory import MemoryStore
from ai_agent.tasks import TaskManager
from ai_agent.types import Message, Task


@dataclass(slots=True)
class AgentContext:
    system_prompt: str
    messages: list[Message]


class ContextBuilder:
    def __init__(
        self,
        memory: MemoryStore,
        tasks: TaskManager,
        action_log: ActionLogStore,
        recent_message_limit: int = 12,
        note_limit: int = 8,
        action_limit: int = 6,
    ) -> None:
        self.memory = memory
        self.tasks = tasks
        self.action_log = action_log
        self.recent_message_limit = recent_message_limit
        self.note_limit = note_limit
        self.action_limit = action_limit

    def build(self, base_system_prompt: str) -> AgentContext:
        system_parts = [
            base_system_prompt.strip(),
            self._memory_context(),
            self._task_context(),
            self._action_context(),
        ]
        system_prompt = "\n\n".join(part for part in system_parts if part)
        return AgentContext(
            system_prompt=system_prompt,
            messages=self.memory.recent_messages(limit=self.recent_message_limit),
        )

    def _memory_context(self) -> str:
        notes = self.memory.list_notes()[-self.note_limit :]
        if not notes:
            return ""

        lines = ["Relevant long-term memory:"]
        lines.extend(f"- {note}" for note in notes)
        return "\n".join(lines)

    def _task_context(self) -> str:
        task = self.tasks.active_task()
        if task is None:
            return "Current task context: no active or queued task."

        return "\n".join(
            [
                "Current task context:",
                _format_task_for_context(task),
                "When the user asks to continue work, use the current running step as the next focus.",
                "When a step is completed or fails, update the task step before moving on.",
            ]
        )

    def _action_context(self) -> str:
        logs = self.action_log.recent(limit=self.action_limit)
        if not logs:
            return ""

        lines = ["Recent agent actions:"]
        for log in reversed(logs):
            result = _shorten(log.result)
            lines.append(f"- {log.created_at}: {log.tool_name} -> {log.status}; result: {result}")
        return "\n".join(lines)


def _format_task_for_context(task: Task) -> str:
    lines = [
        f"- task_id: {task.id}",
        f"- status: {task.status}",
        f"- priority: {task.priority}",
        f"- goal: {task.description}",
    ]
    if task.result:
        lines.append(f"- result: {task.result}")
    if task.steps:
        lines.append("- steps:")
        for step in task.steps:
            result = f"; result: {step.result}" if step.result else ""
            lines.append(
                f"  {step.position}. step_id={step.id}; status={step.status}; "
                f"description={step.description}{result}"
            )
    return "\n".join(lines)


def _shorten(text: str, limit: int = 180) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3]}..."
