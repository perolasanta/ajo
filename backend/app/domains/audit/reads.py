"""Read-only, tenant-scoped audit log queries."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.core.db import transaction
from app.core.errors import Forbidden
from app.domains.auth.display_names import staff_actor_display_name
from app.domains.auth.service import CurrentSession

READ_ROLES = frozenset({"admin", "auditor"})
SAFE_SUMMARY_KEYS = frozenset({"status", "decision", "old_role", "new_role", "count"})
SAFE_STATUS_VALUES = frozenset(
    {
        "active",
        "applicant",
        "approved",
        "approved_awaiting_final",
        "authorized",
        "awaiting_applicant_confirmation",
        "awaiting_borrower_final_confirmation",
        "cancelled",
        "closed",
        "disbursed",
        "disputed",
        "disabled",
        "expired",
        "failed",
        "missing",
        "over",
        "paid",
        "pending",
        "proposed",
        "ready_for_disbursement",
        "repaying",
        "rejected",
        "reversed",
        "short",
        "superseded",
        "suspended",
        "transfer_recorded",
        "unknown_member",
        "verified",
        "withdrawn",
    }
)
SAFE_DECISION_VALUES = frozenset({"approved", "confirm", "dispute", "rejected"})
SAFE_ROLE_VALUES = frozenset({"officer", "committee", "treasurer", "admin", "auditor"})
MAX_SAFE_COUNT = 1_000_000


def read_role(current: CurrentSession) -> str:
    for role in current.roles:
        if role in READ_ROLES:
            return role
    raise Forbidden("Audit log access is restricted to admins and auditors")


def _safe_summary(summary: object) -> dict[str, str | int | None]:
    if not isinstance(summary, dict):
        return {}
    safe: dict[str, str | int | None] = {}
    for key in SAFE_SUMMARY_KEYS.intersection(summary):
        value = summary[key]
        if value is None:
            safe[key] = None
        elif key == "status" and isinstance(value, str) and value in SAFE_STATUS_VALUES:
            safe[key] = value
        elif key == "decision" and isinstance(value, str) and value in SAFE_DECISION_VALUES:
            safe[key] = value
        elif key in {"old_role", "new_role"} and isinstance(value, str) and value in SAFE_ROLE_VALUES:
            safe[key] = value
        elif (
            key == "count"
            and isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= MAX_SAFE_COUNT
        ):
            safe[key] = value
    return safe


def audit_projection(row: dict[str, Any]) -> dict[str, object]:
    actor_id = row["actor_user_id"]
    actor_name = (
        "System"
        if actor_id is None
        else staff_actor_display_name(
            row.get("actor_full_name"),
            row.get("actor_email"),
            user_exists=row.get("joined_actor_id") is not None,
            include_email=True,
        )
    )
    return {
        "event_id": str(row["id"]),
        "timestamp": row["created_at"],
        "actor_id": str(actor_id) if actor_id is not None else None,
        "actor_display_name": actor_name,
        "actor_role": row["role"],
        "action": row["action"],
        "entity_type": row["entity_type"],
        "entity_id": row["entity_id"],
        "safe_summary": _safe_summary(row.get("summary")),
    }


async def list_audit_events(
    current: CurrentSession,
    *,
    from_timestamp: datetime | None = None,
    to_timestamp: datetime | None = None,
    actor_id: uuid.UUID | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, object]:
    read_role(current)
    params: dict[str, object] = {
        "cooperative_id": current.cooperative_id,
        "from_timestamp": from_timestamp,
        "to_timestamp": to_timestamp,
        "actor_id": actor_id,
        "action": action,
        "entity_type": entity_type,
    }
    async with transaction(current.cooperative_id) as session:
        total = int(
            await session.scalar(
                text(
                    """SELECT count(*) FROM audit_logs
                       WHERE cooperative_id=:cooperative_id
                         AND (CAST(:from_timestamp AS timestamptz) IS NULL
                              OR created_at >= CAST(:from_timestamp AS timestamptz))
                         AND (CAST(:to_timestamp AS timestamptz) IS NULL
                              OR created_at < CAST(:to_timestamp AS timestamptz))
                         AND (CAST(:actor_id AS uuid) IS NULL
                              OR actor_user_id = CAST(:actor_id AS uuid))
                         AND (CAST(:action AS text) IS NULL OR action = CAST(:action AS text))
                         AND (CAST(:entity_type AS text) IS NULL
                              OR entity_type = CAST(:entity_type AS text))"""
                ),
                params,
            )
            or 0
        )
        result = await session.execute(
            text(
                """
                SELECT a.id, a.created_at, a.actor_user_id, a.role, a.action,
                       a.entity_type, a.entity_id, a.summary,
                       u.id AS joined_actor_id, u.full_name AS actor_full_name, u.email AS actor_email
                  FROM audit_logs AS a
                  LEFT JOIN users AS u
                    ON u.cooperative_id = a.cooperative_id AND u.id = a.actor_user_id
                 WHERE a.cooperative_id=:cooperative_id
                   AND (CAST(:from_timestamp AS timestamptz) IS NULL
                        OR a.created_at >= CAST(:from_timestamp AS timestamptz))
                   AND (CAST(:to_timestamp AS timestamptz) IS NULL
                        OR a.created_at < CAST(:to_timestamp AS timestamptz))
                   AND (CAST(:actor_id AS uuid) IS NULL
                        OR a.actor_user_id = CAST(:actor_id AS uuid))
                   AND (CAST(:action AS text) IS NULL OR a.action = CAST(:action AS text))
                   AND (CAST(:entity_type AS text) IS NULL
                        OR a.entity_type = CAST(:entity_type AS text))
                 ORDER BY a.created_at DESC, a.id DESC
                 LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": page_size, "offset": (page - 1) * page_size},
        )
        items = [dict(row) for row in result.mappings()]
    return {"items": items, "page": page, "page_size": page_size, "total": total}
