/* App shell and private API responses are deliberately not cached. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("push", (event) => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch { payload = {}; }
  if (!payload || typeof payload !== "object") payload = {};
  const title = typeof payload.title === "string" ? payload.title : "Avelin";
  const body = typeof payload.body === "string" ? payload.body : "Появилось новое событие. Открой Avelin.";
  let url = "/";
  try {
    const candidate = new URL(payload.url || payload.data?.url || "/", self.location.origin);
    if (candidate.origin === self.location.origin) url = candidate.pathname + candidate.search + candidate.hash;
  } catch { /* Use home for malformed or cross-origin destinations. */ }
  event.waitUntil(self.registration.showNotification(title, { body, icon: "/brand/icon-192.png", badge: "/brand/icon-192.png", data: { url }, tag: typeof payload.tag === "string" ? payload.tag : undefined }));
});
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  let target;
  try { target = new URL(event.notification.data?.url || "/", self.location.origin); }
  catch { return; }
  if (target.origin !== self.location.origin) return;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(async (clients) => {
    const windowClient = clients.find((client) => new URL(client.url).origin === self.location.origin);
    if (windowClient) { await windowClient.navigate(target.href); return windowClient.focus(); }
    return self.clients.openWindow(target.href);
  }));
});
