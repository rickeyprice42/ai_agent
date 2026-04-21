from __future__ import annotations

import json
from urllib import error, request

from ai_agent.llm.tool_contract import render_tool_contract
from ai_agent.types import Message, ModelResponse, ToolCall


class OllamaProvider:
    def __init__(self, model: str = "phi3", base_url: str = "http://localhost:11434") -> None:
        self.model = model
        self.url = f"{base_url.rstrip('/')}/api/generate"

    def generate(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict],
    ) -> ModelResponse:
        prompt = self._build_prompt(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
        )

        payload = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
            }
        ).encode("utf-8")

        http_request = request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(http_request, timeout=60) as response:
                raw_data = response.read().decode("utf-8")
        except error.URLError as exc:
            return ModelResponse(
                text=(
                    "Не удалось подключиться к Ollama. "
                    f"Проверь, что сервис запущен по адресу {self.url}. "
                    f"Детали: {exc}"
                )
            )

        data = json.loads(raw_data)
        raw_response = str(data.get("response", "")).strip()
        return self._parse_model_response(raw_response)

    def _build_prompt(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict],
    ) -> str:
        prompt_parts = [render_tool_contract(system_prompt=system_prompt, tools=tools), "Conversation:"]

        for message in messages:
            if message.role == "tool" and message.name:
                prompt_parts.append(f"tool[{message.name}]: {message.content}")
                continue
            prompt_parts.append(f"{message.role}: {message.content}")

        prompt_parts.append("assistant_json:")
        return "\n".join(prompt_parts)

    def _parse_model_response(self, raw_response: str) -> ModelResponse:
        if not raw_response:
            return ModelResponse(text="Модель вернула пустой ответ.")

        parsed = self._load_json_object(raw_response)
        if parsed is None:
            return ModelResponse(text=raw_response)

        response_type = str(parsed.get("type", "message")).strip()

        if response_type == "tool_call":
            name = str(parsed.get("name", "")).strip()
            if name:
                return ModelResponse(
                    tool_calls=[
                        ToolCall(
                            name=name,
                            arguments=self._as_arguments(parsed.get("arguments", {})),
                        )
                    ]
                )

        if response_type == "tool_calls":
            calls: list[ToolCall] = []
            raw_calls = parsed.get("calls", [])
            if isinstance(raw_calls, list):
                for call in raw_calls:
                    if not isinstance(call, dict):
                        continue
                    name = str(call.get("name", "")).strip()
                    if not name:
                        continue
                    calls.append(
                        ToolCall(
                            name=name,
                            arguments=self._as_arguments(call.get("arguments", {})),
                        )
                    )
            if calls:
                return ModelResponse(tool_calls=calls)

        text = str(parsed.get("text", "")).strip()
        return ModelResponse(text=text or raw_response)

    def _load_json_object(self, raw_response: str) -> dict | None:
        candidates = [raw_response.strip()]

        if "```" in raw_response:
            stripped = raw_response.strip()
            for prefix in ("```json", "```JSON", "```"):
                if stripped.startswith(prefix) and stripped.endswith("```"):
                    candidates.append(stripped[len(prefix) : -3].strip())
                    break

        start = raw_response.find("{")
        end = raw_response.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(raw_response[start : end + 1].strip())

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

        return None

    def _as_arguments(self, value: object) -> dict:
        if isinstance(value, dict):
            return value
        return {}
