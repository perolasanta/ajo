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
};

export type MemberBalances = {
  savings_kobo: number;
  shares_kobo: number;
  dues_paid_kobo: number;
  locked_kobo: number;
  available_kobo: number;
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

export interface paths {
  "/auth/csrf": Record<string, never>;
  "/auth/login": Record<string, never>;
  "/auth/mfa/enroll": Record<string, never>;
  "/auth/mfa/verify": Record<string, never>;
  "/auth/me": Record<string, never>;
  "/auth/logout": Record<string, never>;
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
}
