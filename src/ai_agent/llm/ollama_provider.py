from __future__ import annotations

from collections.abc import Iterator
import json
import socket
from urllib import error, request

from ai_agent.llm.base import ProviderCapabilities, ProviderDescriptor, ProviderError
from ai_agent.types import Message, ModelResponse, ModelStreamChunk, ToolCall


class OllamaProvider:
    def __init__(
        self,
        model: str = "phi3",
        base_url: str = "http://localhost:11434",
        api_key: str = "",
        timeout_seconds: int = 120,
        provider_name: str = "ollama",
    ) -> None:
        self.model = model
        self.url = f"{base_url.rstrip('/')}/api/chat"
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.provider_name = provider_name

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            name=self.provider_name,
            model=self.model,
            capabilities=ProviderCapabilities(),
        )

    def generate(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict],
    ) -> ModelResponse:
        payload = self._payload(system_prompt, messages, tools, stream=False)
        try:
            with request.urlopen(self._request(payload), timeout=self.timeout_seconds) as response:
                data = json.load(response)
        except json.JSONDecodeError as exc:
            raise ProviderError("Ollama вернула некорректный JSON.", "invalid_response") from exc
        except Exception as exc:
            raise self._safe_error(exc) from None
        return self._model_response(data.get("message"))

    def stream(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict],
    ) -> Iterator[ModelStreamChunk]:
        payload = self._payload(system_prompt, messages, tools, stream=True)
        content = ""
        calls: dict[int, dict] = {}
        completed = False
        try:
            with request.urlopen(self._request(payload), timeout=self.timeout_seconds) as response:
                for raw_line in response:
                    if not raw_line.strip():
                        continue
                    try:
                        chunk = json.loads(raw_line)
                    except json.JSONDecodeError as exc:
                        raise ProviderError(
                            "Ollama вернула некорректный поток данных.",
                            "invalid_stream",
                        ) from exc
                    if chunk.get("error"):
                        raise ProviderError("Ollama прервала генерацию.", "provider_error")
                    message = chunk.get("message") or {}
                    delta = str(message.get("content") or "")
                    if delta:
                        content += delta
                        yield ModelStreamChunk(delta=delta)
                    self._merge_tool_calls(calls, message.get("tool_calls"))
                    if chunk.get("done"):
                        completed = True
                        break
        except ProviderError:
            raise
        except Exception as exc:
            raise self._safe_error(exc) from None

        if not completed:
            raise ProviderError(
                "Соединение с Ollama прервалось до завершения ответа.",
                "incomplete_stream",
            )
        response = ModelResponse(
            text=content.strip(),
            tool_calls=[self._tool_call(item) for _, item in sorted(calls.items())],
        )
        yield ModelStreamChunk(response=response)

    def _payload(self, system_prompt, messages, tools, stream):
        ollama_messages = [{"role": "system", "content": system_prompt}]
        for message in messages:
            item = {"role": message.role, "content": message.content}
            if message.role == "tool" and message.name:
                item["tool_name"] = message.name
            ollama_messages.append(item)
        ollama_tools = [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("schema", {"type": "object", "properties": {}}),
                },
            }
            for tool in tools
        ]
        return {
            "model": self.model,
            "messages": ollama_messages,
            "tools": ollama_tools,
            "stream": stream,
        }

    def _request(self, payload):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )

    def _safe_error(self, exc: Exception) -> ProviderError:
        if isinstance(exc, error.HTTPError):
            if exc.code == 401:
                return ProviderError("Ollama отклонила API-ключ.", "unauthorized")
            if exc.code == 404:
                return ProviderError(
                    f"Модель Ollama '{self.model}' не найдена.",
                    "model_not_found",
                )
            if exc.code == 429:
                return ProviderError("Лимит Ollama исчерпан. Повтори запрос позже.", "rate_limited")
            return ProviderError(f"Ollama вернула HTTP {exc.code}.", "http_error")
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return ProviderError(
                f"Ollama не ответила за {self.timeout_seconds} секунд.",
                "timeout",
            )
        if isinstance(exc, (error.URLError, OSError)):
            return ProviderError("Не удалось подключиться к Ollama.", "unavailable")
        return ProviderError("Не удалось получить ответ Ollama.", "provider_error")

    def _model_response(self, raw_message) -> ModelResponse:
        if not isinstance(raw_message, dict):
            raise ProviderError("Ollama не вернула сообщение.", "invalid_response")
        calls = raw_message.get("tool_calls") or []
        return ModelResponse(
            text=str(raw_message.get("content") or "").strip(),
            tool_calls=[self._tool_call(item) for item in calls if isinstance(item, dict)],
        )

    def _tool_call(self, item: dict) -> ToolCall:
        function = item.get("function") or item
        name = str(function.get("name") or "").strip()
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        return ToolCall(name=name, arguments=arguments if isinstance(arguments, dict) else {})

    def _merge_tool_calls(self, target: dict[int, dict], raw_calls) -> None:
        if not isinstance(raw_calls, list):
            return
        for position, raw_call in enumerate(raw_calls):
            if not isinstance(raw_call, dict):
                continue
            function = raw_call.get("function") or raw_call
            index = int(raw_call.get("index", function.get("index", position)))
            slot = target.setdefault(index, {"function": {"name": "", "arguments": {}}})
            slot_function = slot["function"]
            if function.get("name"):
                slot_function["name"] = function["name"]
            arguments = function.get("arguments")
            if isinstance(arguments, dict):
                current = slot_function.get("arguments")
                if not isinstance(current, dict):
                    current = {}
                current.update(arguments)
                slot_function["arguments"] = current
            elif isinstance(arguments, str):
                current = slot_function.get("arguments")
                if not isinstance(current, str):
                    current = ""
                slot_function["arguments"] = current + arguments
