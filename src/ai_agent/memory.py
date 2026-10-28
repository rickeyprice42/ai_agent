from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import time

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
    score: float = 0.0
    memory_id: str | None = None
    confidence: float = 1.0
    updated_at: str | None = None
    provenance: tuple[str, ...] = field(default_factory=tuple)
    conflict_ids: tuple[str, ...] = field(default_factory=tuple)


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
        self._last_retrieval_trace: dict = {}
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
        note_id = self.database.add_note(
            note,
            user_id=self.user_id,
            project_id=str(project_id) if normalized_scope == "current_project" and project_id else None,
            source_thread_id=self.thread_id if normalized_scope == "current_chat" else None,
        )
        record_scope = "chat" if normalized_scope == "current_chat" else (
            "project" if normalized_scope == "current_project" and project_id else "global"
        )
        self.database.create_memory_record(
            self.user_id,
            kind="fact",
            content=note,
            scope=record_scope,
            project_id=str(project_id) if normalized_scope == "current_project" and project_id else None,
            thread_id=self.thread_id if normalized_scope == "current_chat" else None,
            consent_granted=True,
            source_type="remember_note_tool",
            source_id=note_id,
        )
        return f"Запомнил: {note}"

    def list_notes(self) -> list[str]:
        return self.database.list_notes(user_id=self.user_id)

    def relevant_memories(self, query: str, limit: int = 8) -> list[MemoryItem]:
        started = time.perf_counter()
        safe_limit = max(1, min(int(limit), 50))
        query_terms = _terms(query)
        thread = self.current_thread()
        project_id = str(thread["project_id"]) if thread and thread.get("project_id") else None
        shared_project = self.shared_project()
        if shared_project is not None:
            candidates = []
            for item in self.database.list_shared_project_items(self.user_id, str(shared_project["id"])):
                if item["kind"] not in {"memory", "decision"}:
                    continue
                content = str(item["content"].get("text") or item["title"])
                memory = MemoryItem(
                    kind=str(item["kind"]), content=content, scope="shared_project",
                    memory_id=str(item["id"]), confidence=1.0,
                    updated_at=str(item["updated_at"]), provenance=("explicit_workspace_share",),
                )
                memory.score = _score_memory(memory, query_terms, 0.0)
                candidates.append(memory)
            candidates.sort(key=lambda item: (-item.score, item.updated_at or ""))
            return candidates[:safe_limit]
        persistent = self.database.search_memory_records(
            self.user_id,
            terms=sorted(query_terms),
            project_id=project_id,
            thread_id=self.thread_id if thread and thread.get("memory_enabled", True) else None,
            limit=max(safe_limit * 8, 40),
        )
        candidates: list[MemoryItem] = []
        seen_content: set[str] = set()
        for record in persistent:
            provenance = tuple(
                _format_provenance(source)
                for source in record.get("provenance", [])
                if source.get("source_type")
            )
            item = MemoryItem(
                kind=str(record["kind"]),
                content=str(record["content"]),
                scope=str(record["scope"]),
                memory_id=str(record["id"]),
                confidence=float(record["confidence"]),
                updated_at=str(record["updated_at"]),
                provenance=provenance,
            )
            item.score = _score_memory(item, query_terms, float(record.get("fts_rank", 0.0)))
            candidates.append(item)
            seen_content.add(_normalized_content(item.content))

        for item in self._memory_candidates():
            if _normalized_content(item.content) in seen_content:
                continue
            lexical_score = _legacy_score(item.content, query_terms)
            if lexical_score > 0:
                item.score = float(lexical_score)
                candidates.append(item)

        candidates.sort(
            key=lambda item: (item.score, item.confidence, item.updated_at or "", item.memory_id or ""),
            reverse=True,
        )
        selected = candidates[:safe_limit]
        _annotate_conflicts(selected)
        elapsed_ms = (time.perf_counter() - started) * 1000
        self._last_retrieval_trace = {
            "query_term_count": len(query_terms),
            "candidate_count": len(candidates),
            "result_count": len(selected),
            "estimated_tokens": sum(max(1, len(item.content.encode("utf-8")) // 4) for item in selected),
            "latency_ms": round(elapsed_ms, 3),
            "source_ids": [item.memory_id for item in selected if item.memory_id],
            "conflict_count": sum(1 for item in selected if item.conflict_ids),
        }
        self.database.set_metadata(
            f"memory_retrieval_trace:{self.user_id}:{self.thread_id}",
            json.dumps(self._last_retrieval_trace, ensure_ascii=False, sort_keys=True),
        )
        return selected

    def last_retrieval_trace(self) -> dict:
        if self._last_retrieval_trace:
            return dict(self._last_retrieval_trace)
        raw = self.database.get_metadata(
            f"memory_retrieval_trace:{self.user_id}:{self.thread_id}"
        )
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def append_message(self, message: Message) -> str:
        message_id = self.database.add_message(
            role=message.role,
            content=message.content,
            name=message.name,
            thread_id=self.thread_id,
            artifacts=message.artifacts,
            author_user_id=self.user_id if message.role == "user" else None,
        )
        self.database.auto_title_chat_thread(self.user_id, self.thread_id)
        return message_id

    def append_assistant_state(
        self,
        content: str,
        status: str,
        error: str | None,
        provider: str,
        model: str,
        artifacts: list[dict] | None = None,
    ) -> str:
        return self.database.add_message(
            role="assistant",
            content=content,
            thread_id=self.thread_id,
            status=status,
            error=error,
            provider=provider,
            model=model,
            artifacts=artifacts,
        )

    def update_message(
        self,
        message_id: str,
        *,
        content: str,
        status: str,
        error: str | None = None,
        artifacts: list[dict] | None = None,
    ) -> None:
        self.database.update_message(
            message_id,
            content=content,
            status=status,
            error=error,
            artifacts=artifacts,
        )

    def recent_messages(self, limit: int = 12) -> list[Message]:
        items = [
            item
            for item in self.database.list_messages(thread_id=self.thread_id)
            if item.get("status") == "complete" and not item.get("error")
        ][-limit:]
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
        items = [
            item
            for item in self.database.list_messages(thread_id=self.thread_id)
            if item.get("status") == "complete" and not item.get("error")
        ]
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
            notes=[] if self.shared_project() is not None else self.database.list_notes(user_id=self.user_id),
            history=self.database.list_messages(thread_id=self.thread_id),
        )

    def current_thread(self) -> dict | None:
        return self.database.get_chat_thread(self.user_id, self.thread_id)

    def shared_project(self) -> dict | None:
        thread = self.current_thread()
        if not thread or not thread.get("project_id"):
            return None
        project = self.database.get_project(self.user_id, str(thread["project_id"]))
        return project if project and project.get("shared") else None

    def remember_current_chat(self) -> str:
        if self.shared_project() is not None:
            return (
                "Общий чат не переносится в личную память. "
                "Поделитесь конкретным фактом через общую доску проекта."
            )
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
        if self.shared_project() is not None:
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
                if message.get("status") != "complete" or message.get("error"):
                    continue
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


def _legacy_score(content: str, query_terms: set[str]) -> int:
    if not query_terms:
        return 0
    content_terms = _terms(content)
    if not content_terms:
        return 0
    return len(query_terms & content_terms)


def _terms(text: str) -> set[str]:
    stop_words = {
        "and", "are", "for", "the", "what", "with", "как", "какие", "какой", "мои", "моя",
        "мне", "про", "что", "это",
    }
    raw_terms = {
        term.replace("ё", "е")
        for term in re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", text.casefold())
        if len(term) >= 3 and term not in stop_words
    }
    expanded = set(raw_terms)
    aliases = {
        "накопитель": {"ssd", "nvme", "диск", "storage"},
        "накопителя": {"ssd", "nvme", "диск", "storage"},
        "офис": {"office"},
        "город": {"живет", "проживает"},
        "городе": {"живет", "проживает"},
    }
    for term in raw_terms:
        expanded.update(aliases.get(term, set()))
    return {_term_stem(term) for term in expanded}


def _score_memory(item: MemoryItem, query_terms: set[str], fts_rank: float) -> float:
    content_terms = _terms(item.content)
    overlap = len(query_terms & content_terms)
    if overlap == 0:
        return 0.0
    coverage = overlap / max(len(query_terms), 1)
    scope_bonus = {"chat": 0.35, "project": 0.2, "global": 0.1}.get(item.scope, 0.0)
    provenance_bonus = min(len(item.provenance), 2) * 0.08
    freshness = _freshness_score(item.updated_at)
    fts_bonus = 1.0 / (1.0 + abs(fts_rank))
    return round(
        overlap * 2.0
        + coverage
        + item.confidence
        + scope_bonus
        + provenance_bonus
        + freshness
        + fts_bonus,
        6,
    )


def _freshness_score(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age_days = max((datetime.now(timezone.utc) - parsed).total_seconds() / 86400, 0.0)
    return 0.5 / (1.0 + age_days / 30.0)


def _term_stem(term: str) -> str:
    normalized = term.casefold().replace("ё", "е")
    if normalized.isascii():
        for suffix in ("ing", "ed", "es", "s"):
            if normalized.endswith(suffix) and len(normalized) - len(suffix) >= 4:
                return normalized[: -len(suffix)]
        return normalized
    for suffix in (
        "иями", "ями", "ами", "его", "ого", "ему", "ому", "аться", "яться",
        "ается", "яется", "ать", "ять", "ить", "ают", "яют", "ает", "яет",
        "ение", "ения", "енный", "анная", "ого", "ими", "ыми", "ую", "юю",
        "ый", "ий", "ой", "ая", "яя", "ое", "ее", "ые", "ие", "ом", "ем",
        "ов", "ев", "ам", "ям", "ах", "ях", "ет", "ит", "ся", "сь", "а", "я",
        "ы", "и", "у", "ю", "е", "о",
    ):
        if normalized.endswith(suffix) and len(normalized) - len(suffix) >= 3:
            return normalized[: -len(suffix)]
    return normalized


def _format_provenance(source: dict) -> str:
    label = str(source.get("source_type") or "unknown")
    reference = source.get("source_id") or source.get("source_uri")
    return f"{label}:{reference}" if reference else label


def _normalized_content(content: str) -> str:
    return " ".join(content.casefold().split())


def _conflict_key(content: str) -> frozenset[str]:
    positive = re.sub(r"\b(?:не|нет|никогда|not|never|dislike|ненавиж\w*)\b", " ", content.casefold())
    return frozenset(_terms(positive))


def _is_negative(content: str) -> bool:
    return bool(re.search(r"\b(?:не|нет|никогда|not|never|dislike|ненавиж\w*)\b", content.casefold()))


def _annotate_conflicts(items: list[MemoryItem]) -> None:
    conflicts: dict[int, list[str]] = {}
    for left_index, left in enumerate(items):
        if not left.memory_id:
            continue
        left_key = _conflict_key(left.content)
        for right_index in range(left_index + 1, len(items)):
            right = items[right_index]
            if not right.memory_id or left.kind != right.kind:
                continue
            right_key = _conflict_key(right.content)
            if left_key and left_key == right_key and _is_negative(left.content) != _is_negative(right.content):
                conflicts.setdefault(left_index, []).append(right.memory_id)
                conflicts.setdefault(right_index, []).append(left.memory_id)
    for index, conflict_ids in conflicts.items():
        items[index].conflict_ids = tuple(conflict_ids)


def _note_scope(note: dict, current_thread_id: str) -> str:
    if note.get("source_thread_id") == current_thread_id:
        return "current_chat"
    if note.get("project_id"):
        return "current_project"
    return "global"
