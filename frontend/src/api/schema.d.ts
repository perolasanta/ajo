/* Temporary local contract. Replaced by `npm run gen:api` from the running API. */
export type MemberListItem = {
  id: string;
  member_no: string;
  full_name: string;
  phone_e164: string | null;
  email: string | null;
  staff_no?: string | null;
  status: string;
  joined_on: string;
};

export type MemberBalances = {
  savings_kobo: number;
  shares_kobo: number;
  dues_paid_kobo: number;
  locked_kobo: number;
  available_kobo: number;
};

export type MemberDetail = MemberListItem & {
  staff_no: string | null;
  employer: string | null;
  balances: MemberBalances;
  bank_accounts: Array<{
    bank_code: string;
    last4: string;
    account_name: string;
    verified_at?: string | null;
    status: string;
  }>;
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
}
