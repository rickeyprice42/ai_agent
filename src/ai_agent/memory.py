from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json

from ai_agent.types import Message


@dataclass(slots=True)
class MemoryState:
    notes: list[str] = field(default_factory=list)
    history: list[dict[str, str | None]] = field(default_factory=list)


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.state = MemoryState()
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.state = MemoryState(
            notes=list(payload.get("notes", [])),
            history=list(payload.get("history", [])),
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(asdict(self.state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_note(self, note: str) -> str:
        note = note.strip()
        if not note:
            return "Пустую заметку сохранять не нужно."
        self.state.notes.append(note)
        self.save()
        return f"Запомнил: {note}"

    def list_notes(self) -> list[str]:
        return list(self.state.notes)

    def append_message(self, message: Message) -> None:
        self.state.history.append(
            {"role": message.role, "content": message.content, "name": message.name}
        )
        self.save()

    def recent_messages(self, limit: int = 12) -> list[Message]:
        items = self.state.history[-limit:]
        return [
            Message(
                role=str(item.get("role", "user")),
                content=str(item.get("content", "")),
                name=item.get("name"),
            )
            for item in items
        ]
