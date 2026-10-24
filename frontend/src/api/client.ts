import createClient from "openapi-fetch";
import type { paths } from "./schema";

const API_BASE = "/api/v1";
const STATE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
  }
}

function csrfToken(): string | undefined {
  return document.cookie.split("; ").find((cookie) => cookie.startsWith("ajo_csrf="))?.split("=")[1];
}

export const api = createClient<paths>({ baseUrl: API_BASE, credentials: "include" });

api.use({
  onRequest({ request }) {
    if (STATE_METHODS.has(request.method)) {
      const token = csrfToken();
      if (token) request.headers.set("X-CSRF-Token", decodeURIComponent(token));
    }
    return request;
  },
  onResponse({ response }) {
    if (response.status === 401) window.dispatchEvent(new Event("ajo:unauthenticated"));
    return response;
  },
});

export async function ensureCsrf(): Promise<void> {
  const response = await fetch(`${API_BASE}/auth/csrf`, { credentials: "include" });
  if (!response.ok) throw new ApiError(response.status, "Unable to start your session.");
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const headers = new Headers(init?.headers);
  if (STATE_METHODS.has(method)) {
    const token = csrfToken();
    if (token) headers.set("X-CSRF-Token", decodeURIComponent(token));
  }
  const response = await fetch(`${API_BASE}${path}`, { ...init, credentials: "include", headers });
  if (response.status === 401) window.dispatchEvent(new Event("ajo:unauthenticated"));
  if (!response.ok) throw new ApiError(response.status, "Request failed. Please try again.");
  return response.json() as Promise<T>;
}
