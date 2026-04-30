from __future__ import annotations

from typing import Any


CONFIRMATION_MARKERS = (
    "confirm",
    "confirmed",
    "approve",
    "approved",
    "yes, do it",
    "overwrite",
    "replace",
    "подтверждаю",
    "разрешаю",
    "да, сделай",
    "перезапиши",
    "замени",
)

OPEN_FOLDER_MARKERS = (
    "open folder",
    "show folder",
    "open workspace",
    "открой папку",
    "покажи папку",
    "открой рабочую папку",
    "покажи рабочую папку",
)

DANGEROUS_STEP_MARKERS = (
    "delete",
    "remove",
    "erase",
    "rm ",
    "wipe",
    "clear",
    "удали",
    "удалить",
    "сотри",
    "стереть",
    "очисти",
    "очистить",
)


def tool_safety_block_reason(tool_name: str, arguments: dict[str, Any], user_text: str) -> str | None:
    normalized_user_text = _normalize(user_text)

    if tool_name == "open_workspace_folder" and not _has_marker(normalized_user_text, OPEN_FOLDER_MARKERS):
        return (
            "Действие требует подтверждения пользователя: открытие рабочей папки разрешено только "
            "после явной просьбы пользователя открыть или показать папку."
        )

    if tool_name in {"write_file", "create_docx"} and arguments.get("overwrite") is True:
        if not _has_marker(normalized_user_text, CONFIRMATION_MARKERS):
            return (
                "Действие требует подтверждения пользователя: инструмент запросил overwrite=true. "
                "Пользователь должен явно подтвердить замену существующего файла."
            )

    return None


def step_safety_block_reason(
    step_description: str,
    tool_name: str | None,
    arguments: dict[str, Any],
) -> str | None:
    normalized_step = _normalize(step_description)

    if _has_marker(normalized_step, DANGEROUS_STEP_MARKERS):
        return (
            "Шаг требует подтверждения пользователя: в описании есть потенциально "
            "разрушительное действие."
        )

    if tool_name in {"write_file", "create_docx"} and arguments.get("overwrite") is True:
        return "Шаг требует подтверждения пользователя: запись файла запрошена с overwrite=true."

    return None


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())
