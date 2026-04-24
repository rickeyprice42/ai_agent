from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(slots=True)
class Settings:
    agent_name: str
    model_provider: str
    model_name: str
    ollama_url: str
    memory_file: Path
    system_prompt: str

    @classmethod
    def load(cls, root_dir: Path) -> "Settings":
        load_dotenv(root_dir / ".env")

        memory_raw = os.getenv("MEMORY_FILE", "data/memory.json")
        memory_path = (root_dir / memory_raw).resolve()

        return cls(
            agent_name=os.getenv("AGENT_NAME", "Avelin"),
            model_provider=os.getenv("MODEL_PROVIDER", "mock"),
            model_name=os.getenv("MODEL_NAME", "mock-local"),
            ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
            memory_file=memory_path,
            system_prompt=os.getenv(
                "SYSTEM_PROMPT",
                "Ты личный AI-агент пользователя. Отвечай ясно, полезно и дружелюбно.",
            ),
        )
