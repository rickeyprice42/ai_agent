import { useEffect, useRef, useState, type ReactNode } from "react";
import { fetchImagePreview } from "./api";
import type { FileArtifact } from "./types";

export function SentImage({ artifact, token, onOpen }: { artifact: FileArtifact; token: string; onOpen: (url: string, artifact: FileArtifact) => void }) {
  const [url, setUrl] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let objectUrl = "";
    setUrl(""); setFailed(false);
    void fetchImagePreview(artifact.path, token).then((blob) => {
      if (cancelled) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    }).catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [artifact.path, token]);
  return <button className="sent-image" type="button" disabled={!url || failed} aria-label={`Открыть изображение ${artifact.name}`} onClick={() => onOpen(url, artifact)}>
    {url && !failed ? <img src={url} alt={artifact.name} onError={() => setFailed(true)} /> : <span>{failed ? "Предпросмотр недоступен" : "Загрузка изображения…"}</span>}
    <small>{artifact.name}</small>
  </button>;
}

export function CopyableCode({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const code = useRef<HTMLPreElement | null>(null);
  async function copy() {
    try { await navigator.clipboard.writeText((code.current?.textContent || "").replace(/\n$/, "")); setCopied(true); window.setTimeout(() => setCopied(false), 1800); }
    catch { setCopied(false); }
  }
  return <div className="code-block"><header><span>Код</span><button type="button" onClick={() => void copy()}>{copied ? "Скопировано" : "Копировать"}</button></header><pre ref={code}>{children}</pre></div>;
}
