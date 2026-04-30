import type {
  AuthPayload,
  BootstrapPayload,
  ChatThreadItem,
  ModelProviderOption,
  ModelSettings,
  ProjectItem,
  UserProfile
} from "./types";

function authHeaders(token: string): HeadersInit {
  return {
    Authorization: `Bearer ${token}`
  };
}

async function parseError(response: Response, fallback: string): Promise<Error> {
  try {
    const payload = await response.json();
    return new Error(typeof payload.detail === "string" ? payload.detail : fallback);
  } catch {
    return new Error(fallback);
  }
}

export async function fetchBootstrap(token: string, threadId?: string | null): Promise<BootstrapPayload> {
  const query = threadId ? `?thread_id=${encodeURIComponent(threadId)}` : "";
  const response = await fetch(`/api/bootstrap${query}`, {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось загрузить данные агента.");
  }
  return response.json();
}

export async function sendMessage(
  message: string,
  token: string,
  threadId?: string | null
): Promise<{ reply: string; thread_id: string }> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(token)
    },
    body: JSON.stringify({ message, thread_id: threadId })
  });

  if (!response.ok) {
    throw await parseError(response, "Не удалось получить ответ агента.");
  }

  return response.json();
}

export async function createChatThread(
  token: string,
  title?: string,
  projectId?: string | null
): Promise<{ thread: ChatThreadItem }> {
  const response = await fetch("/api/chats", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(token)
    },
    body: JSON.stringify({ title, project_id: projectId })
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось создать чат.");
  }
  return response.json();
}

export async function updateChatThread(
  token: string,
  threadId: string,
  payload: { title?: string; pinned?: boolean; project_id?: string; clear_project?: boolean; memory_enabled?: boolean }
): Promise<{ thread: ChatThreadItem }> {
  const response = await fetch(`/api/chats/${encodeURIComponent(threadId)}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(token)
    },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось обновить чат.");
  }
  return response.json();
}

export async function assignChatToProject(
  token: string,
  threadId: string,
  projectId: string | null
): Promise<{ thread: ChatThreadItem }> {
  return updateChatThread(token, threadId, projectId ? { project_id: projectId } : { clear_project: true });
}

export async function updateChatMemory(
  token: string,
  threadId: string,
  memoryEnabled: boolean
): Promise<{ thread: ChatThreadItem }> {
  return updateChatThread(token, threadId, { memory_enabled: memoryEnabled });
}

export async function rememberChat(token: string, threadId: string): Promise<{ result: string; thread: ChatThreadItem }> {
  const response = await fetch(`/api/chats/${encodeURIComponent(threadId)}/remember`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось сохранить чат в память.");
  }
  return response.json();
}

export async function createProject(
  token: string,
  title: string,
  description = ""
): Promise<{ project: ProjectItem }> {
  const response = await fetch("/api/projects", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(token)
    },
    body: JSON.stringify({ title, description })
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось создать проект.");
  }
  return response.json();
}

export async function updateProject(
  token: string,
  projectId: string,
  payload: { title?: string; description?: string }
): Promise<{ project: ProjectItem }> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(token)
    },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось обновить проект.");
  }
  return response.json();
}

export async function archiveProject(token: string, projectId: string): Promise<{ project: ProjectItem }> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/archive`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось архивировать проект.");
  }
  return response.json();
}

export async function restoreProject(token: string, projectId: string): Promise<{ project: ProjectItem }> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/restore`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось восстановить проект.");
  }
  return response.json();
}

export async function deleteProject(token: string, projectId: string): Promise<{ project: ProjectItem }> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось удалить проект.");
  }
  return response.json();
}

export async function archiveChatThread(token: string, threadId: string): Promise<{ thread: ChatThreadItem }> {
  const response = await fetch(`/api/chats/${encodeURIComponent(threadId)}/archive`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось архивировать чат.");
  }
  return response.json();
}

export async function unarchiveChatThread(token: string, threadId: string): Promise<{ thread: ChatThreadItem }> {
  const response = await fetch(`/api/chats/${encodeURIComponent(threadId)}/unarchive`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось вернуть чат из архива.");
  }
  return response.json();
}

export async function deleteChatThread(token: string, threadId: string): Promise<{ thread: ChatThreadItem }> {
  const response = await fetch(`/api/chats/${encodeURIComponent(threadId)}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось удалить чат.");
  }
  return response.json();
}

export async function clearChatMessages(token: string, threadId: string): Promise<{ deleted_messages: number }> {
  const response = await fetch(`/api/chats/${encodeURIComponent(threadId)}/messages`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось очистить чат.");
  }
  return response.json();
}

export async function executeNextStep(token: string): Promise<{ result: string }> {
  const response = await fetch("/api/tasks/execute-next", {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось выполнить следующий шаг.");
  }
  return response.json();
}

export async function approveBlockedStep(stepId: string, token: string): Promise<{ result: string }> {
  const response = await fetch(`/api/tasks/steps/${encodeURIComponent(stepId)}/approve`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось подтвердить заблокированный шаг.");
  }
  return response.json();
}

export async function openWorkspaceFolder(token: string): Promise<{ result: string }> {
  const response = await fetch("/api/workspace/open", {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось открыть рабочую папку.");
  }
  return response.json();
}

export async function login(loginValue: string, password: string): Promise<AuthPayload> {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login: loginValue, password })
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось войти.");
  }
  return response.json();
}

export async function register(
  email: string,
  username: string,
  password: string,
  displayName: string
): Promise<AuthPayload> {
  const response = await fetch("/api/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, username, password, display_name: displayName })
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось зарегистрироваться.");
  }
  return response.json();
}

export async function fetchMe(token: string): Promise<UserProfile> {
  const response = await fetch("/api/auth/me", {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Сессия недействительна.");
  }
  return response.json();
}

export async function logout(token: string): Promise<void> {
  await fetch("/api/auth/logout", {
    method: "POST",
    headers: authHeaders(token)
  });
}

export async function fetchModelProviders(token: string): Promise<ModelProviderOption[]> {
  const response = await fetch("/api/models", {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось загрузить список моделей.");
  }
  return response.json();
}

export async function updateModelSettings(
  token: string,
  settings: ModelSettings
): Promise<ModelSettings> {
  const response = await fetch("/api/model-settings", {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(token)
    },
    body: JSON.stringify(settings)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось обновить модель.");
  }
  return response.json();
}
