export type AuditSafeSummary = Partial<{
  status: string | null;
  decision: string | null;
  old_role: string | null;
  new_role: string | null;
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
