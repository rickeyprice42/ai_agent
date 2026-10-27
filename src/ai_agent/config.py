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
    ollama_cloud_url: str
    ollama_api_key: str
    ollama_cloud_model: str
    llm_timeout_seconds: int
    llm_max_retries: int
    context_window_tokens: int
    context_output_reserve_tokens: int
    context_compaction_threshold: float
    context_compaction_keep_recent: int
    database_file: Path
    memory_file: Path
    tool_workspace_dir: Path
    max_file_read_chars: int
    max_file_write_chars: int
    shell_timeout_seconds: int
    http_timeout_seconds: int
    max_http_response_chars: int
    allow_private_http: bool
    browser_timeout_seconds: int
    browser_allowed_hosts: tuple[str, ...]
    system_prompt: str

    @classmethod
    def load(cls, root_dir: Path) -> "Settings":
        load_dotenv(root_dir / ".env")

        api_key = os.getenv("OLLAMA_API_KEY", "").strip()
        key_file = root_dir / "API_KEY.txt"
        if not api_key and key_file.is_file():
            raw_key = key_file.read_text(encoding="utf-8-sig").strip()
            if raw_key.startswith("OLLAMA_API_KEY="):
                raw_key = raw_key.split("=", 1)[1].strip()
            if "\n" not in raw_key and "\r" not in raw_key:
                api_key = raw_key

        configured_provider = os.getenv("MODEL_PROVIDER", "").strip()
        model_provider = configured_provider or ("ollama_cloud" if api_key else "mock")
        default_model = "beam:cloud" if model_provider == "ollama_cloud" else "mock-local"

        memory_raw = os.getenv("MEMORY_FILE", "data/memory.json")
        memory_path = (root_dir / memory_raw).resolve()
        database_raw = os.getenv("DATABASE_FILE", "data/avelin.sqlite3")
        database_path = (root_dir / database_raw).resolve()
        workspace_raw = os.getenv("TOOL_WORKSPACE_DIR", "data/workspace")
        workspace_path = (root_dir / workspace_raw).resolve()

        context_window_tokens = _read_int_env("CONTEXT_WINDOW_TOKENS", default=32768, minimum=4096)
        output_reserve_tokens = _read_int_env("CONTEXT_OUTPUT_RESERVE_TOKENS", default=4096, minimum=256)

        return cls(
            agent_name=os.getenv("AGENT_NAME", "Avelin"),
            model_provider=model_provider,
            model_name=os.getenv("MODEL_NAME", default_model),
            ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
            ollama_cloud_url=os.getenv("OLLAMA_CLOUD_URL", "https://ollama.com"),
            ollama_api_key=api_key,
            ollama_cloud_model=os.getenv("OLLAMA_CLOUD_MODEL", "beam:cloud"),
            llm_timeout_seconds=_read_int_env("LLM_TIMEOUT_SECONDS", default=120, minimum=10),
            llm_max_retries=_read_int_env("LLM_MAX_RETRIES", default=1, minimum=0),
            context_window_tokens=context_window_tokens,
            context_output_reserve_tokens=min(output_reserve_tokens, context_window_tokens - 1024),
            context_compaction_threshold=_read_float_env(
                "CONTEXT_COMPACTION_THRESHOLD",
                default=0.75,
                minimum=0.25,
                maximum=0.95,
            ),
            context_compaction_keep_recent=_read_int_env(
                "CONTEXT_COMPACTION_KEEP_RECENT",
                default=12,
                minimum=2,
            ),
            database_file=database_path,
            memory_file=memory_path,
            tool_workspace_dir=workspace_path,
            max_file_read_chars=_read_int_env("MAX_FILE_READ_CHARS", default=20000, minimum=1000),
            max_file_write_chars=_read_int_env("MAX_FILE_WRITE_CHARS", default=20000, minimum=1000),
            shell_timeout_seconds=_read_int_env("SHELL_TIMEOUT_SECONDS", default=30, minimum=1),
            http_timeout_seconds=_read_int_env("HTTP_TIMEOUT_SECONDS", default=15, minimum=1),
            max_http_response_chars=_read_int_env("MAX_HTTP_RESPONSE_CHARS", default=20000, minimum=1000),
            allow_private_http=_read_bool_env("ALLOW_PRIVATE_HTTP", default=False),
            browser_timeout_seconds=_read_int_env("BROWSER_TIMEOUT_SECONDS", default=20, minimum=1),
            browser_allowed_hosts=tuple(
                host.strip().casefold()
                for host in os.getenv("BROWSER_ALLOWED_HOSTS", "").split(",")
                if host.strip()
            ),
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


def _read_bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _read_float_env(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)
