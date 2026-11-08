from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import logging
import re

from ai_agent.action_log import ActionLogStore
from ai_agent.autonomous_memory import AutonomousMemoryEngine
from ai_agent.chat_approvals import PendingChatActions
from ai_agent.chat_contracts import (local_file_targets, needs_precise_image, normalize_artifact_links, requested_file_names,
                                     wants_docx_creation, wants_resend)
from ai_agent.config import Settings
from ai_agent.connectors import ConnectorRegistry
from ai_agent.context import ContextBuilder, ModelTokenEstimator
from ai_agent.executor import ExecutionEngine
from ai_agent.executor import _tool_status
from ai_agent.executor import decide_step_action
from ai_agent.llm.base import ProviderError
from ai_agent.llm.registry import build_provider
from ai_agent.llm.vision import (
    OllamaVisionProvider,
    OpenAIVisionProvider,
    VisionRouter,
)
from ai_agent.memory import MemoryStore, user_authored_text
from ai_agent.planner import Planner
from ai_agent.security import ToolPolicyEngine, mark_untrusted_tool_output, requires_approval
from ai_agent.tasks import TaskManager
from ai_agent.tools.base import ToolRegistry
from ai_agent.tools.browser import PlaywrightBrowserRunner
from ai_agent.tools.builtin import register_builtin_tools
from ai_agent.tools.documents import DocumentSandbox
from ai_agent.tools.desktop import DesktopRunner
from ai_agent.tools.files import FileSandbox
from ai_agent.tools.http import HttpSandbox
from ai_agent.tools.research import BrowserResearchReader
from ai_agent.tools.presentations import PresentationSandbox
from ai_agent.tools.shell import ShellSandbox
from ai_agent.tool_factory import ToolFactory
from ai_agent.types import Message, ToolCall
from ai_agent.types import TaskStep


@dataclass(slots=True)
class ToolOutcome:
    results: list[str] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    calls: dict[str, str] = field(default_factory=dict)
    preliminary_research: bool = False
    approvals: list[dict] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)

    def merge(self, other: "ToolOutcome") -> None:
        self.results.extend(other.results)
        self.artifacts.extend(other.artifacts)
        self.tool_names.extend(other.tool_names)
        self.statuses.extend(other.statuses)
        self.messages.extend(other.messages)
        self.calls.update(other.calls)
        self.preliminary_research = self.preliminary_research or other.preliminary_research
        self.approvals.extend(other.approvals)
        self.questions.extend(other.questions)


MIN_RESEARCH_RESULTS = 2
MAX_TOOL_ROUNDS = 12
MAX_TOOL_CALLS = 24
MAX_SEARCHES_BEFORE_READ = 3
TASK_MUTATION_TOOLS = {"create_task", "plan_task", "start_next_task", "execute_next_step", "add_task_step", "update_task", "update_task_step"}
logger = logging.getLogger("avelin.agent")


class Agent:
    def __init__(
        self,
        settings: Settings,
        user_id: str = "local-user",
        thread_id: str | None = None,
        *,
        task_queue_context: bool | None = None,
    ) -> None:
        self.settings = settings
        self.task_queue_context = task_queue_context
        self.memory = MemoryStore(
            database_path=settings.database_file,
            legacy_json_path=settings.memory_file,
            user_id=user_id,
            thread_id=thread_id,
        )
        self.planner = Planner()
        self.tasks = TaskManager(self.memory.database, user_id=user_id)
        self.action_log = ActionLogStore(self.memory.database, user_id=user_id)
        self.autonomous_memory = AutonomousMemoryEngine(
            self.memory.database, user_id=user_id, thread_id=self.memory.thread_id
        )
        self.context = ContextBuilder(
            self.memory,
            self.tasks,
            self.action_log,
            context_window_tokens=settings.context_window_tokens,
            output_reserve_tokens=settings.context_output_reserve_tokens,
            compaction_threshold=settings.context_compaction_threshold,
            compaction_keep_recent=settings.context_compaction_keep_recent,
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
        self.browser = PlaywrightBrowserRunner(
            workspace_dir=settings.tool_workspace_dir,
            timeout_seconds=settings.browser_timeout_seconds,
            allowed_hosts=settings.browser_allowed_hosts,
            allow_private_networks=False,
            max_text_chars=settings.max_http_response_chars,
        )
        self.research = BrowserResearchReader(self.http, self.browser)
        vision_providers = []
        if settings.ollama_api_key:
            vision_providers.append(
                OllamaVisionProvider(
                    model=settings.vision_primary_model,
                    api_key=settings.ollama_api_key,
                    base_url=settings.ollama_cloud_url,
                    timeout_seconds=settings.llm_timeout_seconds,
                )
            )
        if settings.openai_api_key:
            vision_providers.append(
                OpenAIVisionProvider(
                    model=settings.vision_fallback_model,
                    api_key=settings.openai_api_key,
                    base_url=settings.openai_base_url,
                    timeout_seconds=settings.llm_timeout_seconds,
                )
            )
        self.vision = VisionRouter(
            settings.tool_workspace_dir,
            vision_providers,
            max_image_bytes=settings.max_image_bytes,
        )
        self.desktop = DesktopRunner(
            settings.tool_workspace_dir,
            allowed_window_titles=settings.desktop_allowed_window_titles,
            emergency_stop=lambda: self.memory.database.security_settings(user_id)[
                "emergency_stop"
            ],
            timeout_seconds=settings.desktop_timeout_seconds,
            allow_pyautogui=settings.desktop_allow_pyautogui,
        )
        self.tools = ToolRegistry()
        self.connectors = ConnectorRegistry(self.tools)
        self.policy = ToolPolicyEngine(self.memory.database, user_id)
        self.pending_chat_actions = PendingChatActions(self.memory.database)
        self.executor = ExecutionEngine(self.tasks, self.tools, self.action_log, self.policy)
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
            self.browser,
            self.research,
            self.vision,
            self.desktop,
            self.executor,
        )
        self.tool_factory = ToolFactory(
            self.memory.database,
            user_id,
            self.tools,
            self.policy,
            workspace_dir=settings.tool_workspace_dir,
        )
        from ai_agent.long_tasks import register_long_task_tool
        register_long_task_tool(self)
        from ai_agent.chat_questions import register_question_tool
        register_question_tool(self)
        self.provider = self._build_provider()

    def _build_provider(self):
        return build_provider(self.settings)

    def respond(self, user_text: str) -> str:
        self.last_turn_outcome = "running"
        self.last_turn_message_id = None
        user_message = Message(role="user", content=user_text, name=self._shared_author_name(),artifacts=list(getattr(self,'incoming_attachments',[])))
        user_message_id = self.memory.append_message(user_message)

        direct_result = self._try_direct_tool_action(user_text)
        if direct_result is not None:
            assistant_text = _tool_completion_text(direct_result)
            self.last_turn_message_id = self.memory.append_message(
                Message(role="assistant", content=assistant_text, artifacts=direct_result.artifacts)
            )
            self._extract_autonomous_memory(user_message_id, user_text)
            self.last_turn_outcome = _completed_turn_outcome(direct_result)
            return assistant_text

        completed_tools = ToolOutcome()
        turn_messages: list[Message] = []
        context = None
        file_repair_requested = False
        for attempt in range(MAX_TOOL_ROUNDS):
            try:
                shared_workspace = self.memory.shared_project() is not None
                phase = _turn_phase(completed_tools, attempt)
                task_queue = self._uses_task_queue(user_text)
                tool_definitions = _tools_for_phase(self.tools.list_for_model(), phase) if not shared_workspace else []
                if not task_queue:
                    tool_definitions = [tool for tool in tool_definitions if tool['name'] not in TASK_MUTATION_TOOLS]
                context = context or self.context.build(
                    self.settings.system_prompt if shared_workspace else self.tool_factory.system_prompt(self.settings.system_prompt),
                    model=self.settings.model_name,
                    tools=tool_definitions,
                    include_task_queue=task_queue,
                )
                response = self.provider.generate(
                    system_prompt=_runtime_system_prompt(context.system_prompt, phase),
                    messages=self._provider_messages(context, turn_messages),
                    tools=tool_definitions,
                )
            except ProviderError as exc:
                self.last_turn_outcome = "failed"
                if completed_tools.artifacts:
                    self.last_turn_outcome = "partial"
                    assistant_text = _provider_failure_artifact_text(completed_tools, exc)
                    self.last_turn_message_id = self.memory.append_message(
                        Message(
                            role="assistant",
                            content=assistant_text,
                            artifacts=completed_tools.artifacts,
                        )
                    )
                    self._extract_autonomous_memory(user_message_id, user_text)
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
                if phase == "final":
                    break
                turn_messages.append(Message(role="assistant", content=response.text, tool_calls=response.tool_calls, provider_items=response.provider_items))
                outcome = self._execute_tool_calls(response.tool_calls, user_text, previous=completed_tools,
                                                   allowed_tools={tool['name'] for tool in tool_definitions})
                completed_tools.merge(outcome)
                turn_messages.extend(outcome.messages)
                if outcome.approvals:
                    self.last_turn_outcome = "waiting_approval"
                    assistant_text = "Ожидаю подтверждения действия в этом чате."
                    self.last_turn_message_id = self.memory.append_message(Message(role="assistant", content=assistant_text, artifacts=completed_tools.artifacts))
                    return assistant_text
                if outcome.questions:
                    self.last_turn_outcome='waiting_clarification'
                    assistant_text=outcome.questions[0]['prompt']
                    self.last_turn_message_id=self.memory.append_message(Message(role='assistant',content=assistant_text,artifacts=completed_tools.artifacts))
                    return assistant_text
                if _stop_tool_loop(completed_tools):
                    break
                continue

            self._ensure_requested_research_docx(user_text, completed_tools)
            if self._missing_requested_docx(user_text, completed_tools) and "research_quality_gate" not in completed_tools.tool_names and not file_repair_requested and attempt < MAX_TOOL_ROUNDS - 1:
                file_repair_requested = True
                turn_messages.extend([Message(role="assistant", content=response.text), Message(role="user", name="agent_runtime", content=_MISSING_DOCX)])
                continue
            if (
                not response.text.strip()
                and completed_tools.results
                and not completed_tools.artifacts
                and attempt < MAX_TOOL_ROUNDS - 1
            ):
                self.memory.append_message(Message(role="tool", name="agent_runtime", content=_CONTINUE_AFTER_TOOL))
                turn_messages.append(Message(role="user", content=_CONTINUE_AFTER_TOOL))
                continue
            assistant_text = response.text.strip() or _tool_completion_text(completed_tools)
            assistant_text = self._verified_file_reply(user_text, assistant_text, completed_tools)
            if completed_tools.preliminary_research and _PRELIMINARY_RESEARCH not in assistant_text:
                assistant_text = _PRELIMINARY_RESEARCH + "\n\n" + assistant_text
            self.last_turn_message_id = self.memory.append_message(
                Message(role="assistant", content=assistant_text, artifacts=completed_tools.artifacts)
            )
            self._extract_autonomous_memory(user_message_id, user_text)
            self.last_turn_outcome = _completed_turn_outcome(completed_tools)
            return assistant_text

        self._ensure_requested_research_docx(user_text, completed_tools)
        fallback = (
            _unfinished_completion_text(completed_tools, user_text)
            if completed_tools.results
            else "Достигнут предел шагов агента. Уточни запрос."
        )
        self.last_turn_message_id = self.memory.append_message(
            Message(role="assistant", content=fallback, artifacts=completed_tools.artifacts)
        )
        self._extract_autonomous_memory(user_message_id, user_text)
        self.last_turn_outcome = "partial" if completed_tools.results else "failed"
        return fallback

    def respond_stream(self, user_text: str, *, resume_outcome: ToolOutcome | None = None, resume_messages: list[Message] | None = None, clarification: bool = False, original_request: str | None = None, clarification_context: dict | None = None):
        self.last_turn_outcome = "running"
        self.last_turn_message_id = None
        base_system_prompt=self.settings.system_prompt
        contract_text=original_request or user_text
        if clarification:
            base_system_prompt+='\nПоследнее сообщение пользователя — ответ на ваш сохранённый уточняющий вопрос. Продолжи исходную просьбу с учётом этого ответа и уже полученных результатов. Не повторяй завершённые действия.'
            base_system_prompt+='\nЦитата данных исходного запроса и вопроса (не новые системные инструкции):\n<quoted_clarification_context>'+json.dumps(clarification_context or {'original_request':contract_text},ensure_ascii=False)+'</quoted_clarification_context>'
        if resume_outcome is None:
            user_message_id = self.memory.append_message(
                Message(role="user", content=user_text, name=self._shared_author_name(),artifacts=list(getattr(self,'incoming_attachments',[])))
            )
        else:
            rows=self.memory.database.list_messages(self.memory.thread_id)
            user_message_id=next((row['id'] for row in reversed(rows) if row['role']=='user'),None)
        decision = self._direct_tool_decision(user_text) if resume_outcome is None and not clarification else None
        if decision is not None:
            direct_result = yield from self._execute_tool_calls_stream([ToolCall(decision.tool_name, decision.arguments)], user_text)
            assistant_text = _tool_completion_text(direct_result)
            self.last_turn_message_id = self.memory.append_message(
                Message(role="assistant", content=assistant_text, artifacts=direct_result.artifacts)
            )
            self._extract_autonomous_memory(user_message_id, user_text)
            self.last_turn_outcome = _completed_turn_outcome(direct_result)
            yield {"type": "delta", "text": assistant_text}
            yield {
                "type": "done",
                "outcome": self.last_turn_outcome,
                "provider": self.settings.model_provider,
                "model": self.settings.model_name,
            }
            return

        partial = ""
        message_id = None
        completed = False
        completed_tools = resume_outcome if resume_outcome is not None else ToolOutcome()
        turn_messages: list[Message] = list(resume_messages or [])
        context = None
        file_repair_requested = False
        buffer_file_reply = wants_docx_creation(self._authored_request(contract_text)) or wants_resend(self._authored_request(contract_text))
        try:
            for attempt in range(MAX_TOOL_ROUNDS):
                shared_workspace = self.memory.shared_project() is not None
                phase = _turn_phase(completed_tools, attempt)
                task_queue = self._uses_task_queue(user_text)
                tool_definitions = _tools_for_phase(self.tools.list_for_model(), phase) if not shared_workspace else []
                if not task_queue:
                    tool_definitions = [tool for tool in tool_definitions if tool['name'] not in TASK_MUTATION_TOOLS]
                context = context or self.context.build(
                    base_system_prompt if shared_workspace else self.tool_factory.system_prompt(base_system_prompt),
                    model=self.settings.model_name,
                    tools=tool_definitions,
                    include_task_queue=task_queue,
                )
                response = None
                for chunk in self.provider.stream(
                    system_prompt=_runtime_system_prompt(context.system_prompt, phase),
                    messages=self._provider_messages(context, turn_messages),
                    tools=tool_definitions,
                ):
                    if chunk.delta:
                        if buffer_file_reply:
                            partial += chunk.delta
                        elif message_id is None:
                            message_id = self.memory.append_assistant_state(
                                "",
                                status="pending",
                                error=None,
                                provider=self.settings.model_provider,
                                model=self.settings.model_name,
                            )
                        if not buffer_file_reply:
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
                    if buffer_file_reply:
                        partial = ""
                    if message_id is not None:
                        self.memory.update_message(
                            message_id,
                            content=partial,
                            status="complete",
                        )
                        message_id = None
                        partial = ""
                    if phase == "final":
                        break
                    turn_messages.append(Message(role="assistant", content=response.text, tool_calls=response.tool_calls, provider_items=response.provider_items))
                    outcome = ToolOutcome()
                    try:
                        yield from self._execute_tool_calls_stream(response.tool_calls, contract_text, previous=completed_tools, round_number=attempt + 1, outcome=outcome,
                                                                  allowed_tools={tool['name'] for tool in tool_definitions})
                    finally:
                        # Closing the stream at a tool-completed status must not lose
                        # the file that was already durably created by that tool.
                        completed_tools.merge(outcome)
                    turn_messages.extend(outcome.messages)
                    if outcome.approvals:
                        self.last_turn_outcome = "waiting_approval"
                        assistant_text = "Ожидаю подтверждения действия в этом чате."
                        self.last_turn_message_id = self.memory.append_message(Message(role="assistant", content=assistant_text, artifacts=completed_tools.artifacts))
                        completed = True
                        yield {"type": "delta", "text": assistant_text}
                        yield {"type": "done", "outcome": self.last_turn_outcome, "provider": self.settings.model_provider, "model": self.settings.model_name}
                        return
                    if outcome.questions:
                        self.last_turn_outcome='waiting_clarification'
                        assistant_text=outcome.questions[0]['prompt']
                        self.last_turn_message_id=self.memory.append_message(Message(role='assistant',content=assistant_text,artifacts=completed_tools.artifacts))
                        completed=True
                        yield {'type':'delta','text':assistant_text}
                        yield {'type':'done','outcome':self.last_turn_outcome,'provider':self.settings.model_provider,'model':self.settings.model_name}
                        return
                    if _stop_tool_loop(completed_tools):
                        break
                    continue

                self._ensure_requested_research_docx(contract_text, completed_tools)
                if self._missing_requested_docx(contract_text, completed_tools) and "research_quality_gate" not in completed_tools.tool_names and not file_repair_requested and attempt < MAX_TOOL_ROUNDS - 1:
                    file_repair_requested = True
                    turn_messages.extend([Message(role="assistant", content=response.text or partial), Message(role="user", name="agent_runtime", content=_MISSING_DOCX)])
                    partial = ""
                    continue
                if (
                    not response.text.strip()
                    and not partial.strip()
                    and completed_tools.results
                    and not completed_tools.artifacts
                    and attempt < MAX_TOOL_ROUNDS - 1
                ):
                    self.memory.append_message(
                        Message(role="tool", name="agent_runtime", content=_CONTINUE_AFTER_TOOL)
                    )
                    turn_messages.append(Message(role="user", content=_CONTINUE_AFTER_TOOL))
                    continue
                assistant_text = response.text.strip() or partial.strip() or _tool_completion_text(completed_tools)
                assistant_text = self._verified_file_reply(contract_text, assistant_text, completed_tools)
                if completed_tools.preliminary_research and _PRELIMINARY_RESEARCH not in assistant_text:
                    assistant_text = _PRELIMINARY_RESEARCH + "\n\n" + assistant_text
                if message_id is None:
                    message_id = self.memory.append_assistant_state(
                        assistant_text,
                        status="complete",
                        error=None,
                        provider=self.settings.model_provider,
                        model=self.settings.model_name,
                        artifacts=completed_tools.artifacts,
                    )
                    if not partial or buffer_file_reply:
                        yield {"type": "delta", "text": assistant_text}
                else:
                    self.memory.update_message(
                        message_id,
                        content=assistant_text,
                        status="complete",
                        artifacts=completed_tools.artifacts,
                    )
                self.last_turn_message_id = message_id
                self.last_turn_outcome = _completed_turn_outcome(completed_tools)
                completed = True
                self._extract_autonomous_memory(user_message_id, user_text)
                yield {
                    "type": "done",
                    "outcome": self.last_turn_outcome,
                    "provider": self.settings.model_provider,
                    "model": self.settings.model_name,
                }
                return

            self._ensure_requested_research_docx(contract_text, completed_tools)
            fallback = (
                _unfinished_completion_text(completed_tools, contract_text)
                if completed_tools.results
                else "Достигнут предел шагов агента. Уточни запрос."
            )
            self.last_turn_message_id = self.memory.append_message(
                Message(role="assistant", content=fallback, artifacts=completed_tools.artifacts)
            )
            self.last_turn_outcome = "partial" if completed_tools.results else "failed"
            completed = True
            self._extract_autonomous_memory(user_message_id, user_text)
            yield {"type": "delta", "text": fallback}
            yield {
                "type": "done",
                "outcome": self.last_turn_outcome,
                "provider": self.settings.model_provider,
                "model": self.settings.model_name,
            }
        except GeneratorExit:
            if not completed:
                self.last_turn_outcome = "cancelled"
            if completed_tools.artifacts and message_id is None and not completed:
                self.memory.append_assistant_state(
                    "Генерация остановлена. Созданные файлы сохранены и доступны ниже.",
                    status="cancelled",
                    error="Генерация остановлена пользователем.",
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
            self.last_turn_outcome = "failed"
            if completed_tools.artifacts:
                self.last_turn_outcome = "partial"
                assistant_text = _provider_failure_artifact_text(completed_tools, exc)
                if message_id is None:
                    message_id = self.memory.append_assistant_state(
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
                self.last_turn_message_id = message_id
                if not partial:
                    yield {"type": "delta", "text": assistant_text}
                self._extract_autonomous_memory(user_message_id, user_text)
                yield {
                    "type": "done",
                    "outcome": self.last_turn_outcome,
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
                "outcome": self.last_turn_outcome,
                "text": exc.user_message,
                "code": exc.code,
                "provider": self.settings.model_provider,
                "model": self.settings.model_name,
            }

    def _extract_autonomous_memory(self, source_message_id: str, user_text: str) -> None:
        if getattr(self, "suppress_autonomous_memory", False):
            return
        if self.memory.shared_project() is not None:
            return
        try:
            source = next((row for row in self.memory.database.list_messages(self.memory.thread_id)
                if row["id"] == source_message_id and row["role"] == "user"), None)
            if source is not None:
                user_text = user_authored_text(Message(role="user", content=str(source["content"]),
                    artifacts=source.get("artifacts") or []))
            self.autonomous_memory.process_completed_turn(source_message_id, user_text)
        except Exception:
            logger.exception("Autonomous memory extraction failed without affecting the chat response.")

    def _shared_author_name(self) -> str | None:
        if self.memory.shared_project() is None:
            return None
        user = self.memory.database.get_user(self.memory.user_id)
        return str(user.get("display_name") or user.get("username") or "Участник")

    def _provider_messages(self, context, turn_messages: list[Message]) -> list[Message]:
        messages = [*context.messages, *turn_messages]
        original_messages = list(messages)
        estimator = ModelTokenEstimator(self.settings.model_name)
        def token_count() -> int:
            return estimator.count_text(context.system_prompt) + sum(
                estimator.count_message(message)
                + estimator.count_text(json.dumps([
                    {"name": call.name, "arguments": call.arguments, "call_id": call.call_id}
                    for call in message.tool_calls
                ], ensure_ascii=False))
                for message in messages
            )
        budget = context.trace.input_budget_tokens - context.trace.tool_schema_tokens
        if token_count() > budget:
            from dataclasses import replace
            from ai_agent.tool_payloads import compact_tool_output
            query = next((message.content for message in reversed(messages) if message.role == 'user' and message.name != 'agent_runtime'), '')
            indices = [index for index,message in enumerate(messages) if message.role == 'tool']
            # First shorten older observations, preserving the newest source and
            # every native call/result pair. JSON dates/offers/URLs remain valid.
            for limit in (6000, 3000, 1500):
                for index in indices[:-1]:
                    message = original_messages[index]
                    messages[index] = replace(message,content=compact_tool_output(message.content,limit,query=query))
                    if token_count() <= budget:
                        return messages
                if indices:
                    index = indices[-1]
                    message = original_messages[index]
                    messages[index] = replace(message,content=compact_tool_output(message.content,limit*3,query=query))
                    if token_count() <= budget:
                        return messages
        if token_count() > budget:
            raise ProviderError("Результаты инструментов превысили доступный контекст. Сократи запрос или продолжи отдельным сообщением.", "context_overflow")
        return messages

    def _execute_tool_calls(self, tool_calls, user_text: str, *, previous: ToolOutcome | None = None, allowed_tools: set[str] | None = None) -> ToolOutcome:
        stream = self._execute_tool_calls_stream(tool_calls, user_text, previous=previous, allowed_tools=allowed_tools)
        while True:
            try:
                next(stream)
            except StopIteration as stopped:
                return stopped.value

    def _execute_tool_calls_stream(self, tool_calls, user_text: str, *, previous: ToolOutcome | None = None, round_number: int = 1, outcome: ToolOutcome | None = None, allowed_tools: set[str] | None = None):
        outcome = outcome if outcome is not None else ToolOutcome()
        if self.memory.shared_project() is not None:
            outcome.results.append(
                "Tool calls are disabled in Shared Workspace MVP; share board items explicitly through the workspace UI."
            )
            outcome.statuses.append("blocked")
            return outcome
        for tool_call in tool_calls:
            if tool_call.name == "analyze_image" and not tool_call.arguments.get("detail") and needs_precise_image(user_text):
                tool_call.arguments = {**tool_call.arguments, "detail": "precise"}
            yield {"type": "status", "text": _tool_activity_text(tool_call.name), "tool": tool_call.name, "phase": "started", "round": round_number}
            invocation_output = None
            signature = json.dumps([tool_call.name, tool_call.arguments], sort_keys=True, ensure_ascii=False, default=str)
            prior_calls = {**(previous.calls if previous else {}), **outcome.calls}
            previous_count = len(previous.tool_names) if previous else 0
            if allowed_tools is not None and tool_call.name not in allowed_tools and self.tools.has(tool_call.name):
                tool_result = "Этот инструмент не доступен для текущего запроса или этапа. Выполни исходную просьбу доступными средствами; обычный ответ не требует изменения очереди задач."
                status = "validation_error"
                arguments = tool_call.arguments
            elif previous_count + len(outcome.tool_names) >= MAX_TOOL_CALLS:
                tool_result = "Достигнут предел вызовов инструментов за один запрос. Продолжи отдельным сообщением."
                status = "blocked"
                arguments = tool_call.arguments
            elif tool_call.name == "web_search" and (_searches_since_read(previous, outcome) >= MAX_SEARCHES_BEFORE_READ or _repeated_discovery(previous, outcome)):
                # Different query spellings must not evade a research boundary.
                # Match the native call with a tool result, but do not hit the
                # search provider again when it has produced no new evidence.
                tool_result = _READ_SOURCE_AFTER_SEARCH
                status = "failed"
                arguments = tool_call.arguments
            elif signature in prior_calls and (
                prior_calls[signature] != "completed"
                or (self.tools.has(tool_call.name) and requires_approval(self.tools.get(tool_call.name),tool_call.arguments))
                or tool_call.name in {"write_file", "append_docx", "create_docx", "create_presentation", "web_search", "plan_task", "create_task"}
            ):
                tool_result = "Повторный одинаковый вызов остановлен. Используй уже полученный результат, исправь аргументы или выбери другой источник."
                status = "blocked"
                arguments = tool_call.arguments
            elif not self.tools.has(tool_call.name):
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
                    policy = self.policy.authorize(self.tools.get(tool_call.name), arguments, step_id=f"chat:{self.memory.thread_id}")
                    if policy.outcome != "allow":
                        tool_result = _policy_message(policy.reason, policy.approval_id)
                        status = "blocked"
                        if policy.outcome == "approval_required" and policy.approval_id:
                            self.pending_chat_actions.save(self.memory.user_id, self.memory.thread_id, policy.approval_id, tool_call.name, arguments, user_text)
                            approval = self.pending_chat_actions.public(self.memory.user_id, self.memory.thread_id, policy.approval_id)
                            outcome.approvals.append(approval)
                            yield {"type": "approval", "approval": approval}
                    else:
                        invocation = self.tools.invoke(tool_call.name, arguments)
                        tool_result = invocation.text()
                        status = invocation.status
                        invocation_output = invocation.output
            self.action_log.record(
                tool_name=tool_call.name,
                status=status,
                arguments=arguments,
                result=tool_result,
            )
            tool_message = Message(role="tool", content=mark_untrusted_tool_output(tool_call.name, tool_result), name=tool_call.name, tool_call_id=tool_call.call_id)
            self.memory.append_message(tool_message)
            outcome.messages.append(tool_message)
            outcome.results.append(tool_result)
            outcome.tool_names.append(tool_call.name)
            outcome.statuses.append(status)
            outcome.calls[signature] = status
            artifact = self._artifact_from_tool(
                tool_call.name, arguments, status, invocation_output, user_text=user_text
            )
            if artifact is not None:
                outcome.artifacts.append(artifact)
            yield {"type": "status", "text": _tool_activity_text(tool_call.name, status), "tool": tool_call.name, "phase": status, "round": round_number}
            if tool_call.name=='ask_user' and status=='completed' and isinstance(invocation_output,dict):
                outcome.questions.append(invocation_output)
                yield {'type':'clarification','question':invocation_output}
            if outcome.approvals or outcome.questions:
                break
            if previous_count + len(outcome.tool_names) >= MAX_TOOL_CALLS:
                break
        return outcome

    def resume_chat_action_stream(self, approval_id: str):
        """Execute the exact approved action once, then continue the original request."""
        pending = self.pending_chat_actions.get(self.memory.user_id, self.memory.thread_id, approval_id)
        if pending is None:
            raise ValueError("Ожидающее действие не найдено.")
        if not self.pending_chat_actions.claim(self.memory.user_id, self.memory.thread_id, approval_id):
            raise ValueError("Это действие уже обработано.")
        self.last_turn_outcome = "running"
        self.last_turn_message_id = None
        outcome = ToolOutcome()
        call=ToolCall(pending["tool_name"],pending["arguments"],f"approved_{approval_id}")
        try:
            yield from self._execute_tool_calls_stream([call], pending["user_prompt"], outcome=outcome)
            if outcome.approvals:
                raise ValueError("Подтверждение истекло или не соответствует действию.")
            self.pending_chat_actions.finish(self.memory.user_id, self.memory.thread_id, approval_id, "completed", {"statuses":outcome.statuses,"artifacts":outcome.artifacts})
            yield {"type":"status","text":"Готовлю ответ по результату подтверждённого действия…","phase":"synthesizing"}
            messages=[Message("assistant","",tool_calls=[call]),*outcome.messages,Message("user","Подтверждённое действие уже выполнено. Продолжи исходную просьбу с его результата. Не выполняй завершённое действие повторно.")]
            yield from self.respond_stream(pending["user_prompt"],resume_outcome=outcome,resume_messages=messages)
        except BaseException:
            # A claimed side effect is never retried blindly after disconnect/crash.
            terminal_completed = getattr(self, "last_turn_outcome", None) == "completed"
            if not terminal_completed:
                self.pending_chat_actions.finish(self.memory.user_id, self.memory.thread_id, approval_id, "interrupted", {"statuses":outcome.statuses,"artifacts":outcome.artifacts})
            if outcome.artifacts and not terminal_completed:
                self.memory.append_message(Message("assistant","Подтверждённое действие завершилось; созданные файлы сохранены.",artifacts=outcome.artifacts))
            raise

    def _direct_tool_decision(self, user_text: str):
        # A compound request needs model synthesis after reading its evidence. The
        # deterministic task-step parser must not silently complete only its first verb.
        if re.search(r"\b(?:затем|после|сравни\w*|проанализируй|объясни|составь|then|compare|analyze|and)\b|\sи\s", user_text, re.IGNORECASE):
            return None
        decision = decide_step_action(
            TaskStep(id="direct-chat", task_id="direct-chat", description=user_text)
        )
        if decision.tool_name is None:
            return None
        if decision.tool_name in {"run_shell", "http_request", "execute_next_step", "browser_read", "research_read", "create_presentation"}:
            return None
        if not self.tools.has(decision.tool_name):
            return None
        if decision.tool_name in TASK_MUTATION_TOOLS and not self._uses_task_queue(user_text):
            return None
        if decision.tool_name == "read_file":
            try:
                self.files.resolve_download_path(str(decision.arguments.get("path", "")))
            except ValueError:
                # Phrases such as "покажи список файлов" are not filenames.
                # Let the model choose a tool rather than returning a parser error.
                return None
        return decision

    def resume_question_stream(self,question,answer):
        previous=getattr(self,'_clarification_original',None)
        self._clarification_original=question
        source=next((row for row in self.memory.database.list_messages(self.memory.thread_id) if row['id']==question['source_message_id']),None)
        original=user_authored_text(Message('user',source['content'],artifacts=source.get('artifacts',[]))) if source else None
        data={'original_request':original,'question':question['prompt'],'options':json.loads(question['options_json']),
            'attachments':[{'path':item.get('path'),'name':item.get('name')} for item in (source or {}).get('artifacts',[]) if item.get('_user_attachment')]}
        try:yield from self.respond_stream(answer,clarification=True,original_request=original,clarification_context=data)
        finally:self._clarification_original=previous

    def _uses_task_queue(self, user_text: str) -> bool:
        if self.task_queue_context is not None:
            return self.task_queue_context
        text = user_authored_text(Message('user', user_text, artifacts=list(getattr(self, 'incoming_attachments', []))))
        if _explicit_task_queue_intent(text):
            return True
        if re.fullmatch(r"\s*(?:да[,! ]*)?(?:продолжай|продолжим|дальше|приступай|continue|go ahead|resume)[.! ]*", text, re.I):
            return any(_explicit_task_queue_intent(user_authored_text(Message('user', row['content'], artifacts=row.get('artifacts', []))))
                       for row in self.memory.database.list_messages(self.memory.thread_id)
                       if row['role'] == 'user')
        return False

    def _try_direct_tool_action(self, user_text: str) -> ToolOutcome | None:
        decision = self._direct_tool_decision(user_text)
        if decision is None:
            return None
        return self._execute_tool_calls([ToolCall(decision.tool_name, decision.arguments)], user_text)

    def _normalize_new_file_overwrite(self, tool_name: str, arguments: dict) -> dict:
        if tool_name not in {"write_file", "create_docx", "create_presentation", "append_docx"}:
            return arguments
        prefix = getattr(self, "automation_output_prefix", "")
        if prefix:
            from pathlib import PurePosixPath, PureWindowsPath
            path = str(arguments.get("path") or "").replace("\\", "/")
            if (path and not PurePosixPath(path).is_absolute() and not PureWindowsPath(path).drive
                    and ".." not in PurePosixPath(path).parts and not path.startswith(prefix + "/")):
                arguments = {**arguments, "path": prefix + "/" + path}
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
        if getattr(self, "automation_disable_fallback_files", False):
            return
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
            outcome.statuses.append("failed")
            return
        path_match = re.search(r"([\w\-./\\\u0400-\u04ff]+\.docx)\b", user_text, flags=re.IGNORECASE)
        requested_path = path_match.group(1).replace("\\", "/") if path_match else "outputs/web_research.docx"
        relative_path = self._unique_docx_path(requested_path)
        query = search_result.splitlines()[0].partition(":")[2].strip()
        paragraphs = [_PRELIMINARY_RESEARCH] + [
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
        fallback = self._execute_tool_calls([ToolCall("create_docx", arguments)], user_text, previous=outcome)
        fallback.preliminary_research = bool(fallback.artifacts)
        outcome.merge(fallback)

    def _unique_docx_path(self, relative_path: str) -> str:
        candidate = Path(relative_path.strip().replace("\\", "/"))
        if candidate.is_absolute() or ".." in candidate.parts:
            return relative_path
        target = (self.documents.workspace_dir / candidate).resolve()
        workspace = self.documents.workspace_dir.resolve()
        if target != workspace and workspace not in target.parents:
            return relative_path
        if not target.exists():
            return candidate.as_posix()
        for suffix in range(2, 1000):
            next_candidate = candidate.with_name(f"{candidate.stem}_{suffix}{candidate.suffix}")
            if not (workspace / next_candidate).exists():
                return next_candidate.as_posix()
        return relative_path

    def _artifact_from_tool(
        self,
        tool_name: str,
        arguments: dict,
        status: str,
        output: object | None = None,
        *, user_text: str = "",
    ) -> dict | None:
        if status == "completed" or (tool_name == "propose_extension" and status == "failed"):
            if tool_name == "propose_extension" and isinstance(output, dict):
                candidate = output.get("review_artifact", {}).get("path")
            elif tool_name in {"transform_image", "edit_office_document"} and isinstance(output, dict):
                candidate = output.get("path")
            else:
                generated = self.tool_factory.artifact_for_invocation(tool_name, arguments)
                candidate = generated.get("path") if generated else None
            if candidate:
                try:
                    file = self.files.describe_file(str(candidate))
                    return {"path": file.path, "name": file.name, "extension": file.extension, "size_bytes": file.size_bytes}
                except ValueError:
                    return None
        if status == "completed" and tool_name in {
            "browser_read", "browser_interact", "desktop_snapshot", "desktop_interact",
        }:
            screenshot = output.get("screenshot") if isinstance(output, dict) else None
            if isinstance(screenshot, dict) and screenshot.get("path"):
                return {
                    "path": str(screenshot["path"]),
                    "name": str(screenshot.get("name") or Path(str(screenshot["path"])).name),
                    "extension": ".png",
                    "size_bytes": int(screenshot.get("size_bytes", 0)),
                }
        delivery_tools = {
            "write_file",
            "create_docx",
            "append_docx",
            "create_presentation",
        }
        if tool_name == "file_metadata" and wants_resend(user_text):
            same_thread = {item['path'] for item in self._previous_thread_artifacts(user_text)}
            if str(arguments.get('path', '')) in same_thread:
                delivery_tools.add("file_metadata")
        if status != "completed" or tool_name not in delivery_tools:
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

    def _missing_requested_docx(self, user_text: str, outcome: ToolOutcome) -> bool:
        return (wants_docx_creation(self._authored_request(user_text)) and not outcome.approvals
                and not any(item.get('extension', '').lower() == '.docx' for item in outcome.artifacts))

    def _authored_request(self, user_text: str) -> str:
        """Attachment names and document text must not become file-delivery intent."""
        artifacts = list(getattr(self, 'incoming_attachments', []))
        if artifacts:
            return user_authored_text(Message('user', user_text, artifacts=artifacts))
        for row in reversed(self.memory.database.list_messages(self.memory.thread_id)):
            if row['role'] == 'user' and row['content'] == user_text:
                return user_authored_text(Message('user', user_text, artifacts=row.get('artifacts', [])))
        return user_text

    def _previous_thread_artifacts(self, user_text: str = '') -> list[dict]:
        """Only a prior message in this thread can supply an implicit resend path."""
        requested = requested_file_names(self._authored_request(user_text))
        for row in reversed(self.memory.database.list_messages(self.memory.thread_id)):
            if row['role'] != 'assistant':
                continue
            candidates = [item.get('path', '') for item in row.get('artifacts', [])]
            candidates.extend(local_file_targets(row['content']))
            if requested:
                candidates = [path for path in candidates if Path(path).name.casefold() in requested]
            verified = []
            for path in dict.fromkeys(candidates):
                if not path:
                    continue
                try:
                    file = self.files.describe_file(path)
                    verified.append({'path': file.path, 'name': file.name, 'extension': file.extension, 'size_bytes': file.size_bytes})
                except ValueError:
                    pass
            if candidates:
                return verified
        return []

    def _verified_file_reply(self, user_text: str, text: str, outcome: ToolOutcome) -> str:
        user_text = self._authored_request(user_text)
        if wants_resend(user_text) and not outcome.artifacts:
            outcome.artifacts.extend(self._previous_thread_artifacts(user_text))
        if self._missing_requested_docx(user_text, outcome):
            quality_reason = next((result for name, result, status in reversed(list(zip(outcome.tool_names, outcome.results, outcome.statuses)))
                                   if name == 'research_quality_gate' and status == 'failed'), None)
            if quality_reason:
                return quality_reason
            outcome.results.append('Word-файл не создан: успешного результата файлового инструмента нет.')
            outcome.tool_names.append('file_delivery')
            outcome.statuses.append('failed')
            return 'Не удалось создать Word-файл в этом запросе. Подтверждённого файла для скачивания нет.'
        if wants_resend(user_text) and not outcome.artifacts:
            outcome.results.append('Не удалось подтвердить файл для повторного прикрепления в этом чате.')
            outcome.tool_names.append('file_delivery')
            outcome.statuses.append('failed')
            return 'Не нашёл доступного файла в этом чате для повторного прикрепления. Укажи его имя или прикрепи файл.'
        if wants_resend(user_text):
            return 'Файл доступен для скачивания ниже.'
        return normalize_artifact_links(text, outcome.artifacts)


def _tool_completion_text(outcome: ToolOutcome) -> str:
    if outcome.artifacts:
        if outcome.preliminary_research:
            return _PRELIMINARY_RESEARCH + " Файл с предварительными результатами можно скачать ниже."
        if outcome.statuses and outcome.statuses[-1] != "completed":
            return "Созданные файлы сохранены и доступны ниже. Последующее действие не завершилось; вся задача пока не выполнена."
        if len(outcome.artifacts) == 1:
            return (
                f"Готово — файл **{outcome.artifacts[0]['name']}** создан. "
                "Его можно скачать ниже."
            )
        return f"Готово — создано файлов: {len(outcome.artifacts)}. Их можно скачать ниже."
    if outcome.results:
        last_result = outcome.results[-1]
        last_tool = outcome.tool_names[-1] if outcome.tool_names else ""
        last_status = outcome.statuses[-1] if outcome.statuses else _tool_status(last_result)
        if last_tool == "create_docx" and last_status == "failed":
            return (
                "Исследование завершено, но не удалось создать Word-файл. "
                "Проверь имя файла и доступность рабочей папки; результаты поиска не потеряны."
            )
        if "no relevant results" in last_result.lower():
            return (
                "Не удалось подтвердить достаточно релевантных результатов по этому запросу. "
                "Итоговый файл не создан; уточни каталог, жанр, регион или критерии доступности."
            )
        if last_result.startswith("Search query:"):
            return (
                "Поиск завершён, но не удалось корректно оформить итоговый ответ. "
                "Попробуй уточнить тему или сократить количество результатов."
            )
        http_error = _http_error_status(last_result)
        if http_error is not None:
            return (
                f"Не удалось получить данные с выбранного сайта: он вернул HTTP {http_error}. "
                "Попробуй изменить запрос или использовать другой источник."
            )
        if "не смог выполнить действие" in last_result.lower():
            return "Не удалось выполнить интернет-запрос. Попробуй изменить запрос или источник."
        return last_result
    return "Не смог сформировать ответ."


def _provider_failure_artifact_text(outcome: ToolOutcome, error: ProviderError) -> str:
    return f"Созданные файлы сохранены и доступны ниже. Продолжить выполнение не удалось: {error.user_message} Вся задача может быть не завершена."


def _explicit_task_queue_intent(text: str) -> bool:
    return bool(re.search(
        r"(?:созда\w*|добав\w*|постав\w*|запланир\w*|обнов\w*|измени|удал\w*|закро\w*|заверш\w*|выполн\w*|продолж\w*|начн\w*|запуст\w*)\s+"
        r"(?:(?:нов\w*|текущ\w*|активн\w*|прошл\w*|следующ\w*)\s+)?(?:задач\w*|очеред\w*)\b|"
        r"(?:список|покажи|текущая|активная)\s+(?:моих\s+)?задач\w*|"
        r"(?:выполн\w*|запуст\w*|продолж\w*)\s+(?:следующ\w*|текущ\w*)\s+шаг\w*|"
        r"(?:продолж\w*|возобнов\w*)\s+работ\w*\b|"
        r"\b(?:create|add|schedule|update|delete|complete|finish|execute|resume|continue|start|show|list)\s+"
        r"(?:(?:a|an|the|my|current|next|active|queued|new)\s+)*(?:tasks?|queue)\b|"
        r"\b(?:task[_ ]id|step[_ ]id|next step)\b",
        text, re.I))


def _completed_turn_outcome(outcome: ToolOutcome) -> str:
    if outcome.questions:
        return 'waiting_clarification'
    if outcome.approvals:
        return "waiting_approval"
    if outcome.preliminary_research:
        return "partial"
    extension_statuses = [status for name, status in zip(outcome.tool_names, outcome.statuses)
                          if name in {"propose_extension", "install_extension"}]
    if extension_statuses and extension_statuses[-1] != "completed":
        if extension_statuses[-1] == "cancelled":
            return "cancelled"
        return "partial" if outcome.artifacts else "failed"
    if outcome.statuses and outcome.statuses[-1] != "completed":
        if outcome.statuses[-1] == "cancelled":
            return "cancelled"
        return "partial" if outcome.artifacts else "failed"
    return "completed"


def _stop_tool_loop(outcome: ToolOutcome) -> bool:
    return "blocked" in outcome.statuses or len(outcome.tool_names) >= MAX_TOOL_CALLS


def _searches_since_read(*outcomes: ToolOutcome | None) -> int:
    count = 0
    actions = [(name, status) for outcome in outcomes if outcome is not None for name, status in zip(outcome.tool_names, outcome.statuses)]
    for name, status in reversed(actions):
        if status == "completed" and name in {"research_read", "browser_read", "http_request", "analyze_image"}:
            break
        if name == "web_search":
            count += 1
    return count


def _turn_phase(outcome: ToolOutcome, attempt: int) -> str:
    searches = _searches_since_read(outcome)
    if attempt >= MAX_TOOL_ROUNDS - 1 or searches > MAX_SEARCHES_BEFORE_READ:
        return "final"
    return "read_sources" if searches >= MAX_SEARCHES_BEFORE_READ or _repeated_discovery(outcome) else "normal"


def _repeated_discovery(*outcomes: ToolOutcome | None) -> bool:
    recent = []
    actions = [(name, status, result) for outcome in outcomes if outcome is not None
               for name, status, result in zip(outcome.tool_names, outcome.statuses, outcome.results)]
    for name, status, result in reversed(actions):
        if status == "completed" and name in {"research_read", "browser_read", "http_request", "analyze_image"}:
            break
        if name == "web_search" and status == "completed":
            urls = frozenset(re.findall(r"(?m)^\s*URL:\s*(https?://\S+)", result))
            if urls:
                recent.append(urls)
                if len(recent) == 2:
                    return recent[0] == recent[1]
    return False


def _tools_for_phase(tools: list[dict], phase: str) -> list[dict]:
    if phase == "final":
        return []
    if phase == "read_sources":
        return [tool for tool in tools if tool["name"] != "web_search"]
    return tools


def _runtime_system_prompt(system_prompt: str, phase: str) -> str:
    # Guidance is ephemeral control state, never an additional user request.
    # Reading a source resets the phase and must remove the search restriction.
    if phase == "normal":
        return system_prompt
    hint = _FINALIZE_FROM_EVIDENCE if phase == "final" else _READ_SOURCE_AFTER_SEARCH
    return (system_prompt + "\n\n[Внутреннее управление текущим шагом агента]\n"
            "Это временная инструкция среды выполнения, а не просьба или ограничение пользователя. "
            "Не приписывай её пользователю и не описывай её как его указание.\n" + hint)


def _unfinished_completion_text(outcome: ToolOutcome, user_text: str = "") -> str:
    if not outcome.artifacts:
        for name, status, value in zip(outcome.tool_names, outcome.statuses, outcome.results):
            if name == "analyze_image" and status == "completed":
                try:
                    observation = json.loads(value).get("text", "")
                except (ValueError, AttributeError):
                    observation = ""
                if observation:
                    gap = ("Точную модель и актуальную цену по проверенной карточке подтвердить не удалось. "
                           if re.search(r"цен|стоим|price|cost", user_text, re.I) else
                           "Точную идентификацию по проверенному источнику подтвердить не удалось. ")
                    return (
                        "По изображению получено предварительное описание:\n\n" + str(observation)[:1800]
                        + "\n\n" + gap + "Описание изображения может содержать ошибку; визуальное сходство не подтверждает точный ID или артикул. "
                        "Поможет крупный снимок отличительных деталей или маркировки."
                    )
    if outcome.statuses and outcome.statuses[-1] == "blocked":
        explanation = outcome.results[-1] if outcome.results else "Действие ожидает подтверждения."
        if outcome.artifacts:
            return explanation + "\n\nСозданные файлы доступны ниже; вся задача ещё не завершена."
        return explanation
    result = "Достигнут предел шагов агента. Задача пока не завершена; полученные результаты сохранены."
    if outcome.artifacts:
        result += " Созданные файлы доступны ниже."
    elif outcome.statuses and all(status != "completed" for status in outcome.statuses):
        result = _tool_completion_text(outcome)
    return result


def _tool_activity_text(name: str, status: str = "started") -> str:
    labels = {
        "web_search": "Ищу информацию в интернете",
        "http_request": "Читаю веб-источник",
        "research_read": "Проверяю источник",
        "browser_read": "Открываю страницу в браузере",
        "browser_interact": "Работаю со страницей в браузере",
        "read_file": "Читаю файл",
        "list_workspace_files": "Проверяю файлы рабочей папки",
        "write_file": "Создаю файл",
        "create_docx": "Создаю документ Word",
        "append_docx": "Дополняю документ Word",
        "create_presentation": "Создаю презентацию",
        "analyze_image": "Анализирую изображение",
        "inspect_image": "Проверяю параметры изображения",
        "transform_image": "Подготавливаю изображение",
        "inspect_office_document": "Читаю структуру документа",
        "edit_office_document": "Создаю исправленную копию документа",
        "tool_search": "Проверяю готовые инструменты и навыки",
        "propose_extension": "Подготавливаю инструмент для проверки",
        "install_extension": "Проверяю и устанавливаю подтверждённое расширение",
        "run_shell": "Выполняю команду",
        "remember_note": "Сохраняю запись памяти",
        "recall_notes": "Проверяю память",
    }
    label = labels.get(name, f"Выполняю {name}")
    if status == "started":
        return label + "…"
    suffix = {"completed": "готово", "blocked": "ожидает подтверждения или уточнения", "cancelled": "отменено"}.get(status, "не удалось выполнить")
    return f"{label} — {suffix}."


def _policy_message(reason: str, approval_id: str | None) -> str:
    suffix = f" Approval ID: {approval_id}." if approval_id else ""
    return f"{reason}{suffix}"


def _http_error_status(result: str) -> int | None:
    for line in result.splitlines():
        if line.startswith("Status:"):
            try:
                status = int(line.partition(":")[2].strip())
            except ValueError:
                return None
            return status if status >= 400 else None
    return None


_MISSING_DOCX = (
    'Пользователь просил создать Word-документ в текущем запросе, но успешного файлового результата нет. '
    'Не выдавай file_metadata существующего файла за создание нового. Вызови create_docx с содержимым '
    'именно этой просьбы и новым путём outputs/*.docx (overwrite=false), либо честно сообщи о препятствии. '
    'Не создавай и не изменяй очередь задач ради обычного документа.'
)

_CONTINUE_AFTER_TOOL = (
    "Продолжи исходную задачу пользователя на основе уже полученных результатов. "
    "Не повторяй тот же поиск. Если пользователь запросил документ или другой файл, "
    "создай его соответствующим инструментом с кратким содержанием и ссылками на источники. "
    "Если инструмент завершился ошибкой, объясни её кратко без сырого технического вывода."
)

_READ_SOURCE_AFTER_SEARCH = (
    "Сейчас выполнено несколько поисков подряд без чтения источников. На этом шаге сначала прочитай источник. "
    "Открой наиболее подходящую уже найденную официальную страницу через research_read или browser_read "
    "и проверь сведения на ней. После успешного чтения поиск снова доступен: при необходимости ищи "
    "другие независимые источники или уточняй запрос по проверенным сведениям. "
    "Если подходящего источника нет, закончи ответ по имеющимся данным: "
    "отдели видимые признаки и гипотезы, честно укажи, что точная модель/цена не подтверждены. "
    "Результаты других брендов и серия, предположенная по фотографии, не являются подтверждением модели."
)

_FINALIZE_FROM_EVIDENCE = (
    "Заверши исходный запрос сейчас на основе уже полученных результатов. Новые инструменты недоступны: "
    "не вызывай их и не обещай будущую проверку. Дай полезный итог, сохрани оговорки об уверенности, "
    "приведи только прочитанные источники и созданные файлы. Неподтвержденные цену, артикул и точную модель "
    "не придумывай. Если данных недостаточно, объясни конкретный пробел и какое уточнение поможет."
)

_PRELIMINARY_RESEARCH = "Предварительная подборка по результатам поиска; содержимое страниц не проверено."


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
