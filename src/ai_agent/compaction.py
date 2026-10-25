from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Callable

from ai_agent.database import AvelinDatabase
from ai_agent.types import Message


DECISION_PATTERN = re.compile(
    r"(?:решил|решили|решено|выбрано|выбираем|будем|используем|принято|decision|decided|will use)",
    re.IGNORECASE,
)
GOAL_PATTERN = re.compile(
    r"(?:нужно|надо|хочу|давай|цель|сделай|создай|проверь|реализ|исправ|need|want|goal|please|implement|fix)",
    re.IGNORECASE,
)


class CompactionManager:
    """Creates reviewable extractive snapshots without deleting source messages."""

    def __init__(
        self,
        database: AvelinDatabase,
        *,
        user_id: str,
        thread_id: str,
        threshold: float = 0.75,
        keep_recent_messages: int = 12,
        minimum_source_messages: int = 6,
    ) -> None:
        self.database = database
        self.user_id = user_id
        self.thread_id = thread_id
        self.threshold = min(max(float(threshold), 0.25), 0.95)
        self.keep_recent_messages = max(2, int(keep_recent_messages))
        self.minimum_source_messages = max(2, int(minimum_source_messages))

    def maybe_create(
        self,
        rows: list[dict],
        *,
        count_message: Callable[[Message], int],
        input_budget_tokens: int,
        fixed_tokens: int,
    ) -> dict | None:
        complete = [
            row
            for row in rows
            if row.get("status") == "complete"
            and not row.get("error")
            and str(row.get("content") or "").strip()
        ]
        total_tokens = fixed_tokens + sum(
            count_message(_message_from_row(row)) for row in complete
        )
        if total_tokens < int(input_budget_tokens * self.threshold):
            return None

        snapshots = self.list(include_rolled_back=True)
        if any(snapshot["status"] == "pending_review" for snapshot in snapshots):
            return None
        covered = {
            message_id
            for snapshot in snapshots
            for message_id in snapshot["source_message_ids"]
        }
        protected = {
            str(row["id"])
            for row in complete[-self.keep_recent_messages :]
        }
        candidates = [
            row
            for row in complete
            if str(row["id"]) not in covered and str(row["id"]) not in protected
        ]
        if len(candidates) < self.minimum_source_messages:
            return None

        target_tokens = max(384, int(input_budget_tokens * 0.30))
        selected: list[dict] = []
        selected_tokens = 0
        for row in candidates:
            selected.append(row)
            selected_tokens += count_message(_message_from_row(row))
            if (
                len(selected) >= self.minimum_source_messages
                and selected_tokens >= target_tokens
            ):
                break

        source_ids = [str(row["id"]) for row in selected]
        artifacts = sorted(
            {
                str(artifact.get("path"))
                for row in selected
                for artifact in row.get("artifacts") or []
                if isinstance(artifact, dict) and artifact.get("path")
            }
        )
        source_hash = _source_hash(selected)
        return self.database.create_context_compaction(
            self.user_id,
            self.thread_id,
            summary=_structured_summary(selected),
            source_message_ids=source_ids,
            artifact_refs=artifacts,
            source_hash=source_hash,
        )

    def list(self, *, include_rolled_back: bool = True) -> list[dict]:
        return self.database.list_context_compactions(
            self.user_id,
            self.thread_id,
            include_rolled_back=include_rolled_back,
        )

    def get(self, compaction_id: str) -> dict | None:
        return self.database.get_context_compaction(
            self.user_id,
            self.thread_id,
            compaction_id,
        )

    def verify(self, compaction_id: str) -> dict | None:
        snapshot = self.get(compaction_id)
        if snapshot is None:
            return None
        if not self.source_hash_matches(compaction_id):
            raise ValueError("Исходные сообщения изменились; summary нельзя подтвердить без повторной проверки.")
        return self.database.set_context_compaction_status(
            self.user_id,
            self.thread_id,
            compaction_id,
            "verified",
        )

    def rollback(self, compaction_id: str) -> dict | None:
        return self.database.set_context_compaction_status(
            self.user_id,
            self.thread_id,
            compaction_id,
            "rolled_back",
        )

    def sources(self, compaction_id: str) -> list[dict] | None:
        return self.database.context_compaction_sources(
            self.user_id,
            self.thread_id,
            compaction_id,
        )

    def source_hash_matches(self, compaction_id: str) -> bool:
        snapshot = self.get(compaction_id)
        sources = self.sources(compaction_id)
        if snapshot is None or sources is None:
            return False
        return _source_hash(sources) == snapshot["source_hash"]

    def verified_source_ids(self) -> set[str]:
        return {
            message_id
            for snapshot in self.list(include_rolled_back=False)
            if snapshot["status"] == "verified"
            for message_id in snapshot["source_message_ids"]
        }

    def verified_context(self) -> str:
        snapshots = [
            snapshot
            for snapshot in self.list(include_rolled_back=False)
            if snapshot["status"] == "verified"
        ]
        if not snapshots:
            return ""
        lines = [
            "## Verified conversation compactions",
            "These are human-verified extractive snapshots. Message and file references point to preserved originals.",
        ]
        labels = {
            "goals": "Goals",
            "decisions": "Decisions",
            "facts": "Facts",
            "open_questions": "Open questions",
        }
        for snapshot in snapshots:
            lines.append(f"### Cycle {snapshot['cycle_number']} [{snapshot['source_hash'][:12]}]")
            summary = snapshot["summary"]
            for key, label in labels.items():
                items = summary.get(key) or []
                if not items:
                    continue
                lines.append(f"{label}:")
                for item in items:
                    refs = ", ".join(f"message:{value}" for value in item.get("message_ids") or [])
                    file_refs = ", ".join(f"file:{value}" for value in item.get("artifact_paths") or [])
                    suffix = "; ".join(value for value in (refs, file_refs) if value)
                    lines.append(f"- {item.get('text', '').strip()} [{suffix}]")
        return "\n".join(lines)

    def counts(self) -> dict[str, int]:
        snapshots = self.list(include_rolled_back=True)
        return {
            "total": len(snapshots),
            "pending_review": sum(item["status"] == "pending_review" for item in snapshots),
            "verified": sum(item["status"] == "verified" for item in snapshots),
            "rolled_back": sum(item["status"] == "rolled_back" for item in snapshots),
        }


def _structured_summary(rows: list[dict]) -> dict:
    sections: dict = {
        "goals": [],
        "decisions": [],
        "facts": [],
        "open_questions": [],
    }
    for row in rows:
        content = " ".join(str(row.get("content") or "").split())
        if not content:
            continue
        role = str(row.get("role") or "message")
        if role == "user" and (content.endswith("?") or "?" in content):
            section = "open_questions"
        elif role == "user" and GOAL_PATTERN.search(content):
            section = "goals"
        elif role in {"assistant", "tool"} and DECISION_PATTERN.search(content):
            section = "decisions"
        else:
            section = "facts"
        artifact_paths = [
            str(artifact.get("path"))
            for artifact in row.get("artifacts") or []
            if isinstance(artifact, dict) and artifact.get("path")
        ]
        sections[section].append(
            {
                "text": _excerpt(content),
                "message_ids": [str(row["id"])],
                "artifact_paths": artifact_paths,
            }
        )

    for key, items in sections.items():
        sections[key] = items[:8]
    sections["source_message_count"] = len(rows)
    sections["summary_method"] = "deterministic_extractive_v1"
    return sections


def _excerpt(text: str, limit: int = 360) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _message_from_row(row: dict) -> Message:
    return Message(
        role=str(row.get("role") or "message"),
        content=str(row.get("content") or ""),
        name=str(row["name"]) if row.get("name") else None,
    )


def _source_hash(rows: list[dict]) -> str:
    payload = [
        {
            "id": row["id"],
            "role": row["role"],
            "name": row.get("name"),
            "content": row["content"],
            "artifacts": row.get("artifacts") or [],
        }
        for row in rows
    ]
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
