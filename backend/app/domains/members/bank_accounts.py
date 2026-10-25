"""Encrypted member bank-account service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from app.core.db import transaction
from app.core.errors import Forbidden, NotFound
from app.domains.audit.service import append_audit
from app.domains.auth.service import CurrentSession
from app.domains.members.service import encrypt_account_number


async def add_bank_account(
    current: CurrentSession,
    member_id: uuid.UUID,
    *,
    bank_code: str,
    account_number: str,
    account_name: str,
    mark_verified: bool = False,
) -> uuid.UUID:
    """Store an encrypted account number and expose only its last four digits."""
    role = next((role for role in current.roles if role in {"officer", "admin"}), None)
    if role is None:
        raise Forbidden("Officer or admin role required")
    digits = "".join(character for character in account_number if character.isdigit())
    if not bank_code.strip() or not account_name.strip() or len(digits) < 4:
        raise ValueError("bank account details are invalid")
    account_id = uuid.uuid4()
    async with transaction(current.cooperative_id) as session:
        member_exists = await session.scalar(
            text("SELECT EXISTS(SELECT 1 FROM members WHERE cooperative_id=:cid AND id=:member)"),
            {"cid": current.cooperative_id, "member": member_id},
        )
        if not member_exists:
            raise NotFound("Member not found")
        verified_at = datetime.now(UTC) if mark_verified else None
        await session.execute(
            text("""INSERT INTO bank_accounts
              (id,cooperative_id,member_id,bank_code,account_number_encrypted,last4,account_name,verified_at,status)
              VALUES(:id,:cid,:member,:bank,:encrypted,:last4,:name,:verified,:status)"""),
            {"id": account_id, "cid": current.cooperative_id, "member": member_id,
             "bank": bank_code.strip(), "encrypted": encrypt_account_number(digits),
             "last4": digits[-4:], "name": account_name.strip(), "verified": verified_at,
             "status": "verified" if mark_verified else "pending"},
        )
        await append_audit(session, cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id, role=role, action="bank_account_added",
            entity_type="bank_account", entity_id=str(account_id),
            summary={"member_id": str(member_id), "bank_code": bank_code.strip(),
                     "last4": digits[-4:], "verified": mark_verified},
            channel="api", ip=None)
    return account_id
