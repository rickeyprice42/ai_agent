from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import re

from ai_agent.database import AvelinDatabase
from ai_agent.types import Message


@dataclass(slots=True)
class MemoryState:
    notes: list[str] = field(default_factory=list)
    history: list[dict[str, str | None]] = field(default_factory=list)


@dataclass(slots=True)
class MemoryItem:
    kind: str
    content: str
    score: int = 0


class MemoryStore:
    def __init__(
        self,
        database_path: Path,
        legacy_json_path: Path | None = None,
        user_id: str = "local-user",
    ) -> None:
        self.database = AvelinDatabase(database_path)
        self.legacy_json_path = legacy_json_path
        self.user_id = user_id
        self.thread_id = "default-thread" if user_id == "local-user" else user_id
        self.database.ensure_user_defaults(user_id)
        self.import_legacy_json()

    def import_legacy_json(self) -> None:
        if self.legacy_json_path is None or not self.legacy_json_path.exists():
            return

        import_key = f"legacy_memory_imported:{self.legacy_json_path.resolve()}"
        if self.user_id != "local-user":
            return

        if self.database.get_metadata(import_key) == "1" or self.database.has_memory_content(self.user_id):
            return

        payload = json.loads(self.legacy_json_path.read_text(encoding="utf-8"))

        for note in payload.get("notes", []):
            if isinstance(note, str) and note.strip():
                self.database.add_note(note.strip(), user_id=self.user_id)

        for item in payload.get("history", []):
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            self.database.add_message(
                role=str(item.get("role", "user")),
                content=content,
                name=item.get("name"),
                thread_id=self.thread_id,
            )

        self.database.set_metadata(import_key, "1")

    def add_note(self, note: str) -> str:
        note = note.strip()
        if not note:
            return "Пустую заметку сохранять не нужно."
        self.database.add_note(note, user_id=self.user_id)
        return f"Запомнил: {note}"

    def list_notes(self) -> list[str]:
        return self.database.list_notes(user_id=self.user_id)

    def relevant_memories(self, query: str, limit: int = 8) -> list[MemoryItem]:
        safe_limit = max(1, min(int(limit), 50))
        query_terms = _terms(query)
        candidates = self._memory_candidates()
        if not candidates:
            return []

        scored: list[MemoryItem] = []
        for item in candidates:
            score = _score_memory(item.content, query_terms)
            if score > 0:
                scored.append(MemoryItem(kind=item.kind, content=item.content, score=score))

        if scored:
            scored.sort(key=lambda item: item.score, reverse=True)
            return scored[:safe_limit]

        return candidates[-safe_limit:]

    def append_message(self, message: Message) -> None:
        self.database.add_message(
            role=message.role,
            content=message.content,
            name=message.name,
            thread_id=self.thread_id,
        )

    def recent_messages(self, limit: int = 12) -> list[Message]:
        items = self.database.list_messages(thread_id=self.thread_id)[-limit:]
        return [
            Message(
                role=str(item.get("role", "user")),
                content=str(item.get("content", "")),
                name=item.get("name"),
            )
            for item in items
        ]

    def messages_before_recent(self, recent_limit: int = 12, older_limit: int = 24) -> list[Message]:
        safe_recent_limit = max(0, int(recent_limit))
        safe_older_limit = max(1, int(older_limit))
        items = self.database.list_messages(thread_id=self.thread_id)
        if safe_recent_limit:
            items = items[:-safe_recent_limit]
        if not items:
            return []
        return [
            Message(
                role=str(item.get("role", "user")),
                content=str(item.get("content", "")),
                name=item.get("name"),
            )
            for item in items[-safe_older_limit:]
        ]

    def snapshot(self) -> MemoryState:
        return MemoryState(
            notes=self.database.list_notes(user_id=self.user_id),
            history=self.database.list_messages(thread_id=self.thread_id),
        )

    def _memory_candidates(self) -> list[MemoryItem]:
        candidates = [MemoryItem(kind="note", content=note) for note in self.list_notes()]
        for task in self.database.list_tasks(self.user_id, limit=100):
            status = str(task.get("status", ""))
            result = str(task.get("result") or "").strip()
            if status != "completed" or not result:
                continue
            description = str(task.get("description", "")).strip()
            content = f"{description}: {result}" if description else result
            candidates.append(MemoryItem(kind="task_result", content=content))
        return candidates


def _score_memory(content: str, query_terms: set[str]) -> int:
    if not query_terms:
        return 0
    content_terms = _terms(content)
    if not content_terms:
        return 0
    return len(query_terms & content_terms)


def _terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[A-Za-zА-Яа-я0-9]+", text.lower()) if len(term) >= 3}
