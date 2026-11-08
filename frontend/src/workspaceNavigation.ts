export type WorkspaceView = "home" | "chat" | "shared" | "projects" | "memory" | "automations" | "tasks" | "files" | "settings";
export type SettingsView = "general" | "connections" | "tools" | "security" | "activity";
export type WorkspaceNavigation = { view: WorkspaceView; settingsView?: SettingsView; projectId?: string; groupId?: string; threadId?: string };
const views = new Set(["home", "chat", "shared", "projects", "memory", "automations", "tasks", "files", "settings"]);
const settings = new Set(["general", "connections", "tools", "security", "activity"]);
export function urlNavigation(): WorkspaceNavigation | null {
  const query = new URLSearchParams(location.search), view = query.get("view");
  if (!view || !views.has(view)) return null;
  return { view: view as WorkspaceView, projectId: query.get("project")?.slice(0, 128) || "", threadId: query.get("thread")?.slice(0, 128) || undefined };
}
export function readNavigation(userId: string): WorkspaceNavigation | null {
  try {
    const value = JSON.parse(localStorage.getItem(`avelin-navigation:${encodeURIComponent(userId)}`) || "null");
    if (!value || !views.has(value.view)) return null;
    return { view: value.view, settingsView: settings.has(value.settingsView) ? value.settingsView : "general", projectId: typeof value.projectId === "string" ? value.projectId.slice(0, 128) : "", groupId: typeof value.groupId === "string" ? value.groupId.slice(0, 128) : "", threadId: typeof value.threadId === "string" ? value.threadId.slice(0, 128) : undefined };
  } catch { return null; }
}
export function saveNavigation(userId: string, value: WorkspaceNavigation) {
  try { localStorage.setItem(`avelin-navigation:${encodeURIComponent(userId)}`, JSON.stringify(value)); } catch { /* Navigation also survives ordinary backgrounding in React state. */ }
}
