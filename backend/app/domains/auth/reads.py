"""Tenant-scoped, admin-only read projections for staff management."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text

from app.core.db import transaction
from app.core.errors import Forbidden, NotFound
from app.domains.auth.display_names import staff_actor_display_name
from app.domains.auth.service import CurrentSession

ProposalStatus = Literal["all", "pending", "approved", "rejected", "cancelled", "expired"]


def _require_admin(current: CurrentSession) -> None:
    if "admin" not in current.roles:
        raise Forbidden("Administrator role required")


async def list_staff(
    current: CurrentSession, *, page: int = 1, page_size: int = 25
) -> dict[str, Any]:
    """Return the minimum staff directory fields needed for role management."""
    _require_admin(current)
    params = {"cooperative_id": current.cooperative_id}
    async with transaction(current.cooperative_id) as session:
        total = int(
            await session.scalar(
                text("SELECT count(*) FROM users WHERE cooperative_id = :cooperative_id"), params
            )
            or 0
        )
        result = await session.execute(
            text(
                """
                SELECT u.id, u.email, u.full_name, u.status, u.created_at,
                       coalesce(array_agg(r.role ORDER BY r.role)
                           FILTER (WHERE r.role IS NOT NULL), ARRAY[]::text[]) AS roles
                  FROM users AS u
                  LEFT JOIN user_roles AS r
                    ON r.cooperative_id = u.cooperative_id AND r.user_id = u.id
                 WHERE u.cooperative_id = :cooperative_id
                 GROUP BY u.cooperative_id, u.id, u.email, u.full_name, u.status, u.created_at
                 ORDER BY lower(u.email), u.id
                 LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": page_size, "offset": (page - 1) * page_size},
        )
        rows = [dict(row) for row in result.mappings()]
    return {
        "items": [
            {
                "user_id": str(row["id"]),
                "email": row["email"],
                "full_name": row["full_name"],
                "status": row["status"],
                "roles": list(row["roles"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


async def update_staff_full_name(
    current: CurrentSession, user_id: UUID, full_name: str | None
) -> dict[str, object]:
    """Update an existing tenant staff user's optional display name."""
    _require_admin(current)
    normalized = full_name.strip() if full_name is not None else None
    normalized = normalized or None
    if normalized is not None and len(normalized) > 200:
        raise ValueError("staff full name is too long")
    async with transaction(current.cooperative_id) as session:
        result = await session.execute(
            text(
                """UPDATE users SET full_name = :full_name
                     WHERE cooperative_id = :cooperative_id AND id = :user_id
                 RETURNING id, full_name"""
            ),
            {
                "cooperative_id": current.cooperative_id,
                "user_id": user_id,
                "full_name": normalized,
            },
        )
        row = result.mappings().one_or_none()
    if row is None:
        raise NotFound("Staff user not found")
    return {"user_id": str(row["id"]), "full_name": row["full_name"]}


async def list_role_change_proposals(
    current: CurrentSession,
    *,
    status: ProposalStatus = "pending",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    """Return role-proposal workflow details, never credentials or session data."""
    _require_admin(current)
    params: dict[str, object] = {
        "cooperative_id": current.cooperative_id,
        "status": status,
    }
    async with transaction(current.cooperative_id) as session:
        total = int(
            await session.scalar(
                text(
                    """
                    SELECT count(*)
                      FROM role_change_proposals AS p
                     WHERE p.cooperative_id = :cooperative_id
                       AND (:status = 'all' OR
                            CASE WHEN p.status = 'pending' AND p.expires_at <= now()
                                 THEN 'expired' ELSE p.status END = :status)
                    """
                ),
                params,
            )
            or 0
        )
        result = await session.execute(
            text(
                """
                SELECT p.id, p.target_user_id, target.email AS target_email,
                       p.proposer_user_id, proposer.id AS joined_proposer_id,
                       proposer.full_name AS proposer_full_name, proposer.email AS proposer_email,
                       decider.id AS joined_decider_id, decider.full_name AS decider_full_name,
                       decider.email AS decider_email,
                       p.role, p.action, p.reason,
                       CASE WHEN p.status = 'pending' AND p.expires_at <= now()
                            THEN 'expired' ELSE p.status END AS status,
                       p.created_at, p.expires_at, p.decided_at, p.decided_by
                  FROM role_change_proposals AS p
                  LEFT JOIN users AS target
                    ON target.cooperative_id = p.cooperative_id AND target.id = p.target_user_id
                  LEFT JOIN users AS proposer
                    ON proposer.cooperative_id = p.cooperative_id AND proposer.id = p.proposer_user_id
                  LEFT JOIN users AS decider
                    ON decider.cooperative_id = p.cooperative_id AND decider.id = p.decided_by
                 WHERE p.cooperative_id = :cooperative_id
                   AND (:status = 'all' OR
                        CASE WHEN p.status = 'pending' AND p.expires_at <= now()
                             THEN 'expired' ELSE p.status END = :status)
                 ORDER BY p.created_at DESC, p.id DESC
                 LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": page_size, "offset": (page - 1) * page_size},
        )
        rows = [dict(row) for row in result.mappings()]
    return {
        "items": [
            {
                "proposal_id": str(row["id"]),
                "target_user_id": str(row["target_user_id"]),
                "target_email": row["target_email"],
                "proposer_user_id": str(row["proposer_user_id"]),
                "proposer_email": row["proposer_email"],
                "proposer_display_name": staff_actor_display_name(
                    row["proposer_full_name"],
                    row["proposer_email"],
                    user_exists=row["joined_proposer_id"] is not None,
                    include_email=True,
                ),
                "decided_by_display_name": (
                    staff_actor_display_name(
                        row["decider_full_name"],
                        row["decider_email"],
                        user_exists=row["joined_decider_id"] is not None,
                        include_email=True,
                    )
                    if row["decided_by"] is not None
                    else None
                ),
                "role": row["role"],
                "action": row["action"],
                "reason": row["reason"],
                "status": row["status"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "decided_at": row["decided_at"],
                "decided_by": str(row["decided_by"]) if row["decided_by"] is not None else None,
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
    }
