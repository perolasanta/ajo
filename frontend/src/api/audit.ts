export type AuditSafeSummary = Partial<{
  status:
    | "active"
    | "applicant"
    | "approved"
    | "approved_awaiting_final"
    | "authorized"
    | "awaiting_applicant_confirmation"
    | "awaiting_borrower_final_confirmation"
    | "cancelled"
    | "closed"
    | "disbursed"
    | "disputed"
    | "disabled"
    | "expired"
    | "failed"
    | "missing"
    | "over"
    | "paid"
    | "pending"
    | "proposed"
    | "ready_for_disbursement"
    | "repaying"
    | "rejected"
    | "reversed"
    | "short"
    | "superseded"
    | "suspended"
    | "transfer_recorded"
    | "unknown_member"
    | "verified"
    | "withdrawn"
    | null;
  decision: "approved" | "confirm" | "dispute" | "rejected" | null;
  old_role: "officer" | "committee" | "treasurer" | "admin" | "auditor" | null;
  new_role: "officer" | "committee" | "treasurer" | "admin" | "auditor" | null;
  count: number | null;
}>;

export type AuditEvent = {
  event_id: string;
  timestamp: string;
  actor_id: string | null;
  actor_role: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  safe_summary: AuditSafeSummary;
};

export type AuditEventPage = {
  items: AuditEvent[];
  page: number;
  page_size: number;
  total: number;
};

export type AuditVerification = {
  ok: boolean;
  entries_checked: number;
  first_broken_position: number | null;
};
