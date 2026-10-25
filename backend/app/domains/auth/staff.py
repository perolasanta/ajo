"""Tenant-scoped staff-user provisioning."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from app.core.db import transaction
from app.core.errors import Forbidden
from app.core.security import hash_password
from app.domains.audit.service import append_audit
from app.domains.auth.service import VALID_ROLES, CurrentSession


async def _create_staff_user_in_session(
    session: Any,
    *,
    cooperative_id: uuid.UUID,
    email: str,
    password: str,
    roles: tuple[str, ...],
    actor_user_id: uuid.UUID,
    actor_role: str,
    channel: str,
    user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    normalized = email.strip().lower()
    if "@" not in normalized or len(normalized) > 320 or not roles or set(roles) - VALID_ROLES:
        raise ValueError("staff email or roles are invalid")
    new_id = user_id or uuid.uuid4()
    await session.execute(
        text("""INSERT INTO users(id,cooperative_id,email,password_hash)
      VALUES(:id,:cid,:email,:password_hash)"""),
        {
            "id": new_id,
            "cid": cooperative_id,
            "email": normalized,
            "password_hash": hash_password(password),
        },
    )
    for role in sorted(set(roles)):
        await session.execute(
            text("""INSERT INTO user_roles(cooperative_id,user_id,role)
          VALUES(:cid,:user,:role)"""),
            {"cid": cooperative_id, "user": new_id, "role": role},
        )
    await append_audit(
        session,
        cooperative_id=cooperative_id,
        actor_user_id=actor_user_id,
        role=actor_role,
        action="staff_user_created",
        entity_type="user",
        entity_id=str(new_id),
        summary={"roles": sorted(set(roles))},
        channel=channel,
        ip=None,
    )
    return new_id


async def create_staff_user(
    current: CurrentSession, *, email: str, password: str, roles: tuple[str, ...]
) -> uuid.UUID:
    if "admin" not in current.roles:
        raise Forbidden("Administrator role required")
    async with transaction(current.cooperative_id) as session:
        return await _create_staff_user_in_session(
            session,
            cooperative_id=current.cooperative_id,
            email=email,
            password=password,
            roles=roles,
            actor_user_id=current.user_id,
            actor_role="admin",
            channel="api",
        )


async def get_bootstrap_admin(cooperative_id: uuid.UUID) -> tuple[uuid.UUID, str] | None:
    """Read the cooperative's first administrator so the seeder can act as it.

    Runs inside tenant context (``transaction``) as the application role; a read, so it
    writes no audit row. Used only by the demo seeder to build an admin ``CurrentSession``
    without a raw lookup in seed code.
    """
    async with transaction(cooperative_id) as session:
        row = (
            await session.execute(
                text("""SELECT u.id, u.email FROM users u
                  JOIN user_roles r ON r.cooperative_id = u.cooperative_id AND r.user_id = u.id
                  WHERE u.cooperative_id = :cid AND r.role = 'admin'
                  ORDER BY u.created_at LIMIT 1"""),
                {"cid": cooperative_id},
            )
        ).one_or_none()
        if row is None:
            return None
        return uuid.UUID(str(row.id)), str(row.email)
