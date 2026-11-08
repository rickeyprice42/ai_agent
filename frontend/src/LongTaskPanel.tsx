import { useEffect, useState } from "react";
import "./LongTaskPanel.css";

type Plan = { id: string; title: string; goal: string; steps: string[]; acceptance_checks: string[]; status: string;
  revision: number; digest: string; budget: Record<string, number>; work_thread_id?: string;
  run?: { state: string; failure_reason?: string; usage: Record<string, number> }; task?: { steps: { id: string; description: string; status: string }[] };
  artifacts?: { name: string; path: string }[]; result?: string };
type Props = { token: string; threadId: string; onOpenChat: (id: string) => void; disabled?: boolean };
const labels: Record<string, string> = { draft: "План на согласование", approved: "План утверждён", queued: "В очереди", running: "В работе", waiting_approval: "Нужно разрешение", paused: "Приостановлено", completed: "Этапы выполнены", failed: "Нужна проверка", cancelled: "Отменено", rejected: "План отклонён" };

export function LongTaskPanel({ token, threadId, onOpenChat, disabled }: Props) {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const load = async (signal?: AbortSignal) => {
    const response = await fetch(`/api/chats/${encodeURIComponent(threadId)}/plans`, { headers: { Authorization: `Bearer ${token}` }, signal });
    if (response.status === 404) return; // Older backend: the global runtime notice explains the update.
    if (!response.ok) throw new Error("Не удалось получить планы задачи.");
    const result: Plan[] = await response.json();
    if (!signal?.aborted) setPlans(result);
  };
  useEffect(() => {
    const controller = new AbortController();
    setPlans([]); setError("");
    const refresh = () => { if (!document.hidden) void load(controller.signal).catch(() => {}); };
    refresh();
    const timer = window.setInterval(refresh, 5000);
    document.addEventListener("visibilitychange", refresh);
    return () => { controller.abort(); window.clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, [token, threadId]);
  async function act(plan: Plan, action: string) {
    setBusy(plan.id); setError("");
    try {
      const decision = action === "approved" || action === "rejected";
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
      const response = await fetch(`/api/long-tasks/${plan.id}/${decision ? "decision" : "control"}`, { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify(decision ? { decision: action, revision: plan.revision, digest: plan.digest, timezone } : { action }) });
      if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || "Не удалось изменить задачу."); }
      await load();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Не удалось изменить задачу."); }
    finally { setBusy(""); }
  }
  if (!plans.length) return null;
  return <section className="long-task-plans" aria-label="Планы и ход работы">
    {plans.map(plan => {
      const state = plan.run?.state || plan.status;
      const completed = plan.task?.steps.filter(step => step.status === "completed").length || 0;
      const total = plan.task?.steps.length || plan.steps.length;
      const locked = disabled || busy === plan.id;
      return <article className="long-task-card" key={plan.id}>
        <div className="long-task-heading"><span>{labels[state] || state}</span>{plan.run ? <span>{completed}/{total}</span> : <span>{total} этапов</span>}</div>
        <h3>{plan.title}</h3>
        {plan.run ? <progress max={total || 1} value={completed} aria-label="Выполненные этапы" /> : null}
        <details open={plan.status === "draft"}><summary>План и условия выполнения</summary>
          <p>{plan.goal}</p><ol>{(plan.task?.steps || plan.steps.map(description => ({ description, status: "pending", id: description }))).map((step, index) => <li key={index} data-state={step.status}>{step.description}{step.status === "completed" ? " ✓" : step.status === "running" ? " · выполняется" : ""}</li>)}</ol>
          <strong>Проверка результата</strong><ul>{plan.acceptance_checks.map((check, index) => <li key={index}>{check}</li>)}</ul>
          <p className="muted">До {plan.budget.max_steps} действий · до {Math.round(plan.budget.max_seconds / 60)} минут активной работы · до {plan.budget.max_tokens.toLocaleString("ru-RU")} оценочных токенов. Внешние действия требуют отдельных разрешений. Работа продолжается, пока сервер включён.</p>
        </details>
        {plan.run?.failure_reason ? <p className="long-task-notice">{plan.run.failure_reason}</p> : null}
        {state === "completed" ? <p className="long-task-notice">Этапы завершены по журналу работы. Проверьте итоговые файлы и критерии приёмки в диалоге.</p> : null}
        <div className="long-task-actions">
          {plan.status === "draft" ? <><button disabled={locked} onClick={() => void act(plan, "approved")} type="button">Утвердить и начать</button><button disabled={locked} onClick={() => void act(plan, "rejected")} type="button">Отклонить</button></> : null}
          {plan.work_thread_id ? <button type="button" onClick={() => onOpenChat(plan.work_thread_id!)}>Ход работы</button> : null}
          {["queued", "running"].includes(state) ? <button disabled={locked} onClick={() => void act(plan, "pause")} type="button">Пауза</button> : null}
          {state === "paused" ? <button disabled={locked} onClick={() => void act(plan, "resume")} type="button">Продолжить</button> : null}
          {["queued", "running", "waiting_approval", "paused"].includes(state) ? <button disabled={locked} onClick={() => void act(plan, "cancel")} type="button">Отменить</button> : null}
        </div>
      </article>;
    })}
    {error ? <p className="long-task-notice" role="alert">{error}</p> : null}
  </section>;
}

export default LongTaskPanel;
