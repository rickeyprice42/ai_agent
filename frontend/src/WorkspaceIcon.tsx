export type IconName = "home" | "chat" | "projects" | "memory" | "clock" | "tasks" | "files" | "settings" | "search" | "arrow" | "plus" | "menu" | "close" | "sun" | "bell";

const paths: Record<IconName, string> = {
  home: "M3 10 12 3l9 7v10a1 1 0 0 1-1 1h-6v-7h-4v7H4a1 1 0 0 1-1-1Z",
  chat: "M21 11a8 8 0 0 1-8 8H7l-4 3V5a2 2 0 0 1 2-2h8a8 8 0 0 1 8 8ZM7 8h9M7 12h6",
  projects: "M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z",
  memory: "M12 5c-4-5-9 1-6 4-5 1-4 8 0 8-1 5 6 6 6 1V5Zm0 0c4-5 9 1 6 4 5 1 4 8 0 8 1 5-6 6-6 1M8 10l4 2 4-2M7 17l2-2m8 2-2-2",
  clock: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM12 7v5l3 2",
  tasks: "M9 6h12M9 12h12M9 18h12M2 6l1 1 2-3M2 12l1 1 2-3M2 18l1 1 2-3",
  files: "M14 2H5v20h14V7l-5-5Zm0 0v6h5M8 12h8M8 16h6",
  settings: "M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8ZM10 2h4l1 3 3 1 3 2-1 4 1 4-3 2-3 1-1 3h-4l-1-3-3-1-3-2 1-4-1-4 3-2 3-1 1-3Z",
  search: "M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0Zm-2 5 6 6",
  arrow: "M5 12h14m-6-6 6 6-6 6",
  plus: "M12 5v14M5 12h14",
  menu: "M4 7h16M4 12h16M4 17h16",
  close: "m6 6 12 12M6 18 18 6",
  sun: "M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1",
  bell: "M6 9a6 6 0 0 1 12 0v6l2 3H4l2-3V9Zm4 12h4",
};

export function WorkspaceIcon({ name }: { name: IconName }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
