import { useEffect, useRef, useState } from "react";
import type { FileArtifact } from "./types";
import { WorkspaceDialog } from "./WorkspaceDialog";
import { WorkspaceIcon } from "./WorkspaceIcon";
import { fileSize, mediaBlob, panelJson } from "./panelApi";
import "./component-panels.css";

export type ThreadMediaFile = FileArtifact & { id: string; available: boolean; download_url: string; preview_url?: string | null; created_at?: string; message_id?: string };
export type ThreadMediaLink = { id: string; url: string; title?: string; created_at?: string; message_id?: string };
export type ThreadMediaPayload = { thread_id: string; images: ThreadMediaFile[]; files: ThreadMediaFile[]; links: ThreadMediaLink[]; next_before?: string | null };
type Props = { open: boolean; token: string; threadId: string | null; onClose: () => void; onError?: (message: string) => void };
const empty = (): ThreadMediaPayload => ({ thread_id: "", images: [], files: [], links: [], next_before: null });
function merge<T extends { id: string }>(old: T[], incoming: T[]): T[] { return Array.from(new Map([...old, ...incoming].map((item) => [item.id, item])).values()); }
function publicLink(value: string): URL | null { try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url : null; } catch { return null; } }

function MediaThumbnail({ file, token, threadId, onOpen }: { file: ThreadMediaFile; token: string; threadId: string; onOpen: () => void }) {
  const ref = useRef<HTMLButtonElement>(null);
  const [visible, setVisible] = useState(false);
  const [url, setUrl] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!ref.current || !file.available) return;
    if (!("IntersectionObserver" in window)) { setVisible(true); return; }
    const observer = new IntersectionObserver((entries) => { if (entries.some((entry) => entry.isIntersecting)) { setVisible(true); observer.disconnect(); } }, { rootMargin: "120px" });
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [file.id, file.available]);
  useEffect(() => {
    setUrl(""); setFailed(false);
    if (!visible || !file.available || !file.preview_url) return;
    const abort = new AbortController();
    let objectUrl = "";
    void mediaBlob(file.preview_url, threadId, token, abort.signal).then((blob) => {
      if (abort.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob); setUrl(objectUrl);
    }).catch(() => { if (!abort.signal.aborted) setFailed(true); });
    return () => { abort.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [visible, file.id, file.preview_url, file.available, threadId, token]);
  return <button ref={ref} className="thread-media-thumbnail" type="button" aria-label={`Открыть изображение ${file.name}`} disabled={!file.available} onClick={onOpen}>
    <span className="thread-media-image">{url && !failed ? <img src={url} alt={file.name} loading="lazy" onError={() => setFailed(true)} /> : <span><WorkspaceIcon name="files" /><small>{!file.available ? "Файл удалён" : failed ? "Нет предпросмотра" : "Изображение"}</small></span>}</span>
    <strong title={file.name}>{file.name}</strong><small>{fileSize(file.size_bytes)}</small>
  </button>;
}

function MediaPreview({ file, token, threadId, onBack, onDownload, busy }: { file: ThreadMediaFile; token: string; threadId: string; onBack: () => void; onDownload: () => void; busy: boolean }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const abort = new AbortController(); let objectUrl = "";
    setUrl(""); setError("");
    if (!file.preview_url) { setError("Предпросмотр недоступен. Можно скачать оригинал."); return; }
    void mediaBlob(file.preview_url, threadId, token, abort.signal).then((blob) => {
      if (abort.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob); setUrl(objectUrl);
    }).catch((reason) => { if (!abort.signal.aborted) setError(reason instanceof Error ? reason.message : "Не удалось показать изображение."); });
    return () => { abort.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [file.id, file.preview_url, threadId, token]);
  return <div className="thread-media-preview"><button type="button" className="text-button" onClick={onBack}>← К вложениям</button>
    {url && !error ? <img src={url} alt={file.name} onError={() => setError("Браузер не смог показать изображение. Можно скачать оригинал.")} /> : <p role="status">{error || "Открываю изображение…"}</p>}
    <button className="ghost-button" type="button" onClick={onDownload} disabled={busy || !file.available}>{busy ? "Скачивание…" : "Скачать оригинал"}</button>
  </div>;
}

export function ThreadMediaDrawer({ open, token, threadId, onClose, onError }: Props) {
  const [tab, setTab] = useState<"images" | "files" | "links">("images");
  const [data, setData] = useState<ThreadMediaPayload>(empty);
  const [dataScope, setDataScope] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<ThreadMediaFile | null>(null);
  const [downloading, setDownloading] = useState("");
  const [retry, setRetry] = useState(0);
  const abort = useRef<AbortController | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    generation.current += 1; const current = generation.current;
    setData(empty()); setDataScope(""); setError(""); setPreview(null); setDownloading(""); setTab("images");
    abort.current?.abort();
    if (!open || !threadId || !token) { setLoading(false); return; }
    const controller = new AbortController(); abort.current = controller;
    setLoading(true);
    void panelJson<ThreadMediaPayload>(`/api/chats/${encodeURIComponent(threadId)}/media`, token, controller.signal).then((value) => {
      if (controller.signal.aborted || generation.current !== current) return;
      if (value.thread_id !== threadId) throw new Error("Вложения этого диалога недоступны.");
      setData(value);
      setDataScope(`${token}\0${threadId}`);
    }).catch((reason) => { if (!controller.signal.aborted && generation.current === current) setError(reason instanceof Error ? reason.message : "Не удалось загрузить вложения."); })
      .finally(() => { if (!controller.signal.aborted && generation.current === current) setLoading(false); });
    return () => controller.abort();
  }, [open, threadId, token, retry]);
  async function loadMore() {
    if (!threadId || !data.next_before || loading) return;
    const current = generation.current;
    setLoading(true); setError("");
    try {
      const value = await panelJson<ThreadMediaPayload>(`/api/chats/${encodeURIComponent(threadId)}/media?before=${encodeURIComponent(data.next_before)}`, token, abort.current?.signal);
      if (generation.current !== current || abort.current?.signal.aborted) return;
      setData((old) => ({ ...value, images: merge(old.images, value.images), files: merge(old.files, value.files), links: merge(old.links, value.links) }));
    } catch (reason) { if (generation.current === current && !abort.current?.signal.aborted) setError(reason instanceof Error ? reason.message : "Не удалось загрузить вложения."); }
    finally { if (generation.current === current && !abort.current?.signal.aborted) setLoading(false); }
  }
  async function download(file: ThreadMediaFile) {
    if (!threadId || !file.available || downloading) return;
    const current = generation.current;
    setDownloading(file.id); setError("");
    try {
      const blob = await mediaBlob(file.download_url, threadId, token, abort.current?.signal);
      if (generation.current !== current || abort.current?.signal.aborted) return;
      const url = URL.createObjectURL(blob); const link = document.createElement("a");
      link.href = url; link.download = file.name; document.body.appendChild(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) { if (generation.current === current && !abort.current?.signal.aborted) { const message = reason instanceof Error ? reason.message : "Не удалось скачать файл."; setError(message); onError?.(message); } }
    finally { if (generation.current === current) setDownloading(""); }
  }
  const scoped=dataScope===`${token}\0${threadId}`;
  const visible=scoped ? data : empty();
  const links = visible.links.filter((item) => publicLink(item.url));
  return <WorkspaceDialog open={open} title={preview && scoped ? preview.name : "Вложения диалога"} onClose={onClose}>
    <div className="thread-media-panel" aria-busy={loading}>
      {preview && threadId && scoped ? <MediaPreview file={preview} token={token} threadId={threadId} onBack={() => setPreview(null)} onDownload={() => void download(preview)} busy={Boolean(downloading)} /> : <>
        <div className="panel-segments" role="tablist" aria-label="Тип вложений">{([['images','Изображения'],['files','Файлы'],['links','Ссылки']] as const).map(([id,label],index,all) => <button type="button" role="tab" tabIndex={tab===id ? 0 : -1} aria-selected={tab===id} aria-controls={`thread-media-${id}`} id={`thread-media-tab-${id}`} key={id} onClick={() => setTab(id)} onKeyDown={(event)=>{const next=event.key==="ArrowRight" ? all[(index+1)%all.length][0] : event.key==="ArrowLeft" ? all[(index+all.length-1)%all.length][0] : event.key==="Home" ? all[0][0] : event.key==="End" ? all[all.length-1][0] : null;if(next){event.preventDefault();setTab(next);document.getElementById(`thread-media-tab-${next}`)?.focus();}}}>{label}</button>)}</div>
        <div role="tabpanel" id={`thread-media-${tab}`} aria-labelledby={`thread-media-tab-${tab}`} className="thread-media-content">
          {tab === "images" ? <div className="thread-media-grid">{visible.images.map((file) => <MediaThumbnail key={file.id} file={file} token={token} threadId={threadId || ""} onOpen={() => setPreview(file)} />)}</div> : null}
          {tab === "files" ? <div className="thread-media-files">{visible.files.map((file) => <button className="thread-media-file" key={file.id} type="button" disabled={!file.available || Boolean(downloading)} onClick={() => void download(file)}><span className="panel-file-icon"><WorkspaceIcon name="files" /></span><span><strong>{file.name}</strong><small>{!file.available ? "Файл удалён" : downloading === file.id ? "Скачивание…" : `${file.extension.replace('.', '').toUpperCase()} · ${fileSize(file.size_bytes)}`}</small></span><WorkspaceIcon name="arrow" /></button>)}</div> : null}
          {tab === "links" ? <div className="thread-media-links">{links.map((item) => { const url = publicLink(item.url)!; return <a key={item.id} href={url.href} target="_blank" rel="noopener noreferrer"><span className="panel-file-icon"><WorkspaceIcon name="arrow" /></span><span><strong>{item.title || url.hostname}</strong><small>{url.hostname}</small></span><span aria-hidden="true">↗</span></a>; })}</div> : null}
          {!loading && (tab === "images" ? !visible.images.length : tab === "files" ? !visible.files.length : !links.length) ? <div className="panel-quiet-empty"><WorkspaceIcon name={tab === "links" ? "chat" : "files"} /><p>{tab === "images" ? "Пока нет изображений" : tab === "files" ? "Пока нет файлов" : "Пока нет ссылок"}</p><span>Они появятся здесь из сообщений этого диалога.</span></div> : null}
          {loading ? <p className="panel-loading" role="status">Загружаю вложения…</p> : null}
        </div>
        {visible.next_before ? <button className="ghost-button panel-load-more" type="button" disabled={loading} onClick={() => void loadMore()}>Показать ранние вложения</button> : null}
      </>}
      {error ? <div className="panel-inline-error" role="alert"><p>{error}</p>{!data.thread_id ? <button className="text-button" type="button" onClick={() => setRetry((value) => value+1)}>Повторить</button> : null}</div> : null}
    </div>
  </WorkspaceDialog>;
}
