"""Read-only, tenant-scoped audit log queries."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.core.db import transaction
from app.core.errors import Forbidden
from app.domains.auth.service import CurrentSession

READ_ROLES = frozenset({"admin", "auditor"})
SAFE_SUMMARY_KEYS = frozenset({"status", "decision", "old_role", "new_role", "count"})


def read_role(current: CurrentSession) -> str:
    for role in current.roles:
        if role in READ_ROLES:
            return role
    raise Forbidden("Audit log access is restricted to admins and auditors")


def _safe_summary(summary: object) -> dict[str, str | int | bool | None]:
    if not isinstance(summary, dict):
        return {}
    safe: dict[str, str | int | bool | None] = {}
    for key in SAFE_SUMMARY_KEYS:
        value = summary.get(key)
        if isinstance(value, (str, int, bool)) or value is None:
            safe[key] = value
    return safe


def audit_projection(row: dict[str, Any]) -> dict[str, object]:
    return {
        "event_id": str(row["id"]),
        "timestamp": row["created_at"],
        "actor_id": str(row["actor_user_id"]) if row["actor_user_id"] is not None else None,
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
    filters = ["cooperative_id = :cooperative_id"]
    params: dict[str, object] = {"cooperative_id": current.cooperative_id}
    if from_timestamp is not None:
        filters.append("created_at >= :from_timestamp")
        params["from_timestamp"] = from_timestamp
    if to_timestamp is not None:
        filters.append("created_at < :to_timestamp")
        params["to_timestamp"] = to_timestamp
    if actor_id is not None:
        filters.append("actor_user_id = :actor_id")
        params["actor_id"] = actor_id
    if action is not None:
        filters.append("action = :action")
        params["action"] = action
    if entity_type is not None:
        filters.append("entity_type = :entity_type")
        params["entity_type"] = entity_type
    where = " AND ".join(filters)
    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size
    async with transaction(current.cooperative_id) as session:
        total = int(
            await session.scalar(text(f"SELECT count(*) FROM audit_logs WHERE {where}"), params)
            or 0
        )
        result = await session.execute(
            text(
                f"""
                SELECT id, created_at, actor_user_id, role, action, entity_type, entity_id, summary
                  FROM audit_logs
                 WHERE {where}
                 ORDER BY created_at DESC, id DESC
                 LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
        items = [audit_projection(dict(row)) for row in result.mappings()]
    return {"items": items, "page": page, "page_size": page_size, "total": total}
