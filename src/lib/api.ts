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

export async function parseError(response: Response, fallback: string) {
  try {
    const data = await response.json();
    if (typeof data.detail === "string") {
      return data.detail;
    }
    if (typeof data.error === "string") {
      return data.error;
    }
  } catch {
    // Ignore JSON parse errors.
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
