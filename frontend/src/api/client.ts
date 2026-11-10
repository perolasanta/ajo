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
  MemberStatementPage,
  MemberLoanPage,
  MemberActivityPage,
  WithdrawalQueuePage,
  NotificationSummary,
  DashboardSummary,
  PayrollDryRunResponse,
  PayrollImportResponse,
  PayrollPostResponse,
  PayrollRunDetail,
  PayrollRunPage,
  PayrollResolutionResponse,
  PayrollSignOffResponse,
  RoleChangeProposalPage,
  StaffPage,
  paths,
} from "./schema";
import type { AuditEventPage, AuditVerification } from "./audit";

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

function readableErrorMessage(value: unknown): string | undefined {
  if (typeof value === "string") return value;
  if (!Array.isArray(value)) return undefined;

  const messages = value.flatMap((issue: unknown) => {
    if (typeof issue !== "object" || issue === null || !("msg" in issue)) return [];
    const message = Reflect.get(issue, "msg");
    if (typeof message !== "string") return [];
    const location = Reflect.get(issue, "loc");
    const field = Array.isArray(location)
      ? location
          .slice(1)
          .filter((part): part is string => typeof part === "string")
          .join(".")
          .replaceAll("_", " ")
      : "";
    return [field ? `${field}: ${message}` : message];
  });
  return messages.length > 0 ? messages.join("; ") : undefined;
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
      .catch(() => null)) as { error?: unknown; detail?: unknown; message?: unknown } | null;
    throw new ApiError(
      response.status,
      response.status === 403
        ? "You do not have permission to view this information."
        : (readableErrorMessage(errorBody?.message) ??
            readableErrorMessage(errorBody?.detail) ??
            readableErrorMessage(errorBody?.error) ??
            "Request failed. Please try again."),
      response.headers.get("X-Request-ID") ?? undefined,
      readableErrorMessage(errorBody?.error),
    );
  }
  return response.json() as Promise<T>;
}

export function getNotificationSummary(): Promise<NotificationSummary> {
  return request<NotificationSummary>("/notifications/summary");
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

export async function getMembers(
  search?: string,
  hasPendingRequest = false,
): Promise<MemberListItem[]> {
  const query = new URLSearchParams();
  if (search) query.set("search", search);
  if (hasPendingRequest) query.set("has_pending_request", "true");
  return request<MemberListItem[]>(`/members?${query.toString()}`);
}

export function getWithdrawalRequests(
  params: { status?: string; mine?: "all" | "needed"; page?: number; pageSize?: number } = {},
): Promise<WithdrawalQueuePage> {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.mine) query.set("mine", params.mine);
  query.set("page", String(params.page ?? 1));
  query.set("page_size", String(params.pageSize ?? 25));
  return request<WithdrawalQueuePage>(`/withdrawal-requests?${query.toString()}`);
}

export async function getMember(memberId: string): Promise<MemberDetail> {
  return unwrap(
    await api.GET("/members/{member_id}", { params: { path: { member_id: memberId } } }),
  );
}

export function getMemberStatement(
  memberId: string,
  params: { page?: number; pageSize?: number } = {},
): Promise<MemberStatementPage> {
  const query = new URLSearchParams({
    page: String(params.page ?? 1),
    page_size: String(params.pageSize ?? 25),
  });
  return request<MemberStatementPage>(`/members/${memberId}/statement?${query.toString()}`);
}

export function getMemberLoans(
  memberId: string,
  params: { page?: number; pageSize?: number } = {},
): Promise<MemberLoanPage> {
  const query = new URLSearchParams({
    page: String(params.page ?? 1),
    page_size: String(params.pageSize ?? 25),
  });
  return request<MemberLoanPage>(`/members/${memberId}/loans?${query.toString()}`);
}

export function getMemberActivity(
  memberId: string,
  params: { page?: number; pageSize?: number } = {},
): Promise<MemberActivityPage> {
  const query = new URLSearchParams({
    page: String(params.page ?? 1),
    page_size: String(params.pageSize ?? 25),
  });
  return request<MemberActivityPage>(`/members/${memberId}/activity?${query.toString()}`);
}

export async function getLoanApplications(
  params: {
    status?: string;
    search?: string;
    page?: number;
    pageSize?: number;
    action?: "needed";
  } = {},
): Promise<LoanApplicationPage> {
  const query = new URLSearchParams();
  if (params.status && params.status !== "all") query.set("status", params.status);
  if (params.search) query.set("search", params.search);
  if (params.action) query.set("action", params.action);
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

export type SettingsPolicyVersion = {
  id: string;
  version: number;
  status: string;
  effective_from: string;
  effective_to: string | null;
  salary_basis: string;
  deduction_cap_bps: number;
  max_active_loans: number;
  guarantors_required: number;
  min_savings_kobo: number;
  loan_to_savings_multiple_x100: number;
  interest_method: string;
  payroll_allocation_order: string[];
  confirmation_expiry_minutes: number;
  otp_validity_seconds: number;
  cash_banking_days: number;
  settings: Record<string, unknown>;
  proposed_by?: string;
  approved_by?: string | null;
};

export type SettingsLoanProduct = LoanProduct & {
  status: string;
  supersedes_id: string | null;
  approved_at: string | null;
};

export function getPolicyVersions(): Promise<SettingsPolicyVersion[]> {
  return request<SettingsPolicyVersion[]>("/policies/versions");
}

export function getSettingsLoanProducts(): Promise<SettingsLoanProduct[]> {
  return request<SettingsLoanProduct[]>("/settings/loan-products");
}

export function submitPolicyProposal(
  body: Record<string, unknown>,
): Promise<{ policy_version_id: string }> {
  return request<{ policy_version_id: string }>("/policies", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify(body),
  });
}

export function decidePolicy(
  policyId: string,
  action: "approve" | "supersede",
  body: Record<string, unknown>,
): Promise<unknown> {
  const suffix = action === "approve" ? "approve" : "supersede";
  return request(`/policies/${encodeURIComponent(policyId)}/${suffix}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify(body),
  });
}

export function submitLoanProductProposal(
  body: Record<string, unknown>,
): Promise<{ loan_product_id: string }> {
  return request<{ loan_product_id: string }>("/loan-products", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify(body),
  });
}

export function approveSettingsLoanProduct(productId: string, reason: string): Promise<unknown> {
  const date = new Date().toLocaleDateString("en-CA", { timeZone: "Africa/Lagos" });
  return request(`/loan-products/${encodeURIComponent(productId)}/approve?on_date=${date}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify({ reason }),
  });
}

export function getDashboardSummary(): Promise<DashboardSummary> {
  return request<DashboardSummary>("/dashboard/summary");
}

export async function getAuditEvents(
  params: {
    from?: string;
    to?: string;
    actorId?: string;
    action?: string;
    entityType?: string;
    page?: number;
    pageSize?: number;
  } = {},
): Promise<AuditEventPage> {
  const query = new URLSearchParams();
  if (params.from) query.set("from", params.from);
  if (params.to) query.set("to", params.to);
  if (params.actorId) query.set("actor_id", params.actorId);
  if (params.action) query.set("action", params.action);
  if (params.entityType) query.set("entity_type", params.entityType);
  query.set("page", String(params.page ?? 1));
  query.set("page_size", String(params.pageSize ?? 25));
  return request<AuditEventPage>(`/audit-events?${query.toString()}`);
}

export function verifyAuditChain(): Promise<AuditVerification> {
  return request<AuditVerification>("/audit-events/verify");
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
  params: {
    status?: string;
    search?: string;
    page?: number;
    pageSize?: number;
    action?: "needed";
  } = {},
): Promise<DisbursementPage> {
  const query = new URLSearchParams();
  if (params.status && params.status !== "all") query.set("status", params.status);
  if (params.search) query.set("search", params.search);
  if (params.action) query.set("action", params.action);
  query.set("page", String(params.page ?? 1));
  query.set("page_size", String(params.pageSize ?? 10));
  return request<DisbursementPage>(`/disbursements?${query.toString()}`);
}

export function getDisbursement(disbursementId: string): Promise<DisbursementRecord> {
  return request<DisbursementRecord>(`/disbursements/${disbursementId}`);
}

export async function getPayrollRuns(
  params: { page?: number; pageSize?: number; action?: "needed" } = {},
): Promise<PayrollRunPage> {
  const query = new URLSearchParams({
    page: String(params.page ?? 1),
    page_size: String(params.pageSize ?? 10),
  });
  if (params.action) query.set("action", params.action);
  return request<PayrollRunPage>(`/payroll/runs?${query.toString()}`);
}

export type PayrollProfile = {
  id: string;
  name: string;
  staff_no_column: string;
  amount_column: string;
  name_column: string | null;
  created_at: string;
};

export function getPayrollProfiles(): Promise<{ items: PayrollProfile[] }> {
  return request<{ items: PayrollProfile[] }>("/payroll/profiles");
}

export function createPayrollProfile(body: {
  staff_no_column: string;
  amount_column: string;
  name_column?: string | null;
  header_row?: number;
}): Promise<{ profile_id: string }> {
  return request<{ profile_id: string }>("/payroll/profiles", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function getPayrollRun(runId: string): Promise<PayrollRunDetail> {
  return unwrap(await api.GET("/payroll/runs/{run_id}", { params: { path: { run_id: runId } } }));
}

export function addPayrollResolutionNote(
  runId: string,
  itemId: string,
  note: string,
  idempotencyKey: string,
): Promise<PayrollResolutionResponse> {
  return request<PayrollResolutionResponse>(
    `/payroll/runs/${encodeURIComponent(runId)}/items/${encodeURIComponent(itemId)}/resolution`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ note }),
    },
  );
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

export async function getStaff(
  params: { page?: number; pageSize?: number } = {},
): Promise<StaffPage> {
  const query = new URLSearchParams({
    page: String(params.page ?? 1),
    page_size: String(params.pageSize ?? 25),
  });
  return request<StaffPage>(`/staff?${query.toString()}`);
}

export function updateStaffFullName(
  userId: string,
  fullName: string | null,
): Promise<{
  user_id: string;
  full_name: string | null;
}> {
  return request<{ user_id: string; full_name: string | null }>(
    `/staff/${encodeURIComponent(userId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ full_name: fullName }),
    },
  );
}

export async function getRoleChangeProposals(
  params: {
    status?: "all" | "pending" | "approved" | "rejected" | "cancelled" | "expired";
    page?: number;
    pageSize?: number;
    action?: "needed";
  } = {},
): Promise<RoleChangeProposalPage> {
  const query = new URLSearchParams({
    status: params.status ?? "pending",
    page: String(params.page ?? 1),
    page_size: String(params.pageSize ?? 25),
  });
  if (params.action) query.set("action", params.action);
  return request<RoleChangeProposalPage>(`/role-change-proposals?${query.toString()}`);
}

export function createRoleChangeProposal(body: {
  target_user_id: string;
  role: "officer" | "committee" | "treasurer" | "admin" | "auditor";
  action: "add" | "remove";
  reason: string;
}): Promise<{ proposal_id: string; status: "pending" }> {
  return request<{ proposal_id: string; status: "pending" }>("/auth/role-change-proposals", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function decideRoleChangeProposal(
  proposalId: string,
  action: "approve" | "reject" | "cancel",
): Promise<Record<string, boolean>> {
  return request<Record<string, boolean>>(
    `/auth/role-change-proposals/${encodeURIComponent(proposalId)}/${action}`,
    { method: "POST" },
  );
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
