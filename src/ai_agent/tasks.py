from __future__ import annotations

from ai_agent.database import AvelinDatabase
from ai_agent.types import Task, TaskStep


TASK_STATUSES = {"created", "planned", "executing", "completed", "failed"}
STEP_STATUSES = {"pending", "running", "completed", "failed", "skipped"}


class TaskManager:
    def __init__(self, database: AvelinDatabase, user_id: str) -> None:
        self.database = database
        self.user_id = user_id

    def create_task(
        self,
        description: str,
        priority: int = 3,
        steps: list[str] | None = None,
    ) -> Task:
        description = description.strip()
        if not description:
            raise ValueError("Описание задачи не должно быть пустым.")

        task = self.database.create_task(
            user_id=self.user_id,
            description=description,
            priority=_normalize_priority(priority),
            steps=steps,
        )
        return _task_from_row(task)

    def list_tasks(self, limit: int = 20) -> list[Task]:
        limit = max(1, min(int(limit), 100))
        return [_task_from_row(item) for item in self.database.list_tasks(self.user_id, limit=limit)]

    def add_step(self, task_id: str, description: str) -> Task:
        description = description.strip()
        if not description:
            raise ValueError("Описание шага не должно быть пустым.")

        task = self.database.add_task_step(task_id=task_id.strip(), description=description)
        if task is None:
            raise ValueError("Задача не найдена.")
        return _task_from_row(task)

    def update_task(self, task_id: str, status: str, result: str | None = None) -> Task:
        normalized_status = status.strip().lower()
        if normalized_status not in TASK_STATUSES:
            raise ValueError(f"Неизвестный статус задачи: {status}.")

        task = self.database.update_task_status(
            task_id=task_id.strip(),
            status=normalized_status,
            result=result.strip() if isinstance(result, str) and result.strip() else None,
        )
        if task is None:
            raise ValueError("Задача не найдена.")
        return _task_from_row(task)

    def update_step(self, step_id: str, status: str, result: str | None = None) -> Task:
        normalized_status = status.strip().lower()
        if normalized_status not in STEP_STATUSES:
            raise ValueError(f"Неизвестный статус шага: {status}.")

        task = self.database.update_task_step_status(
            step_id=step_id.strip(),
            status=normalized_status,
            result=result.strip() if isinstance(result, str) and result.strip() else None,
        )
        if task is None:
            raise ValueError("Шаг задачи не найден.")
        return _task_from_row(task)


def _normalize_priority(priority: int) -> int:
    try:
        value = int(priority)
    except (TypeError, ValueError):
        value = 3
    return max(1, min(value, 5))


def _task_from_row(row: dict) -> Task:
    return Task(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        description=str(row["description"]),
        status=str(row["status"]),
        priority=int(row["priority"]),
        result=row.get("result"),
        steps=[_step_from_row(item) for item in row.get("steps", [])],
    )


def _step_from_row(row: dict) -> TaskStep:
    return TaskStep(
        id=str(row["id"]),
        task_id=str(row["task_id"]),
        description=str(row["description"]),
        status=str(row["status"]),
        position=int(row["position"]),
        result=row.get("result"),
    )
