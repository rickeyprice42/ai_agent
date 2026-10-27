from __future__ import annotations

from collections.abc import Iterator
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
        return self._model_response(data)

    def stream(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict],
    ) -> Iterator[ModelStreamChunk]:
        response = self.generate(system_prompt, messages, tools)
        if response.text:
            yield ModelStreamChunk(delta=response.text)
        yield ModelStreamChunk(response=response)

    def _payload(self, system_prompt: str, messages: list[Message], tools: list[dict]) -> dict:
        input_items: list[dict] = []
        for message in messages:
            role = message.role if message.role in {"user", "assistant"} else "user"
            content = message.content
            if message.role == "tool":
                content = f"Tool output ({message.name or 'unknown'}):\n{content}"
            input_items.append({"role": role, "content": content})
        openai_tools = [
            {
                "type": "function",
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("schema", {"type": "object", "properties": {}}),
                "strict": True,
            }
            for tool in tools
        ]
        payload: dict = {
            "model": self.model,
            "instructions": system_prompt,
            "input": input_items,
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

    def _model_response(self, data: dict) -> ModelResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for item in data.get("output", []):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call":
                arguments = item.get("arguments") or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                tool_calls.append(
                    ToolCall(
                        name=str(item.get("name") or ""),
                        arguments=arguments if isinstance(arguments, dict) else {},
                    )
                )
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if isinstance(content, dict) and content.get("type") in {
                        "output_text", "text"
                    }:
                        text_parts.append(str(content.get("text") or ""))
        if not text_parts and data.get("output_text"):
            text_parts.append(str(data["output_text"]))
        return ModelResponse(text="".join(text_parts).strip(), tool_calls=tool_calls)

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
