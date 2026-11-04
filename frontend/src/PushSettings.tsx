import { useEffect, useState } from "react";

type PushConfig = { enabled: boolean; public_key?: string | null; reason?: string };
async function api(path: string, token: string, method = "GET", body?: unknown) {
  const response = await fetch(`/api/notifications/push/${path}`, { method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || "Не удалось настроить уведомления."); }
  return response.status === 204 ? {} : response.json();
}

export async function detachPush(token: string): Promise<void> {
  if (!("serviceWorker" in navigator)) return;
  const registration = await navigator.serviceWorker.getRegistration("/");
  const subscription = await registration?.pushManager.getSubscription();
  if (subscription) {
    try { await api("subscription", token, "DELETE", { endpoint: subscription.endpoint }); }
    finally { await subscription.unsubscribe(); }
  }
}

export function PushSettings({ token }: { token: string }) {
  const [config, setConfig] = useState<PushConfig | null>(null);
  const [subscribed, setSubscribed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [permission, setPermission] = useState(() => typeof Notification === "undefined" ? "unsupported" : Notification.permission);
  const supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window && window.isSecureContext;
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const standalone = window.matchMedia("(display-mode: standalone)").matches || Boolean((navigator as Navigator & { standalone?: boolean }).standalone);
  useEffect(() => {
    let active = true;
    void api("config", token).then((value) => { if (active) setConfig(value); }).catch((err) => { if (active) setMessage(err.message); });
    if (supported) void navigator.serviceWorker.getRegistration("/").then((registration) => registration?.pushManager.getSubscription()).then((subscription) => { if (active) setSubscribed(Boolean(subscription)); }).catch(() => {});
    return () => { active = false; };
  }, [token, supported]);

  async function enable() {
    if (!config?.public_key) return;
    setBusy(true); setMessage("");
    try {
      const allowed = await Notification.requestPermission(); setPermission(allowed);
      if (allowed !== "granted") { setMessage("Уведомления не разрешены. Изменить это можно в настройках браузера."); return; }
      await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      const registration = await navigator.serviceWorker.ready;
      const padded = config.public_key.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(config.public_key.length / 4) * 4, "=");
      const key = Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
      const subscription = await registration.pushManager.getSubscription() || await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
      await api("subscription", token, "PUT", subscription.toJSON());
      setSubscribed(true); setMessage("Уведомления включены на этом устройстве.");
    } catch (err) { setMessage(err instanceof Error ? err.message : "Не удалось включить уведомления."); }
    finally { setBusy(false); }
  }

  async function disable() {
    setBusy(true); setMessage("");
    try { await detachPush(token); setSubscribed(false); setMessage("Уведомления на этом устройстве отключены."); }
    catch (err) { setMessage(err instanceof Error ? err.message : "Не удалось отключить уведомления."); }
    finally { setBusy(false); }
  }

  async function test() {
    setBusy(true);
    try { const result = await api("test", token, "POST"); setMessage(result.queued > 0 ? "Тестовое уведомление поставлено в очередь доставки." : "Нет подключённых устройств для доставки. Включи уведомления ещё раз."); }
    catch (err) { setMessage(err instanceof Error ? err.message : "Не удалось отправить тест."); }
    finally { setBusy(false); }
  }

  return <section className="panel panel-compact push-settings"><div className="panel-heading panel-heading-compact"><h3>Уведомления</h3><span className="muted">{subscribed ? "Включены" : "Выключены"}</span></div><p className="muted">Результаты автоматизаций и важные события на этом устройстве.</p>{ios && !standalone ? <p className="muted">На iPhone открой меню «Поделиться» в Safari → «На экран Домой». Запусти Avelin с этого значка, затем включи уведомления здесь.</p> : !supported ? <p className="muted">Этот браузер пока не поддерживает push-уведомления. События остаются в разделе задач.</p> : null}{config && !config.enabled ? <p className="muted">{config.reason || "Доставка уведомлений на сервере пока не настроена."}</p> : null}{permission === "denied" ? <p className="muted">Разрешение заблокировано в настройках браузера.</p> : null}<div className="dialog-button-row">{subscribed ? <><button className="ghost-button" type="button" disabled={busy} onClick={() => void disable()}>Отключить уведомления</button><button className="ghost-button" type="button" disabled={busy || !config?.enabled} onClick={() => void test()}>Проверить уведомление</button></> : <button className="ghost-button" type="button" disabled={busy || !supported || !config?.enabled || (ios && !standalone) || permission === "denied"} onClick={() => void enable()}>{busy ? "Подключаем…" : "Включить уведомления"}</button>}</div>{message ? <p className="muted" role="status">{message}</p> : null}</section>;
}
