from __future__ import annotations

from dataclasses import dataclass
from base64 import b64encode
from pathlib import Path
from typing import Protocol
from urllib import error, request
import json
import mimetypes
import socket

from ai_agent.llm.base import ProviderError
from ai_agent.tools.base import ToolResult, failure_result


SUPPORTED_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@dataclass(frozen=True, slots=True)
class VisionResult:
    text: str
    provider: str
    model: str


class VisionProvider(Protocol):
    name: str
    model: str

    def analyze(self, image: bytes, mime_type: str, prompt: str) -> str:
        ...


class OllamaVisionProvider:
    name = "ollama_cloud"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "https://ollama.com",
        timeout_seconds: int = 120,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.url = f"{base_url.rstrip('/')}/api/chat"
        self.timeout_seconds = timeout_seconds

    def analyze(self, image: bytes, mime_type: str, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [b64encode(image).decode("ascii")],
                }
            ],
            "stream": False,
        }
        data = _json_request(
            self.url,
            payload,
            self.timeout_seconds,
            {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            provider="Ollama Vision",
        )
        message = data.get("message") or {}
        text = str(message.get("content") or "").strip()
        if not text:
            raise ProviderError("Ollama Vision не вернула описание изображения.", "invalid_response")
        return text


class OpenAIVisionProvider:
    name = "openai"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 120,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.url = f"{base_url.rstrip('/')}/responses"
        self.timeout_seconds = timeout_seconds

    def analyze(self, image: bytes, mime_type: str, prompt: str) -> str:
        encoded = b64encode(image).decode("ascii")
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": f"data:{mime_type};base64,{encoded}",
                            "detail": "high",
                        },
                    ],
                }
            ],
            "reasoning": {"effort": "none"},
        }
        data = _json_request(
            self.url,
            payload,
            self.timeout_seconds,
            {"Authorization": f"Bearer {self.api_key}"},
            provider="OpenAI Vision",
        )
        text = str(data.get("output_text") or "").strip()
        if not text:
            parts: list[str] = []
            for item in data.get("output", []):
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                for content in item.get("content", []):
                    if isinstance(content, dict) and content.get("type") == "output_text":
                        parts.append(str(content.get("text") or ""))
            text = "".join(parts).strip()
        if not text:
            raise ProviderError("OpenAI Vision не вернула описание изображения.", "invalid_response")
        return text


class VisionRouter:
    def __init__(
        self,
        workspace_dir: Path,
        providers: list[VisionProvider],
        *,
        max_image_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self.workspace_dir = workspace_dir.resolve()
        self.providers = providers
        self.max_image_bytes = max(1024, int(max_image_bytes))

    def availability(self) -> tuple[bool, str | None]:
        if not self.providers:
            return False, "Vision providers не настроены."
        return True, None

    def analyze_path(self, relative_path: str, prompt: str) -> VisionResult:
        target = (self.workspace_dir / relative_path.replace("\\", "/")).resolve()
        if target == self.workspace_dir or self.workspace_dir not in target.parents:
            raise ValueError("Image path выходит за пределы workspace.")
        if not target.is_file():
            raise ValueError("Изображение не найдено.")
        mime_type = SUPPORTED_IMAGE_TYPES.get(target.suffix.casefold())
        if not mime_type:
            guessed, _ = mimetypes.guess_type(target.name)
            if guessed not in SUPPORTED_IMAGE_TYPES.values():
                raise ValueError("Поддерживаются PNG, JPEG, WEBP и non-animated GIF.")
            mime_type = guessed
        if target.stat().st_size > self.max_image_bytes:
            raise ValueError("Изображение превышает разрешённый размер.")
        image = target.read_bytes()
        last_error: ProviderError | None = None
        for provider in self.providers:
            try:
                text = provider.analyze(image, mime_type, prompt)
                return VisionResult(text=text, provider=provider.name, model=provider.model)
            except ProviderError as exc:
                last_error = exc
        raise last_error or ProviderError("Все vision-провайдеры недоступны.", "unavailable")


def analyze_image_result(router: VisionRouter, arguments: dict) -> ToolResult:
    try:
        result = router.analyze_path(
            str(arguments.get("path", "")),
            str(arguments.get("prompt", "")).strip()
            or "Опиши изображение и перечисли видимые элементы интерфейса и текст.",
        )
    except ValueError as exc:
        return failure_result("invalid_image", str(exc))
    except ProviderError as exc:
        return failure_result(exc.code, exc.user_message, retryable=exc.code in {"timeout", "unavailable", "rate_limited"})
    return ToolResult(
        status="completed",
        output={"text": result.text, "provider": result.provider, "model": result.model},
    )


def _json_request(
    url: str,
    payload: dict,
    timeout_seconds: int,
    extra_headers: dict[str, str],
    *,
    provider: str,
) -> dict:
    headers = {"Content-Type": "application/json", **extra_headers}
    req = request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            return json.load(response)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"{provider} вернула некорректный JSON.", "invalid_response") from exc
    except error.HTTPError as exc:
        if exc.code == 401:
            raise ProviderError(f"{provider} отклонила API-ключ.", "unauthorized") from None
        if exc.code == 404:
            raise ProviderError(f"Vision-модель {provider} не найдена.", "model_not_found") from None
        if exc.code == 429:
            raise ProviderError(f"Лимит {provider} исчерпан.", "rate_limited") from None
        raise ProviderError(f"{provider} вернула HTTP {exc.code}.", "http_error") from None
    except (TimeoutError, socket.timeout) as exc:
        raise ProviderError(f"{provider} не ответила вовремя.", "timeout") from exc
    except (error.URLError, OSError) as exc:
        raise ProviderError(f"Не удалось подключиться к {provider}.", "unavailable") from exc
