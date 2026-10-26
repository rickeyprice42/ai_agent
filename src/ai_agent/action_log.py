from __future__ import annotations

from typing import Any
import re

from ai_agent.database import AvelinDatabase
from ai_agent.types import ActionLog


class ActionLogStore:
    def __init__(self, database: AvelinDatabase, user_id: str) -> None:
        self.database = database
        self.user_id = user_id

    def record(self, tool_name: str, status: str, arguments: dict, result: str) -> None:
        self.database.add_action_log(
            user_id=self.user_id,
            tool_name=tool_name,
            status=status,
            arguments=_redact_arguments(arguments),
            result=_redact_result(tool_name, result),
        )

    def recent(self, limit: int = 20) -> list[ActionLog]:
        limit = max(1, min(int(limit), 100))
        return [_action_log_from_row(row) for row in self.database.list_action_logs(self.user_id, limit=limit)]


def _action_log_from_row(row: dict) -> ActionLog:
    return ActionLog(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        tool_name=str(row["tool_name"]),
        status=str(row["status"]),
        arguments=dict(row.get("arguments", {})),
        result=str(row.get("result", "")),
        created_at=str(row.get("created_at", "")),
    )


_SENSITIVE_KEYS = {
    "authorization", "cookie", "content", "note", "paragraphs", "bullets",
    "password", "passcode", "token", "secret", "api_key", "apikey",
}
_MEMORY_TOOLS = {"remember_note", "remember_current_chat", "recall_notes"}
_SECRET_VALUE = re.compile(
    r"(?i)\b(?:api[ _-]?key|password|passcode|token|secret)\b\s*[:=]?\s*\S+"
)


def _redact_arguments(value: Any, key: str = "") -> Any:
    if key.casefold() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _redact_arguments(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact_arguments(item, key) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub("[REDACTED]", value)
    return value


def _redact_result(tool_name: str, result: str) -> str:
    if tool_name in _MEMORY_TOOLS or tool_name == "read_file":
        return "[REDACTED: tool output omitted from action log]"
    return _SECRET_VALUE.sub("[REDACTED]", result)
