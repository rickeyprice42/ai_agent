import type {
  AuthPayload,
  BootstrapPayload,
  ChatThreadItem,
  ContextCompactionItem,
  ContextCompactionReview,
  ExtensionInstallResult,
  ExtensionProposalItem,
  AutonomousMemorySettings,
  MemoryOverview,
  MemorySummary,
  PersonalMemoryItem,
  ModelProviderOption,
  ModelSettings,
  ProjectItem,
  ProjectInvitationItem,
  ProjectMemberItem,
  SharedProjectItem,
  UserProfile
} from "./types";
import type { ChatApprovalItem } from "./types";
import type { AutomationRunItem } from "./types";
import type { ConnectorItem } from "./types";
import type { ChatRunSnapshot, ChatRunState } from "./types";
import type { ChatQuestionItem } from "./types";

function authHeaders(token: string): HeadersInit {
  return {
    Authorization: `Bearer ${token}`
  };
}

export async function parseError(response: Response, fallback: string): Promise<Error> {
  const unavailable = `${fallback} Эта функция недоступна в запущенной версии сервера. Перезапусти Avelin через лаунчер и обнови приложение.`;
  try {
    const payload = await response.json();
    if (response.status === 404 && (typeof payload.detail !== "string" || /^not\s*found$/i.test(payload.detail))) {
      return new Error(unavailable);
    }
    return new Error(typeof payload.detail === "string" ? payload.detail : fallback);
  } catch {
    return new Error(response.status === 404 ? unavailable : fallback);
  }
}

export async function fetchRuntimeNotice(): Promise<string | null> {
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    if (!response.ok && response.status !== 404) return null;
    const health = response.ok ? await response.json().catch(() => null) : null;
    if (Number(health?.runtime?.contract) >= 2 && !health?.restart_required) return null;
    return "Сервер Avelin использует предыдущую версию. Перезапусти Avelin через лаунчер, затем обнови приложение. Новые фото, предпросмотры и уведомления требуют обновлённого сервера.";
  } catch { return null; } // Connection failures are reported by the main authenticated bootstrap.
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

export type ChatStreamEvent = {
  type: "delta" | "status" | "done" | "error" | "approval" | "clarification";
  text?: string;
  code?: string;
  thread_id: string;
  provider?: string;
  model?: string;
  tool?: string;
  phase?: "started" | "completed" | "failed" | "blocked";
  round?: number;
  approval?: ChatApprovalItem;
  question?: ChatQuestionItem;
  run_id?: string;
  seq?: number;
  state?: ChatRunState;
  outcome?: string;
};

export class ChatRunHttpError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function runRequest(path: string, token: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<ChatRunSnapshot | null> {
  const encoded = body === undefined ? undefined : JSON.stringify(body);
  const response = await fetch(`/api/${path}`, { method, cache: "no-store", headers: { ...authHeaders(token), "Content-Type": "application/json" },
    ...(encoded === undefined ? {} : { body: encoded, keepalive: new TextEncoder().encode(encoded).byteLength < 60 * 1024 }), signal });
  if (!response.ok) throw new ChatRunHttpError(response.status, (await parseError(response, "Не удалось получить состояние ответа.")).message);
  return response.json();
}

export async function startChatRun(message: string, threadId: string, attachments: string[], requestId: string, token: string): Promise<ChatRunSnapshot> {
  return (await runRequest("chat/runs", token, "POST", { message, thread_id: threadId, attachments, request_id: requestId }))!;
}
export async function startChatApprovalRun(threadId: string, approvalId: string, decision: "approved" | "denied", requestId: string, token: string): Promise<ChatRunSnapshot> {
  return (await runRequest(`chats/${encodeURIComponent(threadId)}/approvals/${encodeURIComponent(approvalId)}/runs`, token, "POST", { request_id: requestId, decision }))!;
}
export async function startChatQuestionRun(threadId: string, questionId: string, answer: { option_id?: string; text?: string }, requestId: string, token: string): Promise<ChatRunSnapshot> {
  return (await runRequest(`chats/${encodeURIComponent(threadId)}/questions/${encodeURIComponent(questionId)}/runs`, token, "POST", { request_id: requestId, ...answer }))!;
}
export async function fetchChatModelSettings(threadId: string, token: string): Promise<ModelSettings> {
  const response=await fetch(`/api/chats/${encodeURIComponent(threadId)}/model-settings`,{cache:"no-store",headers:authHeaders(token)});
  if(!response.ok)throw await parseError(response,"Не удалось загрузить модель чата.");
  return response.json();
}
export async function updateChatModelSettings(threadId: string, token: string, settings: ModelSettings): Promise<ModelSettings> {
  const response=await fetch(`/api/chats/${encodeURIComponent(threadId)}/model-settings`,{method:"PUT",headers:{...authHeaders(token),"Content-Type":"application/json"},body:JSON.stringify(settings)});
  if(!response.ok)throw await parseError(response,"Не удалось изменить модель чата.");
  return response.json();
}
export async function resetChatModelSettings(threadId: string, token: string): Promise<ModelSettings> {
  const response=await fetch(`/api/chats/${encodeURIComponent(threadId)}/model-settings`,{method:"DELETE",headers:authHeaders(token)});
  if(!response.ok)throw await parseError(response,"Не удалось вернуть модель по умолчанию.");
  return response.json();
}
export function fetchChatRun(runId: string, token: string, signal?: AbortSignal) { return runRequest(`chat/runs/${encodeURIComponent(runId)}`, token, "GET", undefined, signal); }
export function fetchChatRunByRequest(requestId: string, token: string, signal?: AbortSignal) { return runRequest(`chat/runs/by-request/${encodeURIComponent(requestId)}`, token, "GET", undefined, signal); }
export function fetchActiveChatRun(threadId: string, token: string, signal?: AbortSignal) { return runRequest(`chats/${encodeURIComponent(threadId)}/runs/active`, token, "GET", undefined, signal); }
export function cancelChatRun(runId: string, token: string) { return runRequest(`chat/runs/${encodeURIComponent(runId)}/cancel`, token, "POST"); }
export async function subscribeChatRun(runId: string, after: number, token: string, signal: AbortSignal, onEvent: (event: ChatStreamEvent) => void) {
  const response = await fetch(`/api/chat/runs/${encodeURIComponent(runId)}/events?after=${after}`, { cache: "no-store", headers: { ...authHeaders(token), Accept: "application/x-ndjson" }, signal });
  if (!response.ok) throw new ChatRunHttpError(response.status, (await parseError(response, "Не удалось подключиться к ответу.")).message);
  return consumeChatStream(response, onEvent, true);
}

export type ChatAttachment = { path: string; name: string; mime_type: string; size_bytes: number };

export async function uploadChatAttachment(file: File, token: string): Promise<ChatAttachment> {
  const response = await fetch("/api/attachments", {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": file.type || "application/octet-stream", "X-Filename": encodeURIComponent(file.name) },
    body: file
  });
  if (!response.ok) throw await parseError(response, "Не удалось прикрепить файл.");
  return response.json();
}

export async function sendMessageStream(
  message: string,
  token: string,
  threadId: string | null,
  signal: AbortSignal,
  onEvent: (event: ChatStreamEvent) => void,
  attachments: string[] = [],
  onAccepted?: () => void
): Promise<void> {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/x-ndjson",
      ...authHeaders(token)
    },
    body: JSON.stringify({ message, thread_id: threadId, attachments }),
    signal
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось получить ответ агента.");
  }
  onAccepted?.();
  return consumeChatStream(response, onEvent);
}

export async function fetchAvatar(token: string): Promise<Blob | null> {
  const response = await fetch("/api/profile/avatar", { headers: authHeaders(token), cache: "no-store" });
  if (response.status === 404) return null;
  if (!response.ok) throw await parseError(response, "Не удалось загрузить аватар.");
  return response.blob();
}

export async function fetchImagePreview(path: string, token: string): Promise<Blob> {
  const response = await fetch(`/api/workspace/preview?path=${encodeURIComponent(path)}&size=1024`, { headers: authHeaders(token) });
  if (!response.ok) throw await parseError(response, "Не удалось открыть изображение.");
  if (!response.headers.get("Content-Type")?.startsWith("image/")) throw new Error("Сервер не вернул изображение. Перезапусти Avelin через лаунчер и обнови приложение.");
  return response.blob();
}

export async function saveAvatar(blob: Blob, token: string): Promise<void> {
  const response = await fetch("/api/profile/avatar", { method: "PUT", headers: { ...authHeaders(token), "Content-Type": "image/png", "X-Filename": "avatar.png" }, body: blob });
  if (!response.ok) throw await parseError(response, "Не удалось сохранить аватар.");
}

export async function previewAvatar(file: File, token: string): Promise<Blob> {
  const response = await fetch("/api/profile/avatar/preview", { method: "POST", headers: { ...authHeaders(token), "Content-Type": file.type || "application/octet-stream", "X-Filename": encodeURIComponent(file.name) }, body: file });
  if (!response.ok) throw await parseError(response, "Не удалось открыть изображение.");
  if (!response.headers.get("Content-Type")?.startsWith("image/")) throw new Error("Сервер пока не поддерживает подготовку фото. Перезапусти Avelin через лаунчер и обнови приложение.");
  return response.blob();
}

export async function deleteAvatar(token: string): Promise<void> {
  const response = await fetch("/api/profile/avatar", { method: "DELETE", headers: authHeaders(token) });
  if (!response.ok) throw await parseError(response, "Не удалось удалить аватар.");
}

export async function decideChatApproval(threadId: string, approvalId: string, decision: "approved" | "denied", token: string, signal: AbortSignal, onEvent: (event: ChatStreamEvent) => void): Promise<void> {
  const response = await fetch(`/api/chats/${encodeURIComponent(threadId)}/approvals/${encodeURIComponent(approvalId)}/decision`, {
    method: "POST", headers: { ...authHeaders(token), "Content-Type": "application/json", Accept: "application/x-ndjson" },
    body: JSON.stringify({ decision }), signal
  });
  if (!response.ok) throw await parseError(response, "Не удалось подтвердить действие.");
  return consumeChatStream(response, onEvent);
}

async function consumeChatStream(response: Response, onEvent: (event: ChatStreamEvent) => void, durable = false): Promise<void> {
  if (!response.body) {
    throw new Error("Браузер не получил поток ответа.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;
  function consume(line: string) {
    if (!line.trim()) return;
    const event = JSON.parse(line) as ChatStreamEvent;
    onEvent(event);
    if (event.type === "done") completed = true;
    if (event.type === "error" && !durable) throw new Error(event.text || "Провайдер прервал генерацию.");
  }
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) consume(line);
      if (done) break;
    }
    consume(buffer);
    if (!completed && !durable) throw new Error("Соединение прервалось до завершения ответа. Частичный текст сохранён в чате.");
  } finally {
    try { await reader.cancel(); } catch { /* The request may already have been aborted. */ }
    reader.releaseLock();
  }
}

export async function createChatThread(
  token: string,
  title?: string,
  projectId?: string | null,
  reuseEmpty = false
): Promise<{ thread: ChatThreadItem }> {
  const response = await fetch("/api/chats", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(token)
    },
    body: JSON.stringify({ title, project_id: projectId, reuse_empty: reuseEmpty })
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

export async function fetchContextCompactions(
  token: string,
  threadId: string
): Promise<ContextCompactionItem[]> {
  const response = await fetch(`/api/chats/${encodeURIComponent(threadId)}/compactions`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await parseError(response, "Не удалось загрузить снимки контекста.");
  return response.json();
}

export async function fetchContextCompactionReview(
  token: string,
  threadId: string,
  compactionId: string
): Promise<ContextCompactionReview> {
  const response = await fetch(
    `/api/chats/${encodeURIComponent(threadId)}/compactions/${encodeURIComponent(compactionId)}`,
    { headers: authHeaders(token) }
  );
  if (!response.ok) throw await parseError(response, "Не удалось загрузить исходные сообщения.");
  return response.json();
}

async function changeContextCompactionStatus(
  token: string,
  threadId: string,
  compactionId: string,
  action: "verify" | "rollback"
): Promise<ContextCompactionItem> {
  const response = await fetch(
    `/api/chats/${encodeURIComponent(threadId)}/compactions/${encodeURIComponent(compactionId)}/${action}`,
    { method: "POST", headers: authHeaders(token) }
  );
  if (!response.ok) throw await parseError(response, "Не удалось обновить снимок контекста.");
  return response.json();
}

export function verifyContextCompaction(token: string, threadId: string, compactionId: string) {
  return changeContextCompactionStatus(token, threadId, compactionId, "verify");
}

export function rollbackContextCompaction(token: string, threadId: string, compactionId: string) {
  return changeContextCompactionStatus(token, threadId, compactionId, "rollback");
}

export async function fetchMemoryOverview(token: string): Promise<MemoryOverview> {
  const response = await fetch("/api/memory", {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось загрузить память агента.");
  }
  return response.json();
}

export async function fetchAutonomousMemorySettings(token: string): Promise<AutonomousMemorySettings> {
  const response = await fetch("/api/memory/settings", { headers: authHeaders(token) });
  if (!response.ok) throw await parseError(response, "Не удалось загрузить настройки памяти.");
  return response.json();
}

export async function updateAutonomousMemorySettings(
  token: string,
  autonomousEnabled: boolean
): Promise<AutonomousMemorySettings> {
  const response = await fetch("/api/memory/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify({ autonomous_enabled: autonomousEnabled, health_autonomous_enabled: false })
  });
  if (!response.ok) throw await parseError(response, "Не удалось изменить настройки памяти.");
  return response.json();
}

export async function fetchMemorySummary(token: string, rebuild = false): Promise<MemorySummary> {
  const response = await fetch(rebuild ? "/api/memory/summary/rebuild" : "/api/memory/summary", {
    method: rebuild ? "POST" : "GET",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await parseError(response, "Не удалось собрать сводку памяти.");
  return response.json();
}

export async function fetchPersonalMemory(token: string, includeSensitive = false): Promise<PersonalMemoryItem[]> {
  const response = await fetch(`/api/memory/records?include_sensitive=${includeSensitive}`, {
    headers: authHeaders(token)
  });
  if (!response.ok) throw await parseError(response, "Не удалось загрузить персональную память.");
  return response.json();
}

export async function createPersonalMemory(
  token: string,
  payload: {
    kind: PersonalMemoryItem["kind"];
    content: string;
    scope: PersonalMemoryItem["scope"];
    project_id?: string | null;
    thread_id?: string | null;
    sensitive: boolean;
    consent_granted: boolean;
  }
): Promise<PersonalMemoryItem> {
  const response = await fetch("/api/memory/records", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await parseError(response, "Не удалось сохранить персональную память.");
  return response.json();
}

export async function updatePersonalMemory(
  token: string,
  memory: PersonalMemoryItem,
  payload: { content?: string; status?: PersonalMemoryItem["status"] }
): Promise<PersonalMemoryItem> {
  const response = await fetch(`/api/memory/records/${encodeURIComponent(memory.id)}?sensitive=${memory.sensitive}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await parseError(response, "Не удалось изменить персональную память.");
  return response.json();
}

export async function deletePersonalMemory(token: string, memory: PersonalMemoryItem): Promise<void> {
  const response = await fetch(`/api/memory/records/${encodeURIComponent(memory.id)}?sensitive=${memory.sensitive}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await parseError(response, "Не удалось удалить персональную память.");
}

export async function exportAgentMemory(token: string, includeSensitive = false): Promise<Blob> {
  const response = await fetch(`/api/memory/export?include_sensitive=${includeSensitive}`, {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось экспортировать память агента.");
  }
  return response.blob();
}

export async function deleteMemoryNote(token: string, noteId: string): Promise<{ deleted_notes: number }> {
  const response = await fetch(`/api/memory/notes/${encodeURIComponent(noteId)}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось удалить заметку памяти.");
  }
  return response.json();
}

export async function clearAgentMemory(
  token: string,
  payload: { scope: string; thread_id?: string | null; project_id?: string | null; include_action_logs?: boolean }
): Promise<{
  deleted_notes: number;
  deleted_memory_index: number;
  deleted_action_logs: number;
  updated_threads: number;
}> {
  const response = await fetch("/api/memory/clear", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(token)
    },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось очистить память агента.");
  }
  return response.json();
}

export async function createProject(
  token: string,
  title: string,
  description = "",
  shared = false
): Promise<{ project: ProjectItem }> {
  const response = await fetch("/api/projects", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(token)
    },
    body: JSON.stringify({ title, description, shared })
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось создать проект.");
  }
  return response.json();
}

export async function updateProject(
  token: string,
  projectId: string,
  payload: { title?: string; description?: string; agent_mode?: ProjectItem["agent_mode"]; expected_revision?: number }
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

export async function fetchProjectMembers(token: string, projectId: string): Promise<ProjectMemberItem[]> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/members`, { headers: authHeaders(token) });
  if (!response.ok) throw await parseError(response, "Не удалось загрузить участников workspace.");
  return response.json();
}

export async function fetchProjectInvitations(token: string, projectId: string): Promise<ProjectInvitationItem[]> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/invitations`, { headers: authHeaders(token) });
  if (!response.ok) throw await parseError(response, "Не удалось загрузить приглашения.");
  return response.json();
}

export async function createProjectInvitation(token: string, projectId: string, email: string, role: "editor" | "viewer"): Promise<ProjectInvitationItem> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/invitations`, {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ email, role, expires_in_hours: 72 })
  });
  if (!response.ok) throw await parseError(response, "Не удалось создать приглашение.");
  return response.json();
}

export async function acceptProjectInvitation(token: string, invitationToken: string): Promise<ProjectItem> {
  const response = await fetch("/api/project-invitations/accept", {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ token: invitationToken })
  });
  if (!response.ok) throw await parseError(response, "Не удалось принять приглашение.");
  return (await response.json()).project;
}

export async function removeProjectMember(token: string, projectId: string, userId: string): Promise<void> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(userId)}`, {
    method: "DELETE", headers: authHeaders(token)
  });
  if (!response.ok) throw await parseError(response, "Не удалось удалить участника.");
}

export async function fetchSharedProjectItems(token: string, projectId: string): Promise<SharedProjectItem[]> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/shared-items`, { headers: authHeaders(token) });
  if (!response.ok) throw await parseError(response, "Не удалось загрузить общую доску.");
  return response.json();
}

export async function createSharedProjectItem(
  token: string,
  projectId: string,
  payload: { kind: SharedProjectItem["kind"]; title: string; content: Record<string, unknown>; source_type?: string; source_id?: string }
): Promise<SharedProjectItem> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/shared-items`, {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await parseError(response, "Не удалось добавить элемент на общую доску.");
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

export async function downloadWorkspaceFile(path: string, token: string): Promise<Blob> {
  const encodedPath = path.split("/").map(encodeURIComponent).join("/");
  const response = await fetch(`/api/workspace/files/${encodedPath}`, {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось скачать файл.");
  }
  return response.blob();
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

export async function controlTask(
  taskId: string,
  action: "pause" | "resume" | "cancel",
  token: string
): Promise<void> {
  const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/${action}`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось изменить состояние задачи.");
  }
}

export async function createAutomation(
  payload: {
    name: string;
    prompt: string;
    schedule_type: "once" | "weekly";
    timezone: string;
    run_at?: string;
    weekday?: number;
    time_of_day?: string;
    budget: Record<string, number>;
    max_parallel_runs: number;
  },
  token: string
): Promise<void> {
  const response = await fetch("/api/automations", {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await parseError(response, "Не удалось создать автоматизацию.");
}

export async function controlAutomation(
  automationId: string,
  action: "pause" | "resume",
  token: string
): Promise<void> {
  const response = await fetch(`/api/automations/${encodeURIComponent(automationId)}/${action}`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await parseError(response, "Не удалось изменить автоматизацию.");
}

export async function deleteAutomation(automationId: string, token: string): Promise<void> {
  const response = await fetch(`/api/automations/${encodeURIComponent(automationId)}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await parseError(response, "Не удалось удалить автоматизацию.");
}

export async function runAutomationNow(automationId: string, token: string): Promise<void> {
  const response = await fetch(`/api/automations/${encodeURIComponent(automationId)}/run`, { method: "POST", headers: authHeaders(token) });
  if (!response.ok) throw await parseError(response, "Не удалось запустить автоматизацию.");
}

export async function fetchAutomationRuns(automationId: string, token: string): Promise<AutomationRunItem[]> {
  const response = await fetch(`/api/automations/${encodeURIComponent(automationId)}/runs?limit=20`, { headers: authHeaders(token) });
  if (!response.ok) throw await parseError(response, "Не удалось загрузить результаты.");
  return response.json();
}

export async function createRunReport(runId: string, token: string): Promise<void> {
  const response = await fetch(`/api/task-runs/${encodeURIComponent(runId)}/report`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await parseError(response, "Не удалось сформировать отчёт запуска.");
}

export async function createDailyTaskReport(token: string): Promise<void> {
  const response = await fetch("/api/task-reports/daily", {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await parseError(response, "Не удалось сформировать ежедневный отчёт.");
}

export async function markNotificationRead(notificationId: string, token: string): Promise<void> {
  const response = await fetch(`/api/notifications/${encodeURIComponent(notificationId)}/read`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await parseError(response, "Не удалось отметить уведомление.");
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

export async function decideApproval(
  approvalId: string,
  decision: "approved" | "denied" | "revoked",
  token: string
): Promise<void> {
  const response = await fetch(`/api/approvals/${encodeURIComponent(approvalId)}/decision`, {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ decision })
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось сохранить решение по разрешению.");
  }
}

export async function setEmergencyStop(enabled: boolean, token: string): Promise<void> {
  const response = await fetch("/api/security/emergency-stop", {
    method: "PUT",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ enabled })
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось изменить аварийную остановку.");
  }
}

export async function revokePermissionGrant(grantId: string, token: string): Promise<void> {
  const response = await fetch(`/api/permission-grants/${encodeURIComponent(grantId)}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось отозвать разрешение.");
  }
}

export async function createExtensionProposal(
  payload: {
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
  },
  token: string
): Promise<ExtensionProposalItem> {
  const response = await fetch("/api/extensions", {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw await parseError(response, "Не удалось проверить extension proposal.");
  return response.json();
}

export async function installExtension(proposalId: string, version: string, token: string): Promise<ExtensionInstallResult> {
  const response = await fetch(`/api/extensions/${encodeURIComponent(proposalId)}/install`, {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ version })
  });
  if (!response.ok) throw await parseError(response, "Не удалось установить расширение.");
  return response.json();
}

export async function disableExtension(name: string, token: string): Promise<ExtensionProposalItem> {
  const response = await fetch(`/api/extensions/${encodeURIComponent(name)}/disable`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) throw await parseError(response, "Не удалось отключить расширение.");
  return response.json();
}

export async function rollbackExtension(name: string, version: string, token: string): Promise<ExtensionInstallResult> {
  const response = await fetch(`/api/extensions/${encodeURIComponent(name)}/rollback`, {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ version })
  });
  if (!response.ok) throw await parseError(response, "Не удалось откатить расширение.");
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

export async function setConnectorEnabled(
  connectorId: string,
  enabled: boolean,
  token: string
): Promise<ConnectorItem> {
  const response = await fetch(`/api/connectors/${encodeURIComponent(connectorId)}`, {
    method: "PUT",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ enabled })
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось изменить connector.");
  }
  return response.json();
}
