from __future__ import annotations

import json
from urllib import error, request

from ai_agent.types import Message, ModelResponse


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
        _ = tools
        prompt = self._build_prompt(system_prompt=system_prompt, messages=messages)

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
        return ModelResponse(text=str(data.get("response", "")), tool_calls=[])

    def _build_prompt(self, system_prompt: str, messages: list[Message]) -> str:
        prompt_parts = [f"system: {system_prompt}"]
        for message in messages:
            if message.role == "tool" and message.name:
                prompt_parts.append(f"tool[{message.name}]: {message.content}")
                continue
            prompt_parts.append(f"{message.role}: {message.content}")
        prompt_parts.append("assistant:")
        return "\n".join(prompt_parts)
