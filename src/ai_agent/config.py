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
    database_file: Path
    memory_file: Path
    tool_workspace_dir: Path
    max_file_read_chars: int
    max_file_write_chars: int
    system_prompt: str

    @classmethod
    def load(cls, root_dir: Path) -> "Settings":
        load_dotenv(root_dir / ".env")

        memory_raw = os.getenv("MEMORY_FILE", "data/memory.json")
        memory_path = (root_dir / memory_raw).resolve()
        database_raw = os.getenv("DATABASE_FILE", "data/avelin.sqlite3")
        database_path = (root_dir / database_raw).resolve()
        workspace_raw = os.getenv("TOOL_WORKSPACE_DIR", "data/workspace")
        workspace_path = (root_dir / workspace_raw).resolve()

        return cls(
            agent_name=os.getenv("AGENT_NAME", "Avelin"),
            model_provider=os.getenv("MODEL_PROVIDER", "mock"),
            model_name=os.getenv("MODEL_NAME", "mock-local"),
            ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
            database_file=database_path,
            memory_file=memory_path,
            tool_workspace_dir=workspace_path,
            max_file_read_chars=_read_int_env("MAX_FILE_READ_CHARS", default=20000, minimum=1000),
            max_file_write_chars=_read_int_env("MAX_FILE_WRITE_CHARS", default=20000, minimum=1000),
            system_prompt=os.getenv(
                "SYSTEM_PROMPT",
                (
                    "Ты личный AI-агент пользователя. Отвечай ясно, полезно и дружелюбно. "
                    "Когда пользователь просит запомнить факт или управлять задачами, "
                    "используй доступные инструменты вместо обычного текстового обещания. "
                    "Если пользователь просит спланировать работу, создай задачу с шагами."
                ),
            ),
        )


def _read_int_env(name: str, default: int, minimum: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return max(value, minimum)
