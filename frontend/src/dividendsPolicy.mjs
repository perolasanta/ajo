/**
 * Dividend run role-gating, member-label, and request-body helpers.
 *
 * Roles are the staff role strings ("officer" | "committee" | "treasurer" |
 * "admin" | "auditor"). Money is integer kobo. These are pure functions so they
 * can be unit-tested with `node --test` without any React or network.
 */

/**
 * Treasurer prepares a dividend run. Admins are not offered the prepare form in
 * the UI (the backend permits it, but the separation of duties keeps prepare as
 * the treasurer's job).
 * @param {string[]} roles
 */
export function canPrepareDividendRun(roles) {
  return Array.isArray(roles) && roles.includes("treasurer");
}

/**
 * An admin may approve a run only while it is still `unprocessed` and only if
 * they did not prepare it themselves (maker-checker: the preparer never approves
 * their own run).
 * @param {string[]} roles
 * @param {string} status
 * @param {string | null | undefined} preparedBy
 * @param {string} currentUserId
 */
export function canApproveDividendRun(roles, status, preparedBy, currentUserId) {
  return (
    Array.isArray(roles) &&
    roles.includes("admin") &&
    status === "unprocessed" &&
    preparedBy !== currentUserId
  );
}

/** Reject uses the same gate as approve (and requires a reason in the UI). */
export function canRejectDividendRun(roles, status, preparedBy, currentUserId) {
  return canApproveDividendRun(roles, status, preparedBy, currentUserId);
}

/**
 * Only the preparer may cancel a run, and only while it is still `unprocessed`.
 * @param {string[]} roles
 * @param {string} status
 * @param {string | null | undefined} preparedBy
 * @param {string} currentUserId
 */
export function canCancelDividendRun(roles, status, preparedBy, currentUserId) {
  return status === "unprocessed" && preparedBy === currentUserId;
}

/**
 * Member display label: NAME ONLY (never email or member number). An existing
 * member with no name shows "Unnamed user"; a missing member record shows
 * "Unknown user".
 * @param {{ full_name?: string | null } | null | undefined} member
 */
export function memberDisplayLabel(member) {
  if (!member) return "Unknown user";
  const name = (member.full_name ?? "").trim();
  return name ? name : "Unnamed user";
}

/**
 * Build the prepare request body from naira input, converting to integer kobo.
 * Carries only the API-allowed fields — never member names or per-member amounts.
 * @param {{ recordDate: string, declaredByMeetingId: string, poolNaira: number }} args
 */
export function prepareDividendRequestBody({ recordDate, declaredByMeetingId, poolNaira }) {
  return {
    record_date: recordDate,
    declared_by_meeting_id: declaredByMeetingId,
    pool_kobo: Math.round((Number(poolNaira) || 0) * 100),
  };
}

/** Build the reject request body — only the reason, never names or amounts. */
export function rejectDividendRequestBody(reason) {
  return { reason: String(reason ?? "") };
}
