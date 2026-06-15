const TOKEN_KEY = "food_token";

export type AuthUser = {
  id: string;
  name: string | null;
  email: string;
};

export function getToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export async function apiFetch(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const token = getToken();
  const headers = new Headers(options.headers);

  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  if (
    options.body &&
    !(options.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }

  return fetch(path, {
    ...options,
    headers,
  });
}

export async function readJsonResponse<T = unknown>(response: Response): Promise<T> {
  const text = await response.text();

  if (!text) {
    return {} as T;
  }

  try {
    return JSON.parse(text) as T;
  } catch {
    if (text.startsWith("Internal Server Error")) {
      throw new Error(
        "The server timed out or restarted. Receipt analysis can take up to a minute — please try again.",
      );
    }

    throw new Error(text.slice(0, 300));
  }
}

export function errorDetailFromBody(data: unknown, fallback: string): string {
  if (typeof data === "string" && data.trim()) {
    return data;
  }

  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
  }

  if (data && typeof data === "object" && "error" in data) {
    const error = (data as { error?: unknown }).error;
    if (typeof error === "string") {
      return error;
    }
  }

  return fallback;
}

export async function parseError(response: Response, fallback: string) {
  try {
    const text = await response.text();
    if (!text) {
      return fallback;
    }

    try {
      const data = JSON.parse(text);
      return errorDetailFromBody(data, fallback);
    } catch {
      if (text.startsWith("Internal Server Error")) {
        return "The server timed out or restarted. Please try again.";
      }

      return text.slice(0, 300);
    }
  } catch {
    // Ignore read errors.
  }

  return fallback;
}

export async function login(email: string, password: string) {
  const response = await apiFetch("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });

  const data = await response.json();

  if (!response.ok) {
    throw new Error(await parseError(response, "Invalid email or password."));
  }

  setToken(data.access_token);
  return data.user as AuthUser;
}

export async function register(name: string, email: string, password: string) {
  const response = await apiFetch("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ name, email, password }),
  });

  const data = await response.json();

  if (!response.ok) {
    throw new Error(await parseError(response, "Unable to create account."));
  }

  setToken(data.access_token);
  return data.user as AuthUser;
}

export async function getCurrentUser(): Promise<AuthUser | null> {
  const token = getToken();

  if (!token) {
    return null;
  }

  const response = await apiFetch("/api/auth/me");

  if (response.status === 401) {
    clearToken();
    return null;
  }

  if (!response.ok) {
    throw new Error("Unable to load your account.");
  }

  return response.json();
}

export function logout() {
  clearToken();
}
