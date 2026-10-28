export type ChatRole = "user" | "assistant" | "tool";

export type FileArtifact = {
  path: string;
  name: string;
  extension: string;
  size_bytes: number;
};

export type ChatMessage = {
  role: ChatRole;
  content: string;
  name?: string | null;
  author_user_id?: string | null;
  status?: "pending" | "complete" | "cancelled" | "failed" | string;
  error?: string | null;
  provider?: string | null;
  model?: string | null;
  artifacts?: FileArtifact[];
};

export type ChatThreadItem = {
  id: string;
  user_id: string;
  title: string;
  status: string;
  archived_at?: string | null;
  deleted_at?: string | null;
  pinned: boolean;
  project_id?: string | null;
  memory_enabled: boolean;
  memory_saved_at?: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message_at?: string | null;
};

export type ProjectItem = {
  id: string;
  user_id: string;
  title: string;
  description: string;
  status: string;
  shared: boolean;
  agent_mode: "mention_only" | "explicit" | "off";
  revision: number;
  role: "owner" | "editor" | "viewer";
  created_at: string;
  updated_at: string;
  chat_count: number;
};

export type ProjectMemberItem = {
  project_id: string;
  user_id: string;
  role: "owner" | "editor" | "viewer";
  status: string;
  email: string;
  username: string;
  display_name: string;
  joined_at: string;
};

export type ProjectInvitationItem = {
  id: string;
  project_id: string;
  email: string;
  role: "editor" | "viewer";
  status: string;
  expires_at: string;
  token?: string | null;
  created_at: string;
};

export type SharedProjectItem = {
  id: string;
  project_id: string;
  created_by: string;
  kind: "task" | "decision" | "artifact" | "memory";
  title: string;
  content: Record<string, unknown>;
  source_type?: string | null;
  source_id?: string | null;
  revision: number;
  status: string;
  created_at: string;
  updated_at: string;
};

export type BootstrapPayload = {
  agent_name: string;
  provider: string;
  model: string;
  notes: string[];
  history: ChatMessage[];
  active_thread: ChatThreadItem;
  chat_threads: ChatThreadItem[];
  archived_chat_threads: ChatThreadItem[];
  deleted_chat_threads: ChatThreadItem[];
  projects: ProjectItem[];
  archived_projects: ProjectItem[];
  deleted_projects: ProjectItem[];
  active_project?: ProjectItem | null;
  tasks: TaskItem[];
  task_runs: TaskRunItem[];
  automations: AutomationItem[];
  task_reports: TaskReportItem[];
  notifications: InAppNotificationItem[];
  action_logs: ActionLogItem[];
  approval_requests: ApprovalRequestItem[];
  permission_grants: PermissionGrantItem[];
  extension_proposals: ExtensionProposalItem[];
  connectors: ConnectorItem[];
  security: SecuritySettings;
  workspace_files: WorkspaceFileItem[];
  user: UserProfile;
};

export type ApprovalRequestItem = {
  id: string;
  task_id?: string | null;
  step_id?: string | null;
  tool_name: string;
  arguments: Record<string, unknown>;
  scope: Record<string, unknown>;
  risk: "read" | "write" | "external" | "high" | string;
  status: string;
  expires_at: string;
  created_at: string;
};

export type PermissionGrantItem = {
  id: string;
  permission: string;
  scope: Record<string, unknown>;
  status: string;
  expires_at: string;
  created_at: string;
};

export type SecuritySettings = {
  emergency_stop: boolean;
  updated_at: string;
};

export type ExtensionProposalItem = {
  id: string;
  kind: "skill" | "tool";
  name: string;
  version: string;
  description: string;
  manifest: Record<string, unknown>;
  permissions: Array<Record<string, unknown>>;
  threat_model: string;
  source_code: string;
  skill_markdown: string;
  tests: Array<Record<string, unknown>>;
  checks: Array<{ name: string; passed: boolean; detail: string }>;
  diff: string;
  status: string;
  installed_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type ExtensionInstallResult = {
  status: string;
  proposal?: ExtensionProposalItem | null;
  approval_id?: string | null;
  message: string;
};

export type ConnectorItem = {
  id: string;
  name: string;
  description: string;
  kind: string;
  enabled: boolean;
  connected: boolean;
  status: string;
  scopes: string[];
  capabilities: string[];
  tools: string[];
  rate_limit_per_minute: number;
};

export type TaskStepItem = {
  id: string;
  task_id: string;
  description: string;
  status: string;
  position: number;
  result?: string | null;
};

export type TaskItem = {
  id: string;
  user_id: string;
  description: string;
  status: string;
  priority: number;
  result?: string | null;
  steps: TaskStepItem[];
};

export type TaskRunItem = {
  id: string;
  task_id: string;
  state: string;
  worker_id?: string | null;
  lease_expires_at?: string | null;
  heartbeat_at?: string | null;
  plan_version: number;
  budget: Record<string, number>;
  usage: Record<string, number>;
  failure_reason?: string | null;
};

export type AutomationRunItem = {
  id: string;
  automation_id: string;
  scheduled_for: string;
  task_id: string;
  task_run_id: string;
  state: string;
  budget: Record<string, number>;
  usage: Record<string, number>;
  failure_reason?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
};

export type AutomationItem = {
  id: string;
  name: string;
  prompt: string;
  schedule_type: "once" | "weekly" | string;
  schedule: { run_at?: string; weekday?: number; time_of_day?: string };
  timezone: string;
  human_rule: string;
  status: string;
  next_run_at?: string | null;
  last_scheduled_for?: string | null;
  budget: Record<string, number>;
  max_parallel_runs: number;
  last_run?: AutomationRunItem | null;
};

export type TaskReportItem = {
  id: string;
  kind: "run" | "daily" | string;
  report_date: string;
  run_id?: string | null;
  task_id?: string | null;
  payload: {
    summary?: string;
    completed_actions?: Array<Record<string, unknown>>;
    checks?: Array<Record<string, unknown>>;
    commits?: Array<Record<string, unknown>>;
    artifacts?: Array<{ path: string; name?: string; sha256?: string; reference?: string }>;
    blockers?: Array<Record<string, unknown>>;
    budget?: Record<string, unknown>;
    next_action?: string;
    next_actions?: string[];
  };
  created_at: string;
  updated_at: string;
};

export type InAppNotificationItem = {
  id: string;
  kind: string;
  title: string;
  body: string;
  entity_type?: string | null;
  entity_id?: string | null;
  read_at?: string | null;
  created_at: string;
};

export type ActionLogItem = {
  id: string;
  user_id: string;
  tool_name: string;
  status: string;
  arguments: Record<string, unknown>;
  result: string;
  created_at: string;
};

export type WorkspaceFileItem = {
  path: string;
  name: string;
  extension: string;
  size_bytes: number;
  modified_at: string;
};

export type MemoryCounts = {
  notes: number;
  global_notes: number;
  project_notes: number;
  chat_notes: number;
  memory_index: number;
  chat_messages: number;
  tasks: number;
  action_logs: number;
  personal_records: number;
  health_records: number;
};

export type MemoryNoteItem = {
  id: string;
  content: string;
  scope: string;
  project_id?: string | null;
  source_thread_id?: string | null;
  created_at: string;
};

export type MemoryOverview = {
  counts: MemoryCounts;
  notes: MemoryNoteItem[];
};

export type PersonalMemoryItem = {
  id: string;
  kind: "profile" | "preference" | "episode" | "fact";
  content: string;
  scope: "global" | "project" | "chat";
  project_id?: string | null;
  thread_id?: string | null;
  memory_key?: string | null;
  auto_generated: boolean;
  confidence: number;
  status: "active" | "superseded" | "revoked" | "expired";
  consent_status: "granted" | "revoked";
  consented_at?: string | null;
  revoked_at?: string | null;
  expires_at?: string | null;
  sensitive: boolean;
  created_at: string;
  updated_at: string;
  provenance: Array<{
    id: string;
    source_type: string;
    source_id?: string | null;
    source_uri?: string | null;
    created_at: string;
  }>;
};

export type AutonomousMemorySettings = {
  autonomous_enabled: boolean;
  health_autonomous_enabled: boolean;
  updated_at: string;
};

export type MemorySummary = {
  generated_at: string;
  settings: AutonomousMemorySettings;
  sections: Record<PersonalMemoryItem["kind"], PersonalMemoryItem[]>;
  record_count: number;
};

export type UserProfile = {
  id: string;
  email: string;
  username: string;
  display_name: string;
  auth_provider: string;
};

export type AuthPayload = {
  token: string;
  user: UserProfile;
};

export type ModelProviderOption = {
  provider: string;
  label: string;
  description: string;
  models: string[];
  capabilities: string[];
};

export type ModelSettings = {
  provider: string;
  model_name: string;
  ollama_url: string;
};

export type CompactionSummaryItem = {
  text: string;
  message_ids: string[];
  artifact_paths: string[];
};

export type ContextCompactionItem = {
  id: string;
  user_id: string;
  thread_id: string;
  cycle_number: number;
  status: "pending_review" | "verified" | "rolled_back" | string;
  summary: {
    goals: CompactionSummaryItem[];
    decisions: CompactionSummaryItem[];
    facts: CompactionSummaryItem[];
    open_questions: CompactionSummaryItem[];
    source_message_count: number;
    summary_method: string;
  };
  source_message_ids: string[];
  artifact_refs: string[];
  source_hash: string;
  verified_at?: string | null;
  rolled_back_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type ContextCompactionReview = {
  compaction: ContextCompactionItem;
  source_messages: Array<{
    id: string;
    role: string;
    content: string;
    name?: string | null;
    artifacts: Array<Record<string, unknown>>;
    created_at: string;
  }>;
  source_hash_matches: boolean;
};
