/* Checked-in frontend contract for the staff API. */
export type MemberListItem = {
  id: string;
  member_no: string;
  full_name: string;
  phone_e164?: string | null;
  email?: string | null;
  staff_no?: string | null;
  status?: string;
  joined_on?: string | null;
  has_pending_request?: boolean;
};

export type MemberBalances = {
  savings_kobo: number;
  shares_kobo: number;
  dues_paid_kobo: number;
  locked_kobo: number;
  available_kobo: number;
  active_loans_count?: number;
};

export type MemberBankAccount = {
  id: string;
  bank_code: string;
  last4: string;
  account_name?: string;
  verified_at?: string | null;
  status?: string;
};

export type MemberDetail = MemberListItem & {
  employer?: string | null;
  balances?: MemberBalances;
  bank_accounts?: MemberBankAccount[];
};

export type MemberStatementItem = {
  id: string;
  date: string;
  recorded_at?: string | null;
  type: string;
  amount_kobo: number;
  running_balance_kobo: number;
};
export type MemberStatementPage = {
  items: MemberStatementItem[];
  page: number;
  page_size: number;
  total: number;
};
export type MemberLoanItem = {
  application_id: string;
  loan_id?: string | null;
  application_status: string;
  loan_status?: string | null;
  deductions_paused?: boolean | null;
  principal_kobo: number;
  outstanding_balance_kobo?: number | null;
  next_installment_at?: string | null;
  next_installment_amount_kobo?: number | null;
  submitted_at: string;
};
export type MemberLoanPage = {
  items: MemberLoanItem[];
  page: number;
  page_size: number;
  total: number;
};
export type MemberActivityItem = {
  event_id: string;
  timestamp: string;
  actor_id: string | null;
  actor_display_name: string;
  actor_role: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  safe_summary: Record<string, string | number | null>;
};
export type WithdrawalQueueItem = {
  id: string;
  member_id: string;
  member_name: string;
  member_no: string;
  amount_kobo: number;
  requester_name: string;
  requested_by: string;
  requested_at: string;
  status: string;
  reason: string;
  rejection_reason: string | null;
};
export type WithdrawalQueuePage = {
  items: WithdrawalQueueItem[];
  page: number;
  page_size: number;
  total: number;
};
export type MemberActivityPage = {
  items: MemberActivityItem[];
  page: number;
  page_size: number;
  total: number;
};

export type ConsentStatus = { kind: string; status: string };
export type GuarantorSummary = { member_no: string; full_name: string };
export type LoanScheduleItem = {
  installment_no: number;
  due_at: string;
  principal_kobo: number;
  interest_kobo: number;
  payment_kobo: number;
  balance_kobo: number;
  dues_kobo: number;
  savings_kobo: number;
};

export type LoanApplicationSummary = {
  id: string;
  member_no: string;
  full_name: string;
  product_name: string;
  principal_kobo: number;
  tenor_months: number;
  annual_rate_bps: number;
  status: string;
  submitted_at: string;
  decision: string | null;
  decision_reason: string | null;
  decided_at: string | null;
  consent_statuses: ConsentStatus[];
  guarantor_count: number;
};

export type LoanApplicationDetail = LoanApplicationSummary & {
  terms_snapshot: Record<string, unknown> | null;
  status_timeline: Array<Record<string, unknown>>;
  guarantors: GuarantorSummary[];
  schedule: LoanScheduleItem[];
  destination_bank_account?: { bank_code: string; last4: string } | null;
};

export type LoanApplicationPage = {
  items: LoanApplicationSummary[];
  page: number;
  page_size: number;
  total: number;
};

export type DashboardSummary = {
  loans: Record<string, number>;
  disbursements: Record<string, number>;
};

export type LoanProduct = {
  id: string;
  code: string;
  name: string;
  interest_method: string;
  annual_rate_bps: number;
  min_amount_kobo: number;
  max_amount_kobo: number;
  min_tenor_months: number;
  max_tenor_months: number;
  fee_bps: number;
  guarantors_required: number;
};

export type DisbursementBase = {
  id: string;
  loan_id: string;
  application_id: string;
  member_no: string;
  full_name: string;
  amount_kobo: number;
  status: string;
  prepared_by: string;
  prepared_by_name: string;
  authorized_by: string | null;
  authorized_by_name: string | null;
  transfer_reference: string | null;
  transfer_date: string | null;
  confirmed_by: string | null;
  confirmed_by_name: string | null;
  failure_reason: string | null;
  reversal_reason: string | null;
  created_at: string;
  authorized_at: string | null;
  transfer_recorded_at: string | null;
  paid_at: string | null;
  failed_at: string | null;
  reversed_at: string | null;
};
export type DisbursementAdminTreasurer = DisbursementBase & {
  bank_code: string;
  last4: string;
};
export type DisbursementAuditor = DisbursementBase;
export type DisbursementRecord = DisbursementAdminTreasurer | DisbursementAuditor;
export type DisbursementPage = {
  items: DisbursementRecord[];
  page: number;
  page_size: number;
  total: number;
};
export type ReadyDisbursementLoan = {
  loan_id: string;
  application_id: string;
  member_no: string;
  full_name: string;
  amount_kobo: number;
  status: string;
};
export type ReadyDisbursementLoanPage = {
  items: ReadyDisbursementLoan[];
  page: number;
  page_size: number;
  total: number;
};
export type DisbursementActionBody = { reason: string };

export type PayrollRunCounts = {
  matched: number;
  short: number;
  over: number;
  missing: number;
  unknown_member: number;
  duplicate: number;
};

export type PayrollRunSummary = {
  run_id: string;
  period_year: number;
  period_month: number;
  status: string;
  expected_total_kobo: number;
  file_total_kobo: number | null;
  counts: PayrollRunCounts;
  created_by: string;
  created_by_name: string;
  posted_by: string | null;
  posted_by_name: string | null;
  created_at: string;
  imported_at: string | null;
  posted_at: string | null;
  signed_off_at: string | null;
};

export type PayrollRunPage = {
  items: PayrollRunSummary[];
  page: number;
  page_size: number;
  total: number;
};

export type PayrollRunException = {
  item_id: string;
  staff_no_raw: string;
  classification: string;
  actual_kobo: number | null;
  resolution_note: string | null;
};

export type PayrollResolutionResponse = { resolved: boolean };

export type PayrollRunDetail = {
  run_id: string;
  period_year: number;
  period_month: number;
  status: string;
  expected_total_kobo: number;
  file_total_kobo: number | null;
  bank_receipt_total_kobo: number | null;
  variance_kobo: number | null;
  counts: PayrollRunCounts;
  component_totals_kobo: { loan: number; savings: number; dues: number };
  created_by: string;
  created_by_name: string;
  imported_by: string | null;
  imported_by_name: string | null;
  posted_by: string | null;
  posted_by_name: string | null;
  signed_off_by: string | null;
  signed_off_by_name: string | null;
  created_at: string;
  imported_at: string | null;
  posted_at: string | null;
  signed_off_at: string | null;
  exceptions: PayrollRunException[];
};

export type PayrollDryRunRow = {
  row: number;
  staff_no_raw: string;
  member_id: string | null;
  actual_kobo: number;
  classification: string;
};

export type PayrollImportError = { row: number; error: string };

export type PayrollDryRunResponse = {
  file_sha256: string;
  file_total_kobo: number;
  rows: PayrollDryRunRow[];
  errors: PayrollImportError[];
};

export type PayrollImportResponse = {
  file_sha256: string;
  file_total_kobo: number;
  errors: PayrollImportError[];
};

export type PayrollPostResponse = { posted: number };
export type PayrollSignOffResponse = { csv_base64: string; json_base64: string };

export type PaymentListItem = {
  id: string;
  member_id: string;
  member_no: string;
  member_name: string;
  loan_id: string | null;
  loan_name: string | null;
  purpose: string;
  amount_kobo: number;
  channel: string;
  status: string;
  recorded_by: string;
  recorded_by_name: string;
  rejected_reason: string | null;
  recorded_at: string;
  received_on: string;
  receipt_no: number | null;
};
export type PaymentRecordBody = {
  member_id: string;
  channel: "cash" | "bank_transfer";
  purpose: "savings" | "shares" | "dues" | "loan_repayment";
  loan_id: string | null;
  amount_kobo: number;
  provider_reference: string | null;
  received_on: string;
  fee_kobo: number;
  fee_bearer: "member";
  reason: string | null;
};
export type PaymentRecordResponse = {
  id: string;
  status: string;
  receipt_no: number | null;
};
export type PaymentDecisionBody = { reason?: string | null };
export type PaymentDecisionResponse = {
  id: string;
  status: string;
  ledger_entry_id: string | null;
  receipt_no: number | null;
};
export type PaymentDetail = {
  id: string;
  member_id: string;
  member_no: string;
  member_name: string;
  loan_id: string | null;
  channel: string;
  provider_reference: string | null;
  purpose: string;
  amount_kobo: number;
  status: string;
  recorded_by: string;
  recorded_by_name: string;
  recorded_at: string;
  received_on: string;
  confirmed_by: string | null;
  confirmed_at: string | null;
  rejected_reason: string | null;
  receipt_no: number | null;
};
export type CashReceipt = {
  id: string;
  member_id: string;
  member_no: string;
  member_name: string;
  receipt_no: number | null;
  purpose: string;
  amount_kobo: number;
  received_on: string;
  recorded_at: string;
  recorded_by: string;
  recorded_by_name: string;
  cash_banking_days: number;
  age_days: number;
  overdue: boolean;
};
export type CashOfficerBalance = {
  user_id: string;
  display_name: string;
  amount_kobo: number;
  oldest_age_days: number;
};
export type CashOnHandSummary = {
  ledger_balance_kobo: number;
  officers: CashOfficerBalance[];
  unallocated_kobo: number;
};
export type CashDepositBody = {
  slip_reference: string;
  deposited_on: string;
  payment_ids: string[];
};
export type CashDepositResponse = {
  id: string;
  status: string;
  slip_reference: string;
  deposited_on: string;
  amount_kobo: number;
  payment_ids: string[];
  ledger_entry_id: string;
};
export type PaymentsListPage = {
  items: PaymentListItem[];
  total: number;
  page: number;
  page_size: number;
};

export type NotificationSummary = {
  payroll?: { awaiting_posting?: number; awaiting_sign_off?: number };
  payments?: { awaiting_confirmation?: number; awaiting_deposit?: number };
  disbursements?: {
    awaiting_authorization?: number;
    awaiting_confirmation?: number;
    ready_to_record?: number;
  };
  role_changes?: { awaiting_approval?: number };
  loans?: { awaiting_decision?: number; disputed_or_paused?: number };
  withdrawal_requests?: { awaiting_approval?: number };
  settings?: { policy_approval?: number; product_approval?: number };
};

export type StaffRole = "officer" | "committee" | "treasurer" | "admin" | "auditor";
export type StaffRecord = {
  user_id: string;
  email: string;
  full_name: string | null;
  status: "active" | "disabled";
  roles: StaffRole[];
  created_at: string;
};
export type StaffPage = {
  items: StaffRecord[];
  page: number;
  page_size: number;
  total: number;
};

export type RoleChangeProposal = {
  proposal_id: string;
  target_user_id: string;
  target_email: string | null;
  proposer_user_id: string;
  proposer_email: string | null;
  proposer_display_name: string;
  decided_by_display_name: string | null;
  role: StaffRole;
  action: "add" | "remove";
  reason: string;
  status: "pending" | "approved" | "rejected" | "cancelled" | "expired";
  created_at: string;
  expires_at: string;
  decided_at: string | null;
  decided_by: string | null;
};
export type RoleChangeProposalPage = {
  items: RoleChangeProposal[];
  page: number;
  page_size: number;
  total: number;
};

export interface paths {
  "/notifications/summary": {
    get: {
      responses: { 200: { content: { "application/json": NotificationSummary } } };
    };
  };
  "/payments": {
    get: {
      parameters?: {
        query?: {
          status?: string;
          channel?: string;
          purpose?: string;
          member?: string;
          mine?: boolean;
          action?: "needed";
          page?: number;
          page_size?: number;
        };
      };
      responses: { 200: { content: { "application/json": PaymentsListPage } } };
    };
    post: {
      parameters: { header: { "Idempotency-Key": string } };
      requestBody: { content: { "application/json": PaymentRecordBody } };
      responses: { 200: { content: { "application/json": PaymentRecordResponse } } };
    };
  };
  "/payments/{payment_id}": {
    get: { responses: { 200: { content: { "application/json": PaymentDetail } } } };
  };
  "/payments/cash-awaiting-deposit": {
    get: { responses: { 200: { content: { "application/json": CashReceipt[] } } } };
  };
  "/payments/cash-on-hand": {
    get: { responses: { 200: { content: { "application/json": CashOnHandSummary } } } };
  };
  "/payments/cash-deposits": {
    post: {
      parameters: { header: { "Idempotency-Key": string } };
      requestBody: { content: { "application/json": CashDepositBody } };
      responses: { 200: { content: { "application/json": CashDepositResponse } } };
    };
  };
  "/auth/csrf": Record<string, never>;
  "/auth/login": Record<string, never>;
  "/auth/mfa/enroll": Record<string, never>;
  "/auth/mfa/verify": Record<string, never>;
  "/auth/me": Record<string, never>;
  "/auth/logout": Record<string, never>;
  "/staff": {
    get: {
      parameters?: { query?: { page?: number; page_size?: number } };
      responses: { 200: { content: { "application/json": StaffPage } } };
    };
  };
  "/staff/{user_id}": {
    patch: {
      parameters: { path: { user_id: string } };
      requestBody: { content: { "application/json": { full_name: string | null } } };
      responses: {
        200: { content: { "application/json": { user_id: string; full_name: string | null } } };
      };
    };
  };
  "/role-change-proposals": {
    get: {
      parameters?: {
        query?: {
          status?: "all" | "pending" | "approved" | "rejected" | "cancelled" | "expired";
          page?: number;
          page_size?: number;
        };
      };
      responses: { 200: { content: { "application/json": RoleChangeProposalPage } } };
    };
  };
  "/payroll/runs": {
    get: {
      parameters?: { query?: { page?: number; page_size?: number } };
      responses: { 200: { content: { "application/json": PayrollRunPage } } };
    };
  };
  "/payroll/runs/{run_id}": {
    get: {
      parameters: { path: { run_id: string } };
      responses: { 200: { content: { "application/json": PayrollRunDetail } } };
    };
  };
  "/payroll/runs/{run_id}/dry-run": {
    post: {
      parameters: { path: { run_id: string } };
      requestBody: { content: { "text/csv": Blob } };
      responses: { 200: { content: { "application/json": PayrollDryRunResponse } } };
    };
  };
  "/payroll/runs/{run_id}/import": {
    post: {
      parameters: { path: { run_id: string } };
      requestBody: { content: { "text/csv": Blob } };
      responses: { 200: { content: { "application/json": PayrollImportResponse } } };
    };
  };
  "/payroll/runs/{run_id}/post": {
    post: {
      parameters: { path: { run_id: string } };
      responses: { 200: { content: { "application/json": PayrollPostResponse } } };
    };
  };
  "/payroll/runs/{run_id}/sign-off": {
    post: {
      parameters: { path: { run_id: string } };
      requestBody: {
        content: { "application/json": { bank_receipt_total_kobo: number } };
      };
      responses: { 200: { content: { "application/json": PayrollSignOffResponse } } };
    };
  };
  "/payroll/runs/{run_id}/items/{item_id}/resolution": {
    post: {
      parameters: {
        path: { run_id: string; item_id: string };
        header: { "Idempotency-Key": string };
      };
      requestBody: { content: { "application/json": { note: string } } };
      responses: { 200: { content: { "application/json": PayrollResolutionResponse } } };
    };
  };
  "/members": {
    get: {
      parameters?: { query?: { search?: string } };
      responses: { 200: { content: { "application/json": MemberListItem[] } } };
    };
  };
  "/members/{member_id}": {
    get: {
      parameters: { path: { member_id: string } };
      responses: { 200: { content: { "application/json": MemberDetail } } };
    };
  };
  "/members/{member_id}/statement": {
    get: {
      parameters?: { path: { member_id: string }; query?: { page?: number; page_size?: number } };
      responses: { 200: { content: { "application/json": MemberStatementPage } } };
    };
  };
  "/members/{member_id}/loans": {
    get: {
      parameters?: { path: { member_id: string }; query?: { page?: number; page_size?: number } };
      responses: { 200: { content: { "application/json": MemberLoanPage } } };
    };
  };
  "/members/{member_id}/activity": {
    get: {
      parameters?: { path: { member_id: string }; query?: { page?: number; page_size?: number } };
      responses: { 200: { content: { "application/json": MemberActivityPage } } };
    };
  };
  "/loan-applications": {
    get: {
      parameters?: {
        query?: { status?: string; search?: string; page?: number; page_size?: number };
      };
      responses: { 200: { content: { "application/json": LoanApplicationPage } } };
    };
    post: Record<string, never>;
  };
  "/loan-applications/{application_id}": {
    get: {
      parameters: { path: { application_id: string } };
      responses: { 200: { content: { "application/json": LoanApplicationDetail } } };
    };
  };
  "/loan-products": {
    get: { responses: { 200: { content: { "application/json": LoanProduct[] } } } };
  };
  "/loan-applications/{application_id}/decision": { post: Record<string, never> };
  "/disbursements": {
    get: {
      parameters?: {
        query?: { status?: string; search?: string; page?: number; page_size?: number };
      };
      responses: { 200: { content: { "application/json": DisbursementPage } } };
    };
  };
  "/disbursements/ready-loans": {
    get: {
      parameters?: { query?: { search?: string; page?: number; page_size?: number } };
      responses: { 200: { content: { "application/json": ReadyDisbursementLoanPage } } };
    };
  };
  "/disbursements/{disbursement_id}": {
    get: {
      parameters: { path: { disbursement_id: string } };
      responses: { 200: { content: { "application/json": DisbursementRecord } } };
    };
  };
  "/loans/{loan_id}/disbursements": {
    post: { responses: { 200: { content: { "application/json": { disbursement_id: string } } } } };
  };
  "/disbursements/{disbursement_id}/authorize": {
    post: { responses: { 200: { content: { "application/json": { authorized: boolean } } } } };
  };
  "/disbursements/{disbursement_id}/transfer": {
    post: {
      requestBody: {
        content: { "application/json": { reference: string; transfer_date: string } };
      };
      responses: { 200: { content: { "application/json": { recorded: boolean } } } };
    };
  };
  "/disbursements/{disbursement_id}/confirm-paid": {
    post: { responses: { 200: { content: { "application/json": { journal_entry_id: string } } } } };
  };
  "/disbursements/{disbursement_id}/failed": {
    post: {
      requestBody: { content: { "application/json": DisbursementActionBody } };
      responses: { 200: { content: { "application/json": { failed: boolean } } } };
    };
  };
  "/disbursements/{disbursement_id}/reverse": {
    post: {
      requestBody: { content: { "application/json": DisbursementActionBody } };
      responses: { 200: { content: { "application/json": { journal_entry_id: string } } } };
    };
  };
}
