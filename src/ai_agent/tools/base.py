from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


ToolHandler = Callable[[dict[str, Any]], str]


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def list_for_model(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "schema": tool.schema,
            }
            for tool in self._tools.values()
        ]

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        tool = self.get(name)
        schema = tool.schema
        if schema.get("type") != "object":
            return arguments, None

        if not isinstance(arguments, dict):
            return {}, "Аргументы инструмента должны быть JSON-объектом."

        properties = schema.get("properties", {})
        required = schema.get("required", [])
        normalized: dict[str, Any] = {}

        for key, value in arguments.items():
            if key not in properties:
                continue
            expected_type = properties[key].get("type")
            coerced_value, error_message = _coerce_value(expected_type, value, key)
            if error_message:
                return {}, error_message
            normalized[key] = coerced_value

        missing = [key for key in required if key not in normalized]
        if missing:
            return {}, f"Не хватает обязательных аргументов: {', '.join(missing)}."

        return normalized, None

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        return self.get(name).handler(arguments)


def _coerce_value(expected_type: str | None, value: Any, key: str) -> tuple[Any, str | None]:
    if expected_type in (None, "any"):
        return value, None

    if expected_type == "string":
        return value if isinstance(value, str) else str(value), None

    if expected_type == "integer":
        if isinstance(value, bool):
            return 0, f"Аргумент '{key}' должен быть integer, а не boolean."
        if isinstance(value, int):
            return value, None
        if isinstance(value, str):
            try:
                return int(value), None
            except ValueError:
                pass
        return 0, f"Аргумент '{key}' должен быть integer."

    if expected_type == "number":
        if isinstance(value, bool):
            return 0, f"Аргумент '{key}' должен быть number, а не boolean."
        if isinstance(value, (int, float)):
            return value, None
        if isinstance(value, str):
            try:
                return float(value), None
            except ValueError:
                pass
        return 0, f"Аргумент '{key}' должен быть number."

    if expected_type == "boolean":
        if isinstance(value, bool):
            return value, None
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes"}:
                return True, None
            if lowered in {"false", "0", "no"}:
                return False, None
        return False, f"Аргумент '{key}' должен быть boolean."

    if expected_type == "object":
        if isinstance(value, dict):
            return value, None
        return {}, f"Аргумент '{key}' должен быть object."

    if expected_type == "array":
        if isinstance(value, list):
            return value, None
        return [], f"Аргумент '{key}' должен быть array."

    return value, None
