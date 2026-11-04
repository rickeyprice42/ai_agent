/* Exercise the shipped worker handlers without contacting a push service. */
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const handlers = new Map();
const displayed = [];
const navigated = [];
const opened = [];
let focused = 0;
let clients = [];
const self = {
  location: { origin: "https://avelin.example" },
  addEventListener: (name, callback) => handlers.set(name, callback),
  skipWaiting: async () => {},
  registration: { showNotification: async (title, options) => displayed.push({ title, ...options }) },
  clients: {
    claim: async () => {}, matchAll: async () => clients,
    openWindow: async (url) => opened.push(url),
  },
};
vm.runInNewContext(fs.readFileSync(new URL("../frontend/public/sw.js", import.meta.url), "utf8"), { self, URL });
assert.equal(handlers.has("fetch"), false, "private API responses must not be cached by this worker");
async function dispatch(name, properties) {
  let completion = Promise.resolve();
  handlers.get(name)({ ...properties, waitUntil: (promise) => { completion = promise; } });
  await completion;
}
await dispatch("push", { data: { json: () => ({ title: "Avelin", body: "Задача готова", tag: "run-1", data: { url: "/?view=automations" } }) } });
assert.equal(displayed.at(-1).data.url, "/?view=automations");
assert.equal(displayed.at(-1).body, "Задача готова");
await dispatch("push", { data: { json: () => ({ data: { url: "https://external.example/steal" } }) } });
assert.equal(displayed.at(-1).data.url, "/");
await dispatch("push", { data: { json: () => null } });
assert.equal(displayed.at(-1).title, "Avelin");
await dispatch("push", { data: { json: () => { throw new Error("malformed payload"); } } });
assert.equal(displayed.at(-1).data.url, "/");
clients = [{ url: "https://avelin.example/?view=chat", navigate: async (url) => navigated.push(url), focus: async () => { focused += 1; } }];
await dispatch("notificationclick", { notification: { close() {}, data: { url: "/?view=automations" } } });
assert.equal(navigated.at(-1), "https://avelin.example/?view=automations");
assert.equal(focused, 1);
clients = [];
await dispatch("notificationclick", { notification: { close() {}, data: { url: "/?view=settings" } } });
assert.equal(opened.at(-1), "https://avelin.example/?view=settings");
await dispatch("notificationclick", { notification: { close() {}, data: { url: "https://external.example" } } });
await dispatch("notificationclick", { notification: { close() {}, data: { url: "http://[" } } });
assert.equal(opened.length, 1);
console.log("PASS: push payload, fallback, same-origin click/focus/open and no private caching (8 scenarios; delivery not tested)");
