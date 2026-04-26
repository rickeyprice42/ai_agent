from __future__ import annotations

from pathlib import Path

from ai_agent.action_log import ActionLogStore
from ai_agent.config import Settings
from ai_agent.context import ContextBuilder
from ai_agent.executor import ExecutionEngine
from ai_agent.executor import _tool_status
from ai_agent.llm.mock_provider import MockProvider
from ai_agent.llm.ollama_provider import OllamaProvider
from ai_agent.memory import MemoryStore
from ai_agent.planner import Planner
from ai_agent.tasks import TaskManager
from ai_agent.tools.base import ToolRegistry
from ai_agent.tools.builtin import register_builtin_tools
from ai_agent.tools.files import FileSandbox
from ai_agent.tools.http import HttpSandbox
from ai_agent.tools.shell import ShellSandbox
from ai_agent.types import Message


class Agent:
    def __init__(self, settings: Settings, user_id: str = "local-user") -> None:
        self.settings = settings
        self.memory = MemoryStore(
            database_path=settings.database_file,
            legacy_json_path=settings.memory_file,
            user_id=user_id,
        )
        self.planner = Planner()
        self.tasks = TaskManager(self.memory.database, user_id=user_id)
        self.action_log = ActionLogStore(self.memory.database, user_id=user_id)
        self.context = ContextBuilder(self.memory, self.tasks, self.action_log)
        self.files = FileSandbox(
            workspace_dir=settings.tool_workspace_dir,
            max_read_chars=settings.max_file_read_chars,
            max_write_chars=settings.max_file_write_chars,
        )
        self.shell = ShellSandbox(
            workspace_dir=settings.tool_workspace_dir,
            timeout_seconds=settings.shell_timeout_seconds,
        )
        self.http = HttpSandbox(
            timeout_seconds=settings.http_timeout_seconds,
            max_response_chars=settings.max_http_response_chars,
            allow_private_networks=settings.allow_private_http,
        )
        self.tools = ToolRegistry()
        self.executor = ExecutionEngine(self.tasks, self.tools, self.action_log)
        register_builtin_tools(
            self.tools,
            self.memory,
            self.tasks,
            self.planner,
            self.files,
            self.shell,
            self.http,
            self.executor,
        )
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
            context = self.context.build(self.settings.system_prompt)
            response = self.provider.generate(
                system_prompt=context.system_prompt,
                messages=context.messages,
                tools=self.tools.list_for_model(),
            )

            if response.tool_calls:
                for tool_call in response.tool_calls:
                    if not self.tools.has(tool_call.name):
                        tool_result = (
                            f"Инструмент '{tool_call.name}' не найден. "
                            "Используй только инструменты из предоставленного списка."
                        )
                        self.action_log.record(
                            tool_name=tool_call.name,
                            status="not_found",
                            arguments=tool_call.arguments,
                            result=tool_result,
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
                        self.action_log.record(
                            tool_name=tool_call.name,
                            status="validation_error",
                            arguments=tool_call.arguments,
                            result=validation_error,
                        )
                        self.memory.append_message(
                            Message(role="tool", content=validation_error, name=tool_call.name)
                        )
                        continue

                    tool_result = self.tools.execute(tool_call.name, arguments)
                    self.action_log.record(
                        tool_name=tool_call.name,
                        status=_tool_status(tool_result),
                        arguments=arguments,
                        result=tool_result,
                    )
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
