from __future__ import annotations

from pathlib import Path
from time import perf_counter
import json
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PIL import Image, ImageDraw

from ai_agent.config import Settings
from ai_agent.llm.base import ProviderError
from ai_agent.llm.openai_provider import OpenAIProvider
from ai_agent.llm.ollama_provider import OllamaProvider
from ai_agent.llm.vision import OllamaVisionProvider, OpenAIVisionProvider
from ai_agent.types import Message


def main() -> int:
    settings = Settings.load(ROOT)
    target = settings.tool_workspace_dir / "vision-smoke.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (720, 360), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 220, 220), fill="#1769e0")
    draw.text((260, 95), "AVELIN VISION TEST 42", fill="black", stroke_width=1)
    draw.text((260, 145), "Blue square", fill="black")
    image.save(target, format="PNG")
    raw = target.read_bytes()

    checks = [
        (
            "reasoning_primary",
            settings.ollama_cloud_model,
            lambda: OllamaProvider(
                model=settings.ollama_cloud_model,
                base_url=settings.ollama_cloud_url,
                api_key=settings.ollama_api_key,
                timeout_seconds=settings.llm_timeout_seconds,
                provider_name="ollama_cloud",
            ).generate(
                "Answer concisely.",
                [Message(role="user", content="Reply with exactly AVELIN_REASONING_OK")],
                [],
            ).text,
        ),
        (
            "reasoning_fallback",
            settings.openai_model,
            lambda: OpenAIProvider(
                model=settings.openai_model,
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                timeout_seconds=settings.llm_timeout_seconds,
            ).generate(
                "Answer concisely.",
                [Message(role="user", content="Reply with exactly AVELIN_OPENAI_OK")],
                [],
            ).text,
        ),
        (
            "vision_primary",
            settings.vision_primary_model,
            lambda: OllamaVisionProvider(
                model=settings.vision_primary_model,
                api_key=settings.ollama_api_key,
                base_url=settings.ollama_cloud_url,
                timeout_seconds=settings.llm_timeout_seconds,
            ).analyze(raw, "image/png", "Read the large text and identify the colored shape. Be concise."),
        ),
        (
            "vision_fallback",
            settings.vision_fallback_model,
            lambda: OpenAIVisionProvider(
                model=settings.vision_fallback_model,
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                timeout_seconds=settings.llm_timeout_seconds,
            ).analyze(raw, "image/png", "Read the large text and identify the colored shape. Be concise."),
        ),
    ]

    failed = False
    for name, model, operation in checks:
        started = perf_counter()
        try:
            output = operation()
            ok = bool(str(output).strip())
            summary = str(output).strip().replace("\n", " ")[:160]
            result = {
                "check": name,
                "model": model,
                "ok": ok,
                "latency_ms": round((perf_counter() - started) * 1000, 1),
                "summary": summary,
            }
            failed = failed or not ok
        except ProviderError as exc:
            result = {
                "check": name,
                "model": model,
                "ok": False,
                "latency_ms": round((perf_counter() - started) * 1000, 1),
                "error_code": exc.code,
                "error": exc.user_message,
            }
            failed = True
        print(json.dumps(result, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
