import { useEffect, useRef, useState } from "react";
import { deleteAvatar, previewAvatar, saveAvatar } from "./api";
import { WorkspaceDialog } from "./WorkspaceDialog";

export function AvatarEditor({ token, avatarUrl, onChanged }: { token: string; avatarUrl: string; onChanged: () => void }) {
  const fileInput = useRef<HTMLInputElement | null>(null);
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const drag = useRef<{ x: number; y: number; initialX: number; initialY: number } | null>(null);
  const errorRef = useRef<HTMLParagraphElement | null>(null);
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [sourceUrl, setSourceUrl] = useState("");
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { if (error) { errorRef.current?.focus({ preventScroll: true }); errorRef.current?.scrollIntoView({ block: "nearest" }); } }, [error]);
  useEffect(() => () => { if (sourceUrl) URL.revokeObjectURL(sourceUrl); }, [sourceUrl]);
  useEffect(() => {
    if (!image || !canvas.current) return;
    const context = canvas.current.getContext("2d");
    if (!context) return;
    const scale = Math.max(256 / image.naturalWidth, 256 / image.naturalHeight) * zoom;
    const width = image.naturalWidth * scale, height = image.naturalHeight * scale;
    const x = Math.max(-(width - 256) / 2, Math.min((width - 256) / 2, offset.x));
    const y = Math.max(-(height - 256) / 2, Math.min((height - 256) / 2, offset.y));
    context.clearRect(0, 0, 256, 256);
    context.drawImage(image, (256 - width) / 2 + x, (256 - height) / 2 + y, width, height);
  }, [image, zoom, offset]);

  async function choose(file?: File) {
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) { setError("Выбери изображение до 10 МБ."); if (fileInput.current) fileInput.current.value = ""; return; }
    setBusy(true); setError("");
    try {
      const blob = await previewAvatar(file, token);
      const url = URL.createObjectURL(blob);
      const next = new Image(); next.src = url;
      try { await next.decode(); } catch { URL.revokeObjectURL(url); throw new Error("Браузер не смог открыть изображение."); }
      setSourceUrl(url); setImage(next); setZoom(1); setOffset({ x: 0, y: 0 });
    } catch (err) { setError(err instanceof Error ? err.message : "Не удалось открыть фото."); }
    finally { setBusy(false); if (fileInput.current) fileInput.current.value = ""; }
  }

  async function save() {
    if (!canvas.current || busy) return;
    setBusy(true); setError("");
    try {
      const blob = await new Promise<Blob | null>((resolve) => canvas.current!.toBlob(resolve, "image/png"));
      if (!blob) throw new Error("Не удалось подготовить фото.");
      await saveAvatar(blob, token);
      setImage(null); onChanged();
    } catch (err) { setError(err instanceof Error ? err.message : "Не удалось сохранить фото."); }
    finally { setBusy(false); }
  }

  async function remove() {
    setBusy(true); setError("");
    try { await deleteAvatar(token); onChanged(); }
    catch (err) { setError(err instanceof Error ? err.message : "Не удалось удалить фото."); }
    finally { setBusy(false); }
  }

  const errorMessage = error ? <p ref={errorRef} tabIndex={-1} role="alert" className="message-error">{error}</p> : null;
  return <><div className="avatar-controls"><input ref={fileInput} type="file" className="sr-only" tabIndex={-1} aria-label="Выбрать фото профиля" accept="image/*,.heic,.heif" onChange={(event) => void choose(event.target.files?.[0])} />{avatarUrl ? <img className="profile-avatar" src={avatarUrl} alt="Фото профиля" /> : null}<button className="ghost-button" type="button" disabled={busy} onClick={() => fileInput.current?.click()}>{busy ? "Подготавливаем…" : avatarUrl ? "Изменить фото" : "Добавить фото"}</button>{avatarUrl ? <button className="text-button" type="button" disabled={busy} onClick={() => void remove()}>Удалить фото</button> : null}</div>{!image ? errorMessage : null}<WorkspaceDialog open={Boolean(image)} title="Фото профиля" onClose={() => { if (!busy) setImage(null); }}><p className="muted">Перемести фото и выбери масштаб.</p>{image ? errorMessage : null}<canvas className="avatar-crop" aria-label="Область кадрирования фото" tabIndex={0} ref={canvas} width={256} height={256}
      onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); drag.current = { x: event.clientX, y: event.clientY, initialX: offset.x, initialY: offset.y }; }}
      onPointerMove={(event) => { if (drag.current) setOffset({ x: drag.current.initialX + event.clientX - drag.current.x, y: drag.current.initialY + event.clientY - drag.current.y }); }}
      onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}
      onKeyDown={(event) => { const steps: Record<string, { x: number; y: number }> = { ArrowLeft: { x: -10, y: 0 }, ArrowRight: { x: 10, y: 0 }, ArrowUp: { x: 0, y: -10 }, ArrowDown: { x: 0, y: 10 } }; const step = steps[event.key]; if (step) { event.preventDefault(); setOffset((current) => ({ x: current.x + step.x, y: current.y + step.y })); } }} />
      <label className="avatar-zoom">Масштаб<input aria-label="Масштаб фото" type="range" min="1" max="3" step="0.05" value={zoom} onChange={(event) => setZoom(Number(event.target.value))} /></label><div className="dialog-button-row"><button className="ghost-button" disabled={busy} type="button" onClick={() => setImage(null)}>Отмена</button><button disabled={busy} type="button" onClick={() => void save()}>{busy ? "Сохраняем…" : "Сохранить фото"}</button></div></WorkspaceDialog></>;
}
