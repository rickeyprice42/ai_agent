from __future__ import annotations

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
            arguments=arguments,
            result=result,
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
