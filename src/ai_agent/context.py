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
        summary_message_limit: int = 24,
        max_system_prompt_chars: int = 8000,
        max_message_chars: int = 3000,
        max_summary_chars: int = 1800,
    ) -> None:
        self.memory = memory
        self.tasks = tasks
        self.action_log = action_log
        self.recent_message_limit = recent_message_limit
        self.note_limit = note_limit
        self.action_limit = action_limit
        self.summary_message_limit = summary_message_limit
        self.max_system_prompt_chars = max_system_prompt_chars
        self.max_message_chars = max_message_chars
        self.max_summary_chars = max_summary_chars

    def build(self, base_system_prompt: str) -> AgentContext:
        system_parts = [
            _section("Core system instructions", base_system_prompt.strip()),
            self._conversation_summary_context(),
            self._memory_context(),
            self._task_context(),
            self._action_context(),
        ]
        system_prompt = "\n\n".join(part for part in system_parts if part)
        return AgentContext(
            system_prompt=_shorten(system_prompt, self.max_system_prompt_chars, preserve_lines=True),
            messages=self._recent_messages(),
        )

    def _recent_messages(self) -> list[Message]:
        messages = self.memory.recent_messages(limit=self.recent_message_limit)
        return [
            Message(
                role=message.role,
                content=_shorten(message.content, self.max_message_chars, preserve_lines=True),
                name=message.name,
            )
            for message in messages
        ]

    def _conversation_summary_context(self) -> str:
        messages = self.memory.messages_before_recent(
            recent_limit=self.recent_message_limit,
            older_limit=self.summary_message_limit,
        )
        if not messages:
            return ""

        lines = []
        for message in messages:
            role = message.name or message.role
            content = _shorten(message.content, 220)
            if content:
                lines.append(f"- {role}: {content}")
        return _section(
            "Conversation summary from older messages",
            _shorten("\n".join(lines), self.max_summary_chars, preserve_lines=True),
        )

    def _memory_context(self) -> str:
        memories = self.memory.relevant_memories(self._memory_query(), limit=self.note_limit)
        if not memories:
            return ""

        lines = []
        lines.extend(f"- [{memory.kind}] {memory.content}" for memory in memories)
        return _section("Relevant long-term memory", "\n".join(lines))

    def _memory_query(self) -> str:
        parts: list[str] = []
        parts.extend(message.content for message in self._recent_messages()[-3:])
        task = self.tasks.active_task()
        if task is not None:
            parts.append(task.description)
            if task.result:
                parts.append(task.result)
            parts.extend(step.description for step in task.steps if step.status in {"pending", "running", "blocked"})
        return " ".join(parts)

    def _task_context(self) -> str:
        task = self.tasks.active_task()
        if task is None:
            return _section("Current task context", "No active or queued task.")

        return _section(
            "Current task context",
            "\n".join(
                [
                    _format_task_for_context(task),
                    "When the user asks to continue work, use the current running step as the next focus.",
                    "When a step is completed or fails, update the task step before moving on.",
                ]
            ),
        )

    def _action_context(self) -> str:
        logs = self.action_log.recent(limit=self.action_limit)
        if not logs:
            return ""

        lines = []
        for log in reversed(logs):
            result = _shorten(log.result)
            lines.append(f"- {log.created_at}: {log.tool_name} -> {log.status}; result: {result}")
        return _section("Recent agent actions", "\n".join(lines))


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


def _section(title: str, body: str) -> str:
    normalized_body = body.strip()
    if not normalized_body:
        return ""
    return f"## {title}\n{normalized_body}"


def _shorten(text: str, limit: int = 180, preserve_lines: bool = False) -> str:
    normalized = text.strip() if preserve_lines else " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3]}..."
