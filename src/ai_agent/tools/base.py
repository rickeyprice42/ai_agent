from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from hashlib import sha256
from threading import Event, Lock
from time import perf_counter
from typing import Any, Callable, Literal
import inspect
import json
import logging
import re


ToolStatus = Literal["completed", "failed", "blocked", "cancelled", "not_found", "validation_error"]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 1
    retryable_codes: tuple[str, ...] = ("timeout", "temporarily_unavailable")


@dataclass(frozen=True, slots=True)
class ToolPermission:
    name: str
    risk: Literal["read", "write", "external", "high"] = "read"


@dataclass(frozen=True, slots=True)
class ToolError:
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolEvent:
    type: Literal["started", "retrying", "completed", "failed", "cancelled"]
    attempt: int
    elapsed_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class ToolResult:
    status: ToolStatus
    output: Any = None
    display_text: str = ""
    error: ToolError | None = None
    attempts: int = 1
    cached: bool = False
    events: tuple[ToolEvent, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    def text(self) -> str:
        if self.display_text:
            return self.display_text
        if self.error is not None:
            return self.error.message
        if isinstance(self.output, str):
            return self.output
        if self.output is None:
            return ""
        return json.dumps(self.output, ensure_ascii=False, sort_keys=True)

    def with_execution(self, *, attempts: int, cached: bool, events: list[ToolEvent]) -> "ToolResult":
        return ToolResult(
            status=self.status,
            output=self.output,
            display_text=self.display_text,
            error=self.error,
            attempts=attempts,
            cached=cached,
            events=tuple(events),
        )


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    cancellation: Event
    idempotency_key: str | None = None
    attempt: int = 1

    @property
    def cancelled(self) -> bool:
        return self.cancellation.is_set()


ToolHandler = Callable[..., str | ToolResult | Any]
ResultAdapter = Callable[[Any], ToolResult]
AvailabilityCheck = Callable[[], tuple[bool, str | None]]


@dataclass(frozen=True, slots=True)
class ToolManifest:
    name: str
    version: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    capabilities: tuple[str, ...]
    permissions: tuple[ToolPermission, ...]
    timeout_seconds: float
    retry_policy: RetryPolicy
    idempotent: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "capabilities": list(self.capabilities),
            "permissions": [
                {"name": permission.name, "risk": permission.risk}
                for permission in self.permissions
            ],
            "timeout_seconds": self.timeout_seconds,
            "retry_policy": {
                "max_attempts": self.retry_policy.max_attempts,
                "retryable_codes": list(self.retry_policy.retryable_codes),
            },
            "idempotent": self.idempotent,
        }


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    handler: ToolHandler
    version: str = "1.0.0"
    output_schema: dict[str, Any] = field(default_factory=lambda: {"type": "string"})
    capabilities: tuple[str, ...] = ()
    permissions: tuple[ToolPermission, ...] = ()
    timeout_seconds: float = 30.0
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    idempotent: bool = False
    result_adapter: ResultAdapter | None = None
    availability: AvailabilityCheck | None = None

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            version=self.version,
            description=self.description,
            input_schema=_strict_schema(self.schema),
            output_schema=self.output_schema,
            capabilities=self.capabilities,
            permissions=self.permissions,
            timeout_seconds=self.timeout_seconds,
            retry_policy=self.retry_policy,
            idempotent=self.idempotent,
        )


class ToolRegistry:
    def __init__(self, *, granted_permissions: set[str] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._granted_permissions = granted_permissions
        self._idempotency_cache: dict[tuple[str, str], tuple[str, ToolResult]] = {}
        self._cache_lock = Lock()
        self._logger = logging.getLogger("avelin.tools")

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Инструмент уже зарегистрирован: {tool.name}")
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", tool.name):
            raise ValueError(f"Недопустимое имя инструмента: {tool.name}")
        if not re.fullmatch(r"\d+\.\d+\.\d+", tool.version):
            raise ValueError(f"Версия инструмента должна быть SemVer: {tool.version}")
        _adapt_legacy_builtin(tool)
        _validate_schema_definition(tool.manifest.input_schema)
        _validate_schema_definition(tool.manifest.output_schema)
        self._tools[tool.name] = tool

    def has(self, name: str) -> bool:
        return name in self._tools and self._availability(name)[0]

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def get_manifest(self, name: str) -> dict[str, Any]:
        tool = self.get(name)
        available, reason = self._availability(name)
        payload = tool.manifest.as_dict()
        payload.update({"available": available, "unavailable_reason": reason})
        return payload

    def list_manifests(self, *, include_unavailable: bool = True) -> list[dict[str, Any]]:
        manifests = []
        for name in sorted(self._tools):
            manifest = self.get_manifest(name)
            if include_unavailable or manifest["available"]:
                manifests.append(manifest)
        return manifests

    def capabilities(self) -> dict[str, list[str]]:
        return {
            name: list(self._tools[name].capabilities)
            for name in sorted(self._tools)
            if self.has(name)
        }

    def list_for_model(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "schema": tool.manifest.input_schema,
            }
            for tool in self._tools.values()
            if self.has(tool.name)
        ]

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        if name not in self._tools:
            return {}, f"Инструмент '{name}' не найден."
        errors = validate_json_schema(arguments, self.get(name).manifest.input_schema)
        if errors:
            return {}, "Некорректные аргументы инструмента: " + "; ".join(errors)
        return dict(arguments), None

    def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        cancellation: Event | None = None,
    ) -> ToolResult:
        started = perf_counter()
        cancellation = cancellation or Event()
        if name not in self._tools:
            return _failure("not_found", f"Инструмент '{name}' не найден.", status="not_found")
        available, reason = self._availability(name)
        if not available:
            return _failure("unavailable", reason or "Инструмент недоступен.", status="blocked")
        normalized, validation_error = self.validate_arguments(name, arguments)
        if validation_error:
            return _failure("validation_error", validation_error, status="validation_error")
        if cancellation.is_set():
            return _failure("cancelled", "Выполнение инструмента отменено.", status="cancelled")

        tool = self.get(name)
        cache_key: tuple[str, str] | None = None
        fingerprint = _fingerprint(normalized)
        if idempotency_key:
            if not tool.idempotent:
                return _failure(
                    "idempotency_unsupported",
                    f"Инструмент '{name}' не объявлен идемпотентным.",
                )
            cache_key = (name, idempotency_key)
            with self._cache_lock:
                cached = self._idempotency_cache.get(cache_key)
            if cached is not None:
                cached_fingerprint, cached_result = cached
                if cached_fingerprint != fingerprint:
                    return _failure(
                        "idempotency_conflict",
                        "Этот idempotency key уже использован с другими аргументами.",
                    )
                return cached_result.with_execution(
                    attempts=cached_result.attempts,
                    cached=True,
                    events=list(cached_result.events),
                )

        events: list[ToolEvent] = []
        result = _failure("execution_failed", "Инструмент не выполнил действие.")
        attempts = max(1, tool.retry_policy.max_attempts)
        attempt = 0
        for attempt in range(1, attempts + 1):
            if cancellation.is_set():
                result = _failure("cancelled", "Выполнение инструмента отменено.", status="cancelled")
                events.append(ToolEvent("cancelled", attempt, _elapsed_ms(started)))
                break
            events.append(ToolEvent("started", attempt, _elapsed_ms(started)))
            result = self._invoke_once(tool, normalized, cancellation, idempotency_key, attempt)
            if result.ok:
                events.append(ToolEvent("completed", attempt, _elapsed_ms(started)))
                break
            retryable = (
                result.error is not None
                and result.error.retryable
                and result.error.code in tool.retry_policy.retryable_codes
                and attempt < attempts
            )
            if not retryable:
                events.append(
                    ToolEvent("cancelled" if result.status == "cancelled" else "failed", attempt, _elapsed_ms(started))
                )
                break
            events.append(ToolEvent("retrying", attempt, _elapsed_ms(started)))

        result = result.with_execution(attempts=attempt, cached=False, events=events)
        if cache_key is not None and result.ok:
            with self._cache_lock:
                self._idempotency_cache[cache_key] = (fingerprint, result)
        self._logger.info(
            "tool invocation name=%s version=%s status=%s attempts=%s cached=%s latency_ms=%.3f",
            name,
            tool.version,
            result.status,
            result.attempts,
            result.cached,
            _elapsed_ms(started),
        )
        return result

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """Compatibility adapter for older callers; new orchestrators should use invoke()."""
        return self.invoke(name, arguments).text()

    def _invoke_once(
        self,
        tool: Tool,
        arguments: dict[str, Any],
        cancellation: Event,
        idempotency_key: str | None,
        attempt: int,
    ) -> ToolResult:
        context = ToolExecutionContext(cancellation, idempotency_key, attempt)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"tool-{tool.name}")
        try:
            parameters = inspect.signature(tool.handler).parameters
            future = executor.submit(
                tool.handler,
                arguments,
                context,
            ) if len(parameters) >= 2 else executor.submit(tool.handler, arguments)
            try:
                raw = future.result(timeout=max(0.001, tool.timeout_seconds))
            except FutureTimeoutError:
                cancellation.set()
                future.cancel()
                return _failure(
                    "timeout",
                    f"Инструмент '{tool.name}' превысил таймаут {tool.timeout_seconds:g} сек.",
                    retryable=True,
                )
        except ValueError as exc:
            return _failure("invalid_operation", str(exc))
        except Exception as exc:
            self._logger.error(
                "tool handler failed name=%s error_type=%s",
                tool.name,
                type(exc).__name__,
            )
            return _failure("internal_error", "Инструмент завершился внутренней ошибкой.")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        if cancellation.is_set():
            return _failure("cancelled", "Выполнение инструмента отменено.", status="cancelled")
        result = tool.result_adapter(raw) if tool.result_adapter else _adapt_result(raw)
        if result.ok:
            output_errors = validate_json_schema(result.output, tool.manifest.output_schema)
            if output_errors:
                return _failure(
                    "invalid_output",
                    "Инструмент вернул данные, не соответствующие output schema.",
                    details={"errors": output_errors},
                )
        return result

    def _availability(self, name: str) -> tuple[bool, str | None]:
        tool = self._tools[name]
        if self._granted_permissions is not None:
            missing = sorted(
                permission.name
                for permission in tool.permissions
                if permission.name not in self._granted_permissions
            )
            if missing:
                return False, "Не выданы разрешения: " + ", ".join(missing)
        if tool.availability is not None:
            return tool.availability()
        return True, None


def validate_json_schema(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    errors: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: значение не входит в enum")
        return errors
    expected = schema.get("type")
    allowed = expected if isinstance(expected, list) else ([expected] if expected else [])
    if allowed and not any(_matches_type(value, item) for item in allowed):
        errors.append(f"{path}: ожидается {expected}, получено {_json_type(value)}")
        return errors

    if expected == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: обязательное поле отсутствует")
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                errors.extend(validate_json_schema(item, properties[key], f"{path}.{key}"))
            elif additional is False:
                errors.append(f"{path}.{key}: неизвестное поле")
            elif isinstance(additional, dict):
                errors.extend(validate_json_schema(item, additional, f"{path}.{key}"))
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errors.append(f"{path}: слишком мало полей")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            errors.append(f"{path}: слишком много полей")

    if expected == "array" and isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: слишком мало элементов")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: слишком много элементов")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(validate_json_schema(item, item_schema, f"{path}[{index}]"))

    if expected == "string" and isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: строка короче minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: строка длиннее maxLength")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path}: строка не соответствует pattern")

    numeric_schema = (
        expected in {"integer", "number"}
        if isinstance(expected, str)
        else isinstance(expected, list) and any(item in {"integer", "number"} for item in expected)
    )
    if numeric_schema and isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: значение меньше minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: значение больше maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: значение должно быть больше exclusiveMinimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: значение должно быть меньше exclusiveMaximum")
    return errors


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(schema))
    if copied.get("type") == "object":
        copied.setdefault("additionalProperties", False)
        for key, child in list(copied.get("properties", {}).items()):
            if isinstance(child, dict):
                copied["properties"][key] = _strict_schema(child)
    if copied.get("type") == "array" and isinstance(copied.get("items"), dict):
        copied["items"] = _strict_schema(copied["items"])
    return copied


def _validate_schema_definition(schema: dict[str, Any]) -> None:
    if not isinstance(schema, dict):
        raise ValueError("JSON Schema должна быть объектом.")
    allowed_types = {"object", "array", "string", "integer", "number", "boolean", "null"}
    schema_type = schema.get("type")
    values = schema_type if isinstance(schema_type, list) else [schema_type]
    if any(value is not None and value not in allowed_types for value in values):
        raise ValueError(f"Неподдерживаемый JSON Schema type: {schema_type}")
    if schema_type == "object" and not isinstance(schema.get("properties", {}), dict):
        raise ValueError("JSON Schema properties должна быть объектом.")
    if schema_type == "array" and "items" in schema and not isinstance(schema["items"], dict):
        raise ValueError("JSON Schema items должна быть объектом.")


def _adapt_result(raw: Any) -> ToolResult:
    if isinstance(raw, ToolResult):
        return raw
    return ToolResult(status="completed", output=raw, display_text=raw if isinstance(raw, str) else "")


def _adapt_legacy_builtin(tool: Tool) -> None:
    """Attach production metadata to pre-contract built-ins without rewriting handlers."""
    if not tool.capabilities:
        tool.capabilities = (_capability_for_name(tool.name),)
    if not tool.permissions:
        tool.permissions = (_permission_for_name(tool.name),)
    if tool.name in {"http_request", "web_search"}:
        tool.retry_policy = RetryPolicy(max_attempts=2)
        tool.timeout_seconds = min(tool.timeout_seconds, 20.0)
    elif tool.name == "run_shell":
        tool.retry_policy = RetryPolicy(max_attempts=2)
    if tool.result_adapter is None:
        tool.result_adapter = lambda raw, name=tool.name: _legacy_text_result(name, raw)


def _legacy_text_result(name: str, raw: Any) -> ToolResult:
    if isinstance(raw, ToolResult):
        return raw
    if not isinstance(raw, str):
        return _adapt_result(raw)
    if name == "http_request":
        match = re.search(r"^Status:\s*(\d{3})", raw, flags=re.MULTILINE)
        if match and int(match.group(1)) >= 400:
            status_code = int(match.group(1))
            return ToolResult(
                status="failed",
                output=raw,
                display_text=raw,
                error=ToolError(
                    "http_error",
                    f"HTTP request returned {status_code}.",
                    retryable=status_code >= 500,
                    details={"status": status_code},
                ),
            )
    if name == "run_shell":
        match = re.search(r"^Код возврата:\s*(-?\d+)", raw, flags=re.MULTILINE)
        if match and int(match.group(1)) != 0:
            code = int(match.group(1))
            return ToolResult(
                status="failed",
                output=raw,
                display_text=raw,
                error=ToolError("process_failed", f"Process exited with code {code}.", retryable=code == -1),
            )
    return ToolResult(status="completed", output=raw, display_text=raw)


def _capability_for_name(name: str) -> str:
    if name in {"read_file", "list_workspace_files", "open_workspace_folder"}:
        return "files.read"
    if name in {"write_file", "create_docx", "append_docx", "create_presentation"}:
        return "files.write"
    if name in {"web_search", "http_request"}:
        return "network.read"
    if name == "run_shell":
        return "process.execute"
    if "task" in name or name == "execute_next_step":
        return "tasks.manage"
    if name in {"remember_note", "remember_current_chat", "recall_notes"}:
        return "memory.manage"
    return "system.read"


def _permission_for_name(name: str) -> ToolPermission:
    if name in {"write_file", "create_docx", "append_docx", "create_presentation"}:
        return ToolPermission("workspace.write", "write")
    if name in {"web_search", "http_request"}:
        return ToolPermission("network.read", "read")
    if name == "run_shell":
        return ToolPermission("process.execute", "high")
    if name == "open_workspace_folder":
        return ToolPermission("desktop.open", "external")
    if name.startswith(("create_task", "update_task", "add_task", "execute_next", "start_next")):
        return ToolPermission("tasks.write", "write")
    if name in {"remember_note", "remember_current_chat"}:
        return ToolPermission("memory.write", "write")
    return ToolPermission("local.read", "read")


def failure_result(
    code: str,
    message: str,
    *,
    status: ToolStatus = "failed",
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> ToolResult:
    return _failure(code, message, status=status, retryable=retryable, details=details)


def _failure(
    code: str,
    message: str,
    *,
    status: ToolStatus = "failed",
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> ToolResult:
    return ToolResult(
        status=status,
        error=ToolError(code, message, retryable, details or {}),
        display_text=message,
    )


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, True)


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def _fingerprint(arguments: dict[str, Any]) -> str:
    payload = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)
