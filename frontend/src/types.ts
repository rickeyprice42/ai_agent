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
  user: UserProfile;
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
