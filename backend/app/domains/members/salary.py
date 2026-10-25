"""Append-only salary record service."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import text

from app.core.db import transaction
from app.core.errors import Forbidden, NotFound
from app.domains.audit.service import append_audit
from app.domains.auth.service import CurrentSession


async def add_salary_record(
    current: CurrentSession,
    member_id: uuid.UUID,
    *,
    effective_from: date,
    basis: str,
    amount_kobo: int,
    source: str = "declared",
) -> uuid.UUID:
    """Add an auditable, immutable salary basis for a member."""
    role = next((role for role in current.roles if role in {"officer", "admin"}), None)
    if role is None:
        raise Forbidden("Officer or admin role required")
    if basis not in {"net", "gross"} or source not in {"declared", "payroll", "hr"}:
        raise ValueError("salary basis or source is invalid")
    if amount_kobo < 0:
        raise ValueError("salary amount cannot be negative")
    salary_id = uuid.uuid4()
    async with transaction(current.cooperative_id) as session:
        member_exists = await session.scalar(
            text("SELECT EXISTS(SELECT 1 FROM members WHERE cooperative_id=:cid AND id=:member)"),
            {"cid": current.cooperative_id, "member": member_id},
        )
        if not member_exists:
            raise NotFound("Member not found")
        await session.execute(
            text("""INSERT INTO salary_records
              (id,cooperative_id,member_id,effective_from,basis,amount_kobo,source,recorded_by)
              VALUES(:id,:cid,:member,:effective,:basis,:amount,:source,:actor)"""),
            {"id": salary_id, "cid": current.cooperative_id, "member": member_id,
             "effective": effective_from, "basis": basis, "amount": amount_kobo,
             "source": source, "actor": current.user_id},
        )
        await append_audit(session, cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id, role=role, action="salary_record_added",
            entity_type="salary_record", entity_id=str(salary_id),
            summary={"member_id": str(member_id), "basis": basis, "amount_kobo": amount_kobo},
            channel="api", ip=None)
    return salary_id
