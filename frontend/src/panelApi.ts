import { parseError } from "./api";

export async function panelJson<T>(url: string, token: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { headers: { Authorization: `Bearer ${token}` }, signal, cache: "no-store" });
  if (!response.ok) throw await parseError(response, "Не удалось загрузить данные. Попробуй ещё раз.");
  return response.json() as Promise<T>;
}

export function authorizedMediaUrl(value: string, threadId: string): string {
  const url = new URL(value, location.origin);
  const prefix = `/api/chats/${encodeURIComponent(threadId)}/media/`;
  if (url.origin !== location.origin || ![`${prefix}file`, `${prefix}preview`].includes(url.pathname)) {
    throw new Error("Ссылка на файл недоступна в этом диалоге.");
  }
  return url.pathname + url.search;
}

export async function mediaBlob(url: string, threadId: string, token: string, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(authorizedMediaUrl(url, threadId), { headers: { Authorization: `Bearer ${token}` }, signal });
  if (!response.ok) throw await parseError(response, "Не удалось открыть файл. Возможно, он был удалён.");
  return response.blob();
}

export function panelTime(value: string, timezone?: string): string {
  if (!value) return "";
  const normalized = value.replace(" ", "T");
  const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(normalized) ? normalized : normalized + "Z");
  if (!Number.isFinite(date.getTime())) return "";
  try { return new Intl.DateTimeFormat("ru", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: timezone }).format(date); }
  catch { return new Intl.DateTimeFormat("ru", { day: "numeric", month: "short" }).format(date); }
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
  return `${(bytes / 1024 / 1024).toLocaleString("ru", { maximumFractionDigits: 1 })} МБ`;
}
