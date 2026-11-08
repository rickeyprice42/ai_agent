import { parseError } from "./api";

export type PushTestStatus = { state: "not_found" | "pending" | "sending" | "accepted" | "failed"; reason?: string | null };

export async function pushApi(path: string, token: string, method = "GET", body?: unknown) {
  const response = await fetch(`/api/notifications/push/${path}`, {
    method, cache: "no-store", keepalive: method === "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) throw await parseError(response, "Не удалось настроить уведомления.");
  return response.status === 204 ? {} : response.json();
}

export function pushError(error: unknown): string {
  if (error instanceof TypeError || (error instanceof Error && error.name === "AbortError")) {
    return "Связь с Avelin прервалась. Проверь подключение к Tailscale и вернись в приложение.";
  }
  return error instanceof Error ? error.message : "Не удалось настроить уведомления.";
}

export function pushTestMessage(status: PushTestStatus): string {
  if (status.state === "accepted") return "Служба push приняла уведомление. Проверь экран уведомлений iPhone; это ещё не подтверждение показа на устройстве.";
  if (status.state === "failed") {
    if (status.reason?.startsWith("push_http_403") || status.reason?.startsWith("push_http_401")) {
      return "Служба push отклонила авторизацию сервера. Подключение iPhone сохранено; требуется проверить настройки доставки на сервере.";
    }
    if (status.reason?.startsWith("push_http_404") || status.reason?.startsWith("push_http_410")) return "Подписка устройства истекла. Отключи и снова включи уведомления.";
    return "Служба push не приняла уведомление. Повтори проверку позже; подключение устройства сохранено.";
  }
  if (status.state === "not_found") return "Сервер пока не подтвердил запрос проверки. Повтори проверку, когда приложение открыто и подключено к сети.";
  return "Тестовое уведомление поставлено в очередь доставки. Можно свернуть Avelin; статус проверится после возвращения.";
}
