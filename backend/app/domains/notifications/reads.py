from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.core.db import transaction
from app.domains.auth.service import CurrentSession
from app.domains.notifications import eligibility

_OTHER_ACTION_COUNTS_SQL = text(
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
      CASE WHEN :can_loan_decide THEN (
        SELECT count(*)::int
        FROM loan_applications a
        WHERE a.cooperative_id = :cooperative_id
          AND a.status = 'under_review'
          AND a.submitted_by IS DISTINCT FROM :user_id
      ) ELSE 0 END AS loans_awaiting_decision
    """
)


async def get_action_counts(current: CurrentSession) -> dict[str, dict[str, int]]:
    roles = set(current.roles)
    params: dict[str, Any] = {
        "cooperative_id": current.cooperative_id,
        "user_id": current.user_id,
        "can_payroll_post": "treasurer" in roles,
        "can_disbursement_confirm": "admin" in roles,
        "can_disbursement_record": "treasurer" in roles,
        "can_loan_decide": "committee" in roles,
    }
    async with transaction(current.cooperative_id) as session:
        row = (await session.execute(_OTHER_ACTION_COUNTS_SQL, params)).mappings().one()
        action_counts: dict[str, int] = {}
        for event_type in eligibility.ACTION_SPECS:
            candidates = await eligibility.get_action_candidates(
                session,
                current.cooperative_id,
                event_type,
                include_paused_disputed=(event_type == eligibility.LOAN_DISPUTED),
            )
            count = 0
            for candidate in candidates:
                eligible_users = eligibility.eligible_recipients_for_action(
                    event_type,
                    excluded_user_ids=set(candidate["excluded_user_ids"] or ()),
                    roles_by_user={current.user_id: roles},
                )
                count += current.user_id in eligible_users
            action_counts[event_type] = count

    summary: dict[str, dict[str, int]] = {}
    if "treasurer" in roles:
        summary["payroll"] = {"awaiting_posting": int(row["payroll_awaiting_posting"])}
        summary["disbursements"] = {"ready_to_record": int(row["disbursement_ready_to_record"])}
        summary["loans"] = {"disputed_or_paused": action_counts[eligibility.LOAN_DISPUTED]}
        summary["members"] = {
            "withdrawals_awaiting_approval": action_counts[eligibility.WITHDRAWAL_APPROVAL]
        }
    if "admin" in roles:
        summary.setdefault("payroll", {})["awaiting_sign_off"] = action_counts[
            eligibility.PAYROLL_SIGN_OFF
        ]
        disbursements = summary.setdefault("disbursements", {})
        disbursements["awaiting_authorization"] = action_counts[
            eligibility.DISBURSEMENT_AUTHORIZATION
        ]
        disbursements["awaiting_confirmation"] = int(row["disbursement_awaiting_confirmation"])
        summary["role_changes"] = {
            "awaiting_approval": action_counts[eligibility.ROLE_CHANGE_APPROVAL]
        }
    if "committee" in roles:
        summary.setdefault("loans", {})["awaiting_decision"] = int(row["loans_awaiting_decision"])
    return summary
