export type ChatRole = "user" | "assistant" | "tool";

export type ChatMessage = {
  role: ChatRole;
  content: string;
  name?: string | null;
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
  created_at: string;
  updated_at: string;
  chat_count: number;
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
  action_logs: ActionLogItem[];
  workspace_files: WorkspaceFileItem[];
  user: UserProfile;
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
};

export type ModelSettings = {
  provider: string;
  model_name: string;
  ollama_url: string;
};
