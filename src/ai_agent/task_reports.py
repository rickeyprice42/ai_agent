from __future__ import annotations

from datetime import date
from typing import Any

from ai_agent.database import AvelinDatabase


class TaskReportService:
    """Build evidence-backed reports without asking a model to summarize work."""

    def __init__(self, database: AvelinDatabase, user_id: str) -> None:
        self.database = database
        self.user_id = user_id

    def run_report(self, run_id: str) -> dict:
        run = self.database.get_task_run(self.user_id, run_id)
        if run is None:
            raise ValueError("Durable task run не найден.")
        task = self.database.get_task(run["task_id"])
        checkpoints = self.database.list_task_checkpoints(self.user_id, run_id, 500)
        payload = _report_payload(run, task, checkpoints)
        report = self.database.save_task_report(
            self.user_id,
            kind="run",
            report_date=date.today().isoformat(),
            run_id=run_id,
            task_id=run["task_id"],
            payload=payload,
        )
        if run["state"] in {"completed", "failed", "cancelled"}:
            self.database.create_in_app_notification(
                self.user_id,
                deduplication_key=f"run-report:{run_id}:{run['state']}",
                kind="task_report",
                title=f"Задача: {run['state']}",
                body=payload["summary"],
                entity_type="task_report",
                entity_id=report["id"],
            )
        return report

    def daily_report(self, report_date: str | None = None) -> dict:
        normalized_date = _valid_date(report_date or date.today().isoformat())
        runs = self.database.list_task_runs_for_date(self.user_id, normalized_date)
        run_reports = [
            _report_payload(
                run,
                self.database.get_task(run["task_id"]),
                self.database.list_task_checkpoints(self.user_id, run["id"], 500),
            )
            for run in runs
        ]
        completed = sum(len(item["completed_actions"]) for item in run_reports)
        verified = sum(len(item["checks"]) for item in run_reports)
        payload = {
            "summary": (
                f"За {normalized_date}: запусков {len(run_reports)}, "
                f"подтверждённых действий {completed}, проверок {verified}."
            ),
            "runs": run_reports,
            "completed_actions": [action for item in run_reports for action in item["completed_actions"]],
            "checks": [check for item in run_reports for check in item["checks"]],
            "commits": [commit for item in run_reports for commit in item["commits"]],
            "artifacts": _unique_dicts([artifact for item in run_reports for artifact in item["artifacts"]], "path"),
            "blockers": [blocker for item in run_reports for blocker in item["blockers"]],
            "budget": {
                "usage": _sum_maps(item["budget"]["usage"] for item in run_reports),
                "limits": [item["budget"]["limits"] for item in run_reports],
            },
            "next_actions": [item["next_action"] for item in run_reports if item["next_action"]],
            "evidence_only": True,
        }
        report = self.database.save_task_report(
            self.user_id,
            kind="daily",
            report_date=normalized_date,
            run_id=None,
            task_id=None,
            payload=payload,
        )
        self.database.create_in_app_notification(
            self.user_id,
            deduplication_key=f"daily-report:{normalized_date}",
            kind="daily_report",
            title=f"Ежедневный отчёт · {normalized_date}",
            body=payload["summary"],
            entity_type="task_report",
            entity_id=report["id"],
        )
        return report

    def list_reports(self, limit: int = 30) -> list[dict]:
        return self.database.list_task_reports(self.user_id, limit)


def _report_payload(run: dict, task: dict | None, checkpoints: list[dict]) -> dict:
    completed_actions = []
    checks = []
    commits = []
    artifacts = []
    blockers = []
    for checkpoint in checkpoints:
        evidence = checkpoint.get("evidence", {})
        reference = f"checkpoint:{checkpoint['id']}"
        if checkpoint["status"] == "completed" and evidence.get("verified") is True:
            action = {
                "reference": reference,
                "tool": checkpoint.get("tool_name"),
                "observation": checkpoint.get("observation", ""),
            }
            completed_actions.append(action)
            if checkpoint.get("tool_name") in {
                "run_code_check", "git_diff", "git_status", "file_metadata", "process_status"
            }:
                checks.append({**action, "evidence": evidence})
            if checkpoint.get("tool_name") == "git_commit":
                commits.append({**action, "evidence": evidence})
            for artifact in checkpoint.get("artifacts", []):
                if isinstance(artifact, dict) and artifact.get("path"):
                    artifacts.append({**artifact, "reference": reference})
        elif checkpoint["status"] in {"failed", "blocked", "waiting_approval", "denied"}:
            blockers.append(
                {
                    "reference": reference,
                    "status": checkpoint["status"],
                    "reason": checkpoint.get("observation", ""),
                }
            )

    if run.get("failure_reason") and not any(
        item["reason"] == run["failure_reason"] for item in blockers
    ):
        blockers.append({"reference": f"run:{run['id']}", "status": run["state"], "reason": run["failure_reason"]})
    next_action = next(
        (item.get("next_action") for item in reversed(checkpoints) if item.get("next_action")),
        _next_action_for_state(run["state"]),
    )
    return {
        "run_id": run["id"],
        "task_id": run["task_id"],
        "task": task.get("description", "") if task else "",
        "state": run["state"],
        "summary": (
            f"{task.get('description', 'Задача') if task else 'Задача'}: {run['state']}; "
            f"проверено действий {len(completed_actions)}, блокеров {len(blockers)}."
        ),
        "completed_actions": completed_actions,
        "checks": checks,
        "commits": commits,
        "artifacts": _unique_dicts(artifacts, "path"),
        "blockers": blockers,
        "budget": {"usage": run.get("usage", {}), "limits": run.get("budget", {})},
        "next_action": next_action,
        "evidence_only": True,
    }


def _next_action_for_state(state: str) -> str:
    return {
        "queued": "execute_next_step",
        "running": "wait_for_worker",
        "waiting_approval": "await_user_approval",
        "paused": "resume_or_cancel",
        "failed": "inspect_blockers",
        "completed": "none",
        "cancelled": "none",
    }.get(state, "inspect_run")


def _valid_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError("Дата отчёта должна быть в формате YYYY-MM-DD.") from exc


def _unique_dicts(items: list[dict], key: str) -> list[dict]:
    result: list[dict] = []
    seen: set[str] = set()
    for item in items:
        marker = str(item.get(key, ""))
        if marker and marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


def _sum_maps(values: Any) -> dict[str, int]:
    total: dict[str, int] = {}
    for value in values:
        for key, amount in value.items():
            total[key] = total.get(key, 0) + int(amount or 0)
    return total
