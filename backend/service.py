from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_agent.agent import Agent
from ai_agent.config import Settings


class AgentService:
    def __init__(self) -> None:
        self.root_dir = ROOT
        self.settings = Settings.load(self.root_dir)
        self.agent = Agent(self.settings)

    def chat(self, message: str) -> str:
        return self.agent.respond(message)

    def bootstrap(self) -> dict:
        memory = self.agent.memory.snapshot()
        return {
            "agent_name": self.settings.agent_name,
            "provider": self.settings.model_provider,
            "model": self.settings.model_name,
            "notes": memory.notes,
            "history": memory.history,
        }


agent_service = AgentService()
