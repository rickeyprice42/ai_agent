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
        {
            "type": "tool_call",
            "name": "plan_task",
            "arguments": {"goal": "подготовить отчет", "priority": 3},
        },
        {"type": "tool_call", "name": "read_file", "arguments": {"path": "notes/example.txt"}},
        {"type": "tool_call", "name": "list_workspace_files", "arguments": {"limit": 20}},
        {"type": "tool_call", "name": "open_workspace_folder", "arguments": {"path": ""}},
        {
            "type": "tool_call",
            "name": "write_file",
            "arguments": {"path": "notes/example.txt", "content": "Текст заметки", "overwrite": False},
        },
        {
            "type": "tool_call",
            "name": "create_docx",
            "arguments": {
                "path": "docs/report.docx",
                "title": "Отчет",
                "paragraphs": ["Первый абзац", "Текст с **жирным** и *курсивом*"],
                "bullets": ["Первый пункт", "Второй пункт"],
                "overwrite": False,
            },
        },
        {
            "type": "tool_call",
            "name": "append_docx",
            "arguments": {
                "path": "docs/report.docx",
                "paragraphs": ["Новый абзац", "Еще один абзац"],
                "bullets": ["Новый пункт"],
            },
        },
        {"type": "tool_call", "name": "run_shell", "arguments": {"command": "dir"}},
        {"type": "tool_call", "name": "http_request", "arguments": {"url": "https://example.com", "method": "GET"}},
        {"type": "tool_call", "name": "execute_next_step", "arguments": {}},
    ]

    sections = [
        "You are the model layer for a personal AI agent.",
        "Return exactly one valid JSON object.",
        "Do not add markdown, explanations, comments, or code fences.",
        "Use only tools from the provided list.",
        "If a tool can answer more reliably than free text, call the tool.",
        "If the user asks you to plan, organize, queue, or execute work, prefer task/planner tools.",
        "Use file tools only with relative paths provided by the user or by trusted task context.",
        "Use list_workspace_files when the user asks what files or documents you created.",
        "Use open_workspace_folder only when the user explicitly asks to open the working folder.",
        "Do not overwrite files unless the user explicitly asked to replace an existing file.",
        "If a tool result says confirmation is required, ask the user for explicit confirmation instead of retrying.",
        "Use create_docx when the user asks to create a Word/docx document.",
        "Use append_docx when the user asks to add text to an existing Word/docx document.",
        "Use run_shell only for allowed verification commands; never invent destructive or network shell commands.",
        "Use http_request only for simple GET/HEAD reads of user-provided public http/https URLs.",
        "When the user asks to continue or execute queued work, prefer execute_next_step.",
        "If the latest conversation entry is a tool result, answer with type=message and summarize the result for the user.",
        "If no tool is needed, answer with type=message.",
        "Agent instructions and runtime context:",
        system_prompt,
        "Allowed output examples:",
        *[json.dumps(example, ensure_ascii=False) for example in examples],
        "Available tools:",
        json.dumps(tools, ensure_ascii=False, indent=2),
    ]
    return "\n".join(sections)
