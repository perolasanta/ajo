"""Shared eligibility rules and action candidates for badges and notifications."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ActionSpec:
    roles: frozenset[str]
    section: str
    path: str


@dataclass(frozen=True)
class ActionRecipient:
    event_type: str
    entity_id: uuid.UUID
    user_id: uuid.UUID


ACTION_SPECS = {
    "payroll_sign_off": ActionSpec(frozenset({"admin"}), "Payroll", "/payroll/runs"),
    "disbursement_authorization": ActionSpec(
        frozenset({"admin"}), "Disbursements", "/disbursements"
    ),
    "role_change_approval": ActionSpec(frozenset({"admin"}), "Staff", "/staff"),
    "loan_disputed": ActionSpec(frozenset({"treasurer"}), "Loans", "/loans"),
}

_CANDIDATE_SQL = {
    "payroll_sign_off": """
        SELECT r.id AS entity_id,
               ARRAY[coalesce(r.posted_by, r.imported_by)]::uuid[] AS excluded_user_ids
        FROM payroll_runs r
        WHERE r.cooperative_id=:cooperative_id AND r.status='posted'
          AND (:entity_id IS NULL OR r.id=:entity_id)
          AND NOT EXISTS (
            SELECT 1 FROM payroll_items pi
            WHERE pi.cooperative_id=r.cooperative_id AND pi.run_id=r.id
              AND pi.classification<>'matched' AND pi.resolution_note IS NULL
          )
    """,
    "disbursement_authorization": """
        SELECT d.id AS entity_id,
               array_remove(
                 ARRAY[d.prepared_by, a.submitted_by] || coalesce((
                   SELECT array_agg(ap.approver_id)
                   FROM approvals ap
                   WHERE ap.cooperative_id=a.cooperative_id AND ap.application_id=a.id
                 ), ARRAY[]::uuid[]), NULL
               ) AS excluded_user_ids
        FROM disbursements d
        JOIN loans l ON l.cooperative_id=d.cooperative_id AND l.id=d.loan_id
        JOIN loan_applications a ON a.cooperative_id=l.cooperative_id AND a.id=l.application_id
        WHERE d.cooperative_id=:cooperative_id AND d.status='pending_authorization'
          AND (:entity_id IS NULL OR d.id=:entity_id)
    """,
    "role_change_approval": """
        SELECT p.id AS entity_id,
               ARRAY[p.proposer_user_id, p.target_user_id]::uuid[] AS excluded_user_ids
        FROM role_change_proposals p
        WHERE p.cooperative_id=:cooperative_id AND p.status='pending'
          AND p.expires_at>now() AND (:entity_id IS NULL OR p.id=:entity_id)
          AND (
            (p.action='add' AND NOT EXISTS (
              SELECT 1 FROM user_roles ur
              WHERE ur.cooperative_id=p.cooperative_id
                AND ur.user_id=p.target_user_id AND ur.role=p.role
            ))
            OR
            (p.action='remove' AND EXISTS (
              SELECT 1 FROM user_roles ur
              WHERE ur.cooperative_id=p.cooperative_id
                AND ur.user_id=p.target_user_id AND ur.role=p.role
            ))
          )
          AND (
            p.action<>'remove' OR p.role<>'admin' OR (
              SELECT count(*) FROM users active_admin
              JOIN user_roles admin_role
                ON admin_role.cooperative_id=active_admin.cooperative_id
                AND admin_role.user_id=active_admin.id
              WHERE active_admin.cooperative_id=p.cooperative_id
                AND active_admin.status='active' AND admin_role.role='admin'
            )>1
          )
    """,
    "loan_disputed": """
        SELECT l.id AS entity_id, ARRAY[]::uuid[] AS excluded_user_ids
        FROM loans l
        WHERE l.cooperative_id=:cooperative_id AND l.status='disputed'
          AND (:entity_id IS NULL OR l.id=:entity_id)
    """,
}

_DISPUTED_BADGE_SQL = """
    SELECT l.id AS entity_id, ARRAY[]::uuid[] AS excluded_user_ids
    FROM loans l
    WHERE l.cooperative_id=:cooperative_id
      AND (l.status='disputed' OR (l.status='repaying' AND coalesce(l.deductions_paused,false)))
      AND (:entity_id IS NULL OR l.id=:entity_id)
"""


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
    statement = (
        _DISPUTED_BADGE_SQL
        if event_type == "loan_disputed" and include_paused_disputed
        else _CANDIDATE_SQL[event_type]
    )
    rows = await session.execute(
        text(statement), {"cooperative_id": cooperative_id, "entity_id": entity_id}
    )
    return [dict(row) for row in rows.mappings().all()]


async def get_action_recipients(
    session: AsyncSession,
    cooperative_id: uuid.UUID,
    event_type: str,
    entity_id: uuid.UUID,
) -> tuple[ActionRecipient, ...]:
    candidates = await get_action_candidates(
        session, cooperative_id, event_type, entity_id=entity_id
    )
    if not candidates:
        return ()
    roles_result = await session.execute(
        text("""SELECT ur.user_id, ur.role
          FROM user_roles ur JOIN users u
            ON u.cooperative_id=ur.cooperative_id AND u.id=ur.user_id
          WHERE ur.cooperative_id=:cooperative_id AND u.status='active'
            AND ur.role = ANY(:required_roles)"""),
        {
            "cooperative_id": cooperative_id,
            "required_roles": list(ACTION_SPECS[event_type].roles),
        },
    )
    roles_by_user: dict[uuid.UUID, set[str]] = {}
    for row in roles_result.mappings().all():
        roles_by_user.setdefault(row["user_id"], set()).add(row["role"])
    recipients: list[ActionRecipient] = []
    for candidate in candidates:
        candidate_id = candidate["entity_id"]
        excluded_user_ids = set(candidate["excluded_user_ids"] or ())
        for user_id in eligible_recipients_for_action(
            event_type,
            excluded_user_ids=excluded_user_ids,
            roles_by_user=roles_by_user,
        ):
            recipients.append(ActionRecipient(event_type, candidate_id, user_id))
    return tuple(recipients)
