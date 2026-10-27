from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ai_agent.database import AvelinDatabase


DEFAULT_AUTOMATION_BUDGET = {
    "max_steps": 20,
    "max_failures": 3,
    "max_seconds": 1800,
    "max_tokens": 50000,
    "max_cost_micros": 0,
}
MAX_ACTIVE_AUTOMATIONS = 20
MAX_PARALLEL_RUNS = 3
WEEKDAYS_RU = (
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
)


class AutomationService:
    """Persist schedules and enqueue durable work without executing the model."""

    def __init__(self, database: AvelinDatabase, user_id: str | None = None) -> None:
        self.database = database
        self.user_id = user_id

    def create(
        self,
        *,
        name: str,
        prompt: str,
        schedule_type: str,
        timezone_name: str,
        run_at: str | None = None,
        weekday: int | None = None,
        time_of_day: str | None = None,
        budget: dict | None = None,
        max_parallel_runs: int = 1,
    ) -> dict:
        user_id = self._require_user()
        if len(self.database.list_automations(user_id, include_deleted=False, active_only=True)) >= MAX_ACTIVE_AUTOMATIONS:
            raise ValueError(f"Можно включить не более {MAX_ACTIVE_AUTOMATIONS} автоматизаций.")
        normalized = _normalize_definition(
            name=name,
            prompt=prompt,
            schedule_type=schedule_type,
            timezone_name=timezone_name,
            run_at=run_at,
            weekday=weekday,
            time_of_day=time_of_day,
            budget=budget,
            max_parallel_runs=max_parallel_runs,
        )
        return self.database.create_automation(user_id=user_id, **normalized)

    def update(self, automation_id: str, changes: dict) -> dict:
        user_id = self._require_user()
        current = self.database.get_automation(user_id, automation_id)
        if current is None or current["status"] == "deleted":
            raise ValueError("Автоматизация не найдена.")
        schedule = current["schedule"]
        normalized = _normalize_definition(
            name=changes.get("name", current["name"]),
            prompt=changes.get("prompt", current["prompt"]),
            schedule_type=changes.get("schedule_type", current["schedule_type"]),
            timezone_name=changes.get("timezone", current["timezone"]),
            run_at=changes.get("run_at", schedule.get("run_at")),
            weekday=changes.get("weekday", schedule.get("weekday")),
            time_of_day=changes.get("time_of_day", schedule.get("time_of_day")),
            budget=changes.get("budget", current["budget"]),
            max_parallel_runs=changes.get("max_parallel_runs", current["max_parallel_runs"]),
        )
        return self.database.update_automation(user_id, automation_id, **normalized)

    def list(self, include_deleted: bool = False) -> list[dict]:
        return self.database.list_automations(self._require_user(), include_deleted=include_deleted)

    def pause(self, automation_id: str) -> dict:
        return self._set_status(automation_id, "paused")

    def resume(self, automation_id: str, now: datetime | None = None) -> dict:
        user_id = self._require_user()
        current = self.database.get_automation(user_id, automation_id)
        if current is None or current["status"] == "deleted":
            raise ValueError("Автоматизация не найдена.")
        if current["status"] == "completed":
            raise ValueError("Завершённую one-shot автоматизацию нельзя возобновить.")
        active = self.database.list_automations(user_id, include_deleted=False, active_only=True)
        if current["status"] != "active" and len(active) >= MAX_ACTIVE_AUTOMATIONS:
            raise ValueError(f"Можно включить не более {MAX_ACTIVE_AUTOMATIONS} автоматизаций.")
        reference = _as_utc(now or datetime.now(timezone.utc))
        next_run = _resume_next_run(current, reference)
        updated = self.database.set_automation_status(
            user_id, automation_id, "active", next_run_at=next_run.isoformat()
        )
        if updated is None:
            raise ValueError("Автоматизация не найдена.")
        return updated

    def delete(self, automation_id: str) -> dict:
        return self._set_status(automation_id, "deleted")

    def run_due(self, now: datetime | None = None, limit: int = 50) -> list[dict]:
        reference = _as_utc(now or datetime.now(timezone.utc))
        due = self.database.list_due_automations(reference.isoformat(), limit=limit, user_id=self.user_id)
        queued: list[dict] = []
        for automation in due:
            if self.database.count_active_automation_runs(automation["id"]) >= automation["max_parallel_runs"]:
                continue
            scheduled_for = automation["next_run_at"]
            if automation["schedule_type"] == "once":
                next_run_at = None
                terminal = True
            else:
                next_run_at = _next_weekly(automation["schedule"], automation["timezone"], reference)
                terminal = False
            rendered_prompt = _render_prompt(
                automation["prompt"], scheduled_for, automation["timezone"]
            )
            occurrence = self.database.enqueue_automation_occurrence(
                automation["user_id"],
                automation["id"],
                expected_next_run_at=scheduled_for,
                next_run_at=next_run_at.isoformat() if next_run_at else None,
                terminal=terminal,
                scheduled_for=scheduled_for,
                task_description=f"Автоматизация «{automation['name']}»: {rendered_prompt}",
                task_step=rendered_prompt,
                budget=automation["budget"],
            )
            if occurrence is None:
                continue
            self.database.create_in_app_notification(
                automation["user_id"],
                deduplication_key=f"automation-queued:{automation['id']}:{scheduled_for}",
                kind="automation_queued",
                title=f"Автоматизация поставлена в очередь: {automation['name']}",
                body="Durable worker выполнит задачу в пределах заданного budget.",
                entity_type="automation",
                entity_id=automation["id"],
            )
            queued.append(occurrence)
        return queued

    def _set_status(self, automation_id: str, status: str) -> dict:
        user_id = self._require_user()
        updated = self.database.set_automation_status(user_id, automation_id, status)
        if updated is None:
            raise ValueError("Автоматизация не найдена.")
        return updated

    def _require_user(self) -> str:
        if not self.user_id:
            raise ValueError("Для операции требуется пользователь.")
        return self.user_id


def _normalize_definition(
    *,
    name: str,
    prompt: str,
    schedule_type: str,
    timezone_name: str,
    run_at: str | None,
    weekday: int | None,
    time_of_day: str | None,
    budget: dict | None,
    max_parallel_runs: int,
) -> dict:
    clean_name = name.strip()
    clean_prompt = prompt.strip()
    if not clean_name or len(clean_name) > 120:
        raise ValueError("Название автоматизации должно содержать от 1 до 120 символов.")
    if not clean_prompt or len(clean_prompt) > 4000:
        raise ValueError("Задание автоматизации должно содержать от 1 до 4000 символов.")
    zone = _timezone(timezone_name)
    kind = schedule_type.strip().lower()
    now = datetime.now(timezone.utc)
    if kind == "once":
        if not run_at:
            raise ValueError("Для one-shot автоматизации укажите run_at.")
        local_run = _parse_local_datetime(run_at, zone)
        next_run = local_run.astimezone(timezone.utc)
        schedule = {"run_at": local_run.replace(tzinfo=None).isoformat(timespec="minutes")}
        human_rule = f"Один раз {local_run:%d.%m.%Y %H:%M} ({timezone_name})"
    elif kind == "weekly":
        if weekday is None or not 0 <= int(weekday) <= 6:
            raise ValueError("weekday должен быть числом от 0 (понедельник) до 6 (воскресенье).")
        normalized_time = _parse_time(time_of_day or "")
        schedule = {"weekday": int(weekday), "time_of_day": normalized_time.strftime("%H:%M")}
        next_run = _next_weekly(schedule, timezone_name, now)
        human_rule = f"Каждый {WEEKDAYS_RU[int(weekday)]} в {normalized_time:%H:%M} ({timezone_name})"
    else:
        raise ValueError("schedule_type должен быть once или weekly.")
    normalized_budget = _normalize_budget(budget or {})
    parallel = int(max_parallel_runs)
    if not 1 <= parallel <= MAX_PARALLEL_RUNS:
        raise ValueError(f"max_parallel_runs должен быть от 1 до {MAX_PARALLEL_RUNS}.")
    return {
        "name": clean_name,
        "prompt": clean_prompt,
        "schedule_type": kind,
        "schedule": schedule,
        "timezone_name": timezone_name,
        "human_rule": human_rule,
        "next_run_at": next_run.isoformat(),
        "budget": normalized_budget,
        "max_parallel_runs": parallel,
    }


def _normalize_budget(value: dict) -> dict[str, int]:
    result = dict(DEFAULT_AUTOMATION_BUDGET)
    limits = {
        "max_steps": (1, 100),
        "max_failures": (0, 20),
        "max_seconds": (30, 86400),
        "max_tokens": (0, 1000000),
        "max_cost_micros": (0, 1000000000),
    }
    for key, (minimum, maximum) in limits.items():
        if key in value:
            amount = int(value[key])
            if not minimum <= amount <= maximum:
                raise ValueError(f"{key} должен быть от {minimum} до {maximum}.")
            result[key] = amount
    return result


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Неизвестная timezone. Используйте IANA-имя, например Asia/Irkutsk.") from exc


def _parse_local_datetime(value: str, zone: ZoneInfo) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("run_at должен быть ISO datetime.") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _parse_time(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("time_of_day должен быть в формате HH:MM.") from exc
    return parsed.replace(second=0, microsecond=0, tzinfo=None)


def _next_weekly(schedule: dict, timezone_name: str, after: datetime) -> datetime:
    zone = _timezone(timezone_name)
    local_after = _as_utc(after).astimezone(zone)
    target_time = _parse_time(str(schedule["time_of_day"]))
    days = (int(schedule["weekday"]) - local_after.weekday()) % 7
    candidate_date = local_after.date() + timedelta(days=days)
    candidate = datetime.combine(candidate_date, target_time, zone)
    if candidate <= local_after:
        candidate = datetime.combine(candidate_date + timedelta(days=7), target_time, zone)
    return candidate.astimezone(timezone.utc)


def _resume_next_run(automation: dict, now: datetime) -> datetime:
    if automation["schedule_type"] == "weekly":
        return _next_weekly(automation["schedule"], automation["timezone"], now)
    scheduled = _parse_local_datetime(
        str(automation["schedule"]["run_at"]), _timezone(automation["timezone"])
    ).astimezone(timezone.utc)
    return max(scheduled, now)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _render_prompt(prompt: str, scheduled_for: str, timezone_name: str) -> str:
    scheduled = datetime.fromisoformat(scheduled_for)
    local = _as_utc(scheduled).astimezone(_timezone(timezone_name))
    return (
        prompt.replace("{date}", local.strftime("%Y-%m-%d"))
        .replace("{datetime}", local.strftime("%Y-%m-%d_%H-%M"))
    )
