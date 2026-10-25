from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from ai_agent.action_log import ActionLogStore
from ai_agent.config import Settings
from ai_agent.context import ContextBuilder
from ai_agent.executor import ExecutionEngine
from ai_agent.executor import _tool_status
from ai_agent.executor import decide_step_action
from ai_agent.llm.base import ProviderError
from ai_agent.llm.registry import build_provider
from ai_agent.memory import MemoryStore
from ai_agent.planner import Planner
from ai_agent.safety import tool_safety_block_reason
from ai_agent.tasks import TaskManager
from ai_agent.tools.base import ToolRegistry
from ai_agent.tools.builtin import register_builtin_tools
from ai_agent.tools.documents import DocumentSandbox
from ai_agent.tools.files import FileSandbox
from ai_agent.tools.http import HttpSandbox
from ai_agent.tools.presentations import PresentationSandbox
from ai_agent.tools.shell import ShellSandbox
from ai_agent.types import Message
from ai_agent.types import TaskStep


@dataclass(slots=True)
class ToolOutcome:
    results: list[str] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)


MIN_RESEARCH_RESULTS = 2


class Agent:
    def __init__(
        self,
        settings: Settings,
        user_id: str = "local-user",
        thread_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.memory = MemoryStore(
            database_path=settings.database_file,
            legacy_json_path=settings.memory_file,
            user_id=user_id,
            thread_id=thread_id,
        )
        self.planner = Planner()
        self.tasks = TaskManager(self.memory.database, user_id=user_id)
        self.action_log = ActionLogStore(self.memory.database, user_id=user_id)
        self.context = ContextBuilder(
            self.memory,
            self.tasks,
            self.action_log,
            context_window_tokens=settings.context_window_tokens,
            output_reserve_tokens=settings.context_output_reserve_tokens,
        )
        self.files = FileSandbox(
            workspace_dir=settings.tool_workspace_dir,
            max_read_chars=settings.max_file_read_chars,
            max_write_chars=settings.max_file_write_chars,
        )
        self.documents = DocumentSandbox(
            workspace_dir=settings.tool_workspace_dir,
            max_text_chars=settings.max_file_write_chars,
        )
        self.presentations = PresentationSandbox(
            workspace_dir=settings.tool_workspace_dir,
            max_text_chars=settings.max_file_write_chars,
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
            self.documents,
            self.presentations,
            self.shell,
            self.http,
            self.executor,
        )
        self.provider = self._build_provider()

    def _build_provider(self):
        return build_provider(self.settings)

    def respond(self, user_text: str) -> str:
        user_message = Message(role="user", content=user_text)
        self.memory.append_message(user_message)

        direct_result = self._try_direct_tool_action(user_text)
        if direct_result is not None:
            assistant_text = _tool_completion_text(direct_result)
            self.memory.append_message(
                Message(role="assistant", content=assistant_text, artifacts=direct_result.artifacts)
            )
            return assistant_text

        completed_tools = ToolOutcome()
        for attempt in range(4):
            try:
                tool_definitions = self.tools.list_for_model()
                context = self.context.build(
                    self.settings.system_prompt,
                    model=self.settings.model_name,
                    tools=tool_definitions,
                )
                response = self.provider.generate(
                    system_prompt=context.system_prompt,
                    messages=context.messages,
                    tools=tool_definitions,
                )
            except ProviderError as exc:
                if completed_tools.artifacts:
                    assistant_text = _tool_completion_text(completed_tools)
                    self.memory.append_message(
                        Message(
                            role="assistant",
                            content=assistant_text,
                            artifacts=completed_tools.artifacts,
                        )
                    )
                    return assistant_text
                self.memory.append_assistant_state(
                    "",
                    status="failed",
                    error=exc.user_message,
                    provider=self.settings.model_provider,
                    model=self.settings.model_name,
                )
                raise

            if response.tool_calls:
                outcome = self._execute_tool_calls(response.tool_calls, user_text)
                completed_tools.results.extend(outcome.results)
                completed_tools.artifacts.extend(outcome.artifacts)
                completed_tools.tool_names.extend(outcome.tool_names)
                continue

            if (
                not response.text.strip()
                and completed_tools.results
                and not completed_tools.artifacts
                and attempt < 3
            ):
                self.memory.append_message(Message(role="tool", name="agent_runtime", content=_CONTINUE_AFTER_TOOL))
                continue
            self._ensure_requested_research_docx(user_text, completed_tools)
            assistant_text = response.text.strip() or _tool_completion_text(completed_tools)
            self.memory.append_message(
                Message(role="assistant", content=assistant_text, artifacts=completed_tools.artifacts)
            )
            return assistant_text

        self._ensure_requested_research_docx(user_text, completed_tools)
        fallback = (
            _tool_completion_text(completed_tools)
            if completed_tools.results
            else "Достигнут предел шагов агента. Уточни запрос."
        )
        self.memory.append_message(
            Message(role="assistant", content=fallback, artifacts=completed_tools.artifacts)
        )
        return fallback

    def respond_stream(self, user_text: str):
        self.memory.append_message(Message(role="user", content=user_text))
        direct_result = self._try_direct_tool_action(user_text)
        if direct_result is not None:
            assistant_text = _tool_completion_text(direct_result)
            self.memory.append_message(
                Message(role="assistant", content=assistant_text, artifacts=direct_result.artifacts)
            )
            yield {"type": "delta", "text": assistant_text}
            yield {
                "type": "done",
                "provider": self.settings.model_provider,
                "model": self.settings.model_name,
            }
            return

        partial = ""
        message_id = None
        completed = False
        completed_tools = ToolOutcome()
        try:
            for attempt in range(4):
                tool_definitions = self.tools.list_for_model()
                context = self.context.build(
                    self.settings.system_prompt,
                    model=self.settings.model_name,
                    tools=tool_definitions,
                )
                response = None
                for chunk in self.provider.stream(
                    system_prompt=context.system_prompt,
                    messages=context.messages,
                    tools=tool_definitions,
                ):
                    if chunk.delta:
                        if message_id is None:
                            message_id = self.memory.append_assistant_state(
                                "",
                                status="pending",
                                error=None,
                                provider=self.settings.model_provider,
                                model=self.settings.model_name,
                            )
                        partial += chunk.delta
                        self.memory.update_message(
                            message_id,
                            content=partial,
                            status="pending",
                        )
                        yield {"type": "delta", "text": chunk.delta}
                    if chunk.response is not None:
                        response = chunk.response

                if response is None:
                    raise ProviderError("Провайдер не завершил ответ.", "incomplete_stream")

                if response.tool_calls:
                    if message_id is not None:
                        self.memory.update_message(
                            message_id,
                            content=partial,
                            status="complete",
                        )
                        message_id = None
                        partial = ""
                    outcome = self._execute_tool_calls(response.tool_calls, user_text)
                    completed_tools.results.extend(outcome.results)
                    completed_tools.artifacts.extend(outcome.artifacts)
                    completed_tools.tool_names.extend(outcome.tool_names)
                    yield {"type": "status", "text": "Выполняю инструменты…"}
                    continue

                if (
                    not response.text.strip()
                    and not partial.strip()
                    and completed_tools.results
                    and not completed_tools.artifacts
                    and attempt < 3
                ):
                    self.memory.append_message(
                        Message(role="tool", name="agent_runtime", content=_CONTINUE_AFTER_TOOL)
                    )
                    continue
                self._ensure_requested_research_docx(user_text, completed_tools)
                assistant_text = response.text.strip() or partial.strip() or _tool_completion_text(completed_tools)
                if message_id is None:
                    message_id = self.memory.append_assistant_state(
                        assistant_text,
                        status="complete",
                        error=None,
                        provider=self.settings.model_provider,
                        model=self.settings.model_name,
                        artifacts=completed_tools.artifacts,
                    )
                    if not partial:
                        yield {"type": "delta", "text": assistant_text}
                else:
                    self.memory.update_message(
                        message_id,
                        content=assistant_text,
                        status="complete",
                        artifacts=completed_tools.artifacts,
                    )
                completed = True
                yield {
                    "type": "done",
                    "provider": self.settings.model_provider,
                    "model": self.settings.model_name,
                }
                return

            self._ensure_requested_research_docx(user_text, completed_tools)
            fallback = (
                _tool_completion_text(completed_tools)
                if completed_tools.results
                else "Достигнут предел шагов агента. Уточни запрос."
            )
            self.memory.append_message(
                Message(role="assistant", content=fallback, artifacts=completed_tools.artifacts)
            )
            completed = True
            yield {"type": "delta", "text": fallback}
            yield {
                "type": "done",
                "provider": self.settings.model_provider,
                "model": self.settings.model_name,
            }
        except GeneratorExit:
            if completed_tools.artifacts and message_id is None and not completed:
                self.memory.append_assistant_state(
                    _tool_completion_text(completed_tools),
                    status="complete",
                    error=None,
                    provider=self.settings.model_provider,
                    model=self.settings.model_name,
                    artifacts=completed_tools.artifacts,
                )
            elif message_id is not None and not completed:
                self.memory.update_message(
                    message_id,
                    content=partial,
                    status="cancelled",
                    error="Генерация остановлена пользователем.",
                    artifacts=completed_tools.artifacts,
                )
            raise
        except ProviderError as exc:
            if completed_tools.artifacts:
                assistant_text = _tool_completion_text(completed_tools)
                if message_id is None:
                    self.memory.append_assistant_state(
                        assistant_text,
                        status="complete",
                        error=None,
                        provider=self.settings.model_provider,
                        model=self.settings.model_name,
                        artifacts=completed_tools.artifacts,
                    )
                else:
                    self.memory.update_message(
                        message_id,
                        content=assistant_text,
                        status="complete",
                        artifacts=completed_tools.artifacts,
                    )
                if not partial:
                    yield {"type": "delta", "text": assistant_text}
                yield {
                    "type": "done",
                    "provider": self.settings.model_provider,
                    "model": self.settings.model_name,
                }
                return
            if message_id is None:
                message_id = self.memory.append_assistant_state(
                    partial,
                    status="failed",
                    error=exc.user_message,
                    provider=self.settings.model_provider,
                    model=self.settings.model_name,
                )
            else:
                self.memory.update_message(
                    message_id,
                    content=partial,
                    status="failed",
                    error=exc.user_message,
                )
            yield {
                "type": "error",
                "text": exc.user_message,
                "code": exc.code,
                "provider": self.settings.model_provider,
                "model": self.settings.model_name,
            }

    def _execute_tool_calls(self, tool_calls, user_text: str) -> ToolOutcome:
        outcome = ToolOutcome()
        for tool_call in tool_calls:
            if not self.tools.has(tool_call.name):
                tool_result = f"Инструмент '{tool_call.name}' не найден."
                status = "not_found"
                arguments = tool_call.arguments
            else:
                arguments, validation_error = self.tools.validate_arguments(
                    tool_call.name,
                    tool_call.arguments,
                )
                if validation_error:
                    tool_result = validation_error
                    status = "validation_error"
                else:
                    arguments = self._normalize_new_file_overwrite(tool_call.name, arguments)
                    safety_block = tool_safety_block_reason(tool_call.name, arguments, user_text)
                    if safety_block:
                        tool_result = safety_block
                        status = "blocked"
                    else:
                        tool_result = self.tools.execute(tool_call.name, arguments)
                        status = _tool_status(tool_result)
            self.action_log.record(
                tool_name=tool_call.name,
                status=status,
                arguments=arguments,
                result=tool_result,
            )
            self.memory.append_message(
                Message(role="tool", content=tool_result, name=tool_call.name)
            )
            outcome.results.append(tool_result)
            outcome.tool_names.append(tool_call.name)
            artifact = self._artifact_from_tool(tool_call.name, arguments, status)
            if artifact is not None:
                outcome.artifacts.append(artifact)
        return outcome

    def _try_direct_tool_action(self, user_text: str) -> ToolOutcome | None:
        decision = decide_step_action(
            TaskStep(id="direct-chat", task_id="direct-chat", description=user_text)
        )
        if decision.tool_name is None:
            return None
        if decision.tool_name in {"run_shell", "http_request", "execute_next_step"}:
            return None
        if not self.tools.has(decision.tool_name):
            return None

        arguments, validation_error = self.tools.validate_arguments(
            decision.tool_name,
            decision.arguments,
        )
        if validation_error:
            self.action_log.record(
                tool_name=decision.tool_name,
                status="validation_error",
                arguments=decision.arguments,
                result=validation_error,
            )
            self.memory.append_message(
                Message(role="tool", content=validation_error, name=decision.tool_name)
            )
            return ToolOutcome(results=[validation_error])

        arguments = self._normalize_new_file_overwrite(decision.tool_name, arguments)
        safety_block = tool_safety_block_reason(decision.tool_name, arguments, user_text)
        if safety_block:
            self.action_log.record(
                tool_name=decision.tool_name,
                status="blocked",
                arguments=arguments,
                result=safety_block,
            )
            self.memory.append_message(
                Message(role="tool", content=safety_block, name=decision.tool_name)
            )
            return ToolOutcome(results=[safety_block])

        tool_result = self.tools.execute(decision.tool_name, arguments)
        status = _tool_status(tool_result)
        self.action_log.record(
            tool_name=decision.tool_name,
            status=status,
            arguments=arguments,
            result=tool_result,
        )
        self.memory.append_message(
            Message(role="tool", content=tool_result, name=decision.tool_name)
        )
        artifact = self._artifact_from_tool(decision.tool_name, arguments, status)
        return ToolOutcome(
            results=[tool_result],
            artifacts=[artifact] if artifact is not None else [],
            tool_names=[decision.tool_name],
        )

    def _normalize_new_file_overwrite(self, tool_name: str, arguments: dict) -> dict:
        if tool_name not in {"write_file", "create_docx", "create_presentation"}:
            return arguments
        if not arguments.get("overwrite"):
            return arguments
        relative_path = str(arguments.get("path", "")).strip()
        if not relative_path:
            return arguments
        try:
            self.files.resolve_download_path(relative_path)
        except ValueError:
            normalized = dict(arguments)
            normalized["overwrite"] = False
            return normalized
        return arguments

    def _ensure_requested_research_docx(self, user_text: str, outcome: ToolOutcome) -> None:
        if outcome.artifacts or "web_search" not in outcome.tool_names:
            return
        lowered = user_text.lower()
        if not any(marker in lowered for marker in (".docx", "word", "ворд")):
            return
        search_result = next(
            (
                result
                for name, result in zip(outcome.tool_names, outcome.results)
                if name == "web_search" and result.startswith("Search query:")
            ),
            "",
        )
        matches = re.findall(
            r"^\d+\. (.+?)\n\s+URL: (.+?)\n\s+Snippet: (.+?)(?=\n\d+\.|\Z)",
            search_result,
            flags=re.MULTILINE | re.DOTALL,
        )
        quality_match = re.search(r"^Quality: accepted=(\d+);", search_result, flags=re.MULTILINE)
        accepted_count = int(quality_match.group(1)) if quality_match else len(matches)
        if len(matches) < MIN_RESEARCH_RESULTS or accepted_count < MIN_RESEARCH_RESULTS:
            outcome.results.append(
                "Недостаточно релевантных и проверяемых источников для создания документа. "
                "Файл не создан; уточни запрос или источник."
            )
            outcome.tool_names.append("research_quality_gate")
            return
        path_match = re.search(r"([\w\-./\\\u0400-\u04ff]+\.docx)\b", user_text, flags=re.IGNORECASE)
        relative_path = path_match.group(1).replace("\\", "/") if path_match else "web_research.docx"
        query = search_result.splitlines()[0].partition(":")[2].strip()
        paragraphs = [
            f"**{title.strip()}**\n{snippet.strip()}\nИсточник: {url.strip()}"
            for title, url, snippet in matches
        ]
        arguments = {
            "path": relative_path,
            "title": f"Подборка по запросу: {query}",
            "paragraphs": paragraphs,
            "bullets": [],
            "overwrite": False,
        }
        tool_result = self.tools.execute("create_docx", arguments)
        status = _tool_status(tool_result)
        self.action_log.record(
            tool_name="create_docx",
            status=status,
            arguments=arguments,
            result=tool_result,
        )
        self.memory.append_message(Message(role="tool", content=tool_result, name="create_docx"))
        outcome.results.append(tool_result)
        outcome.tool_names.append("create_docx")
        artifact = self._artifact_from_tool("create_docx", arguments, status)
        if artifact is not None:
            outcome.artifacts.append(artifact)

    def _artifact_from_tool(self, tool_name: str, arguments: dict, status: str) -> dict | None:
        if status != "completed" or tool_name not in {
            "write_file",
            "create_docx",
            "append_docx",
            "create_presentation",
        }:
            return None
        relative_path = str(arguments.get("path", "")).strip()
        if not relative_path:
            return None
        try:
            file = self.files.describe_file(relative_path)
        except ValueError:
            return None
        return {
            "path": file.path,
            "name": file.name,
            "extension": file.extension,
            "size_bytes": file.size_bytes,
        }


def _tool_completion_text(outcome: ToolOutcome) -> str:
    if outcome.artifacts:
        if len(outcome.artifacts) == 1:
            return (
                f"Готово — файл **{outcome.artifacts[0]['name']}** создан. "
                "Его можно скачать ниже."
            )
        return f"Готово — создано файлов: {len(outcome.artifacts)}. Их можно скачать ниже."
    if outcome.results:
        if outcome.results[-1].startswith("Search query:"):
            return (
                "Поиск завершён, но не удалось корректно оформить итоговый ответ. "
                "Попробуй уточнить тему или сократить количество результатов."
            )
        http_error = _http_error_status(outcome.results[-1])
        if http_error is not None:
            return (
                f"Не удалось получить данные с выбранного сайта: он вернул HTTP {http_error}. "
                "Попробуй изменить запрос или использовать другой источник."
            )
        if "не смог выполнить действие" in outcome.results[-1].lower():
            return "Не удалось выполнить интернет-запрос. Попробуй изменить запрос или источник."
        return outcome.results[-1]
    return "Не смог сформировать ответ."


def _http_error_status(result: str) -> int | None:
    for line in result.splitlines():
        if line.startswith("Status:"):
            try:
                status = int(line.partition(":")[2].strip())
            except ValueError:
                return None
            return status if status >= 400 else None
    return None


_CONTINUE_AFTER_TOOL = (
    "Продолжи исходную задачу пользователя на основе уже полученных результатов. "
    "Не повторяй тот же поиск. Если пользователь запросил документ или другой файл, "
    "создай его соответствующим инструментом с кратким содержанием и ссылками на источники. "
    "Если инструмент завершился ошибкой, объясни её кратко без сырого технического вывода."
)


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
