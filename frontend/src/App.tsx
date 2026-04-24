import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { fetchBootstrap, sendMessage } from "./api";
import type { BootstrapPayload, ChatMessage } from "./types";

const EMPTY_BOOTSTRAP: BootstrapPayload = {
  agent_name: "Nova",
  provider: "mock",
  model: "mock-local",
  notes: [],
  history: []
};

const QUICK_ACTIONS = [
  "Сколько сейчас времени?",
  "Запомни купить кофе и молоко",
  "Что ты помнишь обо мне?",
  "Помоги спланировать мой день"
];

function formatRoleLabel(message: ChatMessage, agentName: string): string {
  if (message.role === "user") return "Ты";
  if (message.role === "assistant") return agentName;
  return message.name ?? "Tool";
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

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const messageCountLabel = useMemo(() => {
    if (isLoading) return "Загрузка истории...";
    if (messages.length === 0) return "Диалог пока пуст";
    return `Сообщений в истории: ${messages.length}`;
  }, [isLoading, messages.length]);

  useEffect(() => {
    const draft = window.localStorage.getItem("nova-draft");
    if (draft) {
      setInput(draft);
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem("nova-draft", input);
    if (input.trim()) {
      setDraftSavedAt(new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }));
    } else {
      setDraftSavedAt(null);
    }
  }, [input]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, isSending]);

  useEffect(() => {
    void refreshBootstrap(true);
  }, []);

  async function refreshBootstrap(initial = false) {
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

    try {
      await sendMessage(normalized);
      await refreshBootstrap();
      window.localStorage.removeItem("nova-draft");
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
          <div className="brand-mark">N</div>
          <div>
            <p className="eyebrow">Personal Agent</p>
            <h1>{bootstrap.agent_name}</h1>
          </div>
        </div>

        <section className="panel panel-compact">
          <p className="eyebrow">Runtime</p>
          <div className="status-grid">
            <div className="status-pill">
              <span className="status-dot" />
              <strong>{bootstrap.provider}</strong>
            </div>
            <div className="status-box">
              <span className="status-label">Model</span>
              <strong>{bootstrap.model}</strong>
            </div>
            <div className="status-box">
              <span className="status-label">Notes</span>
              <strong>{bootstrap.notes.length}</strong>
            </div>
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Memory</p>
              <h3>Что уже хранит агент</h3>
            </div>
            <button className="ghost-button" onClick={() => void handleRefresh()} type="button">
              {isRefreshing ? "Обновление..." : "Обновить"}
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
          <p className="eyebrow">Desktop Ready</p>
          <h3>Под Tauri позже</h3>
          <p className="muted">
            Интерфейс уже отделен от backend, так что десктопная упаковка не потребует
            переписывать чат с нуля.
          </p>
        </section>
      </aside>

      <main className="workspace">
        <header className="hero">
          <div className="hero-copy">
            <p className="eyebrow">Conversation</p>
            <h2>Личный агент в отдельном пространстве, а не в терминале</h2>
            <p className="hero-text">
              Здесь можно говорить с агентом, хранить заметки и постепенно превращать его в
              полноценное личное приложение.
            </p>
          </div>
          <div className="hero-orbit">
            <div className="hero-ring hero-ring-large" />
            <div className="hero-ring hero-ring-small" />
            <div className="hero-core">Live</div>
          </div>
        </header>

        <section className="quick-actions">
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
              <strong>Session</strong>
              <p className="muted">{messageCountLabel}</p>
            </div>
            <div className="header-tags">
              <span className="header-tag">{isSending ? "Агент думает" : "Готов к диалогу"}</span>
              <span className="header-tag header-tag-muted">
                {draftSavedAt ? `Черновик: ${draftSavedAt}` : "Черновик пуст"}
              </span>
            </div>
          </div>

          <div className="messages">
            {messages.length === 0 && !isLoading ? (
              <div className="empty-state">
                <p>Напиши первое сообщение агенту.</p>
                <span>Попробуй вопрос про время, память или планирование дня.</span>
              </div>
            ) : null}

            {messages.map((message, index) => (
              <article className={`message message-${message.role}`} key={`${message.role}-${index}`}>
                <span className="message-role">
                  {formatRoleLabel(message, bootstrap.agent_name)}
                </span>
                <p>{message.content}</p>
              </article>
            ))}

            {isSending ? (
              <article className="message message-assistant message-pending">
                <span className="message-role">{bootstrap.agent_name}</span>
                <div className="typing-dots" aria-label="agent is typing">
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
              placeholder="Спроси что-нибудь у агента..."
              rows={3}
            />
            <div className="composer-row">
              <span className={`muted ${error ? "muted-error" : ""}`}>
                {error ? error : "Enter отправляет сообщение, Shift+Enter делает перенос строки."}
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
