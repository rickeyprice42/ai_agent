from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Message:
    role: str
    content: str
    name: str | None = None


@dataclass(slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass(slots=True)
class TaskStep:
    id: str
    task_id: str
    description: str
    status: str = "pending"
    position: int = 0
    result: str | None = None


@dataclass(slots=True)
class Task:
    id: str
    user_id: str
    description: str
    status: str = "created"
    priority: int = 3
    result: str | None = None
    steps: list[TaskStep] = field(default_factory=list)
