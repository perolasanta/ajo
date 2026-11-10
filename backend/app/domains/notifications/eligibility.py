"""Shared action eligibility, entity candidates, badge counts, and notification recipients."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PAYROLL_POSTING = "payroll_posting"
PAYROLL_SIGN_OFF = "payroll_sign_off"
PAYMENT_CONFIRMATION = "payment_confirmation"
CASH_DEPOSIT = "cash_deposit"
DISBURSEMENT_RECORD = "disbursement_record"
DISBURSEMENT_AUTHORIZATION = "disbursement_authorization"
DISBURSEMENT_CONFIRMATION = "disbursement_confirmation"
ROLE_CHANGE_APPROVAL = "role_change_approval"
LOAN_DECISION = "loan_decision"
LOAN_DISPUTED = "loan_disputed"
WITHDRAWAL_APPROVAL = "withdrawal_approval"


@dataclass(frozen=True)
class ActionSpec:
    roles: frozenset[str]
    section: str
    section_label: str
    action_kind: str
    sentence: str
    path: str


@dataclass(frozen=True)
class ActionRecipient:
    event_type: str
    entity_id: uuid.UUID
    user_id: uuid.UUID


ACTION_SPECS = {
    PAYROLL_POSTING: ActionSpec(
        frozenset({"treasurer"}),
        "payroll",
        "Payroll",
        "payroll posting",
        "A payroll run needs your posting.",
        "/payroll/runs/{id}",
    ),
    PAYROLL_SIGN_OFF: ActionSpec(
        frozenset({"admin"}),
        "payroll",
        "Payroll",
        "payroll sign-off",
        "A payroll run needs your sign-off.",
        "/payroll/runs/{id}",
    ),
    PAYMENT_CONFIRMATION: ActionSpec(
        frozenset({"officer", "treasurer"}),
        "payments",
        "Payments",
        "payment confirmation",
        "A payment needs your confirmation",
        "/payments/{id}",
    ),
    CASH_DEPOSIT: ActionSpec(
        frozenset({"treasurer"}),
        "payments",
        "Payments",
        "cash deposit",
        "Cash needs to be banked",
        "/payments/{id}",
    ),
    DISBURSEMENT_RECORD: ActionSpec(
        frozenset({"treasurer"}),
        "disbursements",
        "Disbursements",
        "disbursement transfer recording",
        "A disbursement needs its transfer recorded.",
        "/disbursements/{id}",
    ),
    DISBURSEMENT_AUTHORIZATION: ActionSpec(
        frozenset({"admin"}),
        "disbursements",
        "Disbursements",
        "disbursement authorization",
        "A disbursement needs your authorization.",
        "/disbursements/{id}",
    ),
    DISBURSEMENT_CONFIRMATION: ActionSpec(
        frozenset({"admin"}),
        "disbursements",
        "Disbursements",
        "disbursement confirmation",
        "A disbursement needs your confirmation.",
        "/disbursements/{id}",
    ),
    ROLE_CHANGE_APPROVAL: ActionSpec(
        frozenset({"admin"}),
        "staff",
        "Staff",
        "role-change approval",
        "A role change needs your approval.",
        "/staff?proposal={id}",
    ),
    LOAN_DECISION: ActionSpec(
        frozenset({"committee"}),
        "loans",
        "Loans",
        "loan decision",
        "A loan application needs your decision.",
        "/loans/{id}",
    ),
    LOAN_DISPUTED: ActionSpec(
        frozenset({"treasurer"}),
        "loans",
        "Loans",
        "disputed loan repayment",
        "A disputed or paused loan needs manual repayment.",
        "/loans/{id}",
    ),
    WITHDRAWAL_APPROVAL: ActionSpec(
        frozenset({"treasurer"}),
        "withdrawal_requests",
        "Withdrawal requests",
        "withdrawal approval",
        "A withdrawal request needs your approval.",
        "/members/{member_id}?withdrawal_request={id}",
    ),
}

_CANDIDATE_SQL = {
    PAYROLL_POSTING: """
        SELECT r.id AS entity_id, array_remove(ARRAY[r.imported_by]::uuid[], NULL) AS excluded_user_ids
        FROM payroll_runs r
        WHERE r.cooperative_id=:cooperative_id AND r.status='imported'
          AND (CAST(:entity_id AS uuid) IS NULL OR r.id=CAST(:entity_id AS uuid))
          AND r.expected_total_kobo=(SELECT coalesce(sum(pi.expected_loan_kobo+pi.expected_savings_kobo+pi.expected_dues_kobo) FILTER (WHERE pi.classification NOT IN ('duplicate','unknown_member')),0) FROM payroll_items pi WHERE pi.cooperative_id=r.cooperative_id AND pi.run_id=r.id)
          AND r.file_total_kobo=(SELECT coalesce(sum(pi.actual_kobo),0) FROM payroll_items pi WHERE pi.cooperative_id=r.cooperative_id AND pi.run_id=r.id)
    """,
    PAYROLL_SIGN_OFF: """
        SELECT r.id AS entity_id,
               array_remove(ARRAY[r.posted_by, r.imported_by]::uuid[], NULL) AS excluded_user_ids
        FROM payroll_runs r
        WHERE r.cooperative_id=:cooperative_id AND r.status='posted'
          AND (CAST(:entity_id AS uuid) IS NULL OR r.id=CAST(:entity_id AS uuid))
          AND NOT EXISTS (SELECT 1 FROM payroll_items pi WHERE pi.cooperative_id=r.cooperative_id AND pi.run_id=r.id AND pi.classification<>'matched' AND pi.resolution_note IS NULL)
    """,
    PAYMENT_CONFIRMATION: """
        SELECT p.id AS entity_id, ARRAY[p.recorded_by]::uuid[] AS excluded_user_ids
        FROM payments p
        WHERE p.cooperative_id=:cooperative_id AND p.channel='bank_transfer' AND p.status='pending'
          AND (CAST(:entity_id AS uuid) IS NULL OR p.id=CAST(:entity_id AS uuid))
    """,
    CASH_DEPOSIT: """
        SELECT p.id AS entity_id, ARRAY[p.recorded_by]::uuid[] AS excluded_user_ids
        FROM payments p
        WHERE p.cooperative_id=:cooperative_id AND p.channel='cash' AND p.status='received'
          AND (CAST(:entity_id AS uuid) IS NULL OR p.id=CAST(:entity_id AS uuid))
    """,
    DISBURSEMENT_RECORD: """
        SELECT d.id AS entity_id, ARRAY[d.prepared_by,d.authorized_by]::uuid[] AS excluded_user_ids
        FROM disbursements d
        WHERE d.cooperative_id=:cooperative_id AND d.status='authorized'
          AND (CAST(:entity_id AS uuid) IS NULL OR d.id=CAST(:entity_id AS uuid))
    """,
    DISBURSEMENT_AUTHORIZATION: """
        SELECT d.id AS entity_id,
               array_remove(ARRAY[d.prepared_by, a.submitted_by] || coalesce((SELECT array_agg(ap.approver_id) FROM approvals ap WHERE ap.cooperative_id=a.cooperative_id AND ap.application_id=a.id), ARRAY[]::uuid[]), NULL) AS excluded_user_ids
        FROM disbursements d JOIN loans l ON l.cooperative_id=d.cooperative_id AND l.id=d.loan_id
        JOIN loan_applications a ON a.cooperative_id=l.cooperative_id AND a.id=l.application_id
        WHERE d.cooperative_id=:cooperative_id AND d.status='pending_authorization'
          AND (CAST(:entity_id AS uuid) IS NULL OR d.id=CAST(:entity_id AS uuid))
    """,
    DISBURSEMENT_CONFIRMATION: """
        SELECT d.id AS entity_id,
               array_remove(ARRAY[d.prepared_by,d.authorized_by,a.submitted_by] || coalesce((SELECT array_agg(ap.approver_id) FROM approvals ap WHERE ap.cooperative_id=a.cooperative_id AND ap.application_id=a.id), ARRAY[]::uuid[]), NULL) AS excluded_user_ids
        FROM disbursements d JOIN loans l ON l.cooperative_id=d.cooperative_id AND l.id=d.loan_id
        JOIN loan_applications a ON a.cooperative_id=l.cooperative_id AND a.id=l.application_id
        WHERE d.cooperative_id=:cooperative_id AND d.status='transfer_recorded'
          AND (CAST(:entity_id AS uuid) IS NULL OR d.id=CAST(:entity_id AS uuid))
    """,
    ROLE_CHANGE_APPROVAL: """
        SELECT p.id AS entity_id, ARRAY[p.proposer_user_id,p.target_user_id]::uuid[] AS excluded_user_ids
        FROM role_change_proposals p
        WHERE p.cooperative_id=:cooperative_id AND p.status='pending' AND p.expires_at>now()
          AND (CAST(:entity_id AS uuid) IS NULL OR p.id=CAST(:entity_id AS uuid))
          AND ((p.action='add' AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.cooperative_id=p.cooperative_id AND ur.user_id=p.target_user_id AND ur.role=p.role)) OR (p.action='remove' AND EXISTS (SELECT 1 FROM user_roles ur WHERE ur.cooperative_id=p.cooperative_id AND ur.user_id=p.target_user_id AND ur.role=p.role)) )
          AND (p.action<>'remove' OR p.role<>'admin' OR (SELECT count(*) FROM users u JOIN user_roles ur ON ur.cooperative_id=u.cooperative_id AND ur.user_id=u.id WHERE u.cooperative_id=p.cooperative_id AND u.status='active' AND ur.role='admin')>1)
    """,
    LOAN_DECISION: """
        SELECT a.id AS entity_id, ARRAY[a.submitted_by]::uuid[] AS excluded_user_ids
        FROM loan_applications a
        WHERE a.cooperative_id=:cooperative_id AND a.status='under_review'
          AND (CAST(:entity_id AS uuid) IS NULL OR a.id=CAST(:entity_id AS uuid))
    """,
    LOAN_DISPUTED: """
        SELECT l.id AS entity_id, ARRAY[]::uuid[] AS excluded_user_ids FROM loans l
        WHERE l.cooperative_id=:cooperative_id AND (l.status='disputed' OR (l.status='repaying' AND coalesce(l.deductions_paused,false)))
          AND (CAST(:entity_id AS uuid) IS NULL OR l.id=CAST(:entity_id AS uuid))
    """,
    WITHDRAWAL_APPROVAL: """
        SELECT wr.id AS entity_id, ARRAY[wr.requested_by]::uuid[] AS excluded_user_ids
        FROM withdrawal_requests wr
        WHERE wr.cooperative_id=:cooperative_id AND wr.status='pending'
          AND (CAST(:entity_id AS uuid) IS NULL OR wr.id=CAST(:entity_id AS uuid))
    """,
}


def eligible_recipients_for_action(
    event_type: str,
    *,
    excluded_user_ids: set[uuid.UUID],
    roles_by_user: dict[uuid.UUID, set[str]],
) -> tuple[uuid.UUID, ...]:
    """Return users with at least one required role, excluding action participants."""
    spec = ACTION_SPECS[event_type]
    excluded = set(excluded_user_ids)
    return tuple(
        sorted(
            (
                user_id
                for user_id, roles in roles_by_user.items()
                if user_id not in excluded and bool(spec.roles.intersection(roles))
            ),
            key=str,
        )
    )


def action_entity_ids_for_candidate_rows(
    candidates: list[dict[str, Any]], user_id: uuid.UUID, roles: set[str]
) -> dict[str, tuple[uuid.UUID, ...]]:
    """Project shared candidates to the actionable IDs per event for one user."""
    items: dict[str, list[uuid.UUID]] = {}
    for candidate in candidates:
        event_type = candidate["event_type"]
        if user_id in eligible_recipients_for_action(
            event_type,
            excluded_user_ids=set(candidate.get("excluded_user_ids") or ()),
            roles_by_user={user_id: roles},
        ):
            items.setdefault(event_type, []).append(candidate["entity_id"])
    return {section: tuple(dict.fromkeys(ids)) for section, ids in items.items()}


def action_items_for_candidate_rows(
    candidates: list[dict[str, Any]], user_id: uuid.UUID, roles: set[str]
) -> dict[str, tuple[uuid.UUID, ...]]:
    """Project shared candidates to per-user IDs grouped by product section."""
    by_event = action_entity_ids_for_candidate_rows(candidates, user_id, roles)
    items: dict[str, list[uuid.UUID]] = {}
    for event_type, ids in by_event.items():
        items.setdefault(ACTION_SPECS[event_type].section, []).extend(ids)
    return {section: tuple(dict.fromkeys(ids)) for section, ids in items.items()}


async def get_action_candidates(
    session: AsyncSession,
    cooperative_id: uuid.UUID,
    event_type: str,
    *,
    entity_id: uuid.UUID | None = None,
    include_paused_disputed: bool = False,
) -> list[dict[str, Any]]:
    if event_type not in ACTION_SPECS:
        raise ValueError(f"Unsupported action notification event: {event_type}")
    statement = _CANDIDATE_SQL[event_type]
    if event_type == LOAN_DISPUTED and not include_paused_disputed:
        statement = statement.replace(
            " OR (l.status='repaying' AND coalesce(l.deductions_paused,false))", ""
        )
    rows = await session.execute(
        text(statement), {"cooperative_id": cooperative_id, "entity_id": entity_id}
    )
    return [dict(row) for row in rows.mappings().all()]


_ALL_ACTION_CANDIDATES_SQL = text("""
    SELECT 'payroll_posting'::text AS event_type, r.id AS entity_id,
           array_remove(ARRAY[r.imported_by]::uuid[], NULL) AS excluded_user_ids
      FROM payroll_runs r
     WHERE r.cooperative_id=:cooperative_id AND r.status='imported'
       AND r.expected_total_kobo=(SELECT coalesce(sum(pi.expected_loan_kobo+pi.expected_savings_kobo+pi.expected_dues_kobo) FILTER (WHERE pi.classification NOT IN ('duplicate','unknown_member')),0) FROM payroll_items pi WHERE pi.cooperative_id=r.cooperative_id AND pi.run_id=r.id)
       AND r.file_total_kobo=(SELECT coalesce(sum(pi.actual_kobo),0) FROM payroll_items pi WHERE pi.cooperative_id=r.cooperative_id AND pi.run_id=r.id)
    UNION ALL
    SELECT 'payroll_sign_off', r.id,
           array_remove(ARRAY[r.posted_by, r.imported_by]::uuid[], NULL)
      FROM payroll_runs r
     WHERE r.cooperative_id=:cooperative_id AND r.status='posted'
       AND NOT EXISTS (SELECT 1 FROM payroll_items pi WHERE pi.cooperative_id=r.cooperative_id AND pi.run_id=r.id AND pi.classification<>'matched' AND pi.resolution_note IS NULL)
    UNION ALL
    SELECT 'payment_confirmation', p.id, ARRAY[p.recorded_by]::uuid[]
      FROM payments p WHERE p.cooperative_id=:cooperative_id AND p.channel='bank_transfer' AND p.status='pending'
    UNION ALL
    SELECT 'cash_deposit', p.id, ARRAY[p.recorded_by]::uuid[]
      FROM payments p WHERE p.cooperative_id=:cooperative_id AND p.channel='cash' AND p.status='received'
    UNION ALL
    SELECT 'disbursement_record', d.id, ARRAY[d.prepared_by,d.authorized_by]::uuid[]
      FROM disbursements d WHERE d.cooperative_id=:cooperative_id AND d.status='authorized'
    UNION ALL
    SELECT 'disbursement_authorization', d.id,
           array_remove(ARRAY[d.prepared_by, a.submitted_by] || coalesce((SELECT array_agg(ap.approver_id) FROM approvals ap WHERE ap.cooperative_id=a.cooperative_id AND ap.application_id=a.id), ARRAY[]::uuid[]), NULL)
      FROM disbursements d JOIN loans l ON l.cooperative_id=d.cooperative_id AND l.id=d.loan_id
      JOIN loan_applications a ON a.cooperative_id=l.cooperative_id AND a.id=l.application_id
     WHERE d.cooperative_id=:cooperative_id AND d.status='pending_authorization'
    UNION ALL
    SELECT 'disbursement_confirmation', d.id,
           array_remove(ARRAY[d.prepared_by,d.authorized_by,a.submitted_by] || coalesce((SELECT array_agg(ap.approver_id) FROM approvals ap WHERE ap.cooperative_id=a.cooperative_id AND ap.application_id=a.id), ARRAY[]::uuid[]), NULL)
      FROM disbursements d JOIN loans l ON l.cooperative_id=d.cooperative_id AND l.id=d.loan_id
      JOIN loan_applications a ON a.cooperative_id=l.cooperative_id AND a.id=l.application_id
     WHERE d.cooperative_id=:cooperative_id AND d.status='transfer_recorded'
    UNION ALL
    SELECT 'role_change_approval', p.id, ARRAY[p.proposer_user_id,p.target_user_id]::uuid[]
      FROM role_change_proposals p
     WHERE p.cooperative_id=:cooperative_id AND p.status='pending' AND p.expires_at>now()
       AND ((p.action='add' AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.cooperative_id=p.cooperative_id AND ur.user_id=p.target_user_id AND ur.role=p.role)) OR (p.action='remove' AND EXISTS (SELECT 1 FROM user_roles ur WHERE ur.cooperative_id=p.cooperative_id AND ur.user_id=p.target_user_id AND ur.role=p.role)))
       AND (p.action<>'remove' OR p.role<>'admin' OR (SELECT count(*) FROM users u JOIN user_roles ur ON ur.cooperative_id=u.cooperative_id AND ur.user_id=u.id WHERE u.cooperative_id=p.cooperative_id AND u.status='active' AND ur.role='admin')>1)
    UNION ALL
    SELECT 'loan_decision', a.id, ARRAY[a.submitted_by]::uuid[]
      FROM loan_applications a WHERE a.cooperative_id=:cooperative_id AND a.status='under_review'
    UNION ALL
    SELECT 'loan_disputed', l.id, ARRAY[]::uuid[] FROM loans l
     WHERE l.cooperative_id=:cooperative_id AND (l.status='disputed' OR (l.status='repaying' AND coalesce(l.deductions_paused,false)))
    UNION ALL
    SELECT 'withdrawal_approval', wr.id, ARRAY[wr.requested_by]::uuid[]
      FROM withdrawal_requests wr WHERE wr.cooperative_id=:cooperative_id AND wr.status='pending'
""")


async def get_all_action_candidates(
    session: AsyncSession, cooperative_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Read every workflow's eligible entity candidates in one tenant-scoped statement."""
    rows = await session.execute(_ALL_ACTION_CANDIDATES_SQL, {"cooperative_id": cooperative_id})
    return [dict(row) for row in rows.mappings().all()]


async def get_action_items_for_user(
    session: AsyncSession, cooperative_id: uuid.UUID, user_id: uuid.UUID, roles: set[str]
) -> dict[str, tuple[uuid.UUID, ...]]:
    """Return the exact actionable entity IDs for the user's role union, grouped by section."""
    candidates = await get_all_action_candidates(session, cooperative_id)
    return action_items_for_candidate_rows(candidates, user_id, roles)


async def get_action_entity_ids_for_user(
    session: AsyncSession, cooperative_id: uuid.UUID, user_id: uuid.UUID, roles: set[str]
) -> dict[str, tuple[uuid.UUID, ...]]:
    """Return actionable IDs per action kind for summaries and list projections."""
    candidates = await get_all_action_candidates(session, cooperative_id)
    return action_entity_ids_for_candidate_rows(candidates, user_id, roles)


async def get_action_recipients(
    session: AsyncSession, cooperative_id: uuid.UUID, event_type: str, entity_id: uuid.UUID
) -> tuple[ActionRecipient, ...]:
    candidates = await get_action_candidates(
        session, cooperative_id, event_type, entity_id=entity_id
    )
    if not candidates:
        return ()
    roles_result = await session.execute(
        text(
            """SELECT ur.user_id,ur.role FROM user_roles ur JOIN users u ON u.cooperative_id=ur.cooperative_id AND u.id=ur.user_id WHERE ur.cooperative_id=:cooperative_id AND u.status='active' AND ur.role=ANY(:required_roles)"""
        ),
        {"cooperative_id": cooperative_id, "required_roles": list(ACTION_SPECS[event_type].roles)},
    )
    roles_by_user: dict[uuid.UUID, set[str]] = {}
    for row in roles_result.mappings().all():
        roles_by_user.setdefault(row["user_id"], set()).add(row["role"])
    recipients: list[ActionRecipient] = []
    for candidate in candidates:
        for user_id in eligible_recipients_for_action(
            event_type,
            excluded_user_ids=set(candidate["excluded_user_ids"] or ()),
            roles_by_user=roles_by_user,
        ):
            recipients.append(ActionRecipient(event_type, candidate["entity_id"], user_id))
    return tuple(recipients)
