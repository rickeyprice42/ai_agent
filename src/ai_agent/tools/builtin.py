from __future__ import annotations

from datetime import datetime

from ai_agent.memory import MemoryStore
from ai_agent.tools.base import Tool, ToolRegistry


def register_builtin_tools(registry: ToolRegistry, memory: MemoryStore) -> None:
    registry.register(
        Tool(
            name="get_time",
            description="Показывает текущие локальные дату и время.",
            schema={"type": "object", "properties": {}},
            handler=lambda _: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
    )

    registry.register(
        Tool(
            name="remember_note",
            description="Сохраняет заметку в памяти агента.",
            schema={
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Текст заметки"},
                },
                "required": ["note"],
            },
            handler=lambda args: memory.add_note(str(args.get("note", ""))),
        )
    )

    registry.register(
        Tool(
            name="recall_notes",
            description="Возвращает сохраненные заметки пользователя.",
            schema={"type": "object", "properties": {}},
            handler=lambda _: _format_notes(memory),
        )
    )


def _format_notes(memory: MemoryStore) -> str:
    notes = memory.list_notes()
    if not notes:
        return "В памяти пока ничего нет."
    formatted = "\n".join(f"{index}. {note}" for index, note in enumerate(notes, start=1))
    return f"Вот что я помню:\n{formatted}"
