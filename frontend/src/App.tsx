import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  approveBlockedStep,
  executeNextStep,
  fetchBootstrap,
  fetchMe,
  fetchModelProviders,
  login as authLogin,
  logout as authLogout,
  openWorkspaceFolder,
  register as authRegister,
  sendMessage,
  updateModelSettings
} from "./api";
import type { BootstrapPayload, ChatMessage, ModelProviderOption, UserProfile } from "./types";

const DRAFT_KEY = "avelin-draft";
const THEME_KEY = "avelin-theme-mode";
const AUTH_TOKEN_KEY = "avelin-auth-token";

type ThemeMode = "auto" | "light" | "dark";
type ResolvedTheme = "light" | "dark";

const EMPTY_BOOTSTRAP: BootstrapPayload = {
  agent_name: "Avelin",
  provider: "mock",
  model: "mock-local",
  notes: [],
  history: [],
  tasks: [],
  action_logs: [],
  workspace_files: [],
  user: {
    id: "",
    email: "",
    username: "",
    display_name: "",
    auth_provider: "password"
  }
};

const QUICK_ACTIONS = [
  "Сколько сейчас времени?",
  "Запомни: купить кофе и молоко",
  "Что ты помнишь обо мне?",
  "Помоги спланировать мой день"
];

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
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isExecutingStep, setIsExecutingStep] = useState(false);
  const [approvingStepId, setApprovingStepId] = useState<string | null>(null);
  const [isOpeningWorkspace, setIsOpeningWorkspace] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draftSavedAt, setDraftSavedAt] = useState<string | null>(null);
  const [animatedMessageKey, setAnimatedMessageKey] = useState<string | null>(null);
  const [authToken, setAuthToken] = useState(() => window.localStorage.getItem(AUTH_TOKEN_KEY) ?? "");
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
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => readStoredThemeMode());
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>(() => resolveTheme(readStoredThemeMode()));

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

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
        return refreshBootstrap(true, false, authToken);
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

  async function refreshBootstrap(initial = false, animateLatestAssistant = false, token = authToken) {
    if (!token) {
      return;
    }

    if (initial) {
      setIsLoading(true);
    } else {
      setIsRefreshing(true);
    }

    try {
      const data = await fetchBootstrap(token);
      setBootstrap(data);
      setMessages(data.history);
      setCurrentUser(data.user);
      setSelectedProvider(data.provider);
      setSelectedModel(data.model);
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

    const optimisticMessage: ChatMessage = { role: "user", content: normalized };
    setMessages((current) => [...current, optimisticMessage]);
    setInput("");
    setIsSending(true);
    setError(null);
    setAnimatedMessageKey(null);

    try {
      await sendMessage(normalized, authToken);
      await refreshBootstrap(false, true);
      window.localStorage.removeItem(DRAFT_KEY);
      textareaRef.current?.focus();
    } catch (err) {
      const fallback = err instanceof Error ? err.message : "Неизвестная ошибка.";
      setError(fallback);
      setMessages((current) => current.slice(0, -1));
      setInput(normalized);
    } finally {
      setIsSending(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await submitMessage(input);
  }

  async function handleQuickAction(text: string) {
    setInput(text);
    textareaRef.current?.focus();
  }

  async function handleRefresh() {
    setAnimatedMessageKey(null);
    await refreshBootstrap();
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

          <div className="notes">
            {bootstrap.notes.length === 0 ? (
              <p className="muted">Пока без заметок.</p>
            ) : (
              bootstrap.notes.map((note, index) => (
                <article className="note" key={`${note}-${index}`}>
                  <span className="note-index">{String(index + 1).padStart(2, "0")}</span>
                  <p>{note}</p>
                </article>
              ))
            )}
          </div>
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
              <strong>Сессия</strong>
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
                <article className={`message message-${message.role}`} key={messageKey}>
                  <span className="message-role">{formatRoleLabel(message, bootstrap.agent_name)}</span>
                  <p>
                    {animatedMessageKey === messageKey ? (
                      <TypewriterText text={message.content} />
                    ) : (
                      message.content
                    )}
                  </p>
                </article>
              );
            })}

            {isSending ? (
              <article className="message message-assistant message-pending">
                <span className="message-role">{bootstrap.agent_name}</span>
                <div className="typing-dots" aria-label="Avelin печатает">
                  <span />
                  <span />
                  <span />
                </div>
              </article>
            ) : null}

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
                <button disabled={isSending || !input.trim()} type="submit">
                  {isSending ? "Отправка..." : "Отправить"}
                </button>
              </div>
            </div>
          </form>
        </section>
      </main>
    </div>
  );
}
