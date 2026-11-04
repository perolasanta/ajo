from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.core.db import transaction
from app.domains.auth.service import CurrentSession

_ACTION_COUNTS_SQL = text(
    """
    SELECT
      CASE WHEN :can_payroll_post THEN (
        SELECT count(*)::int
        FROM payroll_runs r
        WHERE r.cooperative_id = :cooperative_id
          AND r.status = 'imported'
          AND r.expected_total_kobo = (
            SELECT coalesce(
              sum(pi.expected_loan_kobo + pi.expected_savings_kobo + pi.expected_dues_kobo)
                FILTER (WHERE pi.classification NOT IN ('duplicate', 'unknown_member')),
              0
            )
            FROM payroll_items pi
            WHERE pi.cooperative_id = r.cooperative_id AND pi.run_id = r.id
          )
          AND r.file_total_kobo = (
            SELECT coalesce(sum(pi.actual_kobo), 0)
            FROM payroll_items pi
            WHERE pi.cooperative_id = r.cooperative_id AND pi.run_id = r.id
          )
      ) ELSE 0 END AS payroll_awaiting_posting,
      CASE WHEN :can_payroll_sign_off THEN (
        SELECT count(*)::int
        FROM payroll_runs r
        WHERE r.cooperative_id = :cooperative_id
          AND r.status = 'posted'
          AND coalesce(r.posted_by, r.imported_by) IS DISTINCT FROM :user_id
          AND NOT EXISTS (
            SELECT 1
            FROM payroll_items pi
            WHERE pi.cooperative_id = r.cooperative_id
              AND pi.run_id = r.id
              AND pi.classification <> 'matched'
              AND pi.resolution_note IS NULL
          )
      ) ELSE 0 END AS payroll_awaiting_sign_off,
      CASE WHEN :can_disbursement_authorize THEN (
        SELECT count(*)::int
        FROM disbursements d
        JOIN loans l
          ON l.cooperative_id = d.cooperative_id AND l.id = d.loan_id
        JOIN loan_applications a
          ON a.cooperative_id = l.cooperative_id AND a.id = l.application_id
        WHERE d.cooperative_id = :cooperative_id
          AND d.status = 'pending_authorization'
          AND d.prepared_by IS DISTINCT FROM :user_id
          AND a.submitted_by IS DISTINCT FROM :user_id
          AND NOT EXISTS (
            SELECT 1
            FROM approvals ap
            WHERE ap.cooperative_id = a.cooperative_id
              AND ap.application_id = a.id
              AND ap.approver_id = :user_id
          )
      ) ELSE 0 END AS disbursement_awaiting_authorization,
      CASE WHEN :can_disbursement_confirm THEN (
        SELECT count(*)::int
        FROM disbursements d
        JOIN loans l
          ON l.cooperative_id = d.cooperative_id AND l.id = d.loan_id
        JOIN loan_applications a
          ON a.cooperative_id = l.cooperative_id AND a.id = l.application_id
        WHERE d.cooperative_id = :cooperative_id
          AND d.status = 'transfer_recorded'
          AND d.prepared_by IS DISTINCT FROM :user_id
          AND d.authorized_by IS DISTINCT FROM :user_id
          AND a.submitted_by IS DISTINCT FROM :user_id
          AND NOT EXISTS (
            SELECT 1
            FROM approvals ap
            WHERE ap.cooperative_id = a.cooperative_id
              AND ap.application_id = a.id
              AND ap.approver_id = :user_id
          )
      ) ELSE 0 END AS disbursement_awaiting_confirmation,
      CASE WHEN :can_disbursement_record THEN (
        SELECT count(*)::int
        FROM disbursements d
        WHERE d.cooperative_id = :cooperative_id
          AND d.status = 'authorized'
          AND d.prepared_by IS DISTINCT FROM :user_id
          AND d.authorized_by IS DISTINCT FROM :user_id
      ) ELSE 0 END AS disbursement_ready_to_record,
      CASE WHEN :can_approve_role_changes THEN (
        SELECT count(*)::int
        FROM role_change_proposals p
        JOIN users target
          ON target.cooperative_id = p.cooperative_id AND target.id = p.target_user_id
        WHERE p.cooperative_id = :cooperative_id
          AND p.status = 'pending'
          AND p.expires_at > now()
          AND p.proposer_user_id IS DISTINCT FROM :user_id
          AND p.target_user_id IS DISTINCT FROM :user_id
          AND (
            (p.action = 'add' AND NOT EXISTS (
              SELECT 1 FROM user_roles ur
              WHERE ur.cooperative_id = p.cooperative_id
                AND ur.user_id = p.target_user_id AND ur.role = p.role
            ))
            OR
            (p.action = 'remove' AND EXISTS (
              SELECT 1 FROM user_roles ur
              WHERE ur.cooperative_id = p.cooperative_id
                AND ur.user_id = p.target_user_id AND ur.role = p.role
            ))
          )
          AND (
            p.action <> 'remove' OR p.role <> 'admin' OR (
              SELECT count(*)
              FROM users active_admin
              JOIN user_roles admin_role
                ON admin_role.cooperative_id = active_admin.cooperative_id
                AND admin_role.user_id = active_admin.id
              WHERE active_admin.cooperative_id = p.cooperative_id
                AND active_admin.status = 'active'
                AND admin_role.role = 'admin'
            ) > 1
          )
      ) ELSE 0 END AS role_changes_awaiting_approval,
      CASE WHEN :can_loan_decide THEN (
        SELECT count(*)::int
        FROM loan_applications a
        WHERE a.cooperative_id = :cooperative_id
          AND a.status = 'under_review'
          AND a.submitted_by IS DISTINCT FROM :user_id
      ) ELSE 0 END AS loans_awaiting_decision,
      CASE WHEN :can_repay_disputed_loans THEN (
        SELECT count(*)::int
        FROM loans l
        WHERE l.cooperative_id = :cooperative_id
          AND (
            l.status = 'disputed'
            OR (l.status = 'repaying' AND coalesce(l.deductions_paused, false))
          )
      ) ELSE 0 END AS disputed_or_paused_loans
    """
)


async def get_action_counts(current: CurrentSession) -> dict[str, dict[str, int]]:
    roles = set(current.roles)
    params: dict[str, Any] = {
        "cooperative_id": current.cooperative_id,
        "user_id": current.user_id,
        "can_payroll_post": "treasurer" in roles,
        "can_payroll_sign_off": "admin" in roles,
        "can_disbursement_authorize": "admin" in roles,
        "can_disbursement_confirm": "admin" in roles,
        "can_disbursement_record": "treasurer" in roles,
        "can_approve_role_changes": "admin" in roles,
        "can_loan_decide": "committee" in roles,
        "can_repay_disputed_loans": "treasurer" in roles,
    }
    async with transaction(current.cooperative_id) as session:
        row = (await session.execute(_ACTION_COUNTS_SQL, params)).mappings().one()

    summary: dict[str, dict[str, int]] = {}
    if "treasurer" in roles:
        summary["payroll"] = {"awaiting_posting": int(row["payroll_awaiting_posting"])}
        summary["disbursements"] = {"ready_to_record": int(row["disbursement_ready_to_record"])}
        summary["loans"] = {"disputed_or_paused": int(row["disputed_or_paused_loans"])}
    if "admin" in roles:
        summary.setdefault("payroll", {})["awaiting_sign_off"] = int(
            row["payroll_awaiting_sign_off"]
        )
        disbursements = summary.setdefault("disbursements", {})
        disbursements["awaiting_authorization"] = int(row["disbursement_awaiting_authorization"])
        disbursements["awaiting_confirmation"] = int(row["disbursement_awaiting_confirmation"])
        summary["role_changes"] = {"awaiting_approval": int(row["role_changes_awaiting_approval"])}
    if "committee" in roles:
        summary.setdefault("loans", {})["awaiting_decision"] = int(row["loans_awaiting_decision"])
    return summary
