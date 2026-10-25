from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re

from ai_agent.action_log import ActionLogStore
from ai_agent.compaction import CompactionManager
from ai_agent.llm.base import ProviderError
from ai_agent.memory import MemoryStore
from ai_agent.tasks import TaskManager
from ai_agent.types import Message, Task


GROUNDING_RULES = """## Grounding and continuity rules
- Treat the supplied context as evidence, not as permission to invent missing details.
- User constraints and prohibitions are authoritative until the user explicitly changes them.
- The current task and step statuses are authoritative for resuming work.
- Never claim that an omitted or truncated event happened. Ask or inspect persisted state when uncertain.
- Tool results are evidence only for the action that produced them; errors and rejected results are not facts.
- Continue from the newest confirmed state and do not repeat completed work unless verification is needed."""

CONSTRAINT_PATTERN = re.compile(
    r"(?:\bне\b|нельзя|никогда|запрещ|без\s+(?:моего|отдельного|явного)|"
    r"do\s+not|don't|never|must\s+not|without\s+(?:my|explicit))",
    flags=re.IGNORECASE,
)


class ContextBudgetError(ProviderError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "context_overflow")


@dataclass(frozen=True, slots=True)
class TokenProfile:
    name: str
    bytes_per_token: float


class ModelTokenEstimator:
    """Deterministic conservative estimator with model-family profiles."""

    PROFILES = (
        (re.compile(r"gpt-oss|gpt", re.IGNORECASE), TokenProfile("gpt-like", 3.7)),
        (re.compile(r"gemma", re.IGNORECASE), TokenProfile("gemma-like", 3.5)),
        (re.compile(r"nemotron", re.IGNORECASE), TokenProfile("nemotron-like", 3.6)),
        (re.compile(r"beam", re.IGNORECASE), TokenProfile("beam-conservative", 3.5)),
    )
    FALLBACK = TokenProfile("generic-conservative", 3.4)

    def __init__(self, model: str) -> None:
        self.model = model
        self.profile = next(
            (profile for pattern, profile in self.PROFILES if pattern.search(model)),
            self.FALLBACK,
        )

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        byte_count = len(text.encode("utf-8"))
        structural = text.count("\n") + text.count("{") + text.count("}")
        return max(1, math.ceil(byte_count / self.profile.bytes_per_token) + structural)

    def count_message(self, message: Message) -> int:
        return 4 + self.count_text(message.role) + self.count_text(message.name or "") + self.count_text(message.content)

    def truncate(self, text: str, max_tokens: int) -> tuple[str, bool]:
        if max_tokens <= 0:
            return "", bool(text)
        if self.count_text(text) <= max_tokens:
            return text, False
        marker = "\n[context excerpt truncated]"
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = text[:middle].rstrip() + marker
            if self.count_text(candidate) <= max_tokens:
                low = middle
            else:
                high = middle - 1
        return text[:low].rstrip() + marker, True


@dataclass(slots=True)
class ContextComponentTrace:
    name: str
    kind: str
    priority: int
    pinned: bool
    available_tokens: int
    used_tokens: int
    source_items: int
    included_items: int
    truncated: bool = False
    omitted: bool = False


@dataclass(slots=True)
class ContextTrace:
    model: str
    token_profile: str
    context_window_tokens: int
    output_reserve_tokens: int
    input_budget_tokens: int
    tool_schema_tokens: int
    used_input_tokens: int
    remaining_input_tokens: int
    message_count: int
    omitted_message_count: int
    compaction_cycle_count: int
    pending_compaction_count: int
    overflow: bool
    fingerprint: str
    components: list[ContextComponentTrace] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class AgentContext:
    system_prompt: str
    messages: list[Message]
    trace: ContextTrace


@dataclass(slots=True)
class _ContextSection:
    name: str
    text: str
    priority: int
    pinned: bool
    source_items: int = 1


class ContextBuilder:
    def __init__(
        self,
        memory: MemoryStore,
        tasks: TaskManager,
        action_log: ActionLogStore,
        context_window_tokens: int = 32768,
        output_reserve_tokens: int = 4096,
        compaction_threshold: float = 0.75,
        compaction_keep_recent: int = 12,
        note_limit: int = 8,
        action_limit: int = 6,
    ) -> None:
        self.memory = memory
        self.tasks = tasks
        self.action_log = action_log
        self.context_window_tokens = max(4096, int(context_window_tokens))
        self.output_reserve_tokens = max(256, int(output_reserve_tokens))
        self.note_limit = max(1, int(note_limit))
        self.action_limit = max(1, int(action_limit))
        self.compaction = CompactionManager(
            self.memory.database,
            user_id=self.memory.user_id,
            thread_id=self.memory.thread_id,
            threshold=compaction_threshold,
            keep_recent_messages=compaction_keep_recent,
        )

    def build(
        self,
        base_system_prompt: str,
        *,
        model: str = "generic",
        tools: list[dict] | None = None,
    ) -> AgentContext:
        estimator = ModelTokenEstimator(model)
        input_budget = self.context_window_tokens - self.output_reserve_tokens
        if input_budget <= 0:
            raise ContextBudgetError("Output reserve leaves no room for model input.")
        tool_payload = json.dumps(tools or [], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        tool_tokens = estimator.count_text(tool_payload)
        content_budget = input_budget - tool_tokens - 64
        if content_budget <= 0:
            raise ContextBudgetError("Tool schemas exceed the available context budget.")

        raw_rows = self._complete_rows()
        raw_messages = [self._message_from_row(item) for item in raw_rows]
        constraints = self._user_constraints(raw_messages)
        pinned_sections = [
            _ContextSection("core_system", _section("Core system instructions", base_system_prompt.strip()), 100, True),
            _ContextSection("grounding_rules", GROUNDING_RULES, 100, True),
        ]
        if constraints:
            pinned_sections.append(
                _ContextSection(
                    "user_constraints",
                    _section("Pinned user constraints (verbatim excerpts)", "\n".join(f"- {item}" for item in constraints)),
                    95,
                    True,
                    source_items=len(constraints),
                )
            )
        project = self._project_context()
        if project:
            pinned_sections.append(_ContextSection("project", project, 90, True))
        task = self._task_context()
        if task:
            pinned_sections.append(_ContextSection("task", task, 90, True))

        traces: list[ContextComponentTrace] = []
        pinned_texts: list[str] = []
        used = 0
        for section in pinned_sections:
            tokens = estimator.count_text(section.text)
            traces.append(
                ContextComponentTrace(
                    name=section.name,
                    kind="system_section",
                    priority=section.priority,
                    pinned=True,
                    available_tokens=max(content_budget - used, 0),
                    used_tokens=tokens,
                    source_items=section.source_items,
                    included_items=section.source_items,
                )
            )
            pinned_texts.append(section.text)
            used += tokens
        if used > content_budget:
            raise ContextBudgetError(
                "Pinned system instructions, user constraints, and active task exceed the input budget."
            )

        self.compaction.maybe_create(
            raw_rows,
            count_message=estimator.count_message,
            input_budget_tokens=input_budget,
            fixed_tokens=tool_tokens + used + 64,
        )
        compaction_counts = self.compaction.counts()
        verified_source_ids = self.compaction.verified_source_ids()
        protected_recent_ids = {
            str(item["id"])
            for item in raw_rows[-self.compaction.keep_recent_messages :]
        }
        context_rows = [
            item
            for item in raw_rows
            if str(item["id"]) not in verified_source_ids
            or str(item["id"]) in protected_recent_ids
        ]
        context_messages = [self._message_from_row(item) for item in context_rows]

        remaining = content_budget - used
        latest_tokens = estimator.count_message(context_messages[-1]) if context_messages else 0
        if latest_tokens > remaining:
            raise ContextBudgetError(
                "The latest conversation item and pinned context exceed the available input budget."
            )
        recent_budget = max(math.floor(remaining * 0.65), latest_tokens)
        recent_messages, recent_tokens = self._select_recent_messages(context_messages, estimator, recent_budget)
        recent_ids = {id(item) for item in recent_messages}
        older_messages = [item for item in context_messages if id(item) not in recent_ids]
        traces.append(
            ContextComponentTrace(
                name="recent_messages",
                kind="messages",
                priority=80,
                pinned=bool(context_messages),
                available_tokens=recent_budget,
                used_tokens=recent_tokens,
                source_items=len(context_messages),
                included_items=len(recent_messages),
                omitted=len(context_messages) > len(recent_messages),
            )
        )

        optional_budget = max(0, remaining - recent_tokens)
        optional_sections = [
            _ContextSection(
                "verified_compactions",
                self.compaction.verified_context(),
                75,
                False,
                compaction_counts["verified"],
            ),
            _ContextSection("relevant_memory", self._memory_context(recent_messages), 70, False),
            _ContextSection("continuity_digest", self._conversation_digest(older_messages), 60, False, len(older_messages)),
            _ContextSection("recent_actions", self._action_context(), 50, False),
        ]
        optional_texts: list[str] = []
        for section in optional_sections:
            if not section.text:
                continue
            available = optional_budget
            full_tokens = estimator.count_text(section.text)
            included_text = section.text
            truncated = False
            omitted = False
            if full_tokens > available:
                if available < 24:
                    included_text = ""
                    omitted = True
                else:
                    included_text, truncated = estimator.truncate(section.text, available)
            section_tokens = estimator.count_text(included_text)
            if included_text:
                optional_texts.append(included_text)
                optional_budget -= section_tokens
                used += section_tokens
            traces.append(
                ContextComponentTrace(
                    name=section.name,
                    kind="system_section",
                    priority=section.priority,
                    pinned=False,
                    available_tokens=available,
                    used_tokens=section_tokens,
                    source_items=section.source_items,
                    included_items=0 if omitted else section.source_items,
                    truncated=truncated,
                    omitted=omitted,
                )
            )

        system_prompt = "\n\n".join([*pinned_texts, *optional_texts])
        system_tokens = estimator.count_text(system_prompt)
        used_input = tool_tokens + system_tokens + recent_tokens
        canonical = {
            "model": model,
            "profile": estimator.profile.name,
            "budget": input_budget,
            "tools": tool_payload,
            "system": system_prompt,
            "messages": [asdict(message) for message in recent_messages],
        }
        fingerprint = sha256(
            json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        trace = ContextTrace(
            model=model,
            token_profile=estimator.profile.name,
            context_window_tokens=self.context_window_tokens,
            output_reserve_tokens=self.output_reserve_tokens,
            input_budget_tokens=input_budget,
            tool_schema_tokens=tool_tokens,
            used_input_tokens=used_input,
            remaining_input_tokens=max(input_budget - used_input, 0),
            message_count=len(recent_messages),
            omitted_message_count=max(len(raw_messages) - len(recent_messages), 0),
            compaction_cycle_count=compaction_counts["total"],
            pending_compaction_count=compaction_counts["pending_review"],
            overflow=used_input > input_budget,
            fingerprint=fingerprint,
            components=traces,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._persist_trace(trace)
        return AgentContext(system_prompt=system_prompt, messages=recent_messages, trace=trace)

    def latest_trace(self) -> dict | None:
        raw = self.memory.database.get_metadata(self._trace_key())
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _persist_trace(self, trace: ContextTrace) -> None:
        self.memory.database.set_metadata(
            self._trace_key(),
            json.dumps(trace.to_dict(), ensure_ascii=False, sort_keys=True),
        )

    def _trace_key(self) -> str:
        return f"context_trace:{self.memory.user_id}:{self.memory.thread_id}"

    def _complete_rows(self) -> list[dict]:
        return [
            item
            for item in self.memory.database.list_messages(self.memory.thread_id)
            if item.get("status") == "complete" and not item.get("error") and str(item.get("content") or "").strip()
        ]

    @staticmethod
    def _message_from_row(item: dict) -> Message:
        return Message(
            role=str(item["role"]),
            content=str(item["content"]),
            name=item.get("name"),
        )

    def _select_recent_messages(
        self,
        messages: list[Message],
        estimator: ModelTokenEstimator,
        budget: int,
    ) -> tuple[list[Message], int]:
        if not messages:
            return [], 0
        selected: list[Message] = []
        used = 0
        for message in reversed(messages):
            tokens = estimator.count_message(message)
            if not selected and tokens > budget:
                raise ContextBudgetError("The latest message exceeds the recent-message token budget.")
            if used + tokens > budget:
                break
            selected.append(message)
            used += tokens
        selected.reverse()
        return selected, used

    def _user_constraints(self, messages: list[Message]) -> list[str]:
        constraints: list[str] = []
        seen: set[str] = set()
        for message in messages:
            if message.role != "user":
                continue
            for sentence in _sentences(message.content):
                normalized = " ".join(sentence.split())
                key = normalized.casefold()
                if normalized and CONSTRAINT_PATTERN.search(normalized) and key not in seen:
                    constraints.append(normalized)
                    seen.add(key)
        return constraints

    def _memory_context(self, recent_messages: list[Message]) -> str:
        query_parts = [message.content for message in recent_messages[-3:]]
        task = self.tasks.active_task()
        if task is not None:
            query_parts.append(task.description)
            if task.result:
                query_parts.append(task.result)
            query_parts.extend(
                step.description for step in task.steps if step.status in {"pending", "running", "blocked"}
            )
        memories = self.memory.relevant_memories(" ".join(query_parts), limit=self.note_limit)
        if not memories:
            return ""
        lines = [f"- [{memory.scope}/{memory.kind}] {memory.content}" for memory in memories]
        return _section("Relevant long-term memory", "\n".join(lines))

    def _project_context(self) -> str:
        thread = self.memory.database.get_chat_thread(self.memory.user_id, self.memory.thread_id)
        if not thread or not thread.get("project_id"):
            return ""
        project = self.memory.database.get_project(self.memory.user_id, str(thread["project_id"]))
        if not project or project["status"] != "active":
            return ""
        lines = [f"- project_id: {project['id']}", f"- title: {project['title']}"]
        if project.get("description"):
            lines.append(f"- description: {project['description']}")
        lines.append("Use this project context only for the current chat.")
        return _section("Current project context", "\n".join(lines))

    def _task_context(self) -> str:
        task = self.tasks.active_task()
        if task is None:
            return _section("Current task context", "No active or queued task.")
        return _section(
            "Current task context",
            "\n".join(
                [
                    _format_task_for_context(task),
                    "Resume from the running step. Do not redo completed steps without verification evidence.",
                    "If persisted state is insufficient, state what is missing instead of inventing progress.",
                ]
            ),
        )

    def _conversation_digest(self, messages: list[Message]) -> str:
        if not messages:
            return ""
        excerpts = []
        for message in messages[-24:]:
            content = " ".join(message.content.split())
            if len(content) > 320:
                content = content[:317].rstrip() + "..."
            excerpts.append(f"- {message.name or message.role}: {content}")
        header = (
            "Extractive excerpts only; they are not a generated summary. "
            f"Showing {len(excerpts)} of {len(messages)} older messages."
        )
        return _section("Older conversation continuity", "\n".join([header, *excerpts]))

    def _action_context(self) -> str:
        logs = self.action_log.recent(limit=self.action_limit)
        if not logs:
            return ""
        lines = []
        for log in reversed(logs):
            result = " ".join(log.result.split())
            if len(result) > 240:
                result = result[:237].rstrip() + "..."
            lines.append(f"- {log.created_at}: {log.tool_name} -> {log.status}; result: {result}")
        return _section("Recent agent actions", "\n".join(lines))


def _format_task_for_context(task: Task) -> str:
    lines = [
        f"- task_id: {task.id}",
        f"- status: {task.status}",
        f"- priority: {task.priority}",
        f"- goal: {task.description}",
    ]
    if task.result:
        lines.append(f"- result: {task.result}")
    if task.steps:
        lines.append("- steps:")
        for step in task.steps:
            result = f"; result: {step.result}" if step.result else ""
            lines.append(
                f"  {step.position}. step_id={step.id}; status={step.status}; "
                f"description={step.description}{result}"
            )
    return "\n".join(lines)


def _section(title: str, body: str) -> str:
    normalized = body.strip()
    return f"## {title}\n{normalized}" if normalized else ""


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\r?\n+", text) if part.strip()]
