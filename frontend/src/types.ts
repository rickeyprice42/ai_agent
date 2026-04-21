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
};
