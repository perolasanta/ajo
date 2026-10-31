import createClient from "openapi-fetch";
import type {
  LoanApplicationDetail,
  LoanApplicationPage,
  LoanProduct,
  MemberDetail,
  MemberListItem,
  paths,
} from "./schema";

const API_BASE = "/api/v1";
const STATE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly requestId?: string,
    public readonly code?: string,
  ) {
    super(message);
  }
}

function csrfToken(): string | undefined {
  return document.cookie
    .split("; ")
    .find((cookie) => cookie.startsWith("ajo_csrf="))
    ?.split("=")[1];
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
  if (!response.ok) {
    const errorBody = (await response
      .clone()
      .json()
      .catch(() => null)) as { error?: string; detail?: string; message?: string } | null;
    throw new ApiError(
      response.status,
      response.status === 403
        ? "You do not have permission to view this information."
        : (errorBody?.error ??
            errorBody?.detail ??
            errorBody?.message ??
            "Request failed. Please try again."),
      response.headers.get("X-Request-ID") ?? undefined,
      errorBody?.error,
    );
  }
  return response.json() as Promise<T>;
}

async function unwrap<T>(result: { data?: T; response: Response }): Promise<T> {
  if (!result.response.ok || result.data === undefined) {
    throw new ApiError(
      result.response.status,
      result.response.status === 403
        ? "You do not have permission to view this information."
        : "Request failed. Please try again.",
      result.response.headers.get("X-Request-ID") ?? undefined,
    );
  }
  return result.data;
}

export async function getMembers(search?: string): Promise<MemberListItem[]> {
  return unwrap(await api.GET("/members", { params: { query: search ? { search } : {} } }));
}

export async function getMember(memberId: string): Promise<MemberDetail> {
  return unwrap(
    await api.GET("/members/{member_id}", { params: { path: { member_id: memberId } } }),
  );
}

export async function getLoanApplications(
  params: {
    status?: string;
    search?: string;
    page?: number;
    pageSize?: number;
  } = {},
): Promise<LoanApplicationPage> {
  const query = new URLSearchParams();
  if (params.status && params.status !== "all") query.set("status", params.status);
  if (params.search) query.set("search", params.search);
  query.set("page", String(params.page ?? 1));
  query.set("page_size", String(params.pageSize ?? 10));
  return request<LoanApplicationPage>(`/loan-applications?${query.toString()}`);
}

export function getLoanApplication(applicationId: string): Promise<LoanApplicationDetail> {
  return request<LoanApplicationDetail>(`/loan-applications/${applicationId}`);
}

export function getLoanProducts(): Promise<LoanProduct[]> {
  return request<LoanProduct[]>("/loan-products");
}

export function submitLoanApplication(body: {
  member_id: string;
  product_id: string;
  principal_kobo: number;
  tenor_months: number;
  destination_bank_account_id?: string;
}): Promise<{ application_id: string }> {
  return request<{ application_id: string }>("/loan-applications", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function decideLoanApplication(
  applicationId: string,
  body: { decision: "approved" | "rejected"; reason: string },
): Promise<{ loan_id: string | null; decision: string }> {
  return request<{ loan_id: string | null; decision: string }>(
    `/loan-applications/${applicationId}/decision`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}
