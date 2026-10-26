import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  archiveProject,
  archiveChatThread,
  assignChatToProject,
  approveBlockedStep,
  clearChatMessages,
  clearAgentMemory,
  createPersonalMemory,
  createProject,
  createChatThread,
  decideApproval,
  deleteMemoryNote,
  deletePersonalMemory,
  deleteProject,
  deleteChatThread,
  downloadWorkspaceFile,
  executeNextStep,
  exportAgentMemory,
  fetchAutonomousMemorySettings,
  fetchBootstrap,
  fetchContextCompactionReview,
  fetchContextCompactions,
  fetchMe,
  fetchMemoryOverview,
  fetchMemorySummary,
  fetchPersonalMemory,
  fetchModelProviders,
  login as authLogin,
  logout as authLogout,
  openWorkspaceFolder,
  register as authRegister,
  rememberChat,
  rollbackContextCompaction,
  restoreProject,
  revokePermissionGrant,
  sendMessageStream,
  setEmergencyStop,
  unarchiveChatThread,
  updateChatMemory,
  updateAutonomousMemorySettings,
  updateProject,
  updateChatThread,
  updateModelSettings,
  updatePersonalMemory,
  verifyContextCompaction
} from "./api";
import type {
  BootstrapPayload,
  AutonomousMemorySettings,
  ChatMessage,
  ContextCompactionItem,
  ContextCompactionReview,
  FileArtifact,
  MemoryOverview,
  MemorySummary,
  PersonalMemoryItem,
  ModelProviderOption,
  UserProfile
} from "./types";

const DRAFT_KEY = "avelin-draft";
const THEME_KEY = "avelin-theme-mode";
const AUTH_TOKEN_KEY = "avelin-auth-token";
const ACTIVE_THREAD_KEY = "avelin-active-thread";

type ThemeMode = "auto" | "light" | "dark";
type ResolvedTheme = "light" | "dark";

const EMPTY_BOOTSTRAP: BootstrapPayload = {
  agent_name: "Avelin",
  provider: "mock",
  model: "mock-local",
  notes: [],
  history: [],
  active_thread: {
    id: "",
    user_id: "",
    title: "Main conversation",
    status: "active",
    archived_at: null,
    deleted_at: null,
    pinned: false,
    project_id: null,
    memory_enabled: true,
    memory_saved_at: null,
    created_at: "",
    updated_at: "",
    message_count: 0,
    last_message_at: null
  },
  chat_threads: [],
  archived_chat_threads: [],
  deleted_chat_threads: [],
  projects: [],
  archived_projects: [],
  deleted_projects: [],
  active_project: null,
  tasks: [],
  action_logs: [],
  approval_requests: [],
  permission_grants: [],
  security: { emergency_stop: false, updated_at: "" },
  workspace_files: [],
  user: {
    id: "",
    email: "",
    username: "",
    display_name: "",
    auth_provider: "password"
  }
};

const EMPTY_MEMORY_OVERVIEW: MemoryOverview = {
  counts: {
    notes: 0,
    global_notes: 0,
    project_notes: 0,
    chat_notes: 0,
    memory_index: 0,
    chat_messages: 0,
    tasks: 0,
    action_logs: 0,
    personal_records: 0,
    health_records: 0
  },
  notes: []
};

const EMPTY_AUTONOMOUS_MEMORY_SETTINGS: AutonomousMemorySettings = {
  autonomous_enabled: false,
  health_autonomous_enabled: false,
  updated_at: ""
};

const EMPTY_MEMORY_SUMMARY: MemorySummary = {
  generated_at: "",
  settings: EMPTY_AUTONOMOUS_MEMORY_SETTINGS,
  sections: { profile: [], preference: [], fact: [], episode: [] },
  record_count: 0
};

const QUICK_ACTIONS = [
  "Сколько сейчас времени?",
  "Запомни: купить кофе и молоко",
  "Что ты помнишь обо мне?",
  "Помоги спланировать мой день"
];

const MEMORY_SECTION_LABELS: Record<PersonalMemoryItem["kind"], string> = {
  profile: "О пользователе",
  preference: "Предпочтения",
  fact: "Устойчивые факты",
  episode: "Недавние события"
};

function formatRoleLabel(message: ChatMessage, agentName: string): string {
  if (message.role === "user") return "Ты";
  if (message.role === "assistant") return agentName;
  return message.name ?? "Инструмент";
}

function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatFileTime(value: string): string {
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return value;
  return new Date(timestamp).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function TypewriterText({ text }: { text: string }) {
  const [visibleText, setVisibleText] = useState(text);

  useEffect(() => {
    setVisibleText("");

    if (!text) {
      return;
    }

    let index = 0;
    const timer = window.setInterval(() => {
      index += 1;
      setVisibleText(text.slice(0, index));

      if (index >= text.length) {
        window.clearInterval(timer);
      }
    }, 14);

    return () => window.clearInterval(timer);
  }, [text]);

  return (
    <>
      {visibleText}
      {visibleText.length < text.length ? <span className="type-cursor" aria-hidden="true" /> : null}
    </>
  );
}

function resolveAutoTheme(): ResolvedTheme {
  const hour = new Date().getHours();
  return hour >= 21 || hour < 7 ? "dark" : "light";
}

function resolveTheme(mode: ThemeMode): ResolvedTheme {
  return mode === "auto" ? resolveAutoTheme() : mode;
}

function readStoredThemeMode(): ThemeMode {
  const value = window.localStorage.getItem(THEME_KEY);
  return value === "light" || value === "dark" || value === "auto" ? value : "auto";
}

export default function App() {
  const [bootstrap, setBootstrap] = useState<BootstrapPayload>(EMPTY_BOOTSTRAP);
  const [memoryOverview, setMemoryOverview] = useState<MemoryOverview>(EMPTY_MEMORY_OVERVIEW);
  const [autonomousMemorySettings, setAutonomousMemorySettings] = useState<AutonomousMemorySettings>(EMPTY_AUTONOMOUS_MEMORY_SETTINGS);
  const [memorySummary, setMemorySummary] = useState<MemorySummary>(EMPTY_MEMORY_SUMMARY);
  const [isUpdatingAutonomousMemory, setIsUpdatingAutonomousMemory] = useState(false);
  const [personalMemory, setPersonalMemory] = useState<PersonalMemoryItem[]>([]);
  const [showHealthMemory, setShowHealthMemory] = useState(false);
  const [memoryDraft, setMemoryDraft] = useState("");
  const [memoryKind, setMemoryKind] = useState<PersonalMemoryItem["kind"]>("fact");
  const [memoryScope, setMemoryScope] = useState<PersonalMemoryItem["scope"]>("global");
  const [memorySensitive, setMemorySensitive] = useState(false);
  const [isSavingPersonalMemory, setIsSavingPersonalMemory] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isExecutingStep, setIsExecutingStep] = useState(false);
  const [approvingStepId, setApprovingStepId] = useState<string | null>(null);
  const [isOpeningWorkspace, setIsOpeningWorkspace] = useState(false);
  const [isClearingMemory, setIsClearingMemory] = useState(false);
  const [isExportingMemory, setIsExportingMemory] = useState(false);
  const [deletingNoteId, setDeletingNoteId] = useState<string | null>(null);
  const [downloadingArtifactPath, setDownloadingArtifactPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draftSavedAt, setDraftSavedAt] = useState<string | null>(null);
  const [animatedMessageKey, setAnimatedMessageKey] = useState<string | null>(null);
  const [authToken, setAuthToken] = useState(() => window.localStorage.getItem(AUTH_TOKEN_KEY) ?? "");
  const [activeThreadId, setActiveThreadId] = useState(() => window.localStorage.getItem(ACTIVE_THREAD_KEY) ?? "");
  const [activeProjectId, setActiveProjectId] = useState("");
  const [currentUser, setCurrentUser] = useState<UserProfile | null>(null);
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [authLoginValue, setAuthLoginValue] = useState("");
  const [authEmail, setAuthEmail] = useState("");
  const [authUsername, setAuthUsername] = useState("");
  const [authDisplayName, setAuthDisplayName] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [isAuthSubmitting, setIsAuthSubmitting] = useState(false);
  const [modelProviders, setModelProviders] = useState<ModelProviderOption[]>([]);
  const [selectedProvider, setSelectedProvider] = useState(EMPTY_BOOTSTRAP.provider);
  const [selectedModel, setSelectedModel] = useState(EMPTY_BOOTSTRAP.model);
  const [isModelSaving, setIsModelSaving] = useState(false);
  const [compactions, setCompactions] = useState<ContextCompactionItem[]>([]);
  const [compactionReview, setCompactionReview] = useState<ContextCompactionReview | null>(null);
  const [compactionBusyId, setCompactionBusyId] = useState<string | null>(null);
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => readStoredThemeMode());
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>(() => resolveTheme(readStoredThemeMode()));

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const generationControllerRef = useRef<AbortController | null>(null);

  const messageCountLabel = useMemo(() => {
    if (isLoading) return "Загрузка истории...";
    if (messages.length === 0) return "Диалог пока пуст";
    return `Сообщений в истории: ${messages.length}`;
  }, [isLoading, messages.length]);

  useEffect(() => {
    const draft = window.localStorage.getItem(DRAFT_KEY) ?? window.localStorage.getItem("nova-draft");
    if (draft) {
      setInput(draft);
    }
    window.localStorage.removeItem("nova-draft");
  }, []);

  useEffect(() => {
    window.localStorage.setItem(DRAFT_KEY, input);
    setDraftSavedAt(
      input.trim() ? new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) : null
    );
  }, [input]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, isSending, animatedMessageKey]);

  useEffect(() => {
    if (!authToken) {
      setIsLoading(false);
      return;
    }

    window.localStorage.setItem(AUTH_TOKEN_KEY, authToken);
    void fetchMe(authToken)
      .then((user) => {
        setCurrentUser(user);
        void fetchModelProviders(authToken)
          .then(setModelProviders)
          .catch(() => setModelProviders([]));
        return refreshBootstrap(true, false, authToken, activeThreadId);
      })
      .catch(() => {
        window.localStorage.removeItem(AUTH_TOKEN_KEY);
        setAuthToken("");
        setCurrentUser(null);
        setIsLoading(false);
      });
  }, [authToken]);

  useEffect(() => {
    function applyTheme() {
      const nextTheme = resolveTheme(themeMode);
      setResolvedTheme(nextTheme);
      document.documentElement.dataset.theme = nextTheme;
    }

    applyTheme();
    window.localStorage.setItem(THEME_KEY, themeMode);

    if (themeMode !== "auto") {
      return;
    }

    const timer = window.setInterval(applyTheme, 60_000);
    return () => window.clearInterval(timer);
  }, [themeMode]);

  async function refreshBootstrap(
    initial = false,
    animateLatestAssistant = false,
    token = authToken,
    threadId: string | null = activeThreadId
  ) {
    if (!token) {
      return;
    }

    if (initial) {
      setIsLoading(true);
    } else {
      setIsRefreshing(true);
    }

    try {
      const [data, memoryData, personalMemoryData, autonomousSettingsData, memorySummaryData] = await Promise.all([
        fetchBootstrap(token, threadId || null),
        fetchMemoryOverview(token),
        fetchPersonalMemory(token, showHealthMemory),
        fetchAutonomousMemorySettings(token),
        fetchMemorySummary(token)
      ]);
      setBootstrap(data);
      setMemoryOverview(memoryData);
      setPersonalMemory(personalMemoryData);
      setAutonomousMemorySettings(autonomousSettingsData);
      setMemorySummary(memorySummaryData);
      setMessages(data.history);
      setActiveThreadId(data.active_thread.id);
      setActiveProjectId(data.active_thread.project_id ?? "");
      window.localStorage.setItem(ACTIVE_THREAD_KEY, data.active_thread.id);
      setCurrentUser(data.user);
      setSelectedProvider(data.provider);
      setSelectedModel(data.model);
      const contextSnapshots = await fetchContextCompactions(token, data.active_thread.id).catch(() => []);
      setCompactions(contextSnapshots);
      setCompactionReview(null);
      setError(null);

      if (animateLatestAssistant) {
        const latestAssistantIndex = [...data.history].reverse().findIndex((message) => message.role === "assistant");
        if (latestAssistantIndex >= 0) {
          const index = data.history.length - 1 - latestAssistantIndex;
          setAnimatedMessageKey(`assistant-${index}`);
        }
      }
    } catch (err) {
      const fallback = err instanceof Error ? err.message : "Неизвестная ошибка.";
      setError(fallback);
    } finally {
      if (initial) {
        setIsLoading(false);
      } else {
        setIsRefreshing(false);
      }
    }
  }

  async function submitMessage(message: string) {
    const normalized = message.trim();
    if (!normalized || isSending || !authToken) {
      return;
    }

    const optimisticMessage: ChatMessage = { role: "user", content: normalized, status: "complete" };
    const pendingMessage: ChatMessage = { role: "assistant", content: "", status: "pending" };
    setMessages((current) => [...current, optimisticMessage, pendingMessage]);
    setInput("");
    setIsSending(true);
    setError(null);
    setAnimatedMessageKey(null);

    const controller = new AbortController();
    generationControllerRef.current = controller;

    try {
      await sendMessageStream(
        normalized,
        authToken,
        activeThreadId || null,
        controller.signal,
        (event) => {
          if (event.thread_id) {
            setActiveThreadId(event.thread_id);
            window.localStorage.setItem(ACTIVE_THREAD_KEY, event.thread_id);
          }
          if (event.type === "delta" && event.text) {
            setMessages((current) => {
              const next = [...current];
              let index = -1;
              for (let candidate = next.length - 1; candidate >= 0; candidate -= 1) {
                if (next[candidate].role === "assistant" && next[candidate].status === "pending") {
                  index = candidate;
                  break;
                }
              }
              if (index >= 0) next[index] = { ...next[index], content: next[index].content + event.text };
              return next;
            });
          }
          if (event.type === "done") {
            setMessages((current) => current.map((item) =>
              item.role === "assistant" && item.status === "pending" ? { ...item, status: "complete" } : item
            ));
          }
        }
      );
      await refreshBootstrap(false, false, authToken, window.localStorage.getItem(ACTIVE_THREAD_KEY));
      window.localStorage.removeItem(DRAFT_KEY);
      textareaRef.current?.focus();
    } catch (err) {
      const cancelled = err instanceof DOMException && err.name === "AbortError";
      const fallback = cancelled
        ? "Генерация остановлена. Частичный ответ сохранён в истории."
        : err instanceof Error ? err.message : "Неизвестная ошибка.";
      setError(fallback);
      setMessages((current) => current.map((item) =>
        item.role === "assistant" && item.status === "pending"
          ? { ...item, status: cancelled ? "cancelled" : "failed", error: fallback }
          : item
      ));
    } finally {
      generationControllerRef.current = null;
      setIsSending(false);
    }
  }

  function handleStopGeneration() {
    generationControllerRef.current?.abort();
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await submitMessage(input);
  }

  async function handleQuickAction(text: string) {
    setInput(text);
    textareaRef.current?.focus();
  }

  async function handleDownloadArtifact(artifact: FileArtifact) {
    if (!authToken || downloadingArtifactPath) return;
    setDownloadingArtifactPath(artifact.path);
    setError(null);
    try {
      const blob = await downloadWorkspaceFile(artifact.path, authToken);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = artifact.name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось скачать файл.");
    } finally {
      setDownloadingArtifactPath(null);
    }
  }

  async function handleRefresh() {
    setAnimatedMessageKey(null);
    await refreshBootstrap();
  }

  async function handleCreateChat() {
    if (!authToken) return;
    setError(null);
    try {
      const payload = await createChatThread(authToken, undefined, activeProjectId || null);
      setActiveThreadId(payload.thread.id);
      window.localStorage.setItem(ACTIVE_THREAD_KEY, payload.thread.id);
      setInput("");
      setAnimatedMessageKey(null);
      await refreshBootstrap(false, false, authToken, payload.thread.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать чат.");
    }
  }

  async function handleSelectChat(threadId: string) {
    if (!authToken || threadId === activeThreadId) return;
    setActiveThreadId(threadId);
    window.localStorage.setItem(ACTIVE_THREAD_KEY, threadId);
    setInput("");
    setAnimatedMessageKey(null);
    await refreshBootstrap(false, false, authToken, threadId);
  }

  async function handleFocusProject(projectId: string) {
    setActiveProjectId(projectId);
    const firstThread = bootstrap.chat_threads.find((thread) => thread.project_id === projectId);
    if (firstThread) {
      await handleSelectChat(firstThread.id);
    }
  }

  async function handleCreateProject() {
    if (!authToken) return;
    const title = window.prompt("Название проекта");
    if (!title?.trim()) return;
    const description = window.prompt("Краткое описание проекта", "") ?? "";
    setError(null);
    try {
      const payload = await createProject(authToken, title.trim(), description.trim());
      setActiveProjectId(payload.project.id);
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать проект.");
    }
  }

  async function handleRenameProject() {
    if (!authToken || !activeProjectId) return;
    const project = bootstrap.projects.find((item) => item.id === activeProjectId);
    const title = window.prompt("Новое название проекта", project?.title ?? "");
    if (!title?.trim()) return;
    const description = window.prompt("Описание проекта", project?.description ?? "") ?? project?.description ?? "";
    setError(null);
    try {
      await updateProject(authToken, activeProjectId, {
        title: title.trim(),
        description: description.trim()
      });
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось обновить проект.");
    }
  }

  async function handleArchiveProject() {
    if (!authToken || !activeProjectId) return;
    setError(null);
    try {
      await archiveProject(authToken, activeProjectId);
      setActiveProjectId("");
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось архивировать проект.");
    }
  }

  async function handleRestoreProject(projectId: string) {
    if (!authToken) return;
    setError(null);
    try {
      await restoreProject(authToken, projectId);
      setActiveProjectId(projectId);
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось восстановить проект.");
    }
  }

  async function handleDeleteProject() {
    if (!authToken || !activeProjectId) return;
    if (!window.confirm("Удалить проект из активного списка? Чаты останутся в истории и сохранят привязку.")) return;
    setError(null);
    try {
      await deleteProject(authToken, activeProjectId);
      setActiveProjectId("");
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить проект.");
    }
  }

  async function handleAssignActiveChatToProject(projectId: string | null) {
    if (!authToken || !activeThreadId) return;
    setError(null);
    try {
      await assignChatToProject(authToken, activeThreadId, projectId);
      setActiveProjectId(projectId ?? "");
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось привязать чат к проекту.");
    }
  }

  async function handleToggleChatMemory() {
    if (!authToken || !activeThreadId) return;
    setError(null);
    try {
      await updateChatMemory(authToken, activeThreadId, !bootstrap.active_thread.memory_enabled);
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось обновить правила памяти чата.");
    }
  }

  async function handleRememberChat() {
    if (!authToken || !activeThreadId) return;
    setError(null);
    try {
      await rememberChat(authToken, activeThreadId);
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить чат в память.");
    }
  }

  async function handleReviewCompaction(compactionId: string) {
    if (!authToken || !activeThreadId) return;
    setCompactionBusyId(compactionId);
    setError(null);
    try {
      setCompactionReview(await fetchContextCompactionReview(authToken, activeThreadId, compactionId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось проверить снимок контекста.");
    } finally {
      setCompactionBusyId(null);
    }
  }

  async function handleVerifyCompaction(compactionId: string) {
    if (!authToken || !activeThreadId) return;
    setCompactionBusyId(compactionId);
    setError(null);
    try {
      await verifyContextCompaction(authToken, activeThreadId, compactionId);
      setCompactions(await fetchContextCompactions(authToken, activeThreadId));
      setCompactionReview(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось подтвердить снимок контекста.");
    } finally {
      setCompactionBusyId(null);
    }
  }

  async function handleRollbackCompaction(compactionId: string) {
    if (!authToken || !activeThreadId) return;
    setCompactionBusyId(compactionId);
    setError(null);
    try {
      await rollbackContextCompaction(authToken, activeThreadId, compactionId);
      setCompactions(await fetchContextCompactions(authToken, activeThreadId));
      setCompactionReview(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось откатить снимок контекста.");
    } finally {
      setCompactionBusyId(null);
    }
  }

  async function handleDeleteMemoryNote(noteId: string) {
    if (!authToken || deletingNoteId) return;
    if (!window.confirm("Удалить эту заметку из памяти агента? История чатов и задачи останутся без изменений.")) return;
    setDeletingNoteId(noteId);
    setError(null);
    try {
      await deleteMemoryNote(authToken, noteId);
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить заметку памяти.");
    } finally {
      setDeletingNoteId(null);
    }
  }

  async function handleCreatePersonalMemory(event: FormEvent) {
    event.preventDefault();
    if (!authToken || !memoryDraft.trim() || isSavingPersonalMemory) return;
    const consentText = memorySensitive
      ? "Сохранить эту запись в отдельной памяти Health? Она может содержать чувствительные данные."
      : "Разрешить Avelin сохранить эту персональную запись?";
    if (!window.confirm(consentText)) return;
    setIsSavingPersonalMemory(true);
    setError(null);
    try {
      await createPersonalMemory(authToken, {
        kind: memoryKind,
        content: memoryDraft.trim(),
        scope: memoryScope,
        project_id: memoryScope === "project" ? activeProjectId || null : null,
        thread_id: memoryScope === "chat" ? activeThreadId || null : null,
        sensitive: memorySensitive,
        consent_granted: true
      });
      setMemoryDraft("");
      setPersonalMemory(await fetchPersonalMemory(authToken, showHealthMemory || memorySensitive));
      setMemoryOverview(await fetchMemoryOverview(authToken));
      setMemorySummary(await fetchMemorySummary(authToken, true));
      if (memorySensitive) setShowHealthMemory(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить персональную память.");
    } finally {
      setIsSavingPersonalMemory(false);
    }
  }

  async function handleEditPersonalMemory(memory: PersonalMemoryItem) {
    if (!authToken) return;
    const content = window.prompt("Изменить запись памяти:", memory.content);
    if (content === null || !content.trim() || content.trim() === memory.content) return;
    try {
      await updatePersonalMemory(authToken, memory, { content: content.trim() });
      setPersonalMemory(await fetchPersonalMemory(authToken, showHealthMemory));
      setMemorySummary(await fetchMemorySummary(authToken, true));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось изменить персональную память.");
    }
  }

  async function handleDeletePersonalMemory(memory: PersonalMemoryItem) {
    if (!authToken || !window.confirm("Удалить эту запись памяти без возможности восстановления?")) return;
    try {
      await deletePersonalMemory(authToken, memory);
      setPersonalMemory(await fetchPersonalMemory(authToken, showHealthMemory));
      setMemoryOverview(await fetchMemoryOverview(authToken));
      setMemorySummary(await fetchMemorySummary(authToken, true));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить персональную память.");
    }
  }

  async function handleRevokePersonalMemory(memory: PersonalMemoryItem) {
    if (!authToken || !window.confirm("Отозвать согласие? Запись перестанет использоваться агентом.")) return;
    try {
      await updatePersonalMemory(authToken, memory, { status: "revoked" });
      setPersonalMemory(await fetchPersonalMemory(authToken, showHealthMemory));
      setMemorySummary(await fetchMemorySummary(authToken, true));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отозвать согласие.");
    }
  }

  async function handleToggleHealthMemory() {
    if (!authToken) return;
    const next = !showHealthMemory;
    if (next && !window.confirm("Показать чувствительные записи Health на экране?")) return;
    setShowHealthMemory(next);
    setPersonalMemory(await fetchPersonalMemory(authToken, next));
  }

  async function handleToggleAutonomousMemory() {
    if (!authToken || isUpdatingAutonomousMemory) return;
    const next = !autonomousMemorySettings.autonomous_enabled;
    if (next && !window.confirm(
      "Включить автономную память? Avelin будет анализировать завершённые сообщения, сохранять только устойчивые несекретные факты и показывать их в сводке. Health-данные автоматически не сохраняются."
    )) return;
    setIsUpdatingAutonomousMemory(true);
    setError(null);
    try {
      const settings = await updateAutonomousMemorySettings(authToken, next);
      setAutonomousMemorySettings(settings);
      setMemorySummary(await fetchMemorySummary(authToken, true));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось изменить автономную память.");
    } finally {
      setIsUpdatingAutonomousMemory(false);
    }
  }

  async function handleRebuildMemorySummary() {
    if (!authToken || isUpdatingAutonomousMemory) return;
    setIsUpdatingAutonomousMemory(true);
    try {
      setMemorySummary(await fetchMemorySummary(authToken, true));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось пересобрать сводку памяти.");
    } finally {
      setIsUpdatingAutonomousMemory(false);
    }
  }

  async function handleExportMemory() {
    if (!authToken || isExportingMemory) return;
    setIsExportingMemory(true);
    setError(null);
    try {
      const includeSensitive = showHealthMemory && window.confirm("Включить чувствительные Health-данные в экспорт?");
      const blob = await exportAgentMemory(authToken, includeSensitive);
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      const stamp = new Date().toISOString().slice(0, 10);
      link.href = url;
      link.download = `avelin-memory-${stamp}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось экспортировать память агента.");
    } finally {
      setIsExportingMemory(false);
    }
  }

  async function handleClearMemory(scope: "all" | "global" | "current_chat" | "current_project") {
    if (!authToken || isClearingMemory) return;
    const labels = {
      all: "всю долгосрочную память агента, включая отдельные Health-записи",
      global: "глобальную память агента",
      current_chat: "память текущего чата",
      current_project: "память текущего проекта"
    };
    if (scope === "current_chat" && !activeThreadId) return;
    if (scope === "current_project" && !activeProjectId) return;
    if (!window.confirm(`Очистить ${labels[scope]}? Сообщения чатов, задачи, проекты и настройки модели не будут удалены.`)) {
      return;
    }
    setIsClearingMemory(true);
    setError(null);
    try {
      await clearAgentMemory(authToken, {
        scope,
        thread_id: scope === "current_chat" ? activeThreadId : null,
        project_id: scope === "current_project" ? activeProjectId : null,
        include_action_logs: scope === "all"
      });
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось очистить память агента.");
    } finally {
      setIsClearingMemory(false);
    }
  }

  async function handleRenameChat() {
    if (!authToken || !activeThreadId) return;
    const title = window.prompt("Новое название чата", bootstrap.active_thread.title);
    if (!title?.trim()) return;
    setError(null);
    try {
      await updateChatThread(authToken, activeThreadId, { title: title.trim() });
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось переименовать чат.");
    }
  }

  async function handleArchiveChat() {
    if (!authToken || !activeThreadId) return;
    setError(null);
    try {
      await archiveChatThread(authToken, activeThreadId);
      const nextThread = bootstrap.chat_threads.find((thread) => thread.id !== activeThreadId);
      await refreshBootstrap(false, false, authToken, nextThread?.id ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось архивировать чат.");
    }
  }

  async function handleUnarchiveChat(threadId: string) {
    if (!authToken) return;
    setError(null);
    try {
      await unarchiveChatThread(authToken, threadId);
      await refreshBootstrap(false, false, authToken, threadId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось вернуть чат из архива.");
    }
  }

  async function handleClearChat() {
    if (!authToken || !activeThreadId) return;
    if (!window.confirm("Очистить сообщения текущего чата? Заметки, задачи и долгосрочная память останутся.")) return;
    setError(null);
    try {
      await clearChatMessages(authToken, activeThreadId);
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось очистить чат.");
    }
  }

  async function handleDeleteChat() {
    if (!authToken || !activeThreadId) return;
    if (!window.confirm("Удалить текущий чат? Он будет скрыт через soft-delete, без окончательного удаления.")) return;
    setError(null);
    try {
      await deleteChatThread(authToken, activeThreadId);
      const nextThread = bootstrap.chat_threads.find((thread) => thread.id !== activeThreadId);
      setActiveThreadId(nextThread?.id ?? "");
      await refreshBootstrap(false, false, authToken, nextThread?.id ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить чат.");
    }
  }

  async function handleExecuteNextStep() {
    if (!authToken || isExecutingStep) return;
    setIsExecutingStep(true);
    setError(null);
    setAnimatedMessageKey(null);

    try {
      await executeNextStep(authToken);
      await refreshBootstrap(false, true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось выполнить следующий шаг.");
    } finally {
      setIsExecutingStep(false);
    }
  }

  async function handleApproveBlockedStep(stepId: string) {
    if (!authToken || approvingStepId) return;
    setApprovingStepId(stepId);
    setError(null);
    setAnimatedMessageKey(null);

    try {
      await approveBlockedStep(stepId, authToken);
      await refreshBootstrap(false, true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось подтвердить шаг.");
    } finally {
      setApprovingStepId(null);
    }
  }

  async function handleApprovalDecision(approvalId: string, decision: "approved" | "denied", stepId?: string | null) {
    if (!authToken || approvingStepId) return;
    setApprovingStepId(approvalId);
    setError(null);
    try {
      if (decision === "approved" && stepId) {
        await approveBlockedStep(stepId, authToken);
      } else {
        await decideApproval(approvalId, decision, authToken);
      }
      await refreshBootstrap(false, true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить решение.");
    } finally {
      setApprovingStepId(null);
    }
  }

  async function handleEmergencyStop() {
    if (!authToken) return;
    const enabled = !bootstrap.security.emergency_stop;
    if (enabled && !window.confirm("Остановить все инструменты и отозвать активные разрешения?")) return;
    try {
      await setEmergencyStop(enabled, authToken);
      await refreshBootstrap(false, true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось изменить аварийную остановку.");
    }
  }

  async function handleRevokeGrant(grantId: string) {
    if (!authToken) return;
    try {
      await revokePermissionGrant(grantId, authToken);
      await refreshBootstrap(false, true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отозвать разрешение.");
    }
  }

  async function handleOpenWorkspaceFolder() {
    if (!authToken || isOpeningWorkspace) return;
    setIsOpeningWorkspace(true);
    setError(null);

    try {
      await openWorkspaceFolder(authToken);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось открыть рабочую папку.");
    } finally {
      setIsOpeningWorkspace(false);
    }
  }

  async function handleAuthSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsAuthSubmitting(true);
    setAuthError(null);

    try {
      const payload =
        authMode === "login"
          ? await authLogin(authLoginValue.trim(), authPassword)
          : await authRegister(
              authEmail.trim(),
              authUsername.trim(),
              authPassword,
              authDisplayName.trim() || authUsername.trim()
            );
      window.localStorage.setItem(AUTH_TOKEN_KEY, payload.token);
      setAuthToken(payload.token);
      setCurrentUser(payload.user);
      void fetchModelProviders(payload.token)
        .then(setModelProviders)
        .catch(() => setModelProviders([]));
      setAuthPassword("");
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "Не удалось выполнить вход.");
    } finally {
      setIsAuthSubmitting(false);
    }
  }

  async function handleLogout() {
    if (authToken) {
      await authLogout(authToken);
    }
    window.localStorage.removeItem(AUTH_TOKEN_KEY);
    setAuthToken("");
    setCurrentUser(null);
    setBootstrap(EMPTY_BOOTSTRAP);
    setMessages([]);
    setActiveThreadId("");
    setActiveProjectId("");
    window.localStorage.removeItem(ACTIVE_THREAD_KEY);
    setInput("");
    setModelProviders([]);
  }

  async function handleSaveModelSettings() {
    if (!authToken) return;
    setIsModelSaving(true);
    setError(null);
    try {
      const settings = await updateModelSettings(authToken, {
        provider: selectedProvider,
        model_name: selectedModel,
        ollama_url: ""
      });
      setSelectedProvider(settings.provider);
      setSelectedModel(settings.model_name);
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось обновить модель.");
    } finally {
      setIsModelSaving(false);
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitMessage(input);
    }
  }

  const selectedProviderOption = modelProviders.find((item) => item.provider === selectedProvider);
  const selectedProviderModels = selectedProviderOption?.models ?? [selectedModel];
  const activeProject = bootstrap.projects.find((project) => project.id === activeProjectId) ?? bootstrap.active_project;
  const projectThreads = activeProjectId
    ? bootstrap.chat_threads.filter((thread) => thread.project_id === activeProjectId)
    : [];
  const unassignedThreads = bootstrap.chat_threads.filter((thread) => !thread.project_id);

  if (!authToken || !currentUser) {
    return (
      <main className="auth-page">
        <section className="auth-hero">
          <div className="brand-mark auth-logo">
            <img src={resolvedTheme === "dark" ? "/brand/avelin-dark.png" : "/brand/avelin-light.png"} alt="Avelin" />
          </div>
          <p className="eyebrow">Avelin</p>
          <h1>Личный AI-агент с памятью, заметками и будущей desktop-сборкой</h1>
          <p className="hero-text">
            Создай аккаунт, чтобы отделить свою историю, заметки и настройки модели. Дальше подключим Google,
            ВКонтакте и выбор AI-модели прямо из интерфейса.
          </p>
          <div className="auth-features">
            <span>Чат и память</span>
            <span>SQLite-хранилище</span>
            <span>Темы и Tauri-ready</span>
          </div>
        </section>

        <section className="auth-card">
          <div className="auth-tabs">
            <button className={authMode === "login" ? "theme-option theme-option-active" : "theme-option"} onClick={() => setAuthMode("login")} type="button">
              Вход
            </button>
            <button className={authMode === "register" ? "theme-option theme-option-active" : "theme-option"} onClick={() => setAuthMode("register")} type="button">
              Регистрация
            </button>
          </div>

          <form className="auth-form" onSubmit={handleAuthSubmit}>
            {authMode === "register" ? (
              <>
                <label>
                  Имя
                  <input value={authDisplayName} onChange={(event) => setAuthDisplayName(event.target.value)} required minLength={2} />
                </label>
                <label>
                  Email
                  <input value={authEmail} onChange={(event) => setAuthEmail(event.target.value)} required type="email" />
                </label>
                <label>
                  Логин
                  <input value={authUsername} onChange={(event) => setAuthUsername(event.target.value)} required minLength={2} />
                </label>
              </>
            ) : (
              <label>
                Email или логин
                <input value={authLoginValue} onChange={(event) => setAuthLoginValue(event.target.value)} required minLength={2} />
              </label>
            )}
            <label>
              Пароль
              <input value={authPassword} onChange={(event) => setAuthPassword(event.target.value)} required minLength={8} type="password" />
            </label>

            <button disabled={isAuthSubmitting} type="submit">
              {isAuthSubmitting ? "Подключаем..." : authMode === "login" ? "Войти" : "Создать аккаунт"}
            </button>
            {authError ? <p className="muted muted-error">{authError}</p> : null}
          </form>

          <div className="oauth-row">
            <button className="ghost-button" type="button" onClick={() => setAuthError("Google OAuth подготовлен на backend и будет включен после настройки client id.")}>
              Google
            </button>
            <button className="ghost-button" type="button" onClick={() => setAuthError("ВКонтакте OAuth подготовлен на backend и будет включен после настройки приложения VK.")}>
              ВКонтакте
            </button>
          </div>
        </section>
      </main>
    );
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand-card">
          <div className="brand-mark">
            <img src={resolvedTheme === "dark" ? "/brand/avelin-dark.png" : "/brand/avelin-light.png"} alt="Avelin" />
          </div>
          <div>
            <p className="eyebrow">Личный агент</p>
            <h1>{bootstrap.agent_name || "Avelin"}</h1>
          </div>
        </div>

        <section className="panel project-panel">
          <div className="panel-heading panel-heading-compact">
            <div>
              <p className="eyebrow">Projects</p>
              <h3>Рабочие области</h3>
            </div>
            <button className="ghost-button" onClick={() => void handleCreateProject()} type="button">
              Новый
            </button>
          </div>

          <div className="project-list">
            {bootstrap.projects.length === 0 ? (
              <p className="muted">Проектов пока нет.</p>
            ) : (
              bootstrap.projects.map((project) => (
                <button
                  className={project.id === activeProjectId ? "project-item project-item-active" : "project-item"}
                  key={project.id}
                  onClick={() => void handleFocusProject(project.id)}
                  type="button"
                >
                  <span>{project.title}</span>
                  <small>{project.chat_count} чатов</small>
                </button>
              ))
            )}
          </div>

          <div className="thread-actions">
            <button className="mini-button" disabled={!activeProjectId} onClick={() => void handleRenameProject()} type="button">
              Назвать
            </button>
            <button className="mini-button" disabled={!activeProjectId} onClick={() => void handleArchiveProject()} type="button">
              Архив
            </button>
            <button className="mini-button" disabled={!activeProjectId} onClick={() => void handleDeleteProject()} type="button">
              Удалить
            </button>
            <button className="mini-button" disabled={!activeThreadId} onClick={() => void handleAssignActiveChatToProject(null)} type="button">
              Без проекта
            </button>
          </div>

          {bootstrap.projects.length > 0 ? (
            <label className="project-select">
              Чат в проекте
              <select
                value={bootstrap.active_thread.project_id ?? ""}
                onChange={(event) => void handleAssignActiveChatToProject(event.target.value || null)}
              >
                <option value="">Без проекта</option>
                {bootstrap.projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.title}
                  </option>
                ))}
              </select>
            </label>
          ) : null}

          {bootstrap.archived_projects.length > 0 ? (
            <details className="thread-archive">
              <summary>Архив проектов ({bootstrap.archived_projects.length})</summary>
              <div className="project-list">
                {bootstrap.archived_projects.map((project) => (
                  <button
                    className="project-item"
                    key={project.id}
                    onClick={() => void handleRestoreProject(project.id)}
                    type="button"
                  >
                    <span>{project.title}</span>
                    <small>Вернуть в активные</small>
                  </button>
                ))}
              </div>
            </details>
          ) : null}
        </section>

        <section className="panel chat-workspace-panel">
          <div className="panel-heading panel-heading-compact">
            <div>
              <p className="eyebrow">Chats</p>
              <h3>{activeProject ? activeProject.title : "Диалоги"}</h3>
            </div>
            <button className="ghost-button" onClick={() => void handleCreateChat()} type="button">
              Новый
            </button>
          </div>

          {activeProjectId ? (
            <div className="thread-list">
              {projectThreads.length === 0 ? (
                <p className="muted">В проекте пока нет чатов.</p>
              ) : (
                projectThreads.map((thread) => (
                  <button
                    className={thread.id === activeThreadId ? "thread-item thread-item-active" : "thread-item"}
                    key={thread.id}
                    onClick={() => void handleSelectChat(thread.id)}
                    type="button"
                  >
                    <span>{thread.title}</span>
                    <small>{thread.message_count} сообщений</small>
                  </button>
                ))
              )}
            </div>
          ) : null}

          <div className="thread-list">
            <p className="thread-group-title">Без проекта</p>
            {unassignedThreads.length === 0 ? (
              <p className="muted">Свободных чатов пока нет.</p>
            ) : (
              unassignedThreads.map((thread) => (
                <button
                  className={thread.id === activeThreadId ? "thread-item thread-item-active" : "thread-item"}
                  key={thread.id}
                  onClick={() => void handleSelectChat(thread.id)}
                  type="button"
                >
                  <span>{thread.title}</span>
                  <small>{thread.message_count} сообщений</small>
                </button>
              ))
            )}
          </div>

          <div className="thread-actions">
            <button className="mini-button" disabled={!activeThreadId} onClick={() => void handleRenameChat()} type="button">
              Назвать
            </button>
            <button className="mini-button" disabled={!activeThreadId} onClick={() => void handleClearChat()} type="button">
              Очистить
            </button>
            <button className="mini-button" disabled={!activeThreadId} onClick={() => void handleArchiveChat()} type="button">
              Архив
            </button>
            <button className="mini-button" disabled={!activeThreadId} onClick={() => void handleDeleteChat()} type="button">
              Удалить
            </button>
          </div>

          <div className="memory-rule-box">
            <div>
              <strong>{bootstrap.active_thread.memory_enabled ? "Чат влияет на память" : "Чат исключен из памяти"}</strong>
              <span>
                {bootstrap.active_thread.memory_saved_at
                  ? `Сохранен: ${formatFileTime(bootstrap.active_thread.memory_saved_at)}`
                  : "Можно явно сохранить важное из этой ветки."}
              </span>
            </div>
            <div className="thread-actions">
              <button className="mini-button" disabled={!activeThreadId} onClick={() => void handleRememberChat()} type="button">
                Запомнить
              </button>
              <button className="mini-button" disabled={!activeThreadId} onClick={() => void handleToggleChatMemory()} type="button">
                {bootstrap.active_thread.memory_enabled ? "Исключить" : "Разрешить"}
              </button>
            </div>
          </div>

          {compactions.length > 0 ? (
            <details className="compaction-panel">
              <summary>
                Контекст: {compactions.length} снимков
                {compactions.some((item) => item.status === "pending_review") ? " · нужна проверка" : ""}
              </summary>
              <div className="compaction-list">
                {compactions.map((item) => (
                  <article className="compaction-item" key={item.id}>
                    <div>
                      <strong>Цикл {item.cycle_number}</strong>
                      <span className={`compaction-status compaction-status-${item.status}`}>
                        {item.status === "pending_review"
                          ? "На проверке"
                          : item.status === "verified"
                            ? "Подтверждён"
                            : "Откачен"}
                      </span>
                      <small>{item.summary.source_message_count} сообщений · {item.source_hash.slice(0, 12)}</small>
                    </div>
                    <div className="thread-actions">
                      <button
                        className="mini-button"
                        disabled={compactionBusyId === item.id}
                        onClick={() => void handleReviewCompaction(item.id)}
                        type="button"
                      >
                        Сверить
                      </button>
                      {item.status === "pending_review" ? (
                        <button
                          className="mini-button"
                          disabled={
                            compactionBusyId === item.id ||
                            compactionReview?.compaction.id !== item.id ||
                            !compactionReview.source_hash_matches
                          }
                          onClick={() => void handleVerifyCompaction(item.id)}
                          type="button"
                        >
                          Подтвердить
                        </button>
                      ) : null}
                      {item.status !== "rolled_back" ? (
                        <button
                          className="mini-button"
                          disabled={compactionBusyId === item.id}
                          onClick={() => void handleRollbackCompaction(item.id)}
                          type="button"
                        >
                          Откатить
                        </button>
                      ) : null}
                    </div>
                  </article>
                ))}
              </div>

              {compactionReview ? (
                <div className="compaction-review">
                  <strong>Сверка цикла {compactionReview.compaction.cycle_number}</strong>
                  <p className={compactionReview.source_hash_matches ? "hash-ok" : "hash-error"}>
                    {compactionReview.source_hash_matches
                      ? "Исходники не изменены, контрольная сумма совпадает."
                      : "Исходники изменились — подтверждение заблокировано."}
                  </p>
                  {(["goals", "decisions", "facts", "open_questions"] as const).map((section) => {
                    const items = compactionReview.compaction.summary[section];
                    if (items.length === 0) return null;
                    const labels = {
                      goals: "Цели",
                      decisions: "Решения",
                      facts: "Факты",
                      open_questions: "Открытые вопросы"
                    };
                    return (
                      <div className="compaction-section" key={section}>
                        <b>{labels[section]}</b>
                        <ul>
                          {items.map((summaryItem, index) => (
                            <li key={`${section}-${index}`}>
                              {summaryItem.text}
                              <small>{summaryItem.message_ids.map((id) => `message:${id.slice(0, 8)}`).join(", ")}</small>
                            </li>
                          ))}
                        </ul>
                      </div>
                    );
                  })}
                  <details className="compaction-sources">
                    <summary>Исходные сообщения ({compactionReview.source_messages.length})</summary>
                    {compactionReview.source_messages.map((source) => (
                      <div className="compaction-source" key={source.id}>
                        <small>{source.role} · message:{source.id}</small>
                        <p>{source.content}</p>
                      </div>
                    ))}
                  </details>
                </div>
              ) : null}
            </details>
          ) : null}

          {bootstrap.archived_chat_threads.length > 0 ? (
            <details className="thread-archive">
              <summary>Архив ({bootstrap.archived_chat_threads.length})</summary>
              <div className="thread-list">
                {bootstrap.archived_chat_threads.map((thread) => (
                  <button
                    className="thread-item"
                    key={thread.id}
                    onClick={() => void handleUnarchiveChat(thread.id)}
                    type="button"
                  >
                    <span>{thread.title}</span>
                    <small>Вернуть в активные</small>
                  </button>
                ))}
              </div>
            </details>
          ) : null}
        </section>

        <section className="panel panel-compact">
          <div className="panel-heading panel-heading-compact">
            <p className="eyebrow">Среда</p>
            <div className="theme-switcher" aria-label="Режим темы">
              {(["auto", "light", "dark"] as const).map((mode) => (
                <button
                  className={themeMode === mode ? "theme-option theme-option-active" : "theme-option"}
                  key={mode}
                  onClick={() => setThemeMode(mode)}
                  type="button"
                >
                  {mode === "auto" ? "Авто" : mode === "light" ? "Свет" : "Тьма"}
                </button>
              ))}
            </div>
          </div>
          <div className="status-grid">
            <div className="status-pill">
              <span className="status-dot" />
              <strong>{bootstrap.provider}</strong>
            </div>
            <div className="status-box">
              <span className="status-label">Модель</span>
              <strong>{bootstrap.model}</strong>
            </div>
            <div className="status-box">
              <span className="status-label">Заметки</span>
              <strong>{bootstrap.notes.length}</strong>
            </div>
          </div>
        </section>

        <section className="panel panel-compact">
          <div className="panel-heading panel-heading-compact">
            <div>
              <p className="eyebrow">Модель</p>
              <h3>AI-режим</h3>
            </div>
            <button className="ghost-button" onClick={() => void handleSaveModelSettings()} disabled={isModelSaving} type="button">
              {isModelSaving ? "Сохраняю..." : "Сохранить"}
            </button>
          </div>
          <div className="model-controls">
            <label>
              Provider
              <select
                value={selectedProvider}
                onChange={(event) => {
                  const nextProvider = event.target.value;
                  const nextOption = modelProviders.find((item) => item.provider === nextProvider);
                  setSelectedProvider(nextProvider);
                  setSelectedModel(nextOption?.models[0] ?? "");
                }}
              >
                {modelProviders.map((provider) => (
                  <option key={provider.provider} value={provider.provider}>
                    {provider.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Модель
              <select value={selectedModel} onChange={(event) => setSelectedModel(event.target.value)}>
                {selectedProviderModels.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            </label>
            <p className="muted">{selectedProviderOption?.description ?? "Выбери provider и модель."}</p>
          </div>
        </section>

        <section className="panel panel-compact">
          <div className="panel-heading panel-heading-compact">
            <div>
              <p className="eyebrow">Профиль</p>
              <h3>{currentUser.display_name}</h3>
            </div>
            <button className="ghost-button" onClick={() => void handleLogout()} type="button">
              Выйти
            </button>
          </div>
          <div className="status-grid">
            <div className="status-box">
              <span className="status-label">Логин</span>
              <strong>{currentUser.username || "—"}</strong>
            </div>
            <div className="status-box">
              <span className="status-label">Вход</span>
              <strong>{currentUser.auth_provider}</strong>
            </div>
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Память</p>
              <h3>Что уже хранит агент</h3>
            </div>
            <button className="ghost-button" onClick={() => void handleRefresh()} type="button">
              {isRefreshing ? "Обновляю..." : "Обновить"}
            </button>
          </div>

          <div className="memory-rule-box autonomous-memory-box">
            <div>
              <strong>{autonomousMemorySettings.autonomous_enabled ? "Автономная память включена" : "Автономная память выключена"}</strong>
              <span>
                Анализирует только завершённые сообщения. Пароли, токены, Health, финансы и содержимое документов автоматически не сохраняются.
              </span>
            </div>
            <button className="mini-button" disabled={isUpdatingAutonomousMemory} onClick={() => void handleToggleAutonomousMemory()} type="button">
              {autonomousMemorySettings.autonomous_enabled ? "Выключить" : "Включить"}
            </button>
          </div>

          <div className="memory-summary-card">
            <div className="memory-summary-heading">
              <div>
                <strong>Сводка памяти</strong>
                <span>
                  {memorySummary.generated_at ? `Обновлена ${formatFileTime(memorySummary.generated_at)}` : "Ещё не сформирована"}
                </span>
              </div>
              <button className="mini-button" disabled={isUpdatingAutonomousMemory} onClick={() => void handleRebuildMemorySummary()} type="button">
                Пересобрать
              </button>
            </div>
            {memorySummary.record_count === 0 ? (
              <p className="muted">Пока нет подтверждённых записей для сводки.</p>
            ) : (
              (Object.keys(MEMORY_SECTION_LABELS) as PersonalMemoryItem["kind"][]).map((kind) => (
                memorySummary.sections[kind]?.length ? (
                  <div className="memory-summary-section" key={kind}>
                    <span>{MEMORY_SECTION_LABELS[kind]}</span>
                    <ul>
                      {memorySummary.sections[kind].map((item) => (
                        <li key={item.id}>
                          {item.content}
                          {item.auto_generated ? <small>Автоматически · {item.provenance.length} источник(а)</small> : <small>Добавлено вручную</small>}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null
              ))
            )}
          </div>

          <div className="memory-overview-grid">
            <div className="memory-stat">
              <span>Заметки</span>
              <strong>{memoryOverview.counts.notes}</strong>
            </div>
            <div className="memory-stat">
              <span>Чаты</span>
              <strong>{memoryOverview.counts.chat_messages}</strong>
            </div>
            <div className="memory-stat">
              <span>Задачи</span>
              <strong>{memoryOverview.counts.tasks}</strong>
            </div>
            <div className="memory-stat">
              <span>Журнал</span>
              <strong>{memoryOverview.counts.action_logs}</strong>
            </div>
          </div>

          <div className="memory-scope-list">
            <span>Глобальные: {memoryOverview.counts.global_notes}</span>
            <span>Проектные: {memoryOverview.counts.project_notes}</span>
            <span>Из чатов: {memoryOverview.counts.chat_notes}</span>
            <span>Индекс: {memoryOverview.counts.memory_index}</span>
            <span>Персональные: {memoryOverview.counts.personal_records}</span>
            <span>Health: {memoryOverview.counts.health_records}</span>
          </div>

          <form className="memory-editor" onSubmit={(event) => void handleCreatePersonalMemory(event)}>
            <textarea
              aria-label="Новая запись персональной памяти"
              maxLength={8000}
              onChange={(event) => setMemoryDraft(event.target.value)}
              placeholder="Что Avelin должна помнить? Сохранение произойдет только после подтверждения."
              value={memoryDraft}
            />
            <div className="memory-editor-controls">
              <select value={memoryKind} onChange={(event) => setMemoryKind(event.target.value as PersonalMemoryItem["kind"])}>
                <option value="fact">Факт</option>
                <option value="preference">Предпочтение</option>
                <option value="profile">Профиль</option>
                <option value="episode">Эпизод</option>
              </select>
              <select value={memoryScope} onChange={(event) => setMemoryScope(event.target.value as PersonalMemoryItem["scope"])}>
                <option value="global">Глобально</option>
                <option value="project" disabled={!activeProjectId}>Текущий проект</option>
                <option value="chat">Текущий чат</option>
              </select>
              <label className="memory-sensitive-toggle">
                <input checked={memorySensitive} onChange={(event) => setMemorySensitive(event.target.checked)} type="checkbox" />
                Health
              </label>
              <button className="mini-button" disabled={!memoryDraft.trim() || isSavingPersonalMemory} type="submit">
                {isSavingPersonalMemory ? "Сохраняю..." : "Сохранить с согласием"}
              </button>
            </div>
          </form>

          <div className="memory-actions">
            <button className="mini-button" onClick={() => void handleToggleHealthMemory()} type="button">
              {showHealthMemory ? "Скрыть Health" : "Показать Health"}
            </button>
          </div>

          <div className="notes">
            {personalMemory.length === 0 ? (
              <p className="muted">Персональных записей пока нет.</p>
            ) : (
              personalMemory.map((memory) => (
                <article className="note" key={`${memory.sensitive ? "health" : "personal"}-${memory.id}`}>
                  <span className="note-index">{memory.kind.slice(0, 2).toUpperCase()}</span>
                  <div className="note-body">
                    <div>
                      <p>{memory.content}</p>
                      <small>
                        {memory.scope} · confidence {memory.confidence.toFixed(2)} · {memory.status}
                        {memory.auto_generated ? " · АВТОМАТИЧЕСКИ" : " · ВРУЧНУЮ"}
                        {memory.sensitive ? " · HEALTH" : ""}
                      </small>
                      <small>Источник: {memory.provenance.map((source) => source.source_type).join(", ") || "не указан"}</small>
                    </div>
                    <div className="memory-actions">
                      <button className="mini-button" onClick={() => void handleEditPersonalMemory(memory)} type="button">Изменить</button>
                      <button className="mini-button" onClick={() => void handleRevokePersonalMemory(memory)} type="button">Отозвать</button>
                      <button className="mini-button mini-button-danger" onClick={() => void handleDeletePersonalMemory(memory)} type="button">Удалить</button>
                    </div>
                  </div>
                </article>
              ))
            )}
          </div>

          <div className="memory-actions">
            <button className="mini-button" disabled={isExportingMemory} onClick={() => void handleExportMemory()} type="button">
              {isExportingMemory ? "Экспорт..." : "Экспорт JSON"}
            </button>
            <button className="mini-button" disabled={isClearingMemory} onClick={() => void handleClearMemory("current_chat")} type="button">
              Очистить чат
            </button>
            <button
              className="mini-button"
              disabled={isClearingMemory || !activeProjectId}
              onClick={() => void handleClearMemory("current_project")}
              type="button"
            >
              Очистить проект
            </button>
            <button className="mini-button" disabled={isClearingMemory} onClick={() => void handleClearMemory("global")} type="button">
              Очистить global
            </button>
            <button className="mini-button mini-button-danger" disabled={isClearingMemory} onClick={() => void handleClearMemory("all")} type="button">
              Очистить всё
            </button>
          </div>

          <div className="notes">
            {memoryOverview.notes.length === 0 ? (
              <p className="muted">Пока без заметок.</p>
            ) : (
              memoryOverview.notes.map((note, index) => (
                <article className="note" key={note.id}>
                  <span className="note-index">{String(index + 1).padStart(2, "0")}</span>
                  <div className="note-body">
                    <div>
                      <p>{note.content}</p>
                      <small>{note.scope === "global" ? "global" : note.scope === "current_project" ? "project" : "chat"}</small>
                    </div>
                    <button
                      className="mini-button"
                      disabled={deletingNoteId === note.id}
                      onClick={() => void handleDeleteMemoryNote(note.id)}
                      type="button"
                    >
                      {deletingNoteId === note.id ? "Удаляю..." : "Удалить"}
                    </button>
                  </div>
                </article>
              ))
            )}
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Security</p>
              <h3>Разрешения инструментов</h3>
            </div>
            <button
              className={`ghost-button ${bootstrap.security.emergency_stop ? "" : "mini-button-danger"}`}
              onClick={() => void handleEmergencyStop()}
              type="button"
            >
              {bootstrap.security.emergency_stop ? "Возобновить" : "Emergency stop"}
            </button>
          </div>
          {bootstrap.security.emergency_stop ? (
            <div className="memory-rule-box security-stop-banner">
              <strong>Инструменты остановлены</strong>
              <span>Активные approval и длительные grants отозваны.</span>
            </div>
          ) : null}
          <div className="task-list">
            {bootstrap.approval_requests.length === 0 ? (
              <p className="muted">Нет действий, ожидающих решения.</p>
            ) : (
              bootstrap.approval_requests.map((approval) => (
                <article className="task-item" key={approval.id}>
                  <div className="task-item-head">
                    <strong>{approval.tool_name}</strong>
                    <span className={`state-badge state-${approval.risk}`}>{approval.risk}</span>
                  </div>
                  <p className="muted">Область: {JSON.stringify(approval.scope)}</p>
                  <p className="muted">Аргументы: {JSON.stringify(approval.arguments)}</p>
                  <small>Истекает: {formatFileTime(approval.expires_at)}</small>
                  <div className="memory-actions">
                    <button
                      className="mini-button"
                      disabled={approvingStepId === approval.id}
                      onClick={() => void handleApprovalDecision(approval.id, "approved", approval.step_id)}
                      type="button"
                    >
                      Разрешить один раз
                    </button>
                    <button
                      className="mini-button mini-button-danger"
                      disabled={approvingStepId === approval.id}
                      onClick={() => void handleApprovalDecision(approval.id, "denied")}
                      type="button"
                    >
                      Запретить
                    </button>
                  </div>
                </article>
              ))
            )}
          </div>
          {bootstrap.permission_grants.length > 0 ? (
            <div className="task-list security-grants">
              <strong>Длительные разрешения</strong>
              {bootstrap.permission_grants.map((grant) => (
                <article className="task-item" key={grant.id}>
                  <div className="task-item-head">
                    <span>{grant.permission} · {JSON.stringify(grant.scope)}</span>
                    <button className="mini-button mini-button-danger" onClick={() => void handleRevokeGrant(grant.id)} type="button">
                      Отозвать
                    </button>
                  </div>
                </article>
              ))}
            </div>
          ) : null}
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Execution</p>
              <h3>Очередь задач</h3>
            </div>
            <button className="ghost-button" onClick={() => void handleExecuteNextStep()} disabled={isExecutingStep} type="button">
              {isExecutingStep ? "Выполняю..." : "Выполнить шаг"}
            </button>
          </div>
          <div className="task-list">
            {bootstrap.tasks.length === 0 ? (
              <p className="muted">Очередь задач пуста.</p>
            ) : (
              bootstrap.tasks.slice(0, 4).map((task) => (
                <article className="task-item" key={task.id}>
                  <div className="task-item-head">
                    <strong>{task.description}</strong>
                    <span className={`state-badge state-${task.status}`}>{task.status}</span>
                  </div>
                  <div className="task-steps">
                    {task.steps.slice(0, 4).map((step) => (
                      <div className="task-step-row" key={step.id}>
                        <p>
                          <span>{step.position}.</span> [{step.status}] {step.description}
                        </p>
                        {task.status === "blocked" && step.status === "blocked" ? (
                          <button
                            className="mini-button"
                            disabled={approvingStepId === step.id}
                            onClick={() => void handleApproveBlockedStep(step.id)}
                            type="button"
                          >
                            {approvingStepId === step.id ? "Разрешаю..." : "Разрешить"}
                          </button>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </article>
              ))
            )}
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Files</p>
              <h3>Файлы агента</h3>
            </div>
            <button className="ghost-button" disabled={isOpeningWorkspace} onClick={() => void handleOpenWorkspaceFolder()} type="button">
              {isOpeningWorkspace ? "Открываю..." : "Открыть"}
            </button>
          </div>
          <div className="workspace-file-list">
            {bootstrap.workspace_files.length === 0 ? (
              <p className="muted">Рабочая папка пока пуста.</p>
            ) : (
              bootstrap.workspace_files.slice(0, 6).map((file) => (
                <article className="workspace-file" key={file.path}>
                  <div>
                    <strong>{file.name}</strong>
                    <span>{file.path}</span>
                  </div>
                  <p>
                    {formatFileSize(file.size_bytes)} · {formatFileTime(file.modified_at)}
                  </p>
                </article>
              ))
            )}
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Audit</p>
              <h3>Журнал действий</h3>
            </div>
          </div>
          <div className="action-log-list">
            {bootstrap.action_logs.length === 0 ? (
              <p className="muted">Действий пока нет.</p>
            ) : (
              bootstrap.action_logs.slice(0, 6).map((log) => (
                <article className="action-log-item" key={log.id}>
                  <div>
                    <strong>{log.tool_name}</strong>
                    <span className={`state-badge state-${log.status}`}>{log.status}</span>
                  </div>
                  <p>{log.result}</p>
                </article>
              ))
            )}
          </div>
        </section>

        <section className="panel">
          <p className="eyebrow">Desktop</p>
          <h3>Готовим основу под Tauri</h3>
          <p className="muted">
            Интерфейс отделен от backend, поэтому позже мы сможем упаковать Avelin в настольное приложение
            без переписывания логики чата.
          </p>
        </section>
      </aside>

      <main className="workspace">
        <header className="hero">
          <div className="hero-copy">
            <p className="eyebrow">Диалог</p>
            <h2>Avelin помогает держать мысли, задачи и память в одном спокойном рабочем окне</h2>
            <p className="hero-text">
              Здесь можно разговаривать с агентом, сохранять заметки и постепенно превращать его в полноценное
              личное приложение.
            </p>
          </div>
          <div className="hero-signet" aria-hidden="true">
            <span>A</span>
          </div>
        </header>

        <section className="quick-actions" aria-label="Быстрые действия">
          {QUICK_ACTIONS.map((item) => (
            <button
              className="action-chip"
              key={item}
              onClick={() => void handleQuickAction(item)}
              type="button"
            >
              {item}
            </button>
          ))}
        </section>

        <section className="chat-card">
          <div className="chat-header">
            <div>
              <strong>{bootstrap.active_thread.title || "Диалог"}</strong>
              <p className="muted">{messageCountLabel}</p>
            </div>
            <div className="header-tags">
              <span className="header-tag">{isSending ? "Avelin думает" : "Готов к диалогу"}</span>
              <span className="header-tag header-tag-muted">
                {draftSavedAt ? `Черновик: ${draftSavedAt}` : "Черновик пуст"}
              </span>
            </div>
          </div>

          <div className="messages">
            {messages.length === 0 && !isLoading ? (
              <div className="empty-state">
                <p>Напиши первое сообщение агенту.</p>
                <span>Можно спросить про время, память или планирование дня.</span>
              </div>
            ) : null}

            {messages.map((message, index) => {
              const messageKey = `${message.role}-${index}`;

              return (
                <article className={`message message-${message.role} message-${message.status ?? "complete"}`} key={messageKey}>
                  <span className="message-role">{formatRoleLabel(message, bootstrap.agent_name)}</span>
                  <div className="message-content">
                    {message.status === "pending" && !message.content ? (
                      <div className="typing-dots" aria-label="Avelin печатает"><span /><span /><span /></div>
                    ) : animatedMessageKey === messageKey ? (
                      <TypewriterText text={message.content} />
                    ) : message.role === "assistant" ? (
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />
                        }}
                      >
                        {message.content}
                      </ReactMarkdown>
                    ) : (
                      message.content
                    )}
                    {message.error ? <p className="message-error">{message.error}</p> : null}
                    {message.artifacts?.length ? (
                      <div className="message-artifacts" aria-label="Созданные файлы">
                        {message.artifacts.map((artifact) => (
                          <button
                            className="artifact-card"
                            disabled={downloadingArtifactPath === artifact.path}
                            key={artifact.path}
                            onClick={() => void handleDownloadArtifact(artifact)}
                            type="button"
                          >
                            <span className="artifact-icon" aria-hidden="true">
                              {artifact.extension.replace(".", "").slice(0, 4).toUpperCase() || "FILE"}
                            </span>
                            <span className="artifact-copy">
                              <strong>{artifact.name}</strong>
                              <small>{formatFileSize(artifact.size_bytes)}</small>
                            </span>
                            <span className="artifact-action">
                              {downloadingArtifactPath === artifact.path ? "Скачивание…" : "Скачать ↓"}
                            </span>
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </article>
              );
            })}

            <div ref={messagesEndRef} />
          </div>

          <form className="composer" onSubmit={handleSubmit}>
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              placeholder="Спроси что-нибудь у Avelin..."
              rows={3}
            />
            <div className="composer-row">
              <span className={`muted ${error ? "muted-error" : ""}`}>
                {error ? error : "Enter отправляет сообщение, Shift+Enter переносит строку."}
              </span>
              <div className="composer-actions">
                <button
                  className="ghost-button"
                  onClick={() => setInput("")}
                  disabled={!input.trim() || isSending}
                  type="button"
                >
                  Очистить
                </button>
                {isSending ? (
                  <button className="stop-button" onClick={handleStopGeneration} type="button">Остановить</button>
                ) : (
                  <button disabled={!input.trim()} type="submit">Отправить</button>
                )}
              </div>
            </div>
          </form>
        </section>
      </main>
    </div>
  );
}
