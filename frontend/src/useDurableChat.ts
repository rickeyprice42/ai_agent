import { useEffect, useRef, useState } from "react";
import { cancelChatRun, ChatRunHttpError, fetchActiveChatRun, fetchChatRun, fetchChatRunByRequest, startChatApprovalRun, startChatQuestionRun, startChatRun, subscribeChatRun } from "./api";
import type { ChatMessage, ChatRunSnapshot } from "./types";

type Pending = { requestId: string; runId?: string; cursor: number; accepted?: boolean; cancelRequested?: boolean };
type RetryPayload = { requestId: string; threadId: string } & ({ kind: "message"; message: string; attachments: string[] } | { kind: "approval"; approvalId: string; decision: "approved" | "denied" } | { kind: "question"; questionId: string; answer: { option_id?: string; text?: string } });
type Scope = { userId: string; token: string; threadId: string; generation: number };
type Options = { userId: string; token: string; threadId: string; onAccepted: (run: ChatRunSnapshot) => void; onTerminal: (run: ChatRunSnapshot) => Promise<void> };
const active = (run: ChatRunSnapshot | null) => Boolean(run && ["queued", "running"].includes(run.state));
export function mergeChatRunMessages(history: ChatMessage[], run: ChatRunSnapshot | null): ChatMessage[] {
  if (!run) return history;
  const result = [...history];
  const pending = result.findIndex((item) => item.role === "assistant" && (item.run_id === run.run_id || item.client_request_id === run.request_id));
  const saved = run.message_id ? result.findIndex((item) => item.id === run.message_id) : -1;
  const status = active(run) ? "pending" : run.state === "cancelled" ? "cancelled" : ["failed", "partial", "interrupted"].includes(run.state) ? "failed" : "complete";
  const assistant: ChatMessage = { role: "assistant", content: run.text || "", status, run_id: run.run_id, client_request_id: run.request_id, artifacts: run.artifacts || [], ...(status === "failed" && run.error ? { error: run.error } : {}) };
  if (saved >= 0) {
    const canonical = result[saved];
    // A persisted final row is newer than a still-running snapshot. Pending
    // rows, however, must keep receiving deltas after a reconnect/reload.
    if (!canonical.status || canonical.status === "pending") {
      const text = canonical.content.length > assistant.content.length && canonical.content.startsWith(assistant.content) ? canonical.content : assistant.content;
      result[saved] = { ...canonical, ...assistant, id: canonical.id, content: text };
    }
    return result.filter((item, index) => index === saved || !(item.role === "assistant" && item.client_request_id === run.request_id));
  }
  if (pending >= 0) { result[pending] = assistant; return result; }
  let userIndex = -1;
  for (let index = result.length - 1; index >= 0; index -= 1) if (result[index].role === "user") { userIndex = index; break; }
  const hasUser = userIndex >= 0 && ((run.user_message_id && result[userIndex].id === run.user_message_id) || result[userIndex].content === run.user_text || result[userIndex].client_request_id === run.request_id);
  if (active(run)) {
    if (!hasUser && run.user_text) result.push({ role: "user", content: run.user_text, status: "complete", id: run.user_message_id || undefined, client_request_id: run.request_id });
    result.push(assistant);
  } else if (hasUser && !result.slice(userIndex + 1).some((item) => item.role === "assistant") && run.text) result.push(assistant);
  return result;
}
const key = (userId: string, threadId: string) => `avelin-chat-run:${encodeURIComponent(userId)}:${encodeURIComponent(threadId)}`;
function read(scope: Scope): Pending | null {
  try { const value = JSON.parse(localStorage.getItem(key(scope.userId, scope.threadId)) || "null"); return value && typeof value.requestId === "string" ? value : null; } catch { return null; }
}
function write(scope: Scope, value: Pending | null) {
  try { if (value) localStorage.setItem(key(scope.userId, scope.threadId), JSON.stringify(value)); else localStorage.removeItem(key(scope.userId, scope.threadId)); } catch { /* Server state remains authoritative if browser storage is unavailable. */ }
}
function payload(scope: Scope, value?: RetryPayload | null): RetryPayload | null {
  try {
    const name = `${key(scope.userId, scope.threadId)}:payload`;
    if (value === null) sessionStorage.removeItem(name);
    else if (value) sessionStorage.setItem(name, JSON.stringify(value));
    return JSON.parse(sessionStorage.getItem(name) || "null");
  } catch { return null; }
}
export function forgetChatRetryPayloads(userId: string) {
  try {
    const prefix = `avelin-chat-run:${encodeURIComponent(userId)}:`;
    for (let index = sessionStorage.length - 1; index >= 0; index -= 1) { const name = sessionStorage.key(index); if (name?.startsWith(prefix) && name.endsWith(":payload")) sessionStorage.removeItem(name); }
  } catch { /* Browser storage may be unavailable. */ }
}
export function newChatRequestId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  return [...bytes].map((value, index) => `${[4, 6, 8, 10].includes(index) ? "-" : ""}${value.toString(16).padStart(2, "0")}`).join("");
}

export function useDurableChat(options: Options) {
  const generation = useRef(0);
  const scope = useRef<Scope>({ ...options, generation: 0 });
  scope.current = { userId: options.userId, token: options.token, threadId: options.threadId, generation: generation.current };
  const callbacks = useRef(options); callbacks.current = options;
  const stream = useRef<AbortController | null>(null);
  const timer = useRef<number | null>(null);
  const syncing = useRef<string | null>(null);
  const latest = useRef<ChatRunSnapshot | null>(null);
  const retries = useRef(0);
  const requestVersion = useRef(0);
  const [view, setView] = useState<{ owner: string; run: ChatRunSnapshot | null; checking: boolean; starting: boolean; stopping: boolean; unconfirmed: boolean; notice: string; activities: string[] }>({ owner: "", run: null, checking: false, starting: false, stopping: false, unconfirmed: false, notice: "", activities: [] });
  const owner = (value: Scope) => `${value.userId}\0${value.token}\0${value.threadId}\0${value.generation}`;
  const current = (value: Scope) => owner(value) === owner(scope.current) && Boolean(value.userId && value.token && value.threadId);
  function change(value: Scope, update: Partial<typeof view> | ((before: typeof view) => Partial<typeof view>)) {
    if (!current(value)) return;
    setView((before) => ({ ...before, ...(typeof update === "function" ? update(before) : update), owner: owner(value) }));
  }
  function detach() { stream.current?.abort(); stream.current = null; if (timer.current !== null) window.clearTimeout(timer.current); timer.current = null; }
  function retry(value: Scope) {
    if (!current(value) || document.hidden) return;
    if (timer.current !== null) window.clearTimeout(timer.current);
    const delay = Math.min(10000, 1000 * 2 ** Math.min(retries.current++, 4));
    timer.current = window.setTimeout(() => { timer.current = null; void recover(value); }, delay);
  }

  async function install(value: Scope, snapshot: ChatRunSnapshot) {
    if (!current(value) || snapshot.thread_id !== value.threadId) return;
    if (latest.current?.run_id === snapshot.run_id && latest.current.latest_seq > snapshot.latest_seq) return;
    const previous = read(value);
    const ownUnaccepted = previous?.requestId === snapshot.request_id && !previous.accepted;
    const cancelIntent = previous?.requestId === snapshot.request_id && previous.cancelRequested;
    latest.current = snapshot;
    write(value, { requestId: snapshot.request_id, runId: snapshot.run_id, cursor: snapshot.latest_seq, accepted: true, cancelRequested: Boolean(cancelIntent && active(snapshot)) });
    if (ownUnaccepted) payload(value, null);
    change(value, { run: snapshot, checking: false, starting: false, stopping: Boolean(active(snapshot) && (cancelIntent || snapshot.cancel_requested)), unconfirmed: false,
      notice: active(snapshot) ? snapshot.cancel_requested || cancelIntent ? "Останавливаем ответ. Ждём подтверждение сервера…" : "" : "" });
    if (ownUnaccepted) callbacks.current.onAccepted(snapshot);
    if (!active(snapshot)) {
      detach();
      await callbacks.current.onTerminal(snapshot);
      return;
    }
    if (cancelIntent && !snapshot.cancel_requested) {
      const response = await cancelChatRun(snapshot.run_id, value.token);
      if (response && current(value)) {
        latest.current = response;
        change(value, { run: response, notice: response.cancel_requested ? "Останавливаем ответ. Ждём подтверждение сервера…" : "Ответ уже завершается. Обновляем результат…" });
        if (!active(response)) { await install(value, response); return; }
      }
    }
    if (!current(value) || document.hidden) return;
    stream.current?.abort();
    const controller = new AbortController(); stream.current = controller;
    const runId = snapshot.run_id;
    let cursor = snapshot.latest_seq;
    let completed = false;
    let persistedAt = 0;
    void subscribeChatRun(runId, cursor, value.token, controller.signal, (event) => {
      if (!current(value) || controller.signal.aborted || event.run_id !== runId || event.thread_id !== value.threadId || !event.seq || event.seq <= cursor) return;
      cursor = event.seq;
      const before = latest.current;
      if (!before || before.run_id !== runId) return;
      if (event.seq <= before.latest_seq) { cursor = Math.max(cursor, before.latest_seq); return; }
      const next = { ...before, latest_seq: cursor };
      if (event.type === "delta" && event.text) next.text += event.text;
      if (event.type === "approval" && event.approval) next.approvals = [...(next.approvals || []).filter((item) => item.id !== event.approval!.id), event.approval];
      if (event.type === "clarification" && event.question) next.questions = [...(next.questions || []).filter((item) => item.id !== event.question!.id), event.question];
      if (event.type === "error") next.error = event.text || "Провайдер не завершил ответ.";
      if (event.type === "done") completed = true;
      latest.current = next;
      // No reply text, attachment content or token is persisted here.
      const saved = read(value);
      if (saved?.runId === runId && (event.type === "done" || performance.now() - persistedAt > 250)) { write(value, { ...saved, cursor }); persistedAt = performance.now(); }
      change(value, (previousView) => ({ run: next, ...(event.type === "status" && event.text ? { activities: [...previousView.activities, event.text].slice(-12) } : {}) }));
    }).then(() => {
      if (!current(value) || controller.signal.aborted) return;
      if (completed) void recover(value);
      else { change(value, { notice: "Соединение прервалось. Частичный ответ сохранён; восстанавливаем подключение…" }); retry(value); }
    }).catch(() => {
      if (!current(value) || controller.signal.aborted) return;
      change(value, { notice: "Связь прервалась. Ответ продолжает выполняться на сервере; подключаемся снова…" });
      retry(value);
    });
  }

  async function recover(value = scope.current) {
    const version = requestVersion.current;
    const operation = `${owner(value)}:${version}`;
    if (!current(value) || document.hidden || syncing.current === operation) return;
    syncing.current = operation;
    stream.current?.abort();
    try {
      const saved = read(value);
      let snapshot: ChatRunSnapshot | null = null;
      try {
        snapshot = saved?.runId ? await fetchChatRun(saved.runId, value.token) : saved?.requestId ? await fetchChatRunByRequest(saved.requestId, value.token) : await fetchActiveChatRun(value.threadId, value.token);
      } catch (error) { if (!(error instanceof ChatRunHttpError && error.status === 404)) throw error; }
      if (!current(value) || requestVersion.current !== version) return;
      if (!snapshot && saved?.accepted) snapshot = await fetchActiveChatRun(value.threadId, value.token);
      if (snapshot && !active(snapshot) && saved?.accepted) {
        const newest = await fetchActiveChatRun(value.threadId, value.token);
        if (newest && newest.run_id !== snapshot.run_id) snapshot = newest;
      }
      if (!current(value) || requestVersion.current !== version) return;
      if (!snapshot) {
        latest.current = null;
        const unconfirmed = Boolean(saved && !saved.accepted);
        const retained = payload(value);
        const notice = saved?.cancelRequested ? "Запуск и отмена ещё не подтверждены. Запрос повторно не запускаем; состояние проверится после подключения." : retained ? "Сервер пока не нашёл запрос. Можно повторить отправку с тем же идентификатором." : "Запуск не подтверждён. Исходный запрос недоступен на устройстве; проверяем состояние сервера без повторной отправки. Можно открыть другой диалог.";
        change(value, { run: null, checking: false, starting: false, unconfirmed, notice: unconfirmed ? notice : "" });
        if (unconfirmed) retry(value);
        return;
      }
      retries.current = 0;
      await install(value, snapshot);
    } catch (error) {
      if (!current(value) || requestVersion.current !== version) return;
      // A denied/missing run never exposes another account's saved response.
      if (error instanceof ChatRunHttpError && [401, 403].includes(error.status)) {
        latest.current = null; change(value, { run: null, checking: false, starting: false, unconfirmed: false, notice: "Ответ недоступен этой сессии. Войди в аккаунт заново." });
      } else {
        change(value, { checking: false, notice: "Нет связи с сервером. Восстановим ответ после подключения." });
        retry(value);
      }
    } finally { if (syncing.current === operation) syncing.current = null; }
  }

  useEffect(() => {
    generation.current += 1;
    const value = { ...scope.current, generation: generation.current };
    scope.current = value;
    detach(); latest.current = null; retries.current = 0;
    setView({ owner: owner(value), run: null, checking: Boolean(value.userId && value.token && value.threadId), starting: false, stopping: false, unconfirmed: false, notice: "", activities: [] });
    void recover(value);
    function resumed() {
      if (!current(value)) return;
      if (document.hidden) { detach(); if (active(latest.current)) change(value, { notice: "Ответ выполняется на сервере." }); }
      else void recover(value);
    }
    window.addEventListener("online", resumed);
    document.addEventListener("visibilitychange", resumed);
    return () => { detach(); generation.current += 1; window.removeEventListener("online", resumed); document.removeEventListener("visibilitychange", resumed); };
  }, [options.userId, options.token, options.threadId]);

  async function begin(requestId: string, threadId: string, action: () => Promise<ChatRunSnapshot>, body: RetryPayload) {
    const account = { userId: scope.current.userId, token: scope.current.token };
    const target = { ...scope.current, threadId };
    const pending = read(target);
    if (pending && !pending.accepted && pending.requestId !== requestId) throw new Error("Предыдущая отправка ещё проверяется. Повторно запрос не запускаем.");
    requestVersion.current += 1;
    detach();
    write(target, { requestId, cursor: 0, accepted: false });
    payload(target, body);
    change(target, { starting: true, unconfirmed: false, notice: "", activities: [] });
    try {
      const response = await action();
      if (scope.current.userId !== account.userId || scope.current.token !== account.token) return;
      const fresh = { ...scope.current };
      if (fresh.threadId === threadId) await install(fresh, response);
      else { write(target, { requestId, runId: response.run_id, cursor: response.latest_seq, accepted: true }); payload(target, null); callbacks.current.onAccepted(response); }
    } catch (error) {
      if (scope.current.userId !== account.userId || scope.current.token !== account.token) return;
      if (error instanceof ChatRunHttpError) {
        if (pending?.requestId !== requestId) { write(target, null); payload(target, null); }
        change(target, { starting: false }); throw error;
      }
      if (scope.current.threadId === threadId) {
        change(scope.current, { starting: false, unconfirmed: true, notice: "Связь прервалась при запуске. Проверяем сохранённый запрос без повторной отправки…" });
        void recover(scope.current);
      }
    }
  }
  async function retryStart() {
    const value = { ...scope.current };
    const saved = read(value), body = payload(value);
    if (!saved || !body || body.requestId !== saved.requestId || saved.cancelRequested) return;
    change(value, { starting: true, notice: "Проверяем сохранённый запрос…" });
    try {
      const existing = await fetchChatRunByRequest(saved.requestId, value.token);
      if (!current(value)) return;
      if (existing) { await install(value, existing); return; }
      await begin(saved.requestId, value.threadId, () => body.kind === "message" ? startChatRun(body.message, value.threadId, body.attachments, saved.requestId, value.token) : body.kind === "approval" ? startChatApprovalRun(value.threadId, body.approvalId, body.decision, saved.requestId, value.token) : startChatQuestionRun(value.threadId, body.questionId, body.answer, saved.requestId, value.token), body);
    } catch (error) { change(value, { starting: false, unconfirmed: true, notice: error instanceof Error ? error.message : "Отправка не подтверждена. Повтори проверку позже." }); retry(value); }
  }
  async function cancel() {
    const value = { ...scope.current };
    const saved = read(value);
    if (!saved) return;
    requestVersion.current += 1;
    write(value, { ...saved, cancelRequested: true });
    change(value, { stopping: true, notice: "Останавливаем ответ. Ждём подтверждение сервера…" });
    try {
      const snapshot = latest.current || await fetchChatRunByRequest(saved.requestId, value.token);
      if (!snapshot || !current(value)) { retry(value); return; }
      const response = await cancelChatRun(snapshot.run_id, value.token);
      if (response) {
        if (!response.cancel_requested) write(value, { ...saved, runId: response.run_id, accepted: true, cancelRequested: false });
        await install(value, response);
        if (active(response) && !response.cancel_requested) change(value, { stopping: true, notice: "Ответ уже завершается. Обновляем результат…" });
      }
    } catch { if (current(value)) { change(value, { notice: "Связь прервалась. Остановка пока не подтверждена; уточняем состояние…" }); retry(value); } }
  }
  const visible = view.owner === owner(scope.current) ? view : { ...view, run: null, checking: true, starting: false, unconfirmed: false, notice: "", activities: [] };
  const retained = visible.unconfirmed ? payload(scope.current) : null;
  const pending = visible.unconfirmed ? read(scope.current) : null;
  return { ...visible, busy: visible.starting || active(visible.run), canRetry: Boolean(retained && pending && retained.requestId === pending.requestId && !pending.cancelRequested), pendingRequestId: pending?.requestId, pendingUserText: retained?.kind === "message" ? retained.message : undefined, retryStart,
    start: (message: string, threadId: string, attachments: string[], requestId: string) => begin(requestId, threadId, () => startChatRun(message, threadId, attachments, requestId, scope.current.token), { kind: "message", message, threadId, attachments, requestId }),
    approve: (threadId: string, approvalId: string, decision: "approved" | "denied", requestId: string) => begin(requestId, threadId, () => startChatApprovalRun(threadId, approvalId, decision, requestId, scope.current.token), { kind: "approval", threadId, approvalId, decision, requestId }), cancel, recover,
    answer: (threadId: string, questionId: string, answer: { option_id?: string; text?: string }, requestId: string) => begin(requestId, threadId, () => startChatQuestionRun(threadId, questionId, answer, requestId, scope.current.token), { kind: "question", threadId, questionId, answer, requestId }),
    disconnect: () => { generation.current += 1; scope.current = { ...scope.current, generation: generation.current }; detach(); latest.current = null; } };
}
