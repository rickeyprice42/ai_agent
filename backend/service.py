from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from urllib import error, request
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_agent.agent import Agent
from ai_agent.config import Settings
from ai_agent.database import AvelinDatabase


AVAILABLE_MODELS = [
    {
        "provider": "mock",
        "label": "Mock",
        "description": "Локальный безопасный режим без внешней модели.",
        "models": ["mock-local"],
    },
    {
        "provider": "ollama",
        "label": "Ollama",
        "description": "Локальная LLM через Ollama API.",
        "models": [],
    },
    {
        "provider": "ollama_cloud",
        "label": "Ollama Cloud",
        "description": "Облачная модель через прямой Ollama API и OLLAMA_API_KEY.",
        "models": [],
    },
]


class AgentService:
    def __init__(self) -> None:
        self.root_dir = ROOT
        self.settings = Settings.load(self.root_dir)
        self.database = AvelinDatabase(self.settings.database_file)
        self._agents: dict[str, Agent] = {}

    def agent_for_user(self, user_id: str) -> Agent:
        if user_id not in self._agents:
            self._agents[user_id] = Agent(self.settings_for_user(user_id), user_id=user_id)
        return self._agents[user_id]

    def settings_for_user(self, user_id: str) -> Settings:
        model_settings = self.database.get_model_settings(user_id)
        return replace(
            self.settings,
            model_provider=model_settings["provider"],
            model_name=model_settings["model_name"],
            ollama_url=model_settings.get("ollama_url") or self.settings.ollama_url,
        )

    def available_models(self) -> list[dict]:
        models = [dict(item) for item in AVAILABLE_MODELS]
        ollama_models = self.installed_ollama_models()
        for item in models:
            if item["provider"] == "ollama":
                item["models"] = ollama_models
                if not ollama_models:
                    item["description"] = (
                        "Ollama API доступна, но установленные модели не найдены. "
                        "Установи модель командой `ollama pull <model>`."
                    )
            if item["provider"] == "ollama_cloud":
                item["models"] = [self.settings.ollama_cloud_model]
                if not self.settings.ollama_api_key:
                    item["description"] = "Нужен OLLAMA_API_KEY в локальном .env."
        return models

    def installed_ollama_models(self) -> list[str]:
        url = f"{self.settings.ollama_url.rstrip('/')}/api/tags"
        try:
            with request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, error.URLError, json.JSONDecodeError):
            return []

        models = payload.get("models", [])
        if not isinstance(models, list):
            return []

        names: list[str] = []
        for item in models:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("model") or "").strip()
            if name:
                names.append(name)
        return names

    def model_settings(self, user_id: str) -> dict[str, str]:
        return self.database.get_model_settings(user_id)

    def update_model_settings(
        self,
        user_id: str,
        provider: str,
        model_name: str,
        ollama_url: str | None = None,
    ) -> dict[str, str]:
        allowed = {item["provider"]: item["models"] for item in self.available_models()}
        if provider not in allowed:
            raise ValueError("Неподдерживаемый provider.")
        if provider == "ollama" and not allowed[provider]:
            raise ValueError("Ollama доступна, но установленные модели не найдены.")
        if provider == "ollama_cloud" and not self.settings.ollama_api_key:
            raise ValueError("Для Ollama Cloud нужен OLLAMA_API_KEY в локальном .env.")
        if model_name not in allowed[provider]:
            raise ValueError("Модель не найдена для выбранного provider.")

        settings = self.database.set_model_settings(
            user_id=user_id,
            provider=provider,
            model_name=model_name,
            ollama_url=ollama_url,
        )
        self._agents.pop(user_id, None)
        return settings

    def chat(self, message: str, user_id: str) -> str:
        return self.agent_for_user(user_id).respond(message)

    def execute_next_step(self, user_id: str) -> str:
        return self.agent_for_user(user_id).executor.execute_next_step()

    def approve_blocked_step(self, user_id: str, step_id: str) -> str:
        return self.agent_for_user(user_id).executor.approve_blocked_step(step_id)

    def open_workspace_folder(self, user_id: str) -> str:
        return self.agent_for_user(user_id).files.open_folder()

    def bootstrap(self, user_id: str) -> dict:
        agent = self.agent_for_user(user_id)
        memory = agent.memory.snapshot()
        user = self.database.get_user(user_id)
        model_settings = self.database.get_model_settings(user_id)
        return {
            "agent_name": self.settings.agent_name,
            "provider": model_settings["provider"],
            "model": model_settings["model_name"],
            "notes": memory.notes,
            "history": memory.history,
            "tasks": [_task_to_payload(task) for task in agent.tasks.list_tasks()],
            "action_logs": [_action_log_to_payload(log) for log in agent.action_log.recent(limit=20)],
            "workspace_files": [_workspace_file_to_payload(file) for file in agent.files.list_files(limit=20)],
            "user": user,
        }


agent_service = AgentService()


def _task_to_payload(task) -> dict:
    return {
        "id": task.id,
        "user_id": task.user_id,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "result": task.result,
        "steps": [
            {
                "id": step.id,
                "task_id": step.task_id,
                "description": step.description,
                "status": step.status,
                "position": step.position,
                "result": step.result,
            }
            for step in task.steps
        ],
    }


def _action_log_to_payload(log) -> dict:
    return {
        "id": log.id,
        "user_id": log.user_id,
        "tool_name": log.tool_name,
        "status": log.status,
        "arguments": log.arguments,
        "result": log.result,
        "created_at": log.created_at,
    }


def _workspace_file_to_payload(file) -> dict:
    return {
        "path": file.path,
        "name": file.name,
        "extension": file.extension,
        "size_bytes": file.size_bytes,
        "modified_at": file.modified_at,
    }
