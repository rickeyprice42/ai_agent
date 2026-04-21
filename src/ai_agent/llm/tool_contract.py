from __future__ import annotations

import json


def render_tool_contract(system_prompt: str, tools: list[dict]) -> str:
    examples = [
        {"type": "message", "text": "Короткий ответ пользователю"},
        {"type": "tool_call", "name": "get_time", "arguments": {}},
        {
            "type": "tool_calls",
            "calls": [{"name": "remember_note", "arguments": {"note": "купить молоко"}}],
        },
    ]

    sections = [
        "You are the model layer for a personal AI agent.",
        "Return exactly one valid JSON object.",
        "Do not add markdown, explanations, comments, or code fences.",
        "Use only tools from the provided list.",
        "If a tool can answer more reliably than free text, call the tool.",
        "If the latest conversation entry is a tool result, answer with type=message and summarize the result for the user.",
        "If no tool is needed, answer with type=message.",
        f"System prompt: {system_prompt}",
        "Allowed output examples:",
        *[json.dumps(example, ensure_ascii=False) for example in examples],
        "Available tools:",
        json.dumps(tools, ensure_ascii=False, indent=2),
    ]
    return "\n".join(sections)
