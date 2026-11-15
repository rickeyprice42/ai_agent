import { useEffect, useState } from "react";
import "./TaskRecoveryPanel.css";

type Recovery = { needs_attention: boolean; can_recover: boolean; token: string; reason: string;
  uncertain_actions: Array<{ id: string; tool?: string }> };
type Props = { token: string; runId: string; onRecovered: () => void };

export function TaskRecoveryPanel({ token, runId, onRecovered }: Props) {
  const [status, setStatus] = useState<Recovery | null>(null);
  const [note, setNote] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setStatus(null); setNote(""); setAcknowledged(false); setError("");
    async function load() {
      try {
        const response = await fetch(`/api/task-runs/${encodeURIComponent(runId)}/recovery`, {
          headers: { Authorization: `Bearer ${token}` }, signal: controller.signal });
        if (response.ok) {
          const next: Recovery = await response.json();
          if (!controller.signal.aborted) setStatus(next);
        }
      } catch { /* A transient refresh failure never authorizes recovery. */ }
    }
    void load();
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, 5000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [token, runId]);
  async function recover() {
    if (!status?.can_recover || !acknowledged) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(`/api/task-runs/${encodeURIComponent(runId)}/recover-stop`, {
        method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ token: status.token, note: note.trim(), acknowledge_unknown_effects: acknowledged }) });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || "Не удалось закрыть запуск. Обновите его состояние.");
      }
      setStatus(null); setAcknowledged(false); onRecovered();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Не удалось закрыть запуск."); }
    finally { setBusy(false); }
  }
  if (!status?.needs_attention) return null;
  return <div className="task-recovery" role="region" aria-label="Прерванный запуск">
    <p>{status.reason}</p>
    {status.can_recover ? <>
      <p>Неопределённых действий: {status.uncertain_actions.length}. Проверьте файлы и внешние действия в диалоге запуска. Закрытие сохранит результаты и не повторит действия.</p>
      <label>Что удалось проверить?
        <textarea value={note} onChange={event => setNote(event.target.value)} maxLength={2000} rows={3} disabled={busy} />
      </label>
      <label className="task-recovery-ack"><input type="checkbox" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} disabled={busy} /> Я понимаю, что выполненные действия не откатываются, а неизвестный результат останется отмечен в журнале.</label>
      <button type="button" disabled={busy || !acknowledged || note.trim().length < 10} onClick={() => void recover()}>{busy ? "Закрываем…" : "Закрыть прерванный запуск"}</button>
    </> : null}
    {error ? <p role="alert">{error}</p> : null}
  </div>;
}
