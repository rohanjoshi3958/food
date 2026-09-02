export type AuthUser = {
  id: string;
  name: string | null;
  email: string;
};

export async function apiFetch(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(options.headers);

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
    credentials: "include",
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

  const data = await readJsonResponse<{ user?: AuthUser }>(response);

  if (!response.ok) {
    throw new Error(errorDetailFromBody(data, "Invalid email or password."));
  }

  if (!data.user) {
    throw new Error("Unable to sign in.");
  }

  return data.user;
}

export async function register(name: string, email: string, password: string) {
  const response = await apiFetch("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ name, email, password }),
  });

  const data = await readJsonResponse<{ user?: AuthUser }>(response);

  if (!response.ok) {
    throw new Error(errorDetailFromBody(data, "Unable to create account."));
  }

  if (!data.user) {
    throw new Error("Unable to create account.");
  }

  return data.user;
}

export async function getCurrentUser(): Promise<AuthUser | null> {
  const response = await apiFetch("/api/auth/me");

  if (response.status === 401) {
    return null;
  }

  if (!response.ok) {
    throw new Error("Unable to load your account.");
  }

  return response.json();
}

export async function logout() {
  try {
    await apiFetch("/api/auth/logout", { method: "POST" });
  } catch {
    // Client navigation should still proceed if the request fails.
  }
}

export async function requestPasswordReset(email: string) {
  const response = await apiFetch("/api/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });

  const data = await readJsonResponse<{ message?: string }>(response);

  if (!response.ok) {
    throw new Error(
      errorDetailFromBody(data, "Unable to request a password reset."),
    );
  }

  return (
    data.message ??
    "If an account exists for that email, password reset instructions have been sent."
  );
}

export async function resetPassword(token: string, password: string) {
  const response = await apiFetch("/api/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, password }),
  });

  const data = await readJsonResponse<{ message?: string }>(response);

  if (!response.ok) {
    throw new Error(
      errorDetailFromBody(data, "Unable to reset your password."),
    );
  }

  return data.message ?? "Your password has been reset. You can sign in now.";
}
