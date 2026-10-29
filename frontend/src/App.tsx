import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { WorkspaceIcon, type IconName } from "./WorkspaceIcon";
import {
  archiveProject,
  archiveChatThread,
  assignChatToProject,
  approveBlockedStep,
  acceptProjectInvitation,
  clearChatMessages,
  clearAgentMemory,
  controlAutomation,
  controlTask,
  createAutomation,
  createProjectInvitation,
  createSharedProjectItem,
  createExtensionProposal,
  createDailyTaskReport,
  createRunReport,
  createPersonalMemory,
  createProject,
  createChatThread,
  decideApproval,
  deleteMemoryNote,
  deleteAutomation,
  deletePersonalMemory,
  deleteProject,
  deleteChatThread,
  disableExtension,
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
  fetchProjectInvitations,
  fetchProjectMembers,
  fetchSharedProjectItems,
  installExtension,
  login as authLogin,
  markNotificationRead,
  logout as authLogout,
  openWorkspaceFolder,
  register as authRegister,
  rememberChat,
  rollbackContextCompaction,
  rollbackExtension,
  restoreProject,
  removeProjectMember,
  revokePermissionGrant,
  sendMessageStream,
  setConnectorEnabled,
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
  ProjectInvitationItem,
  ProjectMemberItem,
  SharedProjectItem,
  UserProfile
} from "./types";

const DRAFT_KEY = "avelin-draft";
const THEME_KEY = "avelin-theme-mode";
const AUTH_TOKEN_KEY = "avelin-auth-token";
const ACTIVE_THREAD_KEY = "avelin-active-thread";


type WorkspaceView = "home" | "chat" | "projects" | "memory" | "automations" | "tasks" | "files" | "settings";
type SettingsView = "general" | "connections" | "tools" | "security" | "activity";
const WORKSPACE_NAV: { id: WorkspaceView; label: string; icon: IconName }[] = [
  { id: "home", label: "Главная", icon: "home" },
  { id: "chat", label: "Диалоги", icon: "chat" },
  { id: "projects", label: "Проекты", icon: "projects" },
  { id: "memory", label: "Память", icon: "memory" },
  { id: "automations", label: "Автоматизации", icon: "clock" },
  { id: "tasks", label: "Задачи", icon: "tasks" },
  { id: "files", label: "Файлы", icon: "files" },
];
const SETTINGS_NAV: { id: SettingsView; label: string }[] = [
  { id: "general", label: "Общие" }, { id: "connections", label: "Подключения" },
  { id: "tools", label: "Инструменты" }, { id: "security", label: "Разрешения" },
  { id: "activity", label: "Журнал" },
];

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
  task_runs: [],
  automations: [],
  task_reports: [],
  notifications: [],
  action_logs: [],
  approval_requests: [],
  permission_grants: [],
  extension_proposals: [],
  connectors: [],
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

function formatRoleLabel(message: ChatMessage, agentName: string, shared = false): string {
  if (message.role === "user") return shared ? (message.name ?? "Участник") : "Ты";
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

function defaultAutomationRunAt(): string {
  const value = new Date(Date.now() + 60 * 60 * 1000);
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60 * 1000);
  return local.toISOString().slice(0, 16);
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
  const [isSavingAutomation, setIsSavingAutomation] = useState(false);
  const [automationName, setAutomationName] = useState("Еженедельный черновик");
  const [automationPrompt, setAutomationPrompt] = useState("Создай документ drafts/weekly-{date}.docx с заголовком: Еженедельный черновик с текстом: Подготовь проверяемый локальный черновик. Ничего не публикуй и не отправляй.");
  const [automationScheduleType, setAutomationScheduleType] = useState<"once" | "weekly">("weekly");
  const [automationTimezone, setAutomationTimezone] = useState(() => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const [automationRunAt, setAutomationRunAt] = useState(defaultAutomationRunAt);
  const [automationWeekday, setAutomationWeekday] = useState(0);
  const [automationTime, setAutomationTime] = useState("09:00");
  const [approvingStepId, setApprovingStepId] = useState<string | null>(null);
  const [updatingConnectorId, setUpdatingConnectorId] = useState<string | null>(null);
  const [extensionBusyId, setExtensionBusyId] = useState<string | null>(null);
  const [extensionKind, setExtensionKind] = useState<"skill" | "tool">("skill");
  const [extensionName, setExtensionName] = useState("meeting_summary");
  const [extensionVersion, setExtensionVersion] = useState("1.0.0");
  const [extensionDescription, setExtensionDescription] = useState("Повторяемая процедура подготовки сводки встречи.");
  const [extensionThreatModel, setExtensionThreatModel] = useState("Расширение не получает доступ к сети, файлам или секретам; результат проверяет пользователь.");
  const [extensionContent, setExtensionContent] = useState("# Meeting summary\n\nВыдели решения, ответственных и сроки. Не выполняй внешние действия.\n");
  const [extensionTests, setExtensionTests] = useState('[{"input":{"name":"Avelin"},"expected":{"message":"Hello Avelin"}}]');
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
  const [projectMembers, setProjectMembers] = useState<ProjectMemberItem[]>([]);
  const [projectInvitations, setProjectInvitations] = useState<ProjectInvitationItem[]>([]);
  const [sharedProjectItems, setSharedProjectItems] = useState<SharedProjectItem[]>([]);
  const [workspaceBusy, setWorkspaceBusy] = useState(false);
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
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [view, setView] = useState<WorkspaceView>("home");
  const [settingsView, setSettingsView] = useState<SettingsView>("general");
  const [chatSearch, setChatSearch] = useState("");
  const [isChatListOpen, setIsChatListOpen] = useState(false);
  const [isStartingChat, setIsStartingChat] = useState(false);
  const navigationRef = useRef<HTMLElement | null>(null);
  const workspaceRef = useRef<HTMLElement | null>(null);
  const menuButtonRef = useRef<HTMLButtonElement | null>(null);
  const followLatestMessageRef = useRef(true);

  function navigate(next: WorkspaceView) {
    setView(next);
    setIsSidebarOpen(false);
    if (next === "chat") setIsChatListOpen(true);
  }

  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 901px)");
    const closeOnDesktop = () => { if (desktop.matches) setIsSidebarOpen(false); };
    desktop.addEventListener("change", closeOnDesktop);
    return () => desktop.removeEventListener("change", closeOnDesktop);
  }, []);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [view]);

  useEffect(() => {
    if (!isSidebarOpen) return;
    const previous = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const workspace = workspaceRef.current;
    if (workspace) workspace.inert = true;
    const first = navigationRef.current?.querySelector<HTMLButtonElement>("button");
    first?.focus();
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") { setIsSidebarOpen(false); return; }
      if (event.key !== "Tab") return;
      const elements = Array.from(navigationRef.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input") ?? []).filter((item) => item.getClientRects().length);
      const first = elements[0], last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      if (workspace) workspace.inert = false;
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, [isSidebarOpen]);

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
    const container = messagesEndRef.current?.parentElement;
    if (container && followLatestMessageRef.current) container.scrollTop = container.scrollHeight;
  }, [messages, isSending, animatedMessageKey]);

  useEffect(() => {
    followLatestMessageRef.current = true;
    const container = messagesEndRef.current?.parentElement;
    if (container) container.scrollTop = container.scrollHeight;
  }, [view, activeThreadId]);

  useEffect(() => {
    const container = messagesEndRef.current?.parentElement;
    if (!container) return;
    const observer = new ResizeObserver(() => {
      if (followLatestMessageRef.current) container.scrollTop = container.scrollHeight;
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [view]);

  useEffect(() => {
    if (!authToken || !activeProjectId) {
      setProjectMembers([]);
      setProjectInvitations([]);
      setSharedProjectItems([]);
      return;
    }
    void Promise.all([
      fetchProjectMembers(authToken, activeProjectId),
      fetchProjectInvitations(authToken, activeProjectId).catch(() => []),
      fetchSharedProjectItems(authToken, activeProjectId)
    ]).then(([members, invitations, items]) => {
      setProjectMembers(members);
      setProjectInvitations(invitations);
      setSharedProjectItems(items);
    }).catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить shared workspace."));
  }, [authToken, activeProjectId, bootstrap.projects]);

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

  async function submitMessage(message: string, targetThreadId = activeThreadId) {
    const normalized = message.trim();
    if (!normalized || isSending || !authToken) {
      return;
    }

    const optimisticMessage: ChatMessage = { role: "user", content: normalized, status: "complete" };
    const pendingMessage: ChatMessage = { role: "assistant", content: "", status: "pending" };
    followLatestMessageRef.current = true;
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
        targetThreadId || null,
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
      if (window.matchMedia("(pointer: fine)").matches) textareaRef.current?.focus({ preventScroll: true });
    } catch (err) {
      const cancelled = err instanceof DOMException && err.name === "AbortError";
      const fallback = cancelled
        ? "Генерация остановлена."
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

  async function handleHomeSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = input.trim();
    if (!message || isSending || isStartingChat || !authToken) return;
    setIsStartingChat(true);
    try {
      const payload = await createChatThread(authToken, undefined, null);
      await refreshBootstrap(false, false, authToken, payload.thread.id);
      navigate("chat");
      setChatSearch("");
      setIsChatListOpen(false);
      await submitMessage(message, payload.thread.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось начать диалог.");
    } finally { setIsStartingChat(false); }
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
      navigate("chat");
      setChatSearch("");
      setIsChatListOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать чат.");
    }
  }

  async function handleSelectChat(threadId: string) {
    if (!authToken || isSending) return;
    navigate("chat");
    setIsChatListOpen(false);
    if (threadId === activeThreadId) return;
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
    navigate("projects");
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
        description: description.trim(),
        expected_revision: project?.revision
      });
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось обновить проект.");
    }
  }

  async function handleInviteMember() {
    if (!authToken || !activeProjectId || workspaceBusy) return;
    const email = window.prompt("Email участника workspace");
    if (!email?.trim()) return;
    const roleInput = window.prompt("Роль: editor или viewer", "editor")?.trim().toLowerCase();
    if (roleInput !== "editor" && roleInput !== "viewer") return;
    setWorkspaceBusy(true);
    try {
      const invitation = await createProjectInvitation(authToken, activeProjectId, email.trim(), roleInput);
      await navigator.clipboard?.writeText(invitation.token ?? "");
      window.prompt("Токен приглашения (скопирован в буфер, если браузер разрешил):", invitation.token ?? "");
      setProjectInvitations(await fetchProjectInvitations(authToken, activeProjectId));
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось пригласить участника.");
    } finally {
      setWorkspaceBusy(false);
    }
  }

  async function handleAcceptInvitation() {
    if (!authToken || workspaceBusy) return;
    const token = window.prompt("Вставь токен приглашения workspace");
    if (!token?.trim()) return;
    setWorkspaceBusy(true);
    try {
      const project = await acceptProjectInvitation(authToken, token.trim());
      setActiveProjectId(project.id);
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось принять приглашение.");
    } finally {
      setWorkspaceBusy(false);
    }
  }

  async function handleRemoveMember(member: ProjectMemberItem) {
    if (!authToken || !activeProjectId || workspaceBusy) return;
    if (!window.confirm(`Удалить ${member.display_name} из workspace? Личные данные участника сохранятся.`)) return;
    setWorkspaceBusy(true);
    try {
      await removeProjectMember(authToken, activeProjectId, member.user_id);
      setProjectMembers(await fetchProjectMembers(authToken, activeProjectId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить участника.");
    } finally {
      setWorkspaceBusy(false);
    }
  }

  async function handleAgentMode(mode: "mention_only" | "explicit" | "off") {
    if (!authToken || !activeProject || workspaceBusy) return;
    setWorkspaceBusy(true);
    try {
      await updateProject(authToken, activeProject.id, { agent_mode: mode, expected_revision: activeProject.revision });
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось изменить режим Avelin.");
    } finally {
      setWorkspaceBusy(false);
    }
  }

  async function handleCreateSharedItem() {
    if (!authToken || !activeProjectId || workspaceBusy) return;
    const kind = window.prompt("Тип: task, decision, artifact или memory", "task")?.trim() as SharedProjectItem["kind"] | undefined;
    if (!kind || !["task", "decision", "artifact", "memory"].includes(kind)) return;
    const title = window.prompt("Название");
    if (!title?.trim()) return;
    const text = window.prompt("Содержимое, которое будет явно доступно участникам", "") ?? "";
    setWorkspaceBusy(true);
    try {
      await createSharedProjectItem(authToken, activeProjectId, {
        kind, title: title.trim(), content: { text }, source_type: "explicit_ui_share"
      });
      setSharedProjectItems(await fetchSharedProjectItems(authToken, activeProjectId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось добавить shared item.");
    } finally {
      setWorkspaceBusy(false);
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

  async function handleTaskControl(taskId: string, action: "pause" | "resume" | "cancel") {
    if (!authToken || isExecutingStep) return;
    setIsExecutingStep(true);
    setError(null);
    try {
      await controlTask(taskId, action, authToken);
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось изменить состояние задачи.");
    } finally {
      setIsExecutingStep(false);
    }
  }

  async function handleCreateAutomation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!authToken || isSavingAutomation) return;
    setIsSavingAutomation(true);
    setError(null);
    try {
      await createAutomation({
        name: automationName,
        prompt: automationPrompt,
        schedule_type: automationScheduleType,
        timezone: automationTimezone,
        ...(automationScheduleType === "once"
          ? { run_at: automationRunAt }
          : { weekday: automationWeekday, time_of_day: automationTime }),
        budget: {
          max_steps: 20,
          max_failures: 3,
          max_seconds: 1800,
          max_tokens: 50000,
          max_cost_micros: 0
        },
        max_parallel_runs: 1
      }, authToken);
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать автоматизацию.");
    } finally {
      setIsSavingAutomation(false);
    }
  }

  async function handleAutomationControl(automationId: string, action: "pause" | "resume") {
    if (!authToken || isSavingAutomation) return;
    setIsSavingAutomation(true);
    setError(null);
    try {
      await controlAutomation(automationId, action, authToken);
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось изменить автоматизацию.");
    } finally {
      setIsSavingAutomation(false);
    }
  }

  async function handleDeleteAutomation(automationId: string) {
    if (!authToken || isSavingAutomation) return;
    if (!window.confirm("Удалить автоматизацию? Будущие запуски будут отменены.")) return;
    setIsSavingAutomation(true);
    setError(null);
    try {
      await deleteAutomation(automationId, authToken);
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить автоматизацию.");
    } finally {
      setIsSavingAutomation(false);
    }
  }

  async function handleCreateReport(runId?: string) {
    if (!authToken || isExecutingStep) return;
    setIsExecutingStep(true);
    setError(null);
    try {
      if (runId) await createRunReport(runId, authToken);
      else await createDailyTaskReport(authToken);
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сформировать отчёт.");
    } finally {
      setIsExecutingStep(false);
    }
  }

  async function handleReadNotification(notificationId: string) {
    if (!authToken) return;
    try {
      await markNotificationRead(notificationId, authToken);
      await refreshBootstrap(false, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отметить уведомление.");
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

  async function handleConnectorState(connectorId: string, enabled: boolean) {
    if (!authToken || updatingConnectorId) return;
    setUpdatingConnectorId(connectorId);
    setError(null);
    try {
      await setConnectorEnabled(connectorId, enabled, authToken);
      await refreshBootstrap(false, true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось изменить connector.");
    } finally {
      setUpdatingConnectorId(null);
    }
  }

  async function handleCreateExtension(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!authToken || extensionBusyId) return;
    setExtensionBusyId("create");
    setError(null);
    try {
      const tests = extensionKind === "tool" ? JSON.parse(extensionTests) : [];
      if (!Array.isArray(tests)) throw new Error("Contract tests должны быть JSON-массивом.");
      await createExtensionProposal({
        kind: extensionKind,
        name: extensionName.trim(),
        version: extensionVersion.trim(),
        description: extensionDescription.trim(),
        manifest: {
          name: extensionName.trim(),
          input_schema: { type: "object", additionalProperties: true },
          output_schema: {},
          capabilities: [extensionName.trim()],
          timeout_seconds: 5,
          idempotent: true
        },
        permissions: [],
        threat_model: extensionThreatModel.trim(),
        source_code: extensionKind === "tool" ? extensionContent : "",
        skill_markdown: extensionKind === "skill" ? extensionContent : "",
        tests
      }, authToken);
      await refreshBootstrap(false, true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось проверить расширение.");
    } finally {
      setExtensionBusyId(null);
    }
  }

  async function handleInstallExtension(proposalId: string, version: string) {
    if (!authToken || extensionBusyId) return;
    setExtensionBusyId(proposalId);
    setError(null);
    try {
      const result = await installExtension(proposalId, version, authToken);
      if (result.status === "waiting_approval") {
        setError("Установка подготовлена. Проверь разрешение в Security, разреши один раз и нажми «Установить» повторно.");
      }
      await refreshBootstrap(false, true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось установить расширение.");
    } finally {
      setExtensionBusyId(null);
    }
  }

  async function handleDisableExtension(name: string) {
    if (!authToken || extensionBusyId) return;
    setExtensionBusyId(name);
    try {
      await disableExtension(name, authToken);
      await refreshBootstrap(false, true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отключить расширение.");
    } finally {
      setExtensionBusyId(null);
    }
  }

  async function handleRollbackExtension(name: string, version: string) {
    if (!authToken || extensionBusyId) return;
    setExtensionBusyId(`${name}@${version}`);
    try {
      const result = await rollbackExtension(name, version, authToken);
      if (result.status === "waiting_approval") {
        setError("Откат ожидает одноразового разрешения в Security. После разрешения нажми «Откатить» повторно.");
      }
      await refreshBootstrap(false, true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось откатить расширение.");
    } finally {
      setExtensionBusyId(null);
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
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submitMessage(input);
    }
  }

  const selectedProviderOption = modelProviders.find((item) => item.provider === selectedProvider);
  const selectedProviderModels = selectedProviderOption?.models ?? [selectedModel];
  const activeProject = bootstrap.projects.find((project) => project.id === activeProjectId) ?? bootstrap.active_project;
  const isSharedViewer = Boolean(activeProject?.shared && activeProject.role === "viewer");

  if (authToken && !currentUser && isLoading) {
    return <main className="workspace-splash" role="status"><span className="nav-logo" aria-hidden="true">A</span><p>Открываем твоё пространство…</p></main>;
  }

  if (!authToken || !currentUser) {
    return (
      <main className="auth-page">
        <section className="auth-hero">
          <div className="brand-mark auth-logo">
            <img src={resolvedTheme === "dark" ? "/brand/avelin-dark.png" : "/brand/avelin-light.png"} alt="Avelin" />
          </div>
          <p className="eyebrow">Avelin</p>
          <h1>Пространство для твоих идей.</h1>
          <p className="hero-text">
            Обсуждай задачи, работай с документами и возвращайся к важному. Твои диалоги, проекты и память — в одном месте.
          </p>
          <div className="auth-features">
            <span>Диалоги</span>
            <span>Проекты</span>
            <span>Память</span>
          </div>
        </section>

        <section className="auth-card">
          <div className="auth-tabs">
            <button aria-pressed={authMode === "login"} className={authMode === "login" ? "theme-option theme-option-active" : "theme-option"} onClick={() => setAuthMode("login")} type="button">
              Вход
            </button>
            <button aria-pressed={authMode === "register"} className={authMode === "register" ? "theme-option theme-option-active" : "theme-option"} onClick={() => setAuthMode("register")} type="button">
              Регистрация
            </button>
          </div>

          <form className="auth-form" onSubmit={handleAuthSubmit}>
            {authMode === "register" ? (
              <>
                <label>
                  Имя
                  <input autoComplete="name" value={authDisplayName} onChange={(event) => setAuthDisplayName(event.target.value)} required minLength={2} />
                </label>
                <label>
                  Email
                  <input autoComplete="email" value={authEmail} onChange={(event) => setAuthEmail(event.target.value)} required type="email" />
                </label>
                <label>
                  Логин
                  <input autoComplete="username" autoCapitalize="none" value={authUsername} onChange={(event) => setAuthUsername(event.target.value)} required minLength={2} />
                </label>
              </>
            ) : (
              <label>
                Email или логин
                <input autoComplete="username" autoCapitalize="none" value={authLoginValue} onChange={(event) => setAuthLoginValue(event.target.value)} required minLength={2} />
              </label>
            )}
            <label>
              Пароль
              <input autoComplete={authMode === "register" ? "new-password" : "current-password"} value={authPassword} onChange={(event) => setAuthPassword(event.target.value)} required minLength={8} type="password" />
            </label>

            <button disabled={isAuthSubmitting} type="submit">
              {isAuthSubmitting ? "Подключаем..." : authMode === "login" ? "Войти" : "Создать аккаунт"}
            </button>
            {authError ? <p className="muted muted-error" role="alert">{authError}</p> : null}
          </form>

        </section>
      </main>
    );
  }

  const chatManagement = (
        <section className="panel chat-workspace-panel">

          <div className="thread-actions">
            <button className="mini-button" disabled={!activeThreadId || isSharedViewer} onClick={() => void handleRenameChat()} type="button">
              Назвать
            </button>
            <button className="mini-button" disabled={!activeThreadId || isSharedViewer} onClick={() => void handleClearChat()} type="button">
              Очистить
            </button>
            <button className="mini-button" disabled={!activeThreadId || isSharedViewer} onClick={() => void handleArchiveChat()} type="button">
              Архив
            </button>
            <button className="mini-button" disabled={!activeThreadId || isSharedViewer} onClick={() => void handleDeleteChat()} type="button">
              Удалить
            </button>
          </div>

          {activeProject?.shared ? (
            <div className="memory-rule-box">
              <div>
                <strong>Личная память изолирована</strong>
                <span>Общий чат не сохраняется в личную память. Факты передаются Avelin только через общую доску.</span>
              </div>
            </div>
          ) : (
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
          )}

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
  );
  const panels = {
    projects: (        <section className="panel project-panel">
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
                  <small>{project.shared ? `${project.role} · shared` : `${project.chat_count} чатов`}</small>
                </button>
              ))
            )}
          </div>

          <div className="thread-actions">
            <button className="mini-button" disabled={!activeProjectId || activeProject?.role === "viewer"} onClick={() => void handleRenameProject()} type="button">
              Назвать
            </button>
            <button className="mini-button" disabled={!activeProjectId || activeProject?.role !== "owner"} onClick={() => void handleArchiveProject()} type="button">
              Архив
            </button>
            <button className="mini-button" disabled={!activeProjectId || activeProject?.role !== "owner"} onClick={() => void handleDeleteProject()} type="button">
              Удалить
            </button>
            <button className="mini-button" disabled={!activeThreadId} onClick={() => void handleAssignActiveChatToProject(null)} type="button">
              Без проекта
            </button>
            <button className="mini-button" disabled={workspaceBusy} onClick={() => void handleAcceptInvitation()} type="button">
              Принять invite
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

          {activeProject ? (
            <details className="workspace-panel" open={activeProject.shared}>
              <summary>Shared workspace · {activeProject.role}</summary>
              <label className="project-select">
                Режим Avelin
                <select
                  value={activeProject.agent_mode}
                  disabled={activeProject.role === "viewer" || workspaceBusy}
                  onChange={(event) => void handleAgentMode(event.target.value as "mention_only" | "explicit" | "off")}
                >
                  <option value="mention_only">Только по @Avelin</option>
                  <option value="explicit">На каждый явный запрос</option>
                  <option value="off">Выключен</option>
                </select>
              </label>
              <div className="workspace-actions">
                {activeProject.role === "owner" ? (
                  <button className="mini-button" disabled={workspaceBusy} onClick={() => void handleInviteMember()} type="button">Пригласить</button>
                ) : null}
                {activeProject.role !== "viewer" ? (
                  <button className="mini-button" disabled={workspaceBusy} onClick={() => void handleCreateSharedItem()} type="button">Поделиться</button>
                ) : null}
              </div>
              <div className="workspace-members">
                {projectMembers.map((member) => (
                  <div className="workspace-row" key={member.user_id}>
                    <span>{member.display_name} · {member.role}</span>
                    {activeProject.role === "owner" && member.role !== "owner" ? (
                      <button className="mini-button mini-button-danger" disabled={workspaceBusy} onClick={() => void handleRemoveMember(member)} type="button">Удалить</button>
                    ) : null}
                  </div>
                ))}
              </div>
              {projectInvitations.filter((item) => item.status === "pending").map((invite) => (
                <small key={invite.id}>Invite: {invite.email} · {invite.role} · до {formatFileTime(invite.expires_at)}</small>
              ))}
              <div className="workspace-board">
                {sharedProjectItems.length === 0 ? <small>Общая доска пуста. Личная память и файлы сюда не попадают автоматически.</small> : sharedProjectItems.map((item) => (
                  <article className="workspace-board-item" key={item.id}>
                    <b>{item.kind} · {item.title}</b>
                    <span>{String(item.content.text ?? "")}</span>
                    <small>revision {item.revision}</small>
                  </article>
                ))}
              </div>
            </details>
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
        </section>),
    memory: (        <section className="panel">
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
              <select aria-label="Тип записи памяти" value={memoryKind} onChange={(event) => setMemoryKind(event.target.value as PersonalMemoryItem["kind"])}>
                <option value="fact">Факт</option>
                <option value="preference">Предпочтение</option>
                <option value="profile">Профиль</option>
                <option value="episode">Эпизод</option>
              </select>
              <select aria-label="Область памяти" value={memoryScope} onChange={(event) => setMemoryScope(event.target.value as PersonalMemoryItem["scope"])}>
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
        </section>),
    automations: (        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Automations</p>
              <h3>Расписания Avelin</h3>
            </div>
            <span className="muted">{bootstrap.automations.filter((item) => item.status === "active").length}/20 активных</span>
          </div>
          <form className="automation-form" onSubmit={handleCreateAutomation}>
            <label>
              Название
              <input value={automationName} maxLength={120} onChange={(event) => setAutomationName(event.target.value)} required />
            </label>
            <label className="automation-prompt-field">
              Безопасное задание
              <textarea value={automationPrompt} maxLength={4000} onChange={(event) => setAutomationPrompt(event.target.value)} required />
            </label>
            <label>
              Расписание
              <select value={automationScheduleType} onChange={(event) => setAutomationScheduleType(event.target.value as "once" | "weekly")}>
                <option value="once">Один раз</option>
                <option value="weekly">Раз в неделю</option>
              </select>
            </label>
            <label>
              Часовой пояс
              <input value={automationTimezone} onChange={(event) => setAutomationTimezone(event.target.value)} required />
            </label>
            {automationScheduleType === "once" ? (
              <label>
                Дата и время
                <input type="datetime-local" value={automationRunAt} onChange={(event) => setAutomationRunAt(event.target.value)} required />
              </label>
            ) : (
              <>
                <label>
                  День недели
                  <select value={automationWeekday} onChange={(event) => setAutomationWeekday(Number(event.target.value))}>
                    <option value={0}>Понедельник</option><option value={1}>Вторник</option>
                    <option value={2}>Среда</option><option value={3}>Четверг</option>
                    <option value={4}>Пятница</option><option value={5}>Суббота</option>
                    <option value={6}>Воскресенье</option>
                  </select>
                </label>
                <label>
                  Время
                  <input type="time" value={automationTime} onChange={(event) => setAutomationTime(event.target.value)} required />
                </label>
              </>
            )}
            <button className="ghost-button" disabled={isSavingAutomation} type="submit">
              {isSavingAutomation ? "Сохраняю..." : "Создать автоматизацию"}
            </button>
          </form>
          <p className="muted">Лимит запуска: 20 шагов · 50k токенов · 30 минут · параллелизм 1. Внешняя отправка всё равно потребует approval.</p>
          <div className="task-list">
            {bootstrap.automations.length === 0 ? <p className="muted">Автоматизаций пока нет.</p> : bootstrap.automations.map((automation) => (
              <article className="task-item" key={automation.id}>
                <div className="task-item-head">
                  <strong>{automation.name}</strong>
                  <span className={`state-badge state-${automation.status}`}>{automation.status}</span>
                </div>
                <p>{automation.human_rule}</p>
                <p className="muted">Следующий запуск: {automation.next_run_at ? formatFileTime(automation.next_run_at) : "—"}</p>
                <p className="muted">Последний результат: {automation.last_run?.state ?? "ещё не запускалась"} · steps {automation.last_run?.usage.steps ?? 0}/{automation.budget.max_steps ?? 0} · tokens {automation.last_run?.usage.tokens ?? 0}/{automation.budget.max_tokens ?? 0} · до {automation.budget.max_seconds ?? 0} сек.</p>
                <div className="task-step-row">
                  {automation.status === "active" ? <button className="mini-button" onClick={() => void handleAutomationControl(automation.id, "pause")} type="button">Пауза</button> : null}
                  {automation.status === "paused" ? <button className="mini-button" onClick={() => void handleAutomationControl(automation.id, "resume")} type="button">Продолжить</button> : null}
                  <button className="mini-button mini-button-danger" onClick={() => void handleDeleteAutomation(automation.id)} type="button">Удалить</button>
                </div>
              </article>
            ))}
          </div>
        </section>),
    tasks: (        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Execution</p>
              <h3>Очередь задач</h3>
            </div>
            <div className="memory-actions">
              <button className="ghost-button" onClick={() => void handleCreateReport()} disabled={isExecutingStep} type="button">Отчёт за день</button>
              <button className="ghost-button" onClick={() => void handleExecuteNextStep()} disabled={isExecutingStep} type="button">
                {isExecutingStep ? "Выполняю..." : "Выполнить шаг"}
              </button>
            </div>
          </div>
          <div className="task-list">
            {bootstrap.tasks.length === 0 ? (
              <p className="muted">Очередь задач пуста.</p>
            ) : (
              bootstrap.tasks.slice(0, 4).map((task) => (
                <article className="task-item" key={task.id}>
                  <div className="task-item-head">
                    <strong>{task.description}</strong>
                    <span className={`state-badge state-${bootstrap.task_runs.find((run) => run.task_id === task.id)?.state ?? task.status}`}>
                      {bootstrap.task_runs.find((run) => run.task_id === task.id)?.state ?? task.status}
                    </span>
                  </div>
                  <div className="task-step-row">
                    {(["queued", "running", "waiting_approval"].includes(bootstrap.task_runs.find((run) => run.task_id === task.id)?.state ?? "")) ? (
                      <button className="mini-button" onClick={() => void handleTaskControl(task.id, "pause")} type="button">Пауза</button>
                    ) : null}
                    {bootstrap.task_runs.find((run) => run.task_id === task.id)?.state === "paused" ? (
                      <button className="mini-button" onClick={() => void handleTaskControl(task.id, "resume")} type="button">Продолжить</button>
                    ) : null}
                    {!(["completed", "failed", "cancelled"].includes(bootstrap.task_runs.find((run) => run.task_id === task.id)?.state ?? "")) ? (
                      <button className="mini-button mini-button-danger" onClick={() => void handleTaskControl(task.id, "cancel")} type="button">Отменить</button>
                    ) : null}
                    {bootstrap.task_runs.find((run) => run.task_id === task.id) ? (
                      <button className="mini-button" onClick={() => void handleCreateReport(bootstrap.task_runs.find((run) => run.task_id === task.id)?.id)} type="button">Отчёт</button>
                    ) : null}
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
          {bootstrap.task_reports.length > 0 ? (
            <div className="task-list security-grants">
              <strong>Проверяемые отчёты</strong>
              {bootstrap.task_reports.slice(0, 4).map((report) => (
                <article className="task-item" key={report.id}>
                  <div className="task-item-head">
                    <strong>{report.kind === "daily" ? `Отчёт за ${report.report_date}` : "Отчёт запуска"}</strong>
                    <span className="state-badge state-completed">evidence</span>
                  </div>
                  <p>{report.payload.summary}</p>
                  <p className="muted">Проверок: {report.payload.checks?.length ?? 0} · Блокеров: {report.payload.blockers?.length ?? 0}</p>
                  {(report.payload.artifacts ?? []).map((artifact) => (
                    <button
                      className="artifact-link"
                      key={`${report.id}-${artifact.path}`}
                      onClick={() => void handleDownloadArtifact({ path: artifact.path, name: artifact.name ?? artifact.path.split("/").pop() ?? "artifact", extension: "", size_bytes: 0 })}
                      type="button"
                    >
                      Скачать {artifact.name ?? artifact.path}
                    </button>
                  ))}
                </article>
              ))}
            </div>
          ) : null}
        </section>),
    files: (        <section className="panel">
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
                <button className="artifact-card" key={file.path} title={file.path} type="button" disabled={downloadingArtifactPath === file.path} onClick={() => void handleDownloadArtifact(file)}>
                  <span className="artifact-icon" aria-hidden="true">{file.extension.replace(".", "").slice(0, 4).toUpperCase() || "FILE"}</span>
                  <span className="artifact-copy"><strong>{file.name}</strong><small>{formatFileSize(file.size_bytes)} · {formatFileTime(file.modified_at)}</small></span>
                  <span className="artifact-action">{downloadingArtifactPath === file.path ? "Скачивание…" : "Скачать ↓"}</span>
                </button>
              ))
            )}
          </div>
        </section>),
    settings: (<div className="settings-content">
      {settingsView === "general" ? <>        <section className="panel panel-compact">
          <div className="panel-heading panel-heading-compact">
            <h3>Оформление</h3>
            <div className="theme-switcher" aria-label="Режим темы">
              {(["auto", "light", "dark"] as const).map((mode) => (
                <button
                  className={themeMode === mode ? "theme-option theme-option-active" : "theme-option"}
                  aria-pressed={themeMode === mode}
                  key={mode}
                  onClick={() => setThemeMode(mode)}
                  type="button"
                >
                  {mode === "auto" ? "Авто" : mode === "light" ? "Светлая" : "Тёмная"}
                </button>
              ))}
            </div>
          </div>
          <p className="muted">Выбери тему пространства. В режиме «Авто» светлая тема действует с 07:00 до 21:00.</p>
        </section>
        <section className="panel panel-compact">
          <div className="panel-heading panel-heading-compact">
            <div>
              <p className="eyebrow">Модель</p>
              <h3>Модель для диалога</h3>
            </div>
            <button className="ghost-button" onClick={() => void handleSaveModelSettings()} disabled={isModelSaving} type="button">
              {isModelSaving ? "Сохраняю..." : "Сохранить"}
            </button>
          </div>
          <div className="model-controls">
            <label>
              Провайдер
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
        </section></> : null}
      {settingsView === "connections" ?         <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Connectors</p>
              <h3>Внешние подключения</h3>
            </div>
            <span className="muted">{bootstrap.connectors.filter((item) => item.connected).length}/{bootstrap.connectors.length} подключено</span>
          </div>
          <p className="muted">Внешние данные считаются недоверенными. Отправка и другие side effects всегда требуют отдельного approval.</p>
          <div className="task-list security-grants">
            {bootstrap.connectors.length === 0 ? (
              <p className="muted">Connectors ещё не настроены.</p>
            ) : bootstrap.connectors.map((connector) => (
              <article className="task-item" key={connector.id}>
                <div className="task-item-head">
                  <strong>{connector.name}</strong>
                  <span className={`state-badge state-${connector.connected ? "completed" : "blocked"}`}>{connector.status}</span>
                </div>
                <p>{connector.description}</p>
                <p className="muted">Scopes: {connector.scopes.join(", ") || "нет"}</p>
                <p className="muted">Capabilities: {connector.capabilities.join(", ") || "нет"}</p>
                <p className="muted">Tools: {connector.tools.join(", ") || "не обнаружены"}</p>
                <button
                  className={`mini-button ${connector.enabled ? "mini-button-danger" : ""}`}
                  disabled={updatingConnectorId === connector.id}
                  onClick={() => void handleConnectorState(connector.id, !connector.enabled)}
                  type="button"
                >
                  {updatingConnectorId === connector.id ? "Обновляю..." : connector.enabled ? "Отключить" : "Подключить"}
                </button>
              </article>
            ))}
          </div>
        </section> : null}
      {settingsView === "tools" ?         <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Tool Factory</p>
              <h3>Skills и локальные инструменты</h3>
            </div>
            <span className="muted">{bootstrap.extension_proposals.filter((item) => item.status === "installed").length} установлено</span>
          </div>
          <p className="muted">Сначала Avelin ищет готовый tool/connector/Skill. Новый код проходит статическую проверку и contract tests в изолированном процессе; установка требует отдельного approval.</p>
          <form className="automation-form" onSubmit={handleCreateExtension}>
            <label>
              Тип
              <select value={extensionKind} onChange={(event) => {
                const kind = event.target.value as "skill" | "tool";
                setExtensionKind(kind);
                setExtensionContent(kind === "skill" ? "# New Skill\n\nОпиши повторяемую процедуру без исполняемого кода.\n" : 'def run(arguments):\n    return {"message": "Hello " + arguments["name"]}\n');
              }}>
                <option value="skill">Skill без кода</option>
                <option value="tool">Sandboxed tool</option>
              </select>
            </label>
            <label>
              Имя
              <input value={extensionName} onChange={(event) => setExtensionName(event.target.value)} pattern="[a-z][a-z0-9_]+" required />
            </label>
            <label>
              Версия
              <input value={extensionVersion} onChange={(event) => setExtensionVersion(event.target.value)} pattern="\d+\.\d+\.\d+" required />
            </label>
            <label className="automation-prompt-field">
              Описание
              <input value={extensionDescription} onChange={(event) => setExtensionDescription(event.target.value)} required />
            </label>
            <label className="automation-prompt-field">
              Threat model
              <textarea value={extensionThreatModel} onChange={(event) => setExtensionThreatModel(event.target.value)} minLength={20} required />
            </label>
            <label className="automation-prompt-field">
              {extensionKind === "skill" ? "SKILL.md" : "Python: только def run(arguments), без imports/calls/attributes"}
              <textarea className="factory-source" value={extensionContent} onChange={(event) => setExtensionContent(event.target.value)} required />
            </label>
            {extensionKind === "tool" ? (
              <label className="automation-prompt-field">
                Contract tests (JSON)
                <textarea value={extensionTests} onChange={(event) => setExtensionTests(event.target.value)} required />
              </label>
            ) : null}
            <button className="primary-button" disabled={extensionBusyId === "create"} type="submit">
              {extensionBusyId === "create" ? "Проверяю…" : "Создать proposal и проверить"}
            </button>
          </form>
          <div className="task-list factory-list">
            {bootstrap.extension_proposals.length === 0 ? <p className="muted">Proposal пока нет.</p> : bootstrap.extension_proposals.map((proposal) => (
              <article className="task-item" key={proposal.id}>
                <div className="task-item-head">
                  <strong>{proposal.name} · {proposal.version}</strong>
                  <span className={`state-badge state-${proposal.status}`}>{proposal.kind} · {proposal.status}</span>
                </div>
                <p>{proposal.description}</p>
                <p className="muted">Permissions: {proposal.permissions.length ? JSON.stringify(proposal.permissions) : "нет"}</p>
                <div className="factory-checks">
                  {proposal.checks.map((check) => (
                    <small key={check.name} className={check.passed ? "factory-check-pass" : "factory-check-fail"}>
                      {check.passed ? "PASS" : "FAIL"} · {check.name}: {check.detail}
                    </small>
                  ))}
                </div>
                {proposal.diff ? <details><summary>Показать diff</summary><pre className="factory-diff">{proposal.diff}</pre></details> : null}
                <div className="memory-actions">
                  {proposal.status === "ready" ? (
                    <button className="mini-button" disabled={extensionBusyId === proposal.id} onClick={() => void handleInstallExtension(proposal.id, proposal.version)} type="button">Установить</button>
                  ) : null}
                  {proposal.status === "installed" ? (
                    <button className="mini-button mini-button-danger" disabled={extensionBusyId === proposal.name} onClick={() => void handleDisableExtension(proposal.name)} type="button">Отключить</button>
                  ) : null}
                  {["superseded", "disabled"].includes(proposal.status) ? (
                    <button className="mini-button" disabled={extensionBusyId === `${proposal.name}@${proposal.version}`} onClick={() => void handleRollbackExtension(proposal.name, proposal.version)} type="button">Откатить к этой версии</button>
                  ) : null}
                </div>
              </article>
            ))}
          </div>
        </section> : null}
      {settingsView === "security" ?         <section className="panel">
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
          {bootstrap.notifications.some((item) => !item.read_at) ? (
            <div className="task-list security-grants">
              <strong>Уведомления</strong>
              {bootstrap.notifications.filter((item) => !item.read_at).slice(0, 5).map((item) => (
                <article className="task-item" key={item.id}>
                  <div className="task-item-head">
                    <strong>{item.title}</strong>
                    <button className="mini-button" onClick={() => void handleReadNotification(item.id)} type="button">Прочитано</button>
                  </div>
                  <p className="muted">{item.body}</p>
                  <small>{formatFileTime(item.created_at)}</small>
                </article>
              ))}
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
        </section> : null}
      {settingsView === "activity" ?         <section className="panel">
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
        </section> : null}
    </div>),
  };

  return (
    <div className={`shell workspace-shell ${isSidebarOpen ? "sidebar-open" : ""}`}>
      <button
        className="sidebar-backdrop"
        tabIndex={-1}
        aria-label="Закрыть навигацию"
        onClick={() => setIsSidebarOpen(false)}
        type="button"
      />
      <aside id="workspace-navigation" className="sidebar workspace-navigation" aria-label="Навигация Avelin" ref={navigationRef}>
        <div className="nav-brand"><span className="nav-logo">A</span><h1>Avelin</h1><button className="icon-button nav-close" aria-label="Закрыть навигацию" onClick={() => setIsSidebarOpen(false)} type="button"><WorkspaceIcon name="close" /></button></div>
        <button className="nav-search" onClick={() => navigate("chat")} type="button"><WorkspaceIcon name="search" /><span>Найти диалог</span></button>
        <nav className="primary-nav" aria-label="Разделы">
          {WORKSPACE_NAV.map((item) => (
            <button key={item.id} className={view === item.id ? "nav-item is-active" : "nav-item"} aria-label={item.label} aria-current={view === item.id ? "page" : undefined} onClick={() => navigate(item.id)} type="button">
              <WorkspaceIcon name={item.icon} /><span>{item.label}</span>
              {item.id === "chat" && bootstrap.chat_threads.length > 0 ? <small>{bootstrap.chat_threads.length}</small> : null}
            </button>
          ))}
        </nav>
        <div className="nav-projects"><div className="nav-caption"><span>Рабочие области</span><button className="icon-button" aria-label="Новый проект" onClick={() => { navigate("projects"); void handleCreateProject(); }} type="button"><WorkspaceIcon name="plus" /></button></div>
          {bootstrap.projects.slice(0, 5).map((project) => <button key={project.id} className="nav-project" disabled={isSending} onClick={() => void handleFocusProject(project.id)} type="button" title={project.title}><i /><span>{project.title}</span></button>)}
          {bootstrap.projects.length === 0 ? <p className="nav-empty">Место для твоих идей</p> : null}
          {bootstrap.projects.length > 5 ? <button className="text-button" type="button" onClick={() => navigate("projects")}>Все проекты</button> : null}
        </div>
        <div className="nav-footer">
          <button className={view === "settings" ? "nav-item is-active" : "nav-item"} aria-current={view === "settings" ? "page" : undefined} onClick={() => navigate("settings")} type="button"><WorkspaceIcon name="settings" /><span>Настройки</span></button>
          <div className="nav-user"><button className="nav-profile" onClick={() => navigate("settings")} type="button"><span className="avatar">{currentUser.display_name.slice(0, 1).toUpperCase() || "A"}</span><span><b>{currentUser.display_name}</b><small>Личное пространство</small></span></button><button className="icon-button" aria-label={resolvedTheme === "dark" ? "Включить светлую тему" : "Включить тёмную тему"} onClick={() => setThemeMode(resolvedTheme === "dark" ? "light" : "dark")} type="button"><WorkspaceIcon name="sun" /></button></div>
        </div>
      </aside>

      <main className={`workspace workspace-view-${view}`} ref={workspaceRef}>
        <div className="workspace-toolbar"><button ref={menuButtonRef} className="icon-button mobile-menu-button" aria-label="Открыть навигацию" aria-expanded={isSidebarOpen} aria-controls="workspace-navigation" onClick={() => setIsSidebarOpen(true)} type="button"><WorkspaceIcon name="menu" /></button><span>{view === "home" ? "Личное пространство" : view === "settings" ? "Настройки" : WORKSPACE_NAV.find((item) => item.id === view)?.label}</span><button className="icon-button" aria-label="Уведомления и задачи" onClick={() => navigate("tasks")} type="button"><WorkspaceIcon name="bell" />{bootstrap.notifications.some((item) => !item.read_at) ? <i className="notification-dot" /> : null}</button></div>
        {error ? <div className="workspace-error" role="alert">{error}<button className="icon-button" aria-label="Закрыть сообщение об ошибке" onClick={() => setError(null)} type="button"><WorkspaceIcon name="close" /></button></div> : null}
        {isLoading ? <p className="workspace-loading" role="status">Загружаем твоё пространство…</p> : null}
        {view === "home" ? (
          <div className="workspace-home">
            <header className="home-heading"><p>{new Date().getHours() < 12 ? "Доброе утро" : new Date().getHours() < 18 ? "Добрый день" : "Добрый вечер"}, {currentUser.display_name.split(" ")[0] || currentUser.username}</p><h2>На чём продолжим?</h2></header>
            <form className="home-composer" onSubmit={handleHomeSubmit}>
              <textarea ref={textareaRef} aria-label="Новая задача" value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder="Попроси Avelin сделать что-нибудь…" rows={3} disabled={isStartingChat} />
              <div className="home-composer-bottom"><span>Новый диалог · {bootstrap.model}</span><button className="send-icon" aria-label="Начать диалог" type="submit" disabled={!input.trim() || isSending || isStartingChat || isLoading}><WorkspaceIcon name="arrow" /></button></div>
            </form>
            <div className="home-suggestions">{QUICK_ACTIONS.slice(2).map((action) => <button className="text-button" type="button" key={action} onClick={() => void handleQuickAction(action)}>{action}<WorkspaceIcon name="arrow" /></button>)}</div>
            <section className="home-recent" aria-labelledby="recent-heading">
              <div className="section-heading"><div><h3 id="recent-heading">Продолжить работу</h3><p>Твои последние диалоги</p></div><button className="text-button" type="button" onClick={() => navigate("chat")}>Все диалоги <WorkspaceIcon name="arrow" /></button></div>
              <div className="recent-grid">
                {bootstrap.chat_threads.slice(0, 3).map((thread) => <button className="recent-card" type="button" key={thread.id} disabled={isSending} onClick={() => void handleSelectChat(thread.id)}><div className="recent-card-top"><span className="card-icon"><WorkspaceIcon name={thread.project_id ? "projects" : "chat"} /></span><small>{bootstrap.projects.find((project) => project.id === thread.project_id)?.title ?? "Личное"}</small></div><h4>{thread.title}</h4><p>{thread.message_count} сообщений</p><footer><span>{formatFileTime(thread.updated_at)}</span><WorkspaceIcon name="arrow" /></footer></button>)}
                {bootstrap.chat_threads.length === 0 ? <div className="quiet-empty"><WorkspaceIcon name="chat" /><p>Здесь появятся твои диалоги.</p><span>Начни с первого запроса выше.</span></div> : null}
              </div>
            </section>
            <section className="home-activity"><div className="section-heading"><div><h3>Активность</h3><p>Последние действия Avelin</p></div><button className="text-button" type="button" onClick={() => { setSettingsView("activity"); navigate("settings"); }}>Журнал <WorkspaceIcon name="arrow" /></button></div>
              {bootstrap.action_logs.slice(0, 3).map((log) => <div className="activity-row" key={log.id}><span className="card-icon"><WorkspaceIcon name="tasks" /></span><div><strong>{({ create_docx: "Документ Word", write_file: "Сохранение файла", web_search: "Поиск в интернете", browser_read: "Чтение страницы", http_request: "Запрос к сайту" } as Record<string, string>)[log.tool_name] ?? log.tool_name}</strong><span>{log.status === "completed" ? "Завершено" : log.status === "failed" ? "Не удалось выполнить" : log.status === "blocked" ? "Ожидает разрешения" : log.status}</span></div><time>{formatFileTime(log.created_at)}</time></div>)}
              {bootstrap.action_logs.length === 0 ? <p className="activity-empty">Пока здесь тихо. Выполненные действия появятся автоматически.</p> : null}
            </section>
          </div>
        ) : null}
        {view === "chat" ? <div className={`chat-layout ${isChatListOpen ? "chat-list-open" : ""}`}>
          <button className="mobile-chat-toggle text-button" type="button" aria-expanded={isChatListOpen} onClick={() => setIsChatListOpen(!isChatListOpen)}>
            <WorkspaceIcon name="chat" />{isChatListOpen ? "Вернуться к сообщению" : "Все диалоги"}
          </button>
          <div className="chat-browser">
            <div className="section-heading">
              <h2>Диалоги</h2>
              <button className="icon-button" aria-label="Новый диалог" disabled={isSending || isSharedViewer} type="button" onClick={() => void handleCreateChat()}><WorkspaceIcon name="plus" /></button>
            </div>
            <label className="chat-search">
              <WorkspaceIcon name="search" />
              <input aria-label="Поиск диалогов" placeholder="Поиск диалогов" value={chatSearch} onChange={(event) => setChatSearch(event.target.value)} />
            </label>
            <div className="chat-browser-list">
              {bootstrap.chat_threads.filter((thread) => thread.title.toLocaleLowerCase().includes(chatSearch.toLocaleLowerCase())).map((thread) => (
                <button type="button" key={thread.id} disabled={isSending} aria-current={thread.id === activeThreadId ? "true" : undefined} className={thread.id === activeThreadId ? "chat-list-item is-active" : "chat-list-item"} onClick={() => void handleSelectChat(thread.id)} title={thread.title}>
                  <span>{thread.title}</span><small>{thread.message_count} сообщений</small>
                </button>
              ))}
              {!bootstrap.chat_threads.some((thread) => thread.title.toLocaleLowerCase().includes(chatSearch.toLocaleLowerCase())) ? (
                <p className="chat-search-empty" role="status">{chatSearch ? "Диалоги не найдены. Попробуй другое название." : "Здесь появятся твои диалоги."}</p>
              ) : null}
            </div>
            <details className="chat-management"><summary>Управление диалогом</summary>{chatManagement}</details>
          </div>
        <section className="chat-card">
          <div className="chat-header">
            <div>
              <strong title={bootstrap.active_thread.title || "Диалог"}>{bootstrap.active_thread.title || "Диалог"}</strong>
              <p className="muted">{messageCountLabel}</p>
            </div>
            <div className="header-tags">
              <span className="header-tag">{isSending ? "Avelin думает" : "Готов к диалогу"}</span>
              <span className="header-tag header-tag-muted">
                {draftSavedAt ? `Черновик: ${draftSavedAt}` : "Черновик пуст"}
              </span>
            </div>
          </div>

          <div className="messages" role="region" aria-label="Сообщения диалога" tabIndex={0} onScroll={(event) => {
            const element = event.currentTarget;
            followLatestMessageRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
          }}>
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
                  <span className="message-role">{formatRoleLabel(message, bootstrap.agent_name, Boolean(activeProject?.shared))}</span>
                  <div className="message-content">
                    {message.status === "pending" && !message.content ? (
                      <div className="typing-dots" role="status" aria-label="Avelin печатает"><span /><span /><span /></div>
                    ) : animatedMessageKey === messageKey ? (
                      <TypewriterText text={message.content} />
                    ) : message.role === "assistant" ? (
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
                          table: ({ node: _node, ...props }) => (
                            <div className="markdown-table" role="region" aria-label="Таблица в ответе" tabIndex={0}>
                              <table {...props} />
                            </div>
                          )
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
                            title={artifact.name}
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
              aria-label="Сообщение Avelin"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              placeholder={isSharedViewer ? "Viewer имеет доступ только для чтения" : activeProject?.shared && activeProject.agent_mode === "mention_only" ? "Упомяни @Avelin, чтобы агент ответил…" : "Спроси что-нибудь у Avelin..."}
              disabled={isSharedViewer}
              rows={2}
            />
            <div className="composer-row">
              <span className="muted">Enter — отправить · Shift + Enter — новая строка</span>
              <div className="composer-actions">
                <button
                  className="ghost-button"
                  onClick={() => setInput("")}
                  disabled={!input.trim() || isSending || isSharedViewer}
                  type="button"
                >
                  Очистить
                </button>
                {isSending ? (
                  <button className="stop-button" onClick={handleStopGeneration} type="button">Остановить</button>
                ) : (
                  <button disabled={!input.trim() || isSharedViewer} type="submit">Отправить</button>
                )}
              </div>
            </div>
          </form>
        </section></div> : null}
        {view !== "home" && view !== "chat" ? <div className="workspace-page"><header className="page-heading"><h2>{view === "settings" ? "Настройки" : WORKSPACE_NAV.find((item) => item.id === view)?.label}</h2></header>{view === "settings" ? <nav className="settings-tabs" aria-label="Разделы настроек">{SETTINGS_NAV.map((item) => <button type="button" key={item.id} aria-current={settingsView === item.id ? "page" : undefined} className={settingsView === item.id ? "is-active" : ""} onClick={() => setSettingsView(item.id)}>{item.label}</button>)}</nav> : null}{panels[view]}</div> : null}
      </main>
    </div>
  );
}
