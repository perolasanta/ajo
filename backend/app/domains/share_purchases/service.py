"""Share purchases funded by a member's savings balance."""

from __future__ import annotations

import json
import uuid
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import lock_cooperative_member, transaction
from app.core.errors import Conflict, Forbidden, NotFound
from app.domains.audit.service import append_audit
from app.domains.auth.service import CurrentSession
from app.domains.notifications.eligibility import SHARE_PURCHASE_APPROVAL
from app.domains.notifications.outbox import enqueue_action_notifications_safely


def _role(current: CurrentSession, allowed: tuple[str, ...]) -> str:
    for role in allowed:
        if role in current.roles:
            return role
    raise Forbidden("Role is not permitted for this share purchase action")


async def request_purchase(
    current: CurrentSession, member_id: uuid.UUID, shares_count: int
) -> uuid.UUID:
    actor_role = _role(current, ("officer", "treasurer"))
    if shares_count <= 0:
        raise ValueError("shares count must be positive")
    async with transaction(current.cooperative_id) as session:
        member = (
            await session.execute(
                text("""SELECT status FROM members
                    WHERE cooperative_id=:cid AND id=:mid FOR UPDATE"""),
                {"cid": current.cooperative_id, "mid": member_id},
            )
        ).one_or_none()
        if member is None:
            raise NotFound("Member not found")
        if member.status != "active":
            raise Conflict("Member must be active")
        unit_price = await session.scalar(
            text("SELECT share_unit_price_naira FROM cooperatives WHERE id=:cid"),
            {"cid": current.cooperative_id},
        )
        if unit_price is None:
            raise NotFound("Cooperative not found")
        amount_kobo = shares_count * int(unit_price) * 100
        try:
            request_id = (
                await session.execute(
                    text("""INSERT INTO share_purchase_requests
                      (cooperative_id,member_id,shares_count,unit_price_naira,amount_kobo,requested_by)
                      VALUES (:cid,:mid,:count,:price,:amount,:actor)
                      RETURNING id"""),
                    {
                        "cid": current.cooperative_id,
                        "mid": member_id,
                        "count": shares_count,
                        "price": int(unit_price),
                        "amount": amount_kobo,
                        "actor": current.user_id,
                    },
                )
            ).scalar_one()
        except Exception as exc:
            if "share_purchase_pending_member_uq" in str(exc):
                raise Conflict("Member already has a pending share purchase") from exc
            raise
        await append_audit(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            action="share_purchase_requested",
            entity_type="share_purchase_request",
            role=actor_role,
            entity_id=str(request_id),
            summary={"member_id": str(member_id), "shares_count": shares_count},
            channel="api",
            ip=None,
        )
        await enqueue_action_notifications_safely(
            session, current.cooperative_id, SHARE_PURCHASE_APPROVAL, request_id
        )
        return uuid.UUID(str(request_id))


async def approve_purchase(current: CurrentSession, request_id: uuid.UUID) -> uuid.UUID:
    actor_role = _role(current, ("treasurer",))
    async with transaction(current.cooperative_id) as session:
        row = (
            await session.execute(
                text("""SELECT id,member_id,amount_kobo,requested_by,status,journal_entry_id
                    FROM share_purchase_requests
                    WHERE cooperative_id=:cid AND id=:id FOR UPDATE"""),
                {"cid": current.cooperative_id, "id": request_id},
            )
        ).one_or_none()
        if row is None:
            raise NotFound("Share purchase request not found")
        if row.requested_by == current.user_id:
            raise Conflict("A different treasurer must approve the request")
        if row.status == "approved":
            return uuid.UUID(str(row.journal_entry_id))
        if row.status != "pending":
            raise Conflict("Share purchase request is no longer pending")
        await lock_cooperative_member(session, current.cooperative_id, row.member_id)
        savings = await session.scalar(
            text("""SELECT coalesce(sum(l.credit_kobo-l.debit_kobo),0)
                FROM journal_lines l JOIN accounts a
                  ON a.cooperative_id=l.cooperative_id AND a.id=l.account_id
                WHERE l.cooperative_id=:cid AND l.member_id=:mid
                  AND a.code='member_savings'"""),
            {"cid": current.cooperative_id, "mid": row.member_id},
        )
        if int(savings or 0) < int(row.amount_kobo):
            raise Conflict("Member savings are insufficient for this share purchase")
        accounts = (
            await session.execute(
                text("""SELECT code,id FROM accounts
                    WHERE cooperative_id=:cid AND code IN ('member_savings','member_shares')"""),
                {"cid": current.cooperative_id},
            )
        ).all()
        ids = {item.code: item.id for item in accounts}
        if set(ids) != {"member_savings", "member_shares"}:
            raise Conflict("Share ledger accounts are not configured")
        entry = await session.scalar(
            text("""SELECT post_journal_entry(:cid,:effective,'share_purchase',:source,
                'Member share purchase from savings',:actor,:key,CAST(:lines AS jsonb),NULL)"""),
            {
                "cid": current.cooperative_id,
                "effective": date.today(),
                "source": request_id,
                "actor": current.user_id,
                "key": f"share-purchase:{request_id}",
                "lines": json.dumps(
                    [
                        {
                            "account_id": str(ids["member_savings"]),
                            "member_id": str(row.member_id),
                            "debit_kobo": int(row.amount_kobo),
                        },
                        {
                            "account_id": str(ids["member_shares"]),
                            "member_id": str(row.member_id),
                            "credit_kobo": int(row.amount_kobo),
                        },
                    ]
                ),
            },
        )
        await session.execute(
            text("""UPDATE share_purchase_requests
                SET status='approved',decided_by=:actor,decided_at=now(),journal_entry_id=:entry
                WHERE cooperative_id=:cid AND id=:id"""),
            {"cid": current.cooperative_id, "id": request_id, "actor": current.user_id, "entry": entry},
        )
        await append_audit(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            action="share_purchase_approved",
            entity_type="share_purchase_request",
            role=actor_role,
            entity_id=str(request_id),
            summary={"amount_kobo": int(row.amount_kobo)},
            channel="api",
            ip=None,
        )
        return uuid.UUID(str(entry))


async def reject_purchase(
    current: CurrentSession, request_id: uuid.UUID, note: str
) -> None:
    actor_role = _role(current, ("treasurer",))
    if not note.strip():
        raise ValueError("A rejection note is required")
    async with transaction(current.cooperative_id) as session:
        row = (
            await session.execute(
                text("""SELECT requested_by,status FROM share_purchase_requests
                    WHERE cooperative_id=:cid AND id=:id FOR UPDATE"""),
                {"cid": current.cooperative_id, "id": request_id},
            )
        ).one_or_none()
        if row is None:
            raise NotFound("Share purchase request not found")
        if row.requested_by == current.user_id:
            raise Conflict("A different treasurer must reject the request")
        if row.status != "pending":
            raise Conflict("Share purchase request is no longer pending")
        await session.execute(
            text("""UPDATE share_purchase_requests
                SET status='rejected',decided_by=:actor,decided_at=now(),decision_note=:note
                WHERE cooperative_id=:cid AND id=:id"""),
            {"cid": current.cooperative_id, "id": request_id, "actor": current.user_id, "note": note.strip()},
        )
        await append_audit(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            action="share_purchase_rejected",
            entity_type="share_purchase_request",
            role=actor_role,
            entity_id=str(request_id),
            summary={"decision": "rejected"},
            channel="api",
            ip=None,
        )


async def cancel_purchase(current: CurrentSession, request_id: uuid.UUID) -> None:
    async with transaction(current.cooperative_id) as session:
        row = (
            await session.execute(
                text("""SELECT requested_by,status FROM share_purchase_requests
                    WHERE cooperative_id=:cid AND id=:id FOR UPDATE"""),
                {"cid": current.cooperative_id, "id": request_id},
            )
        ).one_or_none()
        if row is None:
            raise NotFound("Share purchase request not found")
        if row.requested_by != current.user_id:
            raise Forbidden("Only the requester can cancel this request")
        if row.status != "pending":
            raise Conflict("Share purchase request is no longer pending")
        await session.execute(
            text("""UPDATE share_purchase_requests
                SET status='cancelled',cancelled_at=now()
                WHERE cooperative_id=:cid AND id=:id"""),
            {"cid": current.cooperative_id, "id": request_id},
        )
