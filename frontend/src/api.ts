import type { AuthPayload, BootstrapPayload, ModelProviderOption, ModelSettings, UserProfile } from "./types";

function authHeaders(token: string): HeadersInit {
  return {
    Authorization: `Bearer ${token}`
  };
}

async function parseError(response: Response, fallback: string): Promise<Error> {
  try {
    const payload = await response.json();
    return new Error(typeof payload.detail === "string" ? payload.detail : fallback);
  } catch {
    return new Error(fallback);
  }
}

export async function fetchBootstrap(token: string): Promise<BootstrapPayload> {
  const response = await fetch("/api/bootstrap", {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось загрузить данные агента.");
  }
  return response.json();
}

export async function sendMessage(message: string, token: string): Promise<{ reply: string }> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(token)
    },
    body: JSON.stringify({ message })
  });

  if (!response.ok) {
    throw await parseError(response, "Не удалось получить ответ агента.");
  }

  return response.json();
}

export async function login(loginValue: string, password: string): Promise<AuthPayload> {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login: loginValue, password })
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось войти.");
  }
  return response.json();
}

export async function register(
  email: string,
  username: string,
  password: string,
  displayName: string
): Promise<AuthPayload> {
  const response = await fetch("/api/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, username, password, display_name: displayName })
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось зарегистрироваться.");
  }
  return response.json();
}

export async function fetchMe(token: string): Promise<UserProfile> {
  const response = await fetch("/api/auth/me", {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Сессия недействительна.");
  }
  return response.json();
}

export async function logout(token: string): Promise<void> {
  await fetch("/api/auth/logout", {
    method: "POST",
    headers: authHeaders(token)
  });
}

export async function fetchModelProviders(token: string): Promise<ModelProviderOption[]> {
  const response = await fetch("/api/models", {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось загрузить список моделей.");
  }
  return response.json();
}

export async function updateModelSettings(
  token: string,
  settings: ModelSettings
): Promise<ModelSettings> {
  const response = await fetch("/api/model-settings", {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(token)
    },
    body: JSON.stringify(settings)
  });
  if (!response.ok) {
    throw await parseError(response, "Не удалось обновить модель.");
  }
  return response.json();
}
