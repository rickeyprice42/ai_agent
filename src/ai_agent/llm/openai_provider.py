from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
import json
import socket
from urllib import error, request

from ai_agent.llm.base import ProviderCapabilities, ProviderDescriptor, ProviderError
from ai_agent.types import Message, ModelResponse, ModelStreamChunk, ToolCall


class OpenAIProvider:
    """Responses API adapter used as a reasoning fallback for Avelin."""

    def __init__(
        self,
        *,
        model: str = "gpt-6-luna",
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 120,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.url = f"{base_url.rstrip('/')}/responses"
        self.timeout_seconds = timeout_seconds

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            name="openai",
            model=self.model,
            capabilities=ProviderCapabilities(vision=True),
        )

    def generate(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict],
    ) -> ModelResponse:
        payload = self._payload(system_prompt, messages, tools)
        try:
            with request.urlopen(self._request(payload), timeout=self.timeout_seconds) as response:
                data = json.load(response)
        except json.JSONDecodeError as exc:
            raise ProviderError("OpenAI вернула некорректный JSON.", "invalid_response") from exc
        except Exception as exc:
            raise self._safe_error(exc) from None
        return self._model_response(data, tools)

    def stream(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict],
    ) -> Iterator[ModelStreamChunk]:
        payload = self._payload(system_prompt, messages, tools)
        payload["stream"] = True
        completed = False
        try:
            with request.urlopen(self._request(payload), timeout=self.timeout_seconds) as response:
                for event in _sse_events(response):
                    event_type = event.get("type")
                    if event_type == "response.output_text.delta":
                        delta = event.get("delta")
                        if isinstance(delta, str) and delta:
                            yield ModelStreamChunk(delta=delta)
                    elif event_type == "response.completed":
                        completed = True
                        yield ModelStreamChunk(response=self._model_response(event.get("response"), tools))
                        break
                    elif event_type in {"error", "response.failed", "response.incomplete"}:
                        raise ProviderError("OpenAI прервала генерацию ответа.", "incomplete_stream")
        except ProviderError:
            raise
        except Exception as exc:
            raise self._safe_error(exc) from None
        if not completed:
            raise ProviderError("Соединение с OpenAI прервалось до завершения ответа.", "incomplete_stream")

    def _payload(self, system_prompt: str, messages: list[Message], tools: list[dict]) -> dict:
        input_items: list[dict] = []
        for message in messages:
            if message.provider_items:
                input_items.extend(message.provider_items)
            if message.role == "assistant" and message.tool_calls:
                if message.content:
                    input_items.append({"role": "assistant", "content": message.content})
                for index, call in enumerate(message.tool_calls):
                    input_items.append({
                        "type": "function_call",
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        "call_id": call.call_id or f"avelin_call_{len(input_items)}_{index}",
                    })
                continue
            if message.role == "tool" and message.tool_call_id:
                input_items.append({"type": "function_call_output", "call_id": message.tool_call_id, "output": message.content})
                continue
            role = message.role if message.role in {"user", "assistant"} else "user"
            content = message.content
            if message.role == "tool":
                content = f"Tool output ({message.name or 'unknown'}):\n{content}"
            input_items.append({"role": role, "content": content})
        openai_tools = []
        for tool in tools:
            schema = deepcopy(tool.get("schema", {"type": "object", "properties": {}}))
            strict = not _has_open_objects(schema)
            openai_tools.append({
                "type": "function",
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": _strict_schema(schema) if strict else schema,
                "strict": strict,
            })
        payload: dict = {
            "model": self.model,
            "instructions": system_prompt,
            "input": input_items,
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if openai_tools:
            payload["tools"] = openai_tools
        return payload

    def _request(self, payload: dict):
        return request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

    def _model_response(self, data: dict, tools: list[dict] | None = None) -> ModelResponse:
        if not isinstance(data, dict) or not isinstance(data.get("output", []), list):
            raise ProviderError("OpenAI вернула некорректный ответ.", "invalid_response")
        if data.get("status") in {"incomplete", "failed", "cancelled"}:
            raise ProviderError("OpenAI не завершила ответ.", "incomplete_stream")
        schemas = {tool["name"]: tool.get("schema", {}) for tool in tools or []}
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        provider_items: list[dict] = []
        for item in data.get("output", []):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call":
                arguments = item.get("arguments") or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError as exc:
                        raise ProviderError("OpenAI вернула некорректные аргументы инструмента.", "invalid_tool_call") from exc
                if not isinstance(arguments, dict) or not str(item.get("name") or "").strip():
                    raise ProviderError("OpenAI вернула некорректный вызов инструмента.", "invalid_tool_call")
                tool_calls.append(
                    ToolCall(
                        name=str(item.get("name") or ""),
                        arguments=_restore_optional_arguments(arguments, schemas.get(str(item.get("name")), {})),
                        call_id=str(item.get("call_id") or item.get("id") or "") or None,
                    )
                )
            if item.get("type") == "reasoning":
                provider_items.append(item)
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if isinstance(content, dict) and content.get("type") in {
                        "output_text", "text"
                    }:
                        text_parts.append(str(content.get("text") or ""))
        if not text_parts and data.get("output_text"):
            text_parts.append(str(data["output_text"]))
        return ModelResponse(text="".join(text_parts).strip(), tool_calls=tool_calls, provider_items=provider_items)

    def _safe_error(self, exc: Exception) -> ProviderError:
        if isinstance(exc, error.HTTPError):
            if exc.code == 401:
                return ProviderError("OpenAI отклонила API-ключ.", "unauthorized")
            if exc.code == 404:
                return ProviderError(
                    f"Модель OpenAI '{self.model}' недоступна.", "model_not_found"
                )
            if exc.code == 429:
                return ProviderError("Лимит OpenAI исчерпан.", "rate_limited")
            return ProviderError(f"OpenAI вернула HTTP {exc.code}.", "http_error")
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return ProviderError(
                f"OpenAI не ответила за {self.timeout_seconds} секунд.", "timeout"
            )
        if isinstance(exc, (error.URLError, OSError)):
            return ProviderError("Не удалось подключиться к OpenAI.", "unavailable")
        return ProviderError("Не удалось получить ответ OpenAI.", "provider_error")


def _has_open_objects(schema: object) -> bool:
    if isinstance(schema, dict):
        if schema.get("type") == "object" and (schema.get("additionalProperties") is True or isinstance(schema.get("additionalProperties"), dict)):
            return True
        return any(_has_open_objects(value) for value in schema.values())
    if isinstance(schema, list):
        return any(_has_open_objects(value) for value in schema)
    return False


def _strict_schema(schema: dict) -> dict:
    """Adapt optional parameters to Responses strict mode without mutating registry schemas."""
    result = deepcopy(schema)
    if result.get("type") == "object":
        properties = result.get("properties", {})
        required = set(result.get("required", []))
        result["additionalProperties"] = False
        result["required"] = list(properties)
        result["properties"] = {}
        for key, value in properties.items():
            field = _strict_schema(value)
            if key not in required:
                field = {"anyOf": [field, {"type": "null"}]}
            result["properties"][key] = field
    if isinstance(result.get("items"), dict):
        result["items"] = _strict_schema(result["items"])
    for keyword in ("anyOf", "oneOf", "allOf"):
        if isinstance(result.get(keyword), list):
            result[keyword] = [_strict_schema(item) for item in result[keyword]]
    return result


def _restore_optional_arguments(value, schema: dict):
    if isinstance(value, dict):
        required = set(schema.get("required", []))
        properties = schema.get("properties", {})
        return {
            key: _restore_optional_arguments(item, properties.get(key, {}))
            for key, item in value.items()
            if item is not None or key in required or key not in properties
        }
    if isinstance(value, list):
        return [_restore_optional_arguments(item, schema.get("items", {})) for item in value]
    return value


def _sse_events(response) -> Iterator[dict]:
    data_lines: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        line = line.rstrip("\r\n")
        if not line:
            if data_lines:
                data = "\n".join(data_lines)
                data_lines.clear()
                if data == "[DONE]":
                    return
                try:
                    event = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise ProviderError("OpenAI вернула некорректный поток данных.", "invalid_stream") from exc
                if not isinstance(event, dict):
                    raise ProviderError("OpenAI вернула некорректное событие.", "invalid_stream")
                yield event
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
