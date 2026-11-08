export function threadMediaUrl(threadId: string, messageId: string, path: string, preview = false): string {
  const query = new URLSearchParams({ message_id: messageId, path });
  return `/api/chats/${encodeURIComponent(threadId)}/media/${preview ? "preview" : "file"}?${query}`;
}
export async function fetchThreadMedia(url: string, token: string): Promise<Blob> {
  const parsed = new URL(url, location.origin);
  if (parsed.origin !== location.origin || !/^\/api\/chats\/[^/]+\/media\/(?:file|preview)$/.test(parsed.pathname)) throw new Error("Недопустимый адрес вложения.");
  const response = await fetch(parsed.pathname + parsed.search, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
  if (!response.ok) throw new Error(response.status === 404 ? "Файл больше недоступен в этом диалоге." : response.status === 403 ? "Нет доступа к вложению этого диалога." : "Не удалось загрузить вложение. Попробуй снова.");
  return response.blob();
}
