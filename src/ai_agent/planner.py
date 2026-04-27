from __future__ import annotations

from dataclasses import dataclass, field
import re


@dataclass(slots=True)
class Plan:
    goal: str
    steps: list[str] = field(default_factory=list)
    priority: int = 3


class Planner:
    def create_plan(self, goal: str, priority: int = 3) -> Plan:
        normalized_goal = _normalize_goal(goal)
        if not normalized_goal:
            raise ValueError("Цель задачи не должна быть пустой.")

        extracted_steps = _extract_steps(normalized_goal)
        if extracted_steps:
            return Plan(
                goal=_extract_goal_without_steps(normalized_goal),
                steps=extracted_steps,
                priority=_normalize_priority(priority),
            )

        specialized_steps = _specialized_steps(normalized_goal)
        if specialized_steps:
            return Plan(
                goal=normalized_goal,
                steps=specialized_steps,
                priority=_normalize_priority(priority),
            )

        return Plan(
            goal=normalized_goal,
            steps=_default_steps(normalized_goal),
            priority=_normalize_priority(priority),
        )


def _normalize_goal(goal: str) -> str:
    cleaned = re.sub(r"\s+", " ", goal).strip()
    return cleaned.strip(" .")


def _extract_steps(text: str) -> list[str]:
    inline_numbered_steps = _extract_inline_numbered_steps(text)
    if len(inline_numbered_steps) >= 2:
        return inline_numbered_steps

    raw_lines = re.split(r"(?:\r?\n|;\s*)", text)
    steps: list[str] = []

    for line in raw_lines:
        cleaned = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
        if not cleaned:
            continue
        if _looks_like_instruction_list_item(line):
            steps.append(cleaned)

    if len(steps) >= 2:
        return _deduplicate_steps(steps)

    marker_match = re.search(
        r"(?:шаги|план|нужно сделать|задачи)\s*[:：]\s*(.+)",
        text,
        flags=re.IGNORECASE,
    )
    if not marker_match:
        return []

    candidates = re.split(r"\s*(?:,\s*затем\s*|,\s*потом\s*|;\s*|\s+->\s+)\s*", marker_match.group(1))
    steps = [candidate.strip(" .") for candidate in candidates if candidate.strip(" .")]
    return _deduplicate_steps(steps) if len(steps) >= 2 else []


def _extract_inline_numbered_steps(text: str) -> list[str]:
    matches = list(re.finditer(r"(?:^|\s)(\d+)[.)]\s+", text))
    if len(matches) < 2:
        return []

    steps: list[str] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        step = text[start:end].strip(" .")
        if step:
            steps.append(step)
    return _deduplicate_steps(steps)


def _looks_like_instruction_list_item(line: str) -> bool:
    return bool(re.match(r"^\s*(?:[-*]|\d+[.)])\s+\S+", line))


def _extract_goal_without_steps(text: str) -> str:
    first_numbered_step = re.search(r"(?:^|\s)\d+[.)]\s+", text)
    if first_numbered_step:
        goal = text[: first_numbered_step.start()].strip(" .:")
        if goal:
            return goal

    before_marker = re.split(
        r"(?:шаги|план|нужно сделать|задачи)\s*[:：]",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" .")
    return before_marker or text


def _default_steps(goal: str) -> list[str]:
    return [
        f"Уточнить ожидаемый результат: {goal}",
        "Разбить работу на конкретные действия и выбрать нужные инструменты",
        "Выполнить первый безопасный шаг",
        "Проверить результат и зафиксировать итог",
    ]


def _specialized_steps(goal: str) -> list[str]:
    docx_steps = _docx_steps(goal)
    if docx_steps:
        return docx_steps
    return []


def _docx_steps(goal: str) -> list[str]:
    if ".docx" not in goal.lower() and not re.search(r"\b(?:документ|word|docx)\b", goal, flags=re.IGNORECASE):
        return []

    path = _extract_docx_path(goal) or _slug_docx_path(goal)
    title = _extract_title(goal) or PathLikeTitle.from_path(path).title
    content = _extract_content(goal) or _docx_summary_content(goal)

    return [
        f"создай документ {path} с заголовком: {title} с текстом: {content}",
        "покажи файлы",
    ]


def _extract_docx_path(text: str) -> str | None:
    match = re.search(r"([A-Za-zА-Яа-я0-9_./\\-]+\.docx)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip().replace("\\", "/").strip("'\"`")


def _extract_title(text: str) -> str | None:
    match = re.search(
        r"(?:с заголовком|заголовок|title)\s*[:=]\s*(.+?)(?:\s+(?:с текстом|текст|content)\s*[:=]|$)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).strip(" .'\"`")


def _extract_content(text: str) -> str | None:
    match = re.search(r"(?:с текстом|текст|content)\s*[:=]\s*(.+)$", text, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip(" .'\"`")


def _docx_summary_content(goal: str) -> str:
    cleaned = re.sub(r"\s+", " ", goal).strip(" .")
    return f"Черновик документа по задаче: {cleaned}"


def _slug_docx_path(goal: str) -> str:
    words = re.findall(r"[A-Za-zА-Яа-я0-9]+", goal.lower())
    meaningful_words = [
        word
        for word in words
        if word not in {"создай", "сделай", "подготовь", "документ", "word", "docx", "файл"}
    ]
    slug = "-".join(meaningful_words[:4]) or "document"
    return f"documents/{slug}.docx"


@dataclass(slots=True)
class PathLikeTitle:
    title: str

    @classmethod
    def from_path(cls, path: str) -> "PathLikeTitle":
        stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        title = stem.replace("_", " ").replace("-", " ").strip().capitalize()
        return cls(title=title or "Документ")


def _deduplicate_steps(steps: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_steps: list[str] = []
    for step in steps:
        key = step.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_steps.append(step)
    return unique_steps


def _normalize_priority(priority: int) -> int:
    try:
        value = int(priority)
    except (TypeError, ValueError):
        value = 3
    return max(1, min(value, 5))
