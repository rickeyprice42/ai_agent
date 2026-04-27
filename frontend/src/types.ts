export type ChatRole = "user" | "assistant" | "tool";

export type ChatMessage = {
  role: ChatRole;
  content: string;
  name?: string | null;
};

export type BootstrapPayload = {
  agent_name: string;
  provider: string;
  model: string;
  notes: string[];
  history: ChatMessage[];
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
