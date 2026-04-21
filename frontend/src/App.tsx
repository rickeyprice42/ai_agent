import { FormEvent, useEffect, useState } from "react";
import { fetchBootstrap, sendMessage } from "./api";
import type { BootstrapPayload, ChatMessage } from "./types";

const EMPTY_BOOTSTRAP: BootstrapPayload = {
  agent_name: "Nova",
  provider: "mock",
  model: "mock-local",
  notes: [],
  history: []
};

export default function App() {
  const [bootstrap, setBootstrap] = useState<BootstrapPayload>(EMPTY_BOOTSTRAP);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    fetchBootstrap()
      .then((data) => {
        if (!active) return;
        setBootstrap(data);
        setMessages(data.history);
      })
      .catch((err: Error) => {
        if (!active) return;
        setError(err.message);
      })
      .finally(() => {
        if (!active) return;
        setIsLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = input.trim();
    if (!message || isSending) {
      return;
    }

    const optimisticMessage: ChatMessage = { role: "user", content: message };
    setMessages((current) => [...current, optimisticMessage]);
    setInput("");
    setIsSending(true);
    setError(null);

    try {
      const payload = await sendMessage(message);
      setMessages((current) => [...current, { role: "assistant", content: payload.reply }]);
    } catch (err) {
      const fallback = err instanceof Error ? err.message : "Неизвестная ошибка.";
      setError(fallback);
    } finally {
      setIsSending(false);
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

        <section className="panel">
          <p className="eyebrow">Model</p>
          <div className="stack">
            <span className="badge">{bootstrap.provider}</span>
            <span className="meta">{bootstrap.model}</span>
          </div>
        </section>

        <section className="panel">
          <p className="eyebrow">Memory</p>
          <div className="notes">
            {bootstrap.notes.length === 0 ? (
              <p className="muted">Пока без заметок.</p>
            ) : (
              bootstrap.notes.map((note, index) => (
                <article className="note" key={`${note}-${index}`}>
                  {note}
                </article>
              ))
            )}
          </div>
        </section>

        <section className="panel">
          <p className="eyebrow">Desktop Ready</p>
          <p className="muted">Структура уже подготовлена под будущую упаковку в Tauri.</p>
        </section>
      </aside>

      <main className="workspace">
        <header className="hero">
          <div>
            <p className="eyebrow">Conversation</p>
            <h2>Личный агент в отдельном окне, а не в терминале</h2>
          </div>
          <div className="hero-glow" />
        </header>

        <section className="chat-card">
          <div className="chat-header">
            <div>
              <strong>Session</strong>
              <p className="muted">
                {isLoading ? "Загрузка истории..." : `Сообщений в истории: ${messages.length}`}
              </p>
            </div>
          </div>

          <div className="messages">
            {messages.length === 0 && !isLoading ? (
              <div className="empty-state">
                <p>Напиши первое сообщение агенту.</p>
                <span>Например: “сколько сейчас времени?” или “запомни купить кофе”.</span>
              </div>
            ) : null}

            {messages.map((message, index) => (
              <article className={`message message-${message.role}`} key={`${message.role}-${index}`}>
                <span className="message-role">
                  {message.role === "user" ? "Ты" : message.role === "assistant" ? "Nova" : message.name ?? "Tool"}
                </span>
                <p>{message.content}</p>
              </article>
            ))}
          </div>

          <form className="composer" onSubmit={handleSubmit}>
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="Спроси что-нибудь у агента..."
              rows={3}
            />
            <div className="composer-row">
              <span className="muted">
                {error ? error : "UI отделен от backend и подходит для будущего Tauri-приложения."}
              </span>
              <button disabled={isSending || !input.trim()} type="submit">
                {isSending ? "Отправка..." : "Отправить"}
              </button>
            </div>
          </form>
        </section>
      </main>
    </div>
  );
}
