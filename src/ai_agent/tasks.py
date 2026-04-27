from __future__ import annotations

from ai_agent.database import AvelinDatabase
from ai_agent.types import Task, TaskStep


TASK_STATUSES = {"created", "planned", "executing", "completed", "failed", "blocked"}
STEP_STATUSES = {"pending", "running", "completed", "failed", "skipped", "blocked"}


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

    def active_task(self) -> Task | None:
        for task in self.list_tasks(limit=50):
            if task.status == "executing":
                return task
        return self.next_runnable_task()

    def next_runnable_task(self) -> Task | None:
        for task in self.list_tasks(limit=50):
            if task.status in {"created", "planned"}:
                return task
        return None

    def start_next_task(self) -> Task | None:
        task = self.next_runnable_task()
        if task is None:
            return None

        started = self.update_task(task.id, "executing")
        for step in started.steps:
            if step.status == "pending":
                return self.update_step(step.id, "running")
        return started

    def ensure_running_task(self) -> Task | None:
        active = self.active_task()
        if active is None:
            return None
        if active.status == "executing":
            running_step = self.running_step(active)
            if running_step is not None:
                return active
            for step in active.steps:
                if step.status == "pending":
                    return self.update_step(step.id, "running")
            return active
        return self.start_next_task()

    def running_step(self, task: Task | None = None) -> TaskStep | None:
        current_task = task or self.active_task()
        if current_task is None:
            return None
        for step in current_task.steps:
            if step.status == "running":
                return step
        return None

    def complete_running_step(self, result: str) -> Task:
        task = self.ensure_running_task()
        if task is None:
            raise ValueError("Нет активной задачи для выполнения.")

        step = self.running_step(task)
        if step is None:
            return self.update_task(task.id, "completed", result or "Задача завершена без шагов.")

        updated = self.update_step(step.id, "completed", result)
        for next_step in updated.steps:
            if next_step.status == "pending":
                return self.update_step(next_step.id, "running")

        return self.update_task(updated.id, "completed", "Все шаги выполнены.")

    def fail_running_step(self, result: str) -> Task:
        task = self.ensure_running_task()
        if task is None:
            raise ValueError("Нет активной задачи для выполнения.")

        step = self.running_step(task)
        if step is None:
            return self.update_task(task.id, "failed", result or "Задача завершилась ошибкой.")

        updated = self.update_step(step.id, "failed", result)
        return self.update_task(updated.id, "failed", result)

    def approve_blocked_step(self, step_id: str) -> Task:
        normalized_step_id = step_id.strip()
        if not normalized_step_id:
            raise ValueError("ID шага не должен быть пустым.")

        blocked_task = None
        blocked_step = None
        for task in self.list_tasks(limit=100):
            for step in task.steps:
                if step.id == normalized_step_id:
                    blocked_task = task
                    blocked_step = step
                    break
            if blocked_step is not None:
                break

        if blocked_task is None or blocked_step is None:
            raise ValueError("Шаг задачи не найден.")
        if blocked_task.status != "blocked" or blocked_step.status != "blocked":
            raise ValueError("Подтверждать можно только шаги из заблокированной задачи.")

        task = self.update_step(normalized_step_id, "running", "Шаг подтвержден пользователем.")
        return self.update_task(task.id, "executing")

    def block_running_step(self, result: str) -> Task:
        task = self.ensure_running_task()
        if task is None:
            raise ValueError("Нет активной задачи для выполнения.")

        step = self.running_step(task)
        if step is None:
            return self.update_task(task.id, "blocked", result or "Задача требует решения пользователя.")

        updated = self.update_step(step.id, "blocked", result)
        return self.update_task(updated.id, "blocked", result)

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
