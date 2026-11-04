"""Tenant-scoped, count-only dashboard summary reads."""

from __future__ import annotations

from sqlalchemy import text

from app.core.db import transaction
from app.core.errors import Forbidden
from app.domains.auth.service import CurrentSession

LOAN_STATUSES = (
    "under_review",
    "awaiting_applicant_confirmation",
    "approved_awaiting_final",
    "ready_for_disbursement",
    "repaying",
    "closed",
    "disputed",
)
DISBURSEMENT_STATUSES = (
    "pending_authorization",
    "authorized",
    "transfer_recorded",
    "paid",
    "failed",
    "cancelled",
)
LOAN_ROLES = frozenset({"admin", "officer", "treasurer", "auditor"})
DASHBOARD_ROLES = LOAN_ROLES | frozenset({"committee"})

SUMMARY_QUERY = text(
    """
    SELECT 'loans' AS source, a.status, count(*)::int AS total
      FROM loan_applications AS a
      JOIN members AS m
        ON m.cooperative_id = a.cooperative_id AND m.id = a.member_id
     WHERE a.cooperative_id = :cooperative_id
       AND a.status = ANY(CAST(:loan_statuses AS text[]))
     GROUP BY a.status
    UNION ALL
    SELECT 'disbursements' AS source, d.status, count(*)::int AS total
      FROM disbursements AS d
      JOIN loans AS l
        ON l.cooperative_id = d.cooperative_id AND l.id = d.loan_id
      JOIN members AS m
        ON m.cooperative_id = l.cooperative_id AND m.id = l.member_id
     WHERE d.cooperative_id = :cooperative_id
       AND d.status = ANY(CAST(:disbursement_statuses AS text[]))
     GROUP BY d.status
    """
)


def _dashboard_statuses(current: CurrentSession) -> tuple[tuple[str, ...], tuple[str, ...]]:
    roles = set(current.roles)
    if not roles.intersection(DASHBOARD_ROLES):
        raise Forbidden("Staff role required")

    loan_statuses: tuple[str, ...]
    if roles.intersection(LOAN_ROLES):
        loan_statuses = LOAN_STATUSES
    elif "committee" in roles:
        loan_statuses = ("under_review",)
    else:
        loan_statuses = ()

    disbursement_statuses: tuple[str, ...]
    if "admin" in roles:
        disbursement_statuses = ("pending_authorization", "transfer_recorded")
    elif "treasurer" in roles:
        disbursement_statuses = ("authorized",)
    elif "auditor" in roles:
        disbursement_statuses = DISBURSEMENT_STATUSES
    else:
        disbursement_statuses = ()
    return loan_statuses, disbursement_statuses


async def get_dashboard_summary(current: CurrentSession) -> dict[str, dict[str, int]]:
    loan_statuses, disbursement_statuses = _dashboard_statuses(current)
    summary: dict[str, dict[str, int]] = {"loans": {}, "disbursements": {}}
    async with transaction(current.cooperative_id) as session:
        result = await session.execute(
            SUMMARY_QUERY,
            {
                "cooperative_id": current.cooperative_id,
                "loan_statuses": list(loan_statuses),
                "disbursement_statuses": list(disbursement_statuses),
            },
        )
        for row in result.mappings().all():
            source = str(row["source"])
            if source in summary:
                summary[source][str(row["status"])] = int(row["total"])
    return summary
