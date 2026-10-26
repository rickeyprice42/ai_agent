from __future__ import annotations

from typing import Any

from ai_agent.tools.base import Tool, ToolError, ToolPermission, ToolRegistry, ToolResult
from ai_agent.tools.code_tools import CodeSandbox
from ai_agent.tools.files import FileSandbox
from ai_agent.tools.git_tools import GitSandbox


STRING = {"type": "string"}
PROCESS_PROPERTIES = {
    "command": {"type": "array", "items": STRING},
    "cwd": STRING,
    "return_code": {"type": ["integer", "null"]},
    "stdout": STRING,
    "stderr": STRING,
    "timed_out": {"type": "boolean"},
    "truncated": {"type": "boolean"},
    "duration_ms": {"type": "number"},
}


def register_coding_tool_pack(registry: ToolRegistry, files: FileSandbox) -> None:
    code = CodeSandbox(files.workspace_dir)
    git = GitSandbox(files.workspace_dir)

    registry.register(
        Tool(
            name="read_file_lines",
            description="Читает ограниченный диапазон строк UTF-8 файла и возвращает SHA-256 для безопасного edit.",
            schema={
                "type": "object",
                "properties": {
                    "path": STRING,
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                "required": ["path"],
            },
            output_schema=_object_schema(
                {
                    "path": STRING,
                    "sha256": STRING,
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "total_lines": {"type": "integer"},
                    "content": STRING,
                    "truncated": {"type": "boolean"},
                }
            ),
            capabilities=("files.read.lines",),
            permissions=(ToolPermission("workspace.read", "read"),),
            handler=lambda args: files.read_lines(
                str(args["path"]),
                int(args.get("start_line", 1)),
                int(args["end_line"]) if "end_line" in args else None,
            ),
        )
    )
    registry.register(
        Tool(
            name="file_metadata",
            description="Возвращает metadata и SHA-256 файла внутри workspace.",
            schema={"type": "object", "properties": {"path": STRING}, "required": ["path"]},
            output_schema=_object_schema(
                {
                    "path": STRING, "name": STRING, "extension": STRING,
                    "size_bytes": {"type": "integer"}, "modified_at": STRING, "sha256": STRING,
                }
            ),
            capabilities=("files.metadata",),
            permissions=(ToolPermission("workspace.read", "read"),),
            handler=lambda args: files.metadata(str(args["path"])),
        )
    )
    registry.register(
        Tool(
            name="search_workspace",
            description="Ищет Unicode-текст в именах и содержимом файлов workspace с ограниченным результатом.",
            schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 500},
                    "path": STRING,
                    "include_content": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["query"],
            },
            output_schema=_object_schema(
                {
                    "query": STRING, "root": STRING, "scanned_files": {"type": "integer"},
                    "matches": {
                        "type": "array",
                        "items": _object_schema(
                            {"path": STRING, "line": {"type": ["integer", "null"]}, "preview": STRING}
                        ),
                    },
                    "truncated": {"type": "boolean"},
                }
            ),
            capabilities=("files.search",),
            permissions=(ToolPermission("workspace.read", "read"),),
            handler=lambda args: files.search(
                str(args["query"]),
                str(args.get("path", "")),
                include_content=bool(args.get("include_content", True)),
                limit=int(args.get("limit", 50)),
            ),
        )
    )
    registry.register(
        Tool(
            name="edit_file_exact",
            description="Атомарно заменяет ровно один фрагмент, только если SHA-256 файла не изменился.",
            schema={
                "type": "object",
                "properties": {
                    "path": STRING, "expected_sha256": STRING,
                    "old_text": {"type": "string", "minLength": 1}, "new_text": STRING,
                },
                "required": ["path", "expected_sha256", "old_text", "new_text"],
            },
            output_schema=_object_schema(
                {
                    "path": STRING, "before_sha256": STRING, "after_sha256": STRING,
                    "replacements": {"type": "integer"}, "size_bytes": {"type": "integer"},
                }
            ),
            capabilities=("files.edit.exact",),
            permissions=(ToolPermission("workspace.write", "write"),),
            idempotent=True,
            handler=lambda args: files.exact_edit(
                str(args["path"]), str(args["expected_sha256"]),
                str(args["old_text"]), str(args["new_text"]),
            ),
        )
    )
    registry.register(
        Tool(
            name="move_file",
            description="Перемещает файл внутри workspace с SHA-256 precondition; перезапись выключена по умолчанию.",
            schema={
                "type": "object",
                "properties": {
                    "source": STRING, "destination": STRING, "expected_sha256": STRING,
                    "overwrite": {"type": "boolean"},
                },
                "required": ["source", "destination", "expected_sha256"],
            },
            output_schema=_object_schema(
                {"source": STRING, "destination": STRING, "sha256": STRING, "overwritten": {"type": "boolean"}}
            ),
            capabilities=("files.move",),
            permissions=(ToolPermission("workspace.write", "write"),),
            handler=lambda args: files.move_file(
                str(args["source"]), str(args["destination"]), str(args["expected_sha256"]),
                overwrite=bool(args.get("overwrite", False)),
            ),
        )
    )
    registry.register(
        Tool(
            name="delete_file",
            description="Перемещает файл в скрытую recoverable trash-зону; требует отдельного approval.",
            schema={
                "type": "object",
                "properties": {"path": STRING, "expected_sha256": STRING},
                "required": ["path", "expected_sha256"],
            },
            output_schema=_object_schema(
                {"path": STRING, "sha256": STRING, "recovery_path": STRING, "recoverable": {"type": "boolean"}}
            ),
            capabilities=("files.delete.recoverable",),
            permissions=(ToolPermission("workspace.delete", "high"),),
            handler=lambda args: files.trash_file(str(args["path"]), str(args["expected_sha256"])),
        )
    )
    registry.register(
        Tool(
            name="restore_file",
            description="Восстанавливает подтверждённый файл из recoverable trash в свободный workspace path.",
            schema={
                "type": "object",
                "properties": {
                    "recovery_path": STRING, "destination": STRING, "expected_sha256": STRING,
                },
                "required": ["recovery_path", "destination", "expected_sha256"],
            },
            output_schema=_object_schema(
                {
                    "recovery_path": STRING, "destination": STRING, "sha256": STRING,
                    "restored": {"type": "boolean"},
                }
            ),
            capabilities=("files.restore",),
            permissions=(ToolPermission("workspace.restore", "high"),),
            handler=lambda args: files.restore_file(
                str(args["recovery_path"]), str(args["destination"]), str(args["expected_sha256"])
            ),
        )
    )

    registry.register(
        Tool(
            name="inspect_project",
            description="Определяет языки, manifests, размер и Git-состояние проекта без запуска кода.",
            schema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 10, "maximum": 5000}},
            },
            output_schema=_object_schema(
                {
                    "root": STRING, "file_count": {"type": "integer"}, "total_bytes": {"type": "integer"},
                    "languages": {"type": "object", "additionalProperties": {"type": "integer"}},
                    "manifests": {"type": "array", "items": STRING},
                    "git_repository": {"type": "boolean"}, "truncated": {"type": "boolean"},
                }
            ),
            capabilities=("code.inspect",),
            permissions=(ToolPermission("workspace.read", "read"),),
            handler=lambda args: code.inspect_project(int(args.get("limit", 2000))),
        )
    )
    registry.register(
        Tool(
            name="run_code_check",
            description="Запускает только предопределённый test/lint/typecheck/build runner с timeout и bounded output.",
            schema={
                "type": "object",
                "properties": {
                    "runner": {
                        "type": "string",
                        "enum": ["python_unittest", "python_pytest", "python_compile", "npm_test", "npm_lint", "npm_typecheck", "npm_build"],
                    },
                    "target": STRING,
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
                },
                "required": ["runner"],
            },
            output_schema=_process_schema({"check": STRING, "target": STRING}),
            capabilities=("code.check", "process.execute.bounded"),
            permissions=(ToolPermission("process.execute", "high"),),
            result_adapter=_process_result,
            handler=lambda args: code.run_check(
                str(args["runner"]), str(args.get("target", "")), int(args.get("timeout_seconds", 60))
            ),
        )
    )
    registry.register(
        Tool(
            name="process_status",
            description="Показывает активные и последние bounded code-процессы без содержимого окружения.",
            schema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}},
            },
            output_schema=_object_schema(
                {
                    "active": {"type": "array", "items": {"type": "object"}},
                    "recent": {"type": "array", "items": _object_schema(PROCESS_PROPERTIES)},
                }
            ),
            capabilities=("process.status",),
            permissions=(ToolPermission("process.read", "read"),),
            handler=lambda args: code.process_status(int(args.get("limit", 10))),
        )
    )

    _register_git_tools(registry, git)


def _register_git_tools(registry: ToolRegistry, git: GitSandbox) -> None:
    read_tools = [
        ("git_status", "Показывает read-only Git status.", {"type": "object", "properties": {}}, lambda _args: git.status()),
        (
            "git_diff", "Показывает read-only Git diff.",
            {"type": "object", "properties": {"staged": {"type": "boolean"}, "path": STRING, "context_lines": {"type": "integer", "minimum": 0, "maximum": 20}}},
            lambda args: git.diff(staged=bool(args.get("staged", False)), path=str(args.get("path", "")), context_lines=int(args.get("context_lines", 3))),
        ),
        (
            "git_log", "Показывает bounded Git log.",
            {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}},
            lambda args: git.log(int(args.get("limit", 20))),
        ),
        ("git_branches", "Показывает локальные и remote Git-ветки.", {"type": "object", "properties": {}}, lambda _args: git.branches()),
        (
            "git_inspect_ref", "Показывает commit metadata для безопасного Git ref.",
            {"type": "object", "properties": {"ref": STRING}},
            lambda args: git.inspect_ref(str(args.get("ref", "HEAD"))),
        ),
    ]
    for name, description, schema, handler in read_tools:
        registry.register(
            Tool(
                name=name,
                description=description,
                schema=schema,
                output_schema=_process_schema({"operation": STRING}),
                capabilities=(f"git.{name.removeprefix('git_')}",),
                permissions=(ToolPermission("git.read", "read"),),
                result_adapter=_process_result,
                handler=handler,
            )
        )

    registry.register(
        Tool(
            name="git_stage",
            description="Добавляет только явно перечисленные workspace paths в Git index; требует approval.",
            schema={
                "type": "object",
                "properties": {"paths": {"type": "array", "minItems": 1, "maxItems": 100, "items": STRING}},
                "required": ["paths"],
            },
            output_schema=_process_schema({"operation": STRING}),
            capabilities=("git.stage",),
            permissions=(ToolPermission("git.write", "high"),),
            result_adapter=_process_result,
            handler=lambda args: git.stage([str(item) for item in args["paths"]]),
        )
    )
    registry.register(
        Tool(
            name="git_commit",
            description="Создаёт commit только из уже staged изменений; push не выполняется; требует approval.",
            schema={
                "type": "object",
                "properties": {"message": {"type": "string", "minLength": 1, "maxLength": 200}},
                "required": ["message"],
            },
            output_schema=_process_schema({"operation": STRING}),
            capabilities=("git.commit",),
            permissions=(ToolPermission("git.write", "high"),),
            result_adapter=_process_result,
            handler=lambda args: git.commit(str(args["message"])),
        )
    )
    registry.register(
        Tool(
            name="git_create_branch",
            description="Создаёт локальную Git-ветку без checkout и push; требует approval.",
            schema={
                "type": "object",
                "properties": {"name": STRING, "start_point": STRING},
                "required": ["name"],
            },
            output_schema=_process_schema({"operation": STRING}),
            capabilities=("git.branch.create",),
            permissions=(ToolPermission("git.write", "high"),),
            result_adapter=_process_result,
            handler=lambda args: git.create_branch(str(args["name"]), str(args.get("start_point", "HEAD"))),
        )
    )


def _object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _process_schema(extra: dict[str, Any]) -> dict[str, Any]:
    properties = {**PROCESS_PROPERTIES, **extra}
    return _object_schema(properties)


def _process_result(raw: Any) -> ToolResult:
    if not isinstance(raw, dict):
        return ToolResult(status="failed", error=ToolError("invalid_output", "Process result is not structured."))
    timed_out = bool(raw.get("timed_out"))
    return_code = raw.get("return_code")
    if timed_out or return_code not in {0}:
        code = "timeout" if timed_out else "process_failed"
        message = "Процесс остановлен по таймауту." if timed_out else f"Процесс завершился с кодом {return_code}."
        return ToolResult(
            status="failed",
            output=raw,
            display_text=message + "\n" + str(raw.get("stdout", "")) + str(raw.get("stderr", "")),
            error=ToolError(code, message, retryable=timed_out, details={"return_code": return_code}),
        )
    return ToolResult(status="completed", output=raw)
