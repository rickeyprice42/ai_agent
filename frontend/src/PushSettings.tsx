import { useEffect, useRef, useState } from "react";
import { pushApi as api, pushError, pushTestMessage, type PushTestStatus } from "./pushApi";

type PushConfig = { enabled: boolean; public_key?: string | null; reason?: string };
const PENDING_TEST = "avelin-pending-push-test";
function savedTest(): string | null {
  try { return sessionStorage.getItem(PENDING_TEST); } catch { return null; }
}
function saveTest(value: string | null) {
  try { if (value) sessionStorage.setItem(PENDING_TEST, value); else sessionStorage.removeItem(PENDING_TEST); } catch { /* Memory state still supports foreground reconciliation. */ }
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
  const [pendingTest, setPendingTest] = useState(savedTest);
  const pendingRef = useRef(pendingTest);
  const [permission, setPermission] = useState(() => typeof Notification === "undefined" ? "unsupported" : Notification.permission);
  const supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window && window.isSecureContext;
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const standalone = window.matchMedia("(display-mode: standalone)").matches || Boolean((navigator as Navigator & { standalone?: boolean }).standalone);
  useEffect(() => {
    let active = true;
    void api("config", token).then((value) => { if (active) setConfig(value); }).catch((err) => { if (active) setMessage(pushError(err)); });
    if (supported) void navigator.serviceWorker.getRegistration("/").then((registration) => registration?.pushManager.getSubscription()).then((subscription) => { if (active) setSubscribed(Boolean(subscription)); }).catch(() => {});
    return () => { active = false; };
  }, [token, supported]);

  useEffect(() => {
    pendingRef.current = pendingTest;
    if (!pendingTest) return;
    let active = true;
    let checking = false;
    let attempts = 0;
    async function reconcile() {
      if (!active || checking || document.visibilityState === "hidden" || attempts >= 15) return;
      checking = true; attempts += 1;
      try {
        const status: PushTestStatus = await api(`test/${encodeURIComponent(pendingTest!)}`, token);
        if (!active || pendingRef.current !== pendingTest) return;
        // A backgrounded POST may still be in flight when the first GET returns.
        if (status.state !== "not_found" || attempts >= 3) setMessage(pushTestMessage(status));
        if (["accepted", "failed"].includes(status.state)) { pendingRef.current = null; saveTest(null); setPendingTest(null); }
      } catch {
        if (active) setMessage("Проверка отправлена. Статус доставки уточнится после восстановления связи с Avelin.");
      } finally { checking = false; }
    }
    function resumed() { if (document.visibilityState !== "hidden") { attempts = 0; void reconcile(); } }
    void reconcile();
    const timer = window.setInterval(() => void reconcile(), 2000);
    document.addEventListener("visibilitychange", resumed);
    return () => { active = false; window.clearInterval(timer); document.removeEventListener("visibilitychange", resumed); };
  }, [pendingTest, token]);

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
    } catch (err) { setMessage(pushError(err)); }
    finally { setBusy(false); }
  }

  async function disable() {
    setBusy(true); setMessage("");
    try { await detachPush(token); setSubscribed(false); setMessage("Уведомления на этом устройстве отключены."); }
    catch (err) { setMessage(pushError(err)); }
    finally { setBusy(false); }
  }

  async function test() {
    const requestId = crypto.randomUUID();
    pendingRef.current = requestId; saveTest(requestId); setPendingTest(requestId);
    setBusy(true); setMessage("Отправляем проверку уведомления…");
    try {
      const result = await api("test", token, "POST", { request_id: requestId });
      if (pendingRef.current === requestId) setMessage(pushTestMessage(result));
    }
    catch { setMessage("Связь прервалась при отправке проверки. После возвращения в Avelin проверим, принял ли её сервер; повторная отправка не нужна."); }
    finally { setBusy(false); }
  }

  return <section className="panel panel-compact push-settings"><div className="panel-heading panel-heading-compact"><h3>Уведомления</h3><span className="muted">{subscribed ? "Включены" : "Выключены"}</span></div><p className="muted">Результаты автоматизаций и важные события на этом устройстве.</p>{ios && !standalone ? <p className="muted">На iPhone открой меню «Поделиться» в Safari → «На экран Домой». Запусти Avelin с этого значка, затем включи уведомления здесь.</p> : !supported ? <p className="muted">Этот браузер пока не поддерживает push-уведомления. События остаются в разделе задач.</p> : null}{config && !config.enabled ? <p className="muted">{config.reason || "Доставка уведомлений на сервере пока не настроена."}</p> : null}{permission === "denied" ? <p className="muted">Разрешение заблокировано в настройках браузера.</p> : null}<div className="dialog-button-row">{subscribed ? <><button className="ghost-button" type="button" disabled={busy} onClick={() => void disable()}>Отключить уведомления</button><button className="ghost-button" type="button" disabled={busy || !config?.enabled} onClick={() => void test()}>Проверить уведомление</button></> : <button className="ghost-button" type="button" disabled={busy || !supported || !config?.enabled || (ios && !standalone) || permission === "denied"} onClick={() => void enable()}>{busy ? "Подключаем…" : "Включить уведомления"}</button>}</div>{message ? <p className="muted" role="status">{message}</p> : null}</section>;
}
