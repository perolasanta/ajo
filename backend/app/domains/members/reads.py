"""Read-only projections for member detail tabs."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from app.core.db import transaction
from app.core.errors import Forbidden
from app.domains.audit.reads import audit_projection
from app.domains.auth.service import CurrentSession

TAB_ROLES = {
    "overview": frozenset({"admin", "officer", "treasurer", "committee", "auditor"}),
    "statement": frozenset({"admin", "officer", "treasurer", "auditor"}),
    "loans": frozenset({"admin", "officer", "treasurer", "committee", "auditor"}),
    "activity": frozenset({"admin", "auditor"}),
}
PUBLIC_TAB_FIELDS = frozenset(
    {
        "id",
        "member_no",
        "full_name",
        "status",
        "joined_on",
        "date",
        "type",
        "amount_kobo",
        "running_balance_kobo",
        "application_id",
        "loan_id",
        "principal_kobo",
        "outstanding_balance_kobo",
        "next_installment_at",
        "next_installment_amount_kobo",
        "event_id",
        "timestamp",
        "actor_id",
        "actor_display_name",
        "actor_role",
        "action",
        "entity_type",
        "entity_id",
        "safe_summary",
    }
)

STATEMENT_QUERY = text(
    """
    WITH entries AS (
      SELECT e.id, e.effective_date, e.posted_at, e.source_type, e.reverses_entry_id,
             SUM(CASE WHEN a.code <> 'bank' THEN l.credit_kobo - l.debit_kobo ELSE 0 END)::bigint
               AS amount_kobo
        FROM journal_entries e
        JOIN journal_lines l ON l.cooperative_id=e.cooperative_id AND l.entry_id=e.id
        JOIN accounts a ON a.cooperative_id=l.cooperative_id AND a.id=l.account_id
        JOIN members m ON m.cooperative_id=l.cooperative_id AND m.id=l.member_id
       WHERE e.cooperative_id=:cid AND l.cooperative_id=:cid AND l.member_id=:mid
       GROUP BY e.id, e.effective_date, e.posted_at, e.source_type, e.reverses_entry_id
    ), with_balance AS (
      SELECT *, SUM(amount_kobo) OVER (
          ORDER BY effective_date, posted_at, id
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
      )::bigint AS running_balance_kobo,
      count(*) OVER ()::int AS total
      FROM entries
    )
    SELECT id, effective_date AS date,
           CASE
             WHEN reverses_entry_id IS NOT NULL OR source_type='reversal' THEN 'reversal'
             WHEN source_type IN ('contribution','member_contribution') THEN 'contribution'
             WHEN source_type IN ('payroll','payroll_deduction') THEN 'payroll deduction'
             WHEN source_type IN ('withdrawal','member_withdrawal') THEN 'withdrawal'
             WHEN source_type IN ('repayment','loan_repayment') THEN 'repayment'
             ELSE source_type
           END AS type,
           amount_kobo, running_balance_kobo, total
      FROM with_balance
     ORDER BY effective_date DESC, posted_at DESC, id DESC
     LIMIT :limit OFFSET :offset
    """
)

LOANS_QUERY = text(
    """
    SELECT a.id AS application_id, l.id AS loan_id, a.status AS application_status,
           l.status AS loan_status, a.principal_kobo,
           COALESCE(next_due.balance_kobo, 0)::bigint AS outstanding_balance_kobo,
           next_due.due_at AS next_installment_at,
           next_due.payment_kobo AS next_installment_amount_kobo,
           a.created_at AS submitted_at,
           count(*) over()::int AS total
      FROM loan_applications a
      JOIN members m ON m.cooperative_id=a.cooperative_id AND m.id=a.member_id
      LEFT JOIN loans l ON l.cooperative_id=a.cooperative_id AND l.application_id=a.id
      LEFT JOIN LATERAL (
        SELECT s.due_at, s.payment_kobo, s.balance_kobo
          FROM loan_schedule s
         WHERE s.cooperative_id=a.cooperative_id AND s.loan_id=l.id AND s.balance_kobo > 0
         ORDER BY s.due_at, s.installment_no
         LIMIT 1
      ) next_due ON true
     WHERE a.cooperative_id=:cid AND a.member_id=:mid
     ORDER BY a.created_at DESC, a.id DESC
     LIMIT :limit OFFSET :offset
    """
)

ACTIVITY_QUERY = text(
    """
    SELECT a.id, a.created_at, a.actor_user_id, a.role, a.action,
           a.entity_type, a.entity_id, a.summary,
           u.id AS joined_actor_id, u.full_name AS actor_full_name, u.email AS actor_email,
           count(*) OVER ()::int AS total
      FROM audit_logs a
      LEFT JOIN users u ON u.cooperative_id=a.cooperative_id AND u.id=a.actor_user_id
     WHERE a.cooperative_id=:cid
       AND EXISTS (SELECT 1 FROM members m
                    WHERE m.cooperative_id=a.cooperative_id AND m.id=CAST(:mid AS uuid))
       AND (
         a.entity_id=CAST(:mid AS text)
         OR a.summary->>'member_id'=CAST(:mid AS text)
       )
     ORDER BY a.created_at DESC, a.id DESC
     LIMIT :limit OFFSET :offset
    """
)


def tab_access(role: str) -> set[str]:
    return {tab for tab, roles in TAB_ROLES.items() if role in roles}


def require_tab(role: str, tab: str) -> None:
    if role not in TAB_ROLES.get(tab, frozenset()):
        raise Forbidden(f"Role is not permitted to view the member {tab} tab")


def validate_running_balances(rows: list[dict[str, Any]]) -> int:
    balance = 0
    for row in reversed(rows):
        balance += int(row["amount_kobo"])
        if int(row["running_balance_kobo"]) != balance:
            raise AssertionError("running balance does not equal ledger total")
    return int(rows[0]["running_balance_kobo"]) if rows else 0


def _page(rows: list[dict[str, Any]], page: int, page_size: int) -> dict[str, Any]:
    total = int(rows[0].pop("total", 0)) if rows else 0
    return {"items": rows, "page": page, "page_size": page_size, "total": total}


async def list_statement(
    current: CurrentSession, member_id: uuid.UUID, *, page: int, page_size: int
) -> dict[str, Any]:
    role = next((item for item in current.roles if item in TAB_ROLES["statement"]), "")
    require_tab(role, "statement")
    async with transaction(current.cooperative_id) as session:
        result = await session.execute(
            STATEMENT_QUERY,
            {
                "cid": current.cooperative_id,
                "mid": member_id,
                "limit": page_size,
                "offset": (page - 1) * page_size,
            },
        )
        return _page([dict(row) for row in result.mappings()], page, page_size)


async def list_loans(
    current: CurrentSession, member_id: uuid.UUID, *, page: int, page_size: int
) -> dict[str, Any]:
    role = next((item for item in current.roles if item in TAB_ROLES["loans"]), "")
    require_tab(role, "loans")
    async with transaction(current.cooperative_id) as session:
        result = await session.execute(
            LOANS_QUERY,
            {
                "cid": current.cooperative_id,
                "mid": member_id,
                "limit": page_size,
                "offset": (page - 1) * page_size,
            },
        )
        rows = [dict(row) for row in result.mappings()]
    if role in {"committee", "auditor"}:
        for row in rows:
            for field in (
                "loan_id",
                "outstanding_balance_kobo",
                "next_installment_at",
                "next_installment_amount_kobo",
            ):
                row.pop(field, None)
    return _page(rows, page, page_size)


async def list_activity(
    current: CurrentSession, member_id: uuid.UUID, *, page: int, page_size: int
) -> dict[str, Any]:
    role = next((item for item in current.roles if item in TAB_ROLES["activity"]), "")
    require_tab(role, "activity")
    async with transaction(current.cooperative_id) as session:
        result = await session.execute(
            ACTIVITY_QUERY,
            {
                "cid": current.cooperative_id,
                "mid": member_id,
                "limit": page_size,
                "offset": (page - 1) * page_size,
            },
        )
        rows = [audit_projection(dict(row)) | {"total": row["total"]} for row in result.mappings()]
    return _page(rows, page, page_size)
