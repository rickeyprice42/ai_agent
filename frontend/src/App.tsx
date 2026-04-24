import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { fetchBootstrap, sendMessage } from "./api";
import type { BootstrapPayload, ChatMessage } from "./types";

const DRAFT_KEY = "avelin-draft";
const THEME_KEY = "avelin-theme-mode";

type ThemeMode = "auto" | "light" | "dark";
type ResolvedTheme = "light" | "dark";

const EMPTY_BOOTSTRAP: BootstrapPayload = {
  agent_name: "Avelin",
  provider: "mock",
  model: "mock-local",
  notes: [],
  history: []
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
  const [error, setError] = useState<string | null>(null);
  const [draftSavedAt, setDraftSavedAt] = useState<string | null>(null);
  const [animatedMessageKey, setAnimatedMessageKey] = useState<string | null>(null);
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
    void refreshBootstrap(true);
  }, []);

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

  async function refreshBootstrap(initial = false, animateLatestAssistant = false) {
    if (initial) {
      setIsLoading(true);
    } else {
      setIsRefreshing(true);
    }

    try {
      const data = await fetchBootstrap();
      setBootstrap(data);
      setMessages(data.history);
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
    if (!normalized || isSending) {
      return;
    }

    const optimisticMessage: ChatMessage = { role: "user", content: normalized };
    setMessages((current) => [...current, optimisticMessage]);
    setInput("");
    setIsSending(true);
    setError(null);
    setAnimatedMessageKey(null);

    try {
      await sendMessage(normalized);
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

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitMessage(input);
    }
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
