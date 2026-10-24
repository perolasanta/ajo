"""Tenant-scoped append-only audit chain operations."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import transaction

ZERO_HASH = bytes(32)
_FORBIDDEN_KEYS = ("password", "secret", "otp", "token", "cookie", "authorization")


@dataclass(frozen=True)
class AuditVerification:
    valid: bool
    entries_checked: int
    first_invalid_id: uuid.UUID | None = None


def _clean_summary(summary: dict[str, Any]) -> dict[str, Any]:
    for key in summary:
        if any(part in key.lower() for part in _FORBIDDEN_KEYS):
            raise ValueError("audit summaries cannot contain secret fields")
    return summary


def _canonical_payload(row: dict[str, Any]) -> bytes:
    normalized = dict(row)
    normalized["created_at"] = (
        normalized["created_at"].astimezone(UTC).isoformat(timespec="microseconds")
    )
    for key in ("id", "cooperative_id", "actor_user_id"):
        if normalized[key] is not None:
            normalized[key] = str(normalized[key])
    if normalized["ip"] is not None:
        normalized["ip"] = str(normalized["ip"])
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _hash_row(prev_hash: bytes, row: dict[str, Any]) -> bytes:
    return hashlib.sha256(prev_hash + _canonical_payload(row)).digest()


async def append_audit(
    session: AsyncSession,
    *,
    cooperative_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    role: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    summary: dict[str, Any],
    channel: str,
    ip: str | IPv4Address | IPv6Address | None,
    reason: str | None = None,
) -> uuid.UUID:
    """Append an audit event while the caller's cooperative transaction is open."""
    safe_summary = _clean_summary(summary)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:cooperative_id, 0))"),
        {"cooperative_id": str(cooperative_id)},
    )
    previous = await session.execute(
        text(
            """
            SELECT hash FROM audit_logs
             WHERE cooperative_id = :cooperative_id
             ORDER BY created_at DESC, id DESC
             LIMIT 1
            """
        ),
        {"cooperative_id": cooperative_id},
    )
    prev_hash = previous.scalar_one_or_none() or ZERO_HASH
    now = datetime.now(UTC)
    row: dict[str, Any] = {
        "id": uuid.uuid4(),
        "cooperative_id": cooperative_id,
        "created_at": now,
        "actor_user_id": actor_user_id,
        "role": role,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "summary": safe_summary,
        "channel": channel,
        "ip": str(ip) if ip is not None else None,
        "reason": reason,
    }
    entry_hash = _hash_row(prev_hash, row)
    await session.execute(
        text(
            """
            INSERT INTO audit_logs (
                id, cooperative_id, created_at, actor_user_id, role, action,
                entity_type, entity_id, summary, channel, ip, reason, prev_hash, hash
            ) VALUES (
                :id, :cooperative_id, :created_at, :actor_user_id, :role, :action,
                :entity_type, :entity_id, CAST(:summary AS jsonb), :channel,
                CAST(:ip AS inet), :reason, :prev_hash, :hash
            )
            """
        ),
        {
            **row,
            "summary": json.dumps(safe_summary, separators=(",", ":"), ensure_ascii=False),
            "prev_hash": prev_hash,
            "hash": entry_hash,
        },
    )
    return uuid.UUID(str(row["id"]))


async def verify_audit_chain(cooperative_id: uuid.UUID) -> AuditVerification:
    """Recompute every entry in order as the tenant-scoped application role."""
    async with transaction(cooperative_id) as session:
        result = await session.execute(
            text(
                """
                SELECT id, cooperative_id, created_at, actor_user_id, role, action,
                       entity_type, entity_id, summary, channel, ip, reason,
                       prev_hash, hash
                  FROM audit_logs
                 WHERE cooperative_id = :cooperative_id
                 ORDER BY created_at, id
                """
            ),
            {"cooperative_id": cooperative_id},
        )
        previous_hash = ZERO_HASH
        count = 0
        for record in result.mappings():
            row = {key: record[key] for key in record.keys() if key not in ("prev_hash", "hash")}
            stored_prev = bytes(record["prev_hash"])
            stored_hash = bytes(record["hash"])
            if stored_prev != previous_hash or _hash_row(previous_hash, row) != stored_hash:
                return AuditVerification(False, count + 1, record["id"])
            previous_hash = stored_hash
            count += 1
        return AuditVerification(True, count)
