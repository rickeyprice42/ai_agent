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
    scope: str = "global"
    score: int = 0


class MemoryStore:
    def __init__(
        self,
        database_path: Path,
        legacy_json_path: Path | None = None,
        user_id: str = "local-user",
        thread_id: str | None = None,
    ) -> None:
        self.database = AvelinDatabase(database_path)
        self.legacy_json_path = legacy_json_path
        self.user_id = user_id
        self.database.ensure_user_defaults(user_id)
        self.thread_id = thread_id or ("default-thread" if user_id == "local-user" else user_id)
        if self.database.get_chat_thread(user_id, self.thread_id) is None:
            self.thread_id = self.database.create_chat_thread(user_id)["id"]
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

    def add_note(self, note: str, scope: str = "global") -> str:
        note = note.strip()
        if not note:
            return "Пустую заметку сохранять не нужно."
        thread = self.current_thread()
        project_id = thread.get("project_id") if thread else None
        normalized_scope = scope.strip().lower()
        self.database.add_note(
            note,
            user_id=self.user_id,
            project_id=str(project_id) if normalized_scope == "current_project" and project_id else None,
            source_thread_id=self.thread_id if normalized_scope == "current_chat" else None,
        )
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
                scored.append(MemoryItem(kind=item.kind, content=item.content, scope=item.scope, score=score))

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
        self.database.auto_title_chat_thread(self.user_id, self.thread_id)

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

    def current_thread(self) -> dict | None:
        return self.database.get_chat_thread(self.user_id, self.thread_id)

    def remember_current_chat(self) -> str:
        result = self.database.remember_thread(self.user_id, self.thread_id)
        if result is None:
            return "Чат не найден или удален."
        if not result:
            return "В этом чате пока нет сообщений, которые можно запомнить."
        return f"Запомнил из этого чата: {result}"

    def _memory_candidates(self) -> list[MemoryItem]:
        thread = self.current_thread()
        if not thread or thread["deleted_at"] is not None or thread["archived_at"] is not None:
            return []

        project_id = str(thread["project_id"]) if thread.get("project_id") else None
        memory_enabled = bool(thread.get("memory_enabled", True))
        candidates: list[MemoryItem] = []

        for note in self.database.list_note_items(
            user_id=self.user_id,
            project_id=project_id,
            source_thread_id=self.thread_id if memory_enabled else None,
            include_global=True,
        ):
            candidates.append(
                MemoryItem(
                    kind="note",
                    content=str(note["content"]),
                    scope=_note_scope(note, self.thread_id),
                )
            )

        if memory_enabled:
            for message in self.database.list_messages(self.thread_id)[-20:]:
                content = str(message.get("content") or "").strip()
                if not content:
                    continue
                role = str(message.get("role") or "message")
                candidates.append(MemoryItem(kind=f"chat_{role}", content=content, scope="current_chat"))

        for task in self.database.list_tasks(self.user_id, limit=100):
            if project_id and task.get("project_id") not in {None, project_id}:
                continue
            status = str(task.get("status", ""))
            result = str(task.get("result") or "").strip()
            if status != "completed" or not result:
                continue
            description = str(task.get("description", "")).strip()
            content = f"{description}: {result}" if description else result
            candidates.append(
                MemoryItem(
                    kind="task_result",
                    content=content,
                    scope="current_project" if task.get("project_id") else "global",
                )
            )
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


def _note_scope(note: dict, current_thread_id: str) -> str:
    if note.get("source_thread_id") == current_thread_id:
        return "current_chat"
    if note.get("project_id"):
        return "current_project"
    return "global"
