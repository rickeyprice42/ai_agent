from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, min_length=1, max_length=120)


class ChatResponse(BaseModel):
    reply: str
    thread_id: str


class ExecuteStepResponse(BaseModel):
    result: str


class ApproveStepResponse(BaseModel):
    result: str


class ApprovalRequestItem(BaseModel):
    id: str
    user_id: str
    task_id: str | None = None
    step_id: str | None = None
    tool_name: str
    arguments_digest: str
    arguments: dict = Field(default_factory=dict)
    scope: dict = Field(default_factory=dict)
    risk: str
    status: str
    expires_at: str
    approved_at: str | None = None
    decided_at: str | None = None
    consumed_at: str | None = None
    created_at: str


class ApprovalDecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|denied|revoked)$")


class SecuritySettingsResponse(BaseModel):
    emergency_stop: bool
    updated_at: str


class EmergencyStopRequest(BaseModel):
    enabled: bool


class PermissionGrantItem(BaseModel):
    id: str
    user_id: str
    permission: str
    scope: dict = Field(default_factory=dict)
    status: str
    expires_at: str
    revoked_at: str | None = None
    created_at: str


class PermissionGrantCreateRequest(BaseModel):
    permission: str = Field(..., min_length=1, max_length=120)
    scope: dict = Field(default_factory=dict)
    expires_in_minutes: int = Field(default=30, ge=1, le=1440)


class ExtensionProposalCreateRequest(BaseModel):
    kind: str = Field(..., pattern="^(skill|tool)$")
    name: str = Field(..., min_length=2, max_length=64, pattern="^[a-z][a-z0-9_]+$")
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    description: str = Field(..., min_length=3, max_length=500)
    manifest: dict = Field(default_factory=dict)
    permissions: list[dict] = Field(default_factory=list)
    threat_model: str = Field(..., min_length=20, max_length=4000)
    source_code: str = Field(default="", max_length=32000)
    skill_markdown: str = Field(default="", max_length=32000)
    tests: list[dict] = Field(default_factory=list, max_length=50)


class ExtensionProposalItem(BaseModel):
    id: str
    user_id: str
    kind: str
    name: str
    version: str
    description: str
    manifest: dict = Field(default_factory=dict)
    permissions: list[dict] = Field(default_factory=list)
    threat_model: str
    source_code: str = ""
    skill_markdown: str = ""
    tests: list[dict] = Field(default_factory=list)
    checks: list[dict] = Field(default_factory=list)
    diff: str = ""
    status: str
    installed_at: str | None = None
    created_at: str
    updated_at: str


class ExtensionInstallRequest(BaseModel):
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")


class ExtensionInstallResponse(BaseModel):
    status: str
    proposal: ExtensionProposalItem | None = None
    approval_id: str | None = None
    message: str = ""


class ExtensionRollbackRequest(BaseModel):
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")


class OpenWorkspaceResponse(BaseModel):
    result: str


class ConnectorItem(BaseModel):
    id: str
    name: str
    description: str
    kind: str
    enabled: bool
    connected: bool
    status: str
    scopes: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int


class ConnectorStateRequest(BaseModel):
    enabled: bool


class UserProfile(BaseModel):
    id: str
    email: str = ""
    username: str = ""
    display_name: str
    auth_provider: str = "password"


class AuthRequest(BaseModel):
    login: str = Field(..., min_length=2, max_length=120)
    password: str = Field(..., min_length=8, max_length=256)


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=160)
    username: str = Field(..., min_length=2, max_length=80)
    password: str = Field(..., min_length=8, max_length=256)
    display_name: str = Field(..., min_length=2, max_length=120)


class AuthResponse(BaseModel):
    token: str
    user: UserProfile


class OAuthProviderResponse(BaseModel):
    provider: str
    enabled: bool
    auth_url: str | None = None
    message: str


class ModelProviderOption(BaseModel):
    provider: str
    label: str
    description: str
    models: list[str]
    capabilities: list[str] = Field(default_factory=list)


class ModelSettings(BaseModel):
    provider: str
    model_name: str
    ollama_url: str = ""


class ModelSettingsRequest(BaseModel):
    provider: str = Field(..., min_length=2, max_length=40)
    model_name: str = Field(..., min_length=2, max_length=120)
    ollama_url: str | None = Field(default=None, max_length=300)


class ChatThreadItem(BaseModel):
    id: str
    user_id: str
    title: str
    status: str
    archived_at: str | None = None
    deleted_at: str | None = None
    pinned: bool = False
    project_id: str | None = None
    memory_enabled: bool = True
    memory_saved_at: str | None = None
    created_at: str
    updated_at: str
    message_count: int = 0
    last_message_at: str | None = None


class ChatThreadCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    project_id: str | None = Field(default=None, max_length=120)


class ChatThreadUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    pinned: bool | None = None
    project_id: str | None = Field(default=None, max_length=120)
    clear_project: bool = False
    memory_enabled: bool | None = None


class ChatThreadActionResponse(BaseModel):
    thread: ChatThreadItem


class ClearChatResponse(BaseModel):
    deleted_messages: int


class ChatMemoryResponse(BaseModel):
    result: str
    thread: ChatThreadItem


class MemoryCounts(BaseModel):
    notes: int = 0
    global_notes: int = 0
    project_notes: int = 0
    chat_notes: int = 0
    memory_index: int = 0
    chat_messages: int = 0
    tasks: int = 0
    action_logs: int = 0
    personal_records: int = 0
    health_records: int = 0


class MemoryNoteItem(BaseModel):
    id: str
    content: str
    scope: str
    project_id: str | None = None
    source_thread_id: str | None = None
    created_at: str


class MemoryOverviewResponse(BaseModel):
    counts: MemoryCounts
    notes: list[MemoryNoteItem]


class MemorySourceItem(BaseModel):
    id: str
    source_type: str
    source_id: str | None = None
    source_uri: str | None = None
    created_at: str


class PersonalMemoryItem(BaseModel):
    id: str
    kind: str
    content: str
    scope: str
    project_id: str | None = None
    thread_id: str | None = None
    memory_key: str | None = None
    auto_generated: bool = False
    confidence: float
    status: str
    consent_status: str
    consented_at: str | None = None
    revoked_at: str | None = None
    expires_at: str | None = None
    sensitive: bool = False
    created_at: str
    updated_at: str
    provenance: list[MemorySourceItem] = Field(default_factory=list)


class PersonalMemoryCreateRequest(BaseModel):
    kind: str = Field(..., max_length=40)
    content: str = Field(..., min_length=1, max_length=8000)
    scope: str = Field(default="global", max_length=40)
    project_id: str | None = Field(default=None, max_length=120)
    thread_id: str | None = Field(default=None, max_length=120)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    expires_at: str | None = Field(default=None, max_length=80)
    sensitive: bool = False
    consent_granted: bool = False
    source_type: str = Field(default="user", max_length=80)
    source_id: str | None = Field(default=None, max_length=240)
    source_uri: str | None = Field(default=None, max_length=2000)


class PersonalMemoryUpdateRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=8000)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: str | None = Field(default=None, max_length=40)
    expires_at: str | None = Field(default=None, max_length=80)
    clear_expiry: bool = False


class MemoryRetrievalTraceResponse(BaseModel):
    query_term_count: int = 0
    candidate_count: int = 0
    result_count: int = 0
    estimated_tokens: int = 0
    latency_ms: float = 0.0
    source_ids: list[str] = Field(default_factory=list)
    conflict_count: int = 0


class AutonomousMemorySettings(BaseModel):
    autonomous_enabled: bool = False
    health_autonomous_enabled: bool = False
    updated_at: str = ""


class AutonomousMemorySettingsRequest(BaseModel):
    autonomous_enabled: bool
    health_autonomous_enabled: bool = False


class MemorySummaryResponse(BaseModel):
    generated_at: str
    settings: AutonomousMemorySettings
    sections: dict[str, list[PersonalMemoryItem]]
    record_count: int


class MemoryExtractionTraceResponse(BaseModel):
    enabled: bool = False
    candidate_count: int = 0
    saved_count: int = 0
    merged_count: int = 0
    superseded_count: int = 0
    rejected_count: int = 0
    pending_count: int = 0
    estimated_tokens: int = 0
    latency_ms: float = 0.0
    reasons: dict[str, int] = Field(default_factory=dict)


class ClearMemoryRequest(BaseModel):
    scope: str = Field(default="all", max_length=40)
    thread_id: str | None = Field(default=None, max_length=120)
    project_id: str | None = Field(default=None, max_length=120)
    include_action_logs: bool = False


class ClearMemoryResponse(BaseModel):
    deleted_notes: int = 0
    deleted_memory_index: int = 0
    deleted_action_logs: int = 0
    updated_threads: int = 0


class ProjectItem(BaseModel):
    id: str
    user_id: str
    title: str
    description: str = ""
    status: str
    shared: bool = False
    agent_mode: str = "mention_only"
    revision: int = 0
    role: str = "owner"
    created_at: str
    updated_at: str
    chat_count: int = 0


class ProjectCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)


class ProjectUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    agent_mode: str | None = Field(default=None, pattern="^(mention_only|explicit|off)$")
    expected_revision: int | None = Field(default=None, ge=0)


class ProjectActionResponse(BaseModel):
    project: ProjectItem


class ProjectMemberItem(BaseModel):
    project_id: str
    user_id: str
    role: str
    status: str
    email: str = ""
    username: str = ""
    display_name: str = ""
    joined_at: str
    removed_at: str | None = None


class ProjectInvitationCreateRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=160)
    role: str = Field(..., pattern="^(editor|viewer)$")
    expires_in_hours: int = Field(default=72, ge=1, le=720)


class ProjectInvitationAcceptRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=200)


class ProjectInvitationItem(BaseModel):
    id: str
    project_id: str
    email: str
    role: str
    status: str
    invited_by: str
    expires_at: str
    accepted_by: str | None = None
    accepted_at: str | None = None
    revoked_at: str | None = None
    created_at: str
    token: str | None = None


class SharedProjectItemCreateRequest(BaseModel):
    kind: str = Field(..., pattern="^(task|decision|artifact|memory)$")
    title: str = Field(..., min_length=1, max_length=200)
    content: dict = Field(default_factory=dict)
    source_type: str | None = Field(default=None, max_length=80)
    source_id: str | None = Field(default=None, max_length=200)


class SharedProjectItemUpdateRequest(BaseModel):
    content: dict = Field(default_factory=dict)
    expected_revision: int = Field(..., ge=0)


class SharedProjectItem(BaseModel):
    id: str
    project_id: str
    created_by: str
    kind: str
    title: str
    content: dict = Field(default_factory=dict)
    source_type: str | None = None
    source_id: str | None = None
    revision: int
    status: str
    created_at: str
    updated_at: str


class ProjectAuditItem(BaseModel):
    id: str
    project_id: str
    actor_user_id: str
    action: str
    entity_type: str
    entity_id: str | None = None
    details: dict = Field(default_factory=dict)
    created_at: str


class ChatReadRequest(BaseModel):
    message_id: str | None = Field(default=None, max_length=120)


class FileArtifactItem(BaseModel):
    path: str
    name: str
    extension: str = ""
    size_bytes: int = 0


class HistoryItem(BaseModel):
    role: str
    content: str
    name: str | None = None
    author_user_id: str | None = None
    status: str = "complete"
    error: str | None = None
    provider: str | None = None
    model: str | None = None
    artifacts: list[FileArtifactItem] = Field(default_factory=list)


class TaskStepItem(BaseModel):
    id: str
    task_id: str
    description: str
    status: str
    position: int
    result: str | None = None


class TaskItem(BaseModel):
    id: str
    user_id: str
    description: str
    status: str
    priority: int
    result: str | None = None
    steps: list[TaskStepItem] = Field(default_factory=list)


class TaskRunItem(BaseModel):
    id: str
    task_id: str
    user_id: str
    state: str
    worker_id: str | None = None
    lease_expires_at: str | None = None
    heartbeat_at: str | None = None
    plan_version: int
    deduplication_key: str
    budget: dict = Field(default_factory=dict)
    usage: dict = Field(default_factory=dict)
    failure_reason: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str
    updated_at: str


class TaskCheckpointItem(BaseModel):
    id: str
    run_id: str
    task_id: str
    step_id: str | None = None
    sequence: int
    plan_version: int
    status: str
    risk: str
    tool_name: str | None = None
    arguments_digest: str | None = None
    deduplication_key: str
    tool_call: dict = Field(default_factory=dict)
    observation: str = ""
    artifacts: list[dict] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)
    next_action: str | None = None
    created_at: str
    updated_at: str


class TaskControlResponse(BaseModel):
    task: TaskItem
    run: TaskRunItem


class AutomationCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    prompt: str = Field(..., min_length=1, max_length=4000)
    schedule_type: str = Field(..., pattern="^(once|weekly)$")
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    run_at: str | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    time_of_day: str | None = None
    budget: dict = Field(default_factory=dict)
    max_parallel_runs: int = Field(default=1, ge=1, le=3)


class AutomationUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    prompt: str | None = Field(default=None, min_length=1, max_length=4000)
    schedule_type: str | None = Field(default=None, pattern="^(once|weekly)$")
    timezone: str | None = Field(default=None, min_length=1, max_length=80)
    run_at: str | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    time_of_day: str | None = None
    budget: dict | None = None
    max_parallel_runs: int | None = Field(default=None, ge=1, le=3)


class AutomationRunItem(BaseModel):
    id: str
    automation_id: str
    scheduled_for: str
    task_id: str
    task_run_id: str
    state: str
    budget: dict = Field(default_factory=dict)
    usage: dict = Field(default_factory=dict)
    failure_reason: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str


class AutomationOccurrenceItem(BaseModel):
    id: str
    automation_id: str
    scheduled_for: str
    task_id: str
    task_run_id: str
    state: str


class AutomationItem(BaseModel):
    id: str
    user_id: str
    name: str
    prompt: str
    schedule_type: str
    schedule: dict = Field(default_factory=dict)
    timezone: str
    human_rule: str
    status: str
    next_run_at: str | None = None
    last_scheduled_for: str | None = None
    last_task_id: str | None = None
    last_task_run_id: str | None = None
    budget: dict = Field(default_factory=dict)
    max_parallel_runs: int = 1
    deleted_at: str | None = None
    created_at: str
    updated_at: str
    last_run: AutomationRunItem | None = None


class TaskReportItem(BaseModel):
    id: str
    user_id: str
    kind: str
    report_date: str
    run_id: str | None = None
    task_id: str | None = None
    payload: dict = Field(default_factory=dict)
    created_at: str
    updated_at: str


class InAppNotificationItem(BaseModel):
    id: str
    user_id: str
    kind: str
    title: str
    body: str = ""
    entity_type: str | None = None
    entity_id: str | None = None
    read_at: str | None = None
    created_at: str


class ActionLogItem(BaseModel):
    id: str
    user_id: str
    tool_name: str
    status: str
    arguments: dict = Field(default_factory=dict)
    result: str
    created_at: str


class WorkspaceFileItem(BaseModel):
    path: str
    name: str
    extension: str
    size_bytes: int
    modified_at: str


class ContextComponentTraceItem(BaseModel):
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


class ContextTraceResponse(BaseModel):
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
    compaction_cycle_count: int = 0
    pending_compaction_count: int = 0
    overflow: bool
    fingerprint: str
    components: list[ContextComponentTraceItem]
    created_at: str


class CompactionSummaryItem(BaseModel):
    text: str
    message_ids: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)


class CompactionSummary(BaseModel):
    goals: list[CompactionSummaryItem] = Field(default_factory=list)
    decisions: list[CompactionSummaryItem] = Field(default_factory=list)
    facts: list[CompactionSummaryItem] = Field(default_factory=list)
    open_questions: list[CompactionSummaryItem] = Field(default_factory=list)
    source_message_count: int
    summary_method: str


class ContextCompactionItem(BaseModel):
    id: str
    user_id: str
    thread_id: str
    cycle_number: int
    status: str
    summary: CompactionSummary
    source_message_ids: list[str]
    artifact_refs: list[str]
    source_hash: str
    verified_at: str | None = None
    rolled_back_at: str | None = None
    created_at: str
    updated_at: str


class CompactionSourceMessage(BaseModel):
    id: str
    role: str
    content: str
    name: str | None = None
    artifacts: list[dict] = Field(default_factory=list)
    created_at: str


class ContextCompactionReviewResponse(BaseModel):
    compaction: ContextCompactionItem
    source_messages: list[CompactionSourceMessage]
    source_hash_matches: bool


class BootstrapResponse(BaseModel):
    agent_name: str
    provider: str
    model: str
    notes: list[str]
    history: list[HistoryItem]
    active_thread: ChatThreadItem
    chat_threads: list[ChatThreadItem]
    archived_chat_threads: list[ChatThreadItem]
    deleted_chat_threads: list[ChatThreadItem]
    projects: list[ProjectItem]
    archived_projects: list[ProjectItem]
    deleted_projects: list[ProjectItem]
    active_project: ProjectItem | None = None
    tasks: list[TaskItem]
    task_runs: list[TaskRunItem] = Field(default_factory=list)
    automations: list[AutomationItem] = Field(default_factory=list)
    task_reports: list[TaskReportItem] = Field(default_factory=list)
    notifications: list[InAppNotificationItem] = Field(default_factory=list)
    action_logs: list[ActionLogItem]
    approval_requests: list[ApprovalRequestItem] = Field(default_factory=list)
    permission_grants: list[PermissionGrantItem] = Field(default_factory=list)
    extension_proposals: list[ExtensionProposalItem] = Field(default_factory=list)
    connectors: list[ConnectorItem] = Field(default_factory=list)
    security: SecuritySettingsResponse
    workspace_files: list[WorkspaceFileItem]
    user: UserProfile
