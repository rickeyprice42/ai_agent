from __future__ import annotations

from pathlib import Path

from ai_agent.config import Settings
from ai_agent.llm.mock_provider import MockProvider
from ai_agent.llm.ollama_provider import OllamaProvider
from ai_agent.memory import MemoryStore
from ai_agent.tools.base import ToolRegistry
from ai_agent.tools.builtin import register_builtin_tools
from ai_agent.types import Message


class Agent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.memory = MemoryStore(settings.memory_file)
        self.tools = ToolRegistry()
        register_builtin_tools(self.tools, self.memory)
        self.provider = self._build_provider()

    def _build_provider(self):
        if self.settings.model_provider == "mock":
            return MockProvider()
        if self.settings.model_provider == "ollama":
            return OllamaProvider(
                model=self.settings.model_name,
                base_url=self.settings.ollama_url,
            )
        raise ValueError(
            f"Неизвестный провайдер: {self.settings.model_provider}. "
            "Поддерживаются только 'mock' и 'ollama'."
        )

    def respond(self, user_text: str) -> str:
        user_message = Message(role="user", content=user_text)
        self.memory.append_message(user_message)

        for _ in range(4):
            conversation = self.memory.recent_messages()
            response = self.provider.generate(
                system_prompt=self.settings.system_prompt,
                messages=conversation,
                tools=self.tools.list_for_model(),
            )

            if response.tool_calls:
                for tool_call in response.tool_calls:
                    if not self.tools.has(tool_call.name):
                        tool_result = (
                            f"Инструмент '{tool_call.name}' не найден. "
                            "Используй только инструменты из предоставленного списка."
                        )
                        self.memory.append_message(
                            Message(role="tool", content=tool_result, name=tool_call.name)
                        )
                        continue

                    arguments, validation_error = self.tools.validate_arguments(
                        tool_call.name,
                        tool_call.arguments,
                    )
                    if validation_error:
                        self.memory.append_message(
                            Message(role="tool", content=validation_error, name=tool_call.name)
                        )
                        continue

                    tool_result = self.tools.execute(tool_call.name, arguments)
                    self.memory.append_message(
                        Message(role="tool", content=tool_result, name=tool_call.name)
                    )
                continue

            assistant_text = response.text.strip() or "Не смог сформировать ответ."
            self.memory.append_message(Message(role="assistant", content=assistant_text))
            return assistant_text

        fallback = "Достигнут предел шагов агента, но архитектура цикла уже готова для доработки."
        self.memory.append_message(Message(role="assistant", content=fallback))
        return fallback


class AgentApp:
    def __init__(self) -> None:
        root_dir = Path(__file__).resolve().parents[2]
        self.settings = Settings.load(root_dir)
        self.agent = Agent(self.settings)

    def run(self) -> None:
        print(f"{self.settings.agent_name} запущен. Напиши 'exit' для выхода.")

        while True:
            try:
                user_text = input("Ты> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nВыход.")
                return

            if not user_text:
                continue

            if user_text.lower() in {"exit", "quit", "выход"}:
                print("До связи.")
                return

            answer = self.agent.respond(user_text)
            print(f"{self.settings.agent_name}> {answer}")
