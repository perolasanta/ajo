import createClient from "openapi-fetch";
import type {
  LoanApplicationDetail,
  LoanApplicationPage,
  LoanProduct,
  DisbursementPage,
  DisbursementRecord,
  ReadyDisbursementLoanPage,
  MemberDetail,
  MemberListItem,
  PayrollDryRunResponse,
  PayrollImportResponse,
  PayrollPostResponse,
  PayrollRunDetail,
  PayrollRunPage,
  PayrollSignOffResponse,
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
        : (errorBody?.message ??
            errorBody?.detail ??
            errorBody?.error ??
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

export async function getDisbursements(
  params: { status?: string; search?: string; page?: number; pageSize?: number } = {},
): Promise<DisbursementPage> {
  const query = new URLSearchParams();
  if (params.status && params.status !== "all") query.set("status", params.status);
  if (params.search) query.set("search", params.search);
  query.set("page", String(params.page ?? 1));
  query.set("page_size", String(params.pageSize ?? 10));
  return request<DisbursementPage>(`/disbursements?${query.toString()}`);
}

export function getDisbursement(disbursementId: string): Promise<DisbursementRecord> {
  return request<DisbursementRecord>(`/disbursements/${disbursementId}`);
}

export async function getPayrollRuns(
  params: { page?: number; pageSize?: number } = {},
): Promise<PayrollRunPage> {
  return unwrap(
    await api.GET("/payroll/runs", {
      params: {
        query: { page: params.page ?? 1, page_size: params.pageSize ?? 10 },
      },
    }),
  );
}

export async function getPayrollRun(runId: string): Promise<PayrollRunDetail> {
  return unwrap(await api.GET("/payroll/runs/{run_id}", { params: { path: { run_id: runId } } }));
}

export function createPayrollRun(body: {
  profile_id: string;
  period_year: number;
  period_month: number;
}): Promise<{ run_id: string }> {
  return request<{ run_id: string }>("/payroll/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function payrollFilePath(runId: string, action: "dry-run" | "import", file: File): string {
  const query = new URLSearchParams({ filename: file.name });
  return `/payroll/runs/${encodeURIComponent(runId)}/${action}?${query.toString()}`;
}

export function dryRunPayrollFile(runId: string, file: File): Promise<PayrollDryRunResponse> {
  return request<PayrollDryRunResponse>(payrollFilePath(runId, "dry-run", file), {
    method: "POST",
    headers: { "Content-Type": file.type },
    body: file,
  });
}

export function importPayrollFile(runId: string, file: File): Promise<PayrollImportResponse> {
  return request<PayrollImportResponse>(payrollFilePath(runId, "import", file), {
    method: "POST",
    headers: { "Content-Type": file.type },
    body: file,
  });
}

export async function downloadExpectedPayrollCSV(runId: string): Promise<Blob> {
  const response = await fetch(
    `${API_BASE}/payroll/runs/${encodeURIComponent(runId)}/expected.csv`,
    { credentials: "include" },
  );
  if (response.status === 401) window.dispatchEvent(new Event("ajo:unauthenticated"));
  if (!response.ok) {
    const errorBody = (await response
      .clone()
      .json()
      .catch(() => null)) as { message?: string; detail?: string } | null;
    throw new ApiError(
      response.status,
      response.status === 403
        ? "You do not have permission to download this file."
        : (errorBody?.message ?? errorBody?.detail ?? "Unable to download the expected CSV."),
      response.headers.get("X-Request-ID") ?? undefined,
    );
  }
  return response.blob();
}

export function postPayrollRun(runId: string): Promise<PayrollPostResponse> {
  return request<PayrollPostResponse>(`/payroll/runs/${encodeURIComponent(runId)}/post`, {
    method: "POST",
  });
}

export function signOffPayrollRun(
  runId: string,
  bankReceiptTotalKobo: number,
): Promise<PayrollSignOffResponse> {
  return request<PayrollSignOffResponse>(`/payroll/runs/${encodeURIComponent(runId)}/sign-off`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bank_receipt_total_kobo: bankReceiptTotalKobo }),
  });
}

export async function getReadyDisbursementLoans(
  params: { search?: string; page?: number; pageSize?: number } = {},
): Promise<ReadyDisbursementLoanPage> {
  const query = new URLSearchParams();
  if (params.search) query.set("search", params.search);
  query.set("page", String(params.page ?? 1));
  query.set("page_size", String(params.pageSize ?? 10));
  return request<ReadyDisbursementLoanPage>(`/disbursements/ready-loans?${query.toString()}`);
}

export function prepareDisbursement(loanId: string): Promise<{ disbursement_id: string }> {
  return request<{ disbursement_id: string }>(`/loans/${loanId}/disbursements`, { method: "POST" });
}

export function authorizeDisbursement(disbursementId: string): Promise<{ authorized: boolean }> {
  return request<{ authorized: boolean }>(`/disbursements/${disbursementId}/authorize`, {
    method: "POST",
  });
}

export function recordDisbursementTransfer(
  disbursementId: string,
  body: { reference: string; transfer_date: string },
): Promise<{ recorded: boolean }> {
  return request<{ recorded: boolean }>(`/disbursements/${disbursementId}/transfer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function confirmDisbursementPaid(
  disbursementId: string,
): Promise<{ journal_entry_id: string }> {
  return request<{ journal_entry_id: string }>(`/disbursements/${disbursementId}/confirm-paid`, {
    method: "POST",
  });
}

export function failDisbursement(
  disbursementId: string,
  body: { reason: string },
): Promise<{ failed: boolean }> {
  return request<{ failed: boolean }>(`/disbursements/${disbursementId}/failed`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function reverseDisbursement(
  disbursementId: string,
  body: { reason: string },
): Promise<{ journal_entry_id: string }> {
  return request<{ journal_entry_id: string }>(`/disbursements/${disbursementId}/reverse`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
