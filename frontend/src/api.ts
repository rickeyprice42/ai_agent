import type { BootstrapPayload } from "./types";

export async function fetchBootstrap(): Promise<BootstrapPayload> {
  const response = await fetch("/api/bootstrap");
  if (!response.ok) {
    throw new Error("Не удалось загрузить данные агента.");
  }
  return response.json();
}

export async function sendMessage(message: string): Promise<{ reply: string }> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ message })
  });

  if (!response.ok) {
    throw new Error("Не удалось получить ответ агента.");
  }

  return response.json();
}
