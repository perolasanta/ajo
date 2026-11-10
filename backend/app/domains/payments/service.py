"""Tenant-scoped provider-neutral payment inbox operations."""

from __future__ import annotations

import hmac
import json
import uuid
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import transaction
from app.core.errors import Conflict, Forbidden, NotFound
from app.domains.audit.service import append_audit
from app.domains.auth.service import CurrentSession
from app.domains.members.service import record_contribution
from app.domains.payments.providers import MANUAL_PROVIDER
from app.domains.repayments.service import post_repayment

_PAYMENT_READ_ROLES = {"officer", "treasurer", "admin", "auditor"}
_PAYMENT_WRITE_ROLES = {"officer", "treasurer"}


def _role(current: CurrentSession, allowed: set[str]) -> str:
    role = next((item for item in current.roles if item in allowed), None)
    if role is None:
        raise Forbidden("This role cannot perform the payment action")
    return role


def _payment_dict(row: Any) -> dict[str, Any]:
    fields = (
        "id",
        "member_id",
        "loan_id",
        "channel",
        "provider",
        "provider_reference",
        "purpose",
        "amount_kobo",
        "fee_kobo",
        "fee_bearer",
        "status",
        "recorded_by",
        "recorded_at",
        "received_on",
        "confirmed_by",
        "confirmed_at",
        "rejected_reason",
        "receipt_no",
        "ledger_entry_id",
    )
    result = {field: row[field] for field in fields}
    for field in ("id", "member_id", "loan_id", "recorded_by", "confirmed_by", "ledger_entry_id"):
        if result.get(field) is not None:
            result[field] = str(result[field])
    return result


async def _request_hash(session: AsyncSession, payload: dict[str, Any]) -> bytes:
    result = await session.scalar(
        text("SELECT canonical_request_hash(CAST(:payload AS jsonb))"),
        {"payload": json.dumps(payload, separators=(",", ":"))},
    )
    if result is None:
        raise RuntimeError("Could not calculate payment request hash")
    return bytes(result)


async def record_payment(
    current: CurrentSession,
    *,
    member_id: uuid.UUID,
    channel: str,
    purpose: str,
    amount_kobo: int,
    provider: str,
    provider_reference: str | None,
    received_on: date,
    idempotency_key: str,
    loan_id: uuid.UUID | None = None,
    fee_kobo: int = 0,
    fee_bearer: str = "member",
    ip: str | None = None,
) -> dict[str, Any]:
    actor_role = _role(current, _PAYMENT_WRITE_ROLES)
    if channel != "bank_transfer":
        raise Conflict("Cash payments are handled by the cash-receipt workflow")
    if provider != MANUAL_PROVIDER.provider_code:
        raise ValueError("Unknown manual payment provider")
    reference = MANUAL_PROVIDER.normalize_reference(channel, provider_reference)
    if purpose not in {"savings", "shares", "dues", "loan_repayment"}:
        raise ValueError("Payment purpose is invalid")
    if amount_kobo <= 0 or fee_kobo < 0 or fee_bearer not in {"member", "cooperative"}:
        raise ValueError("Payment amount or fee is invalid")
    if (purpose == "loan_repayment") != (loan_id is not None):
        raise ValueError("Loan repayments require a loan; other purposes must not include one")
    if not idempotency_key.strip():
        raise ValueError("Idempotency key is required")

    cid = current.cooperative_id
    request_payload = {
        "operation": "payment_record",
        "member_id": str(member_id),
        "channel": channel,
        "purpose": purpose,
        "amount_kobo": amount_kobo,
        "provider": provider,
        "provider_reference": reference,
        "received_on": received_on.isoformat(),
        "loan_id": str(loan_id) if loan_id else None,
        "fee_kobo": fee_kobo,
        "fee_bearer": fee_bearer,
    }
    async with transaction(cid) as session:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 33301))"),
            {"key": f"{cid}:payment-record:{idempotency_key}"},
        )
        request_hash = await _request_hash(session, request_payload)
        existing = (
            (
                await session.execute(
                    text("""SELECT id,request_hash,status,receipt_no,ledger_entry_id,member_id,
                    loan_id,channel,purpose,amount_kobo,fee_kobo,fee_bearer,provider,
                    provider_reference,recorded_by,recorded_at,received_on,confirmed_by,
                    confirmed_at,rejected_reason FROM payments
                    WHERE cooperative_id=:cid AND idempotency_key=:key"""),
                    {"cid": cid, "key": idempotency_key},
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            if not hmac.compare_digest(bytes(existing["request_hash"]), request_hash):
                raise Conflict("Idempotency key was reused with a different payload")
            return _payment_dict(existing)

        member_exists = await session.scalar(
            text("SELECT EXISTS(SELECT 1 FROM members WHERE cooperative_id=:cid AND id=:mid)"),
            {"cid": cid, "mid": member_id},
        )
        if not member_exists:
            raise NotFound("Member not found")
        if purpose == "loan_repayment":
            loan = (
                (
                    await session.execute(
                        text("""SELECT member_id,status,deductions_paused FROM loans
                        WHERE cooperative_id=:cid AND id=:loan"""),
                        {"cid": cid, "loan": loan_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if loan is None or loan["member_id"] != member_id:
                raise NotFound("Loan for member not found")
            if (
                loan["status"] == "disputed" or loan["deductions_paused"]
            ) and actor_role != "treasurer":
                raise Forbidden("Only a treasurer may record a disputed or paused loan repayment")
        duplicate = await session.scalar(
            text("""SELECT id FROM payments WHERE cooperative_id=:cid
                AND channel=:channel AND provider_reference=:reference"""),
            {"cid": cid, "channel": channel, "reference": reference},
        )
        if duplicate is not None:
            raise Conflict("A payment with this bank reference already exists")

        payment_id = uuid.uuid4()
        try:
            async with session.begin_nested():
                await session.execute(
                    text("""INSERT INTO payments (
                        cooperative_id,id,member_id,channel,provider,provider_reference,purpose,
                        loan_id,amount_kobo,fee_kobo,fee_bearer,status,recorded_by,received_on,
                        idempotency_key,request_hash)
                        VALUES(:cid,:id,:member,:channel,:provider,:reference,:purpose,
                        :loan,:amount,:fee,:fee_bearer,'pending',:actor,:received,:key,:hash)"""),
                    {
                        "cid": cid,
                        "id": payment_id,
                        "member": member_id,
                        "channel": channel,
                        "provider": provider,
                        "reference": reference,
                        "purpose": purpose,
                        "loan": loan_id,
                        "amount": amount_kobo,
                        "fee": fee_kobo,
                        "fee_bearer": fee_bearer,
                        "actor": current.user_id,
                        "received": received_on,
                        "key": idempotency_key,
                        "hash": request_hash,
                    },
                )
        except IntegrityError as exc:
            if reference:
                raise Conflict("A payment with this bank reference already exists") from exc
            raise

        await append_audit(
            session,
            cooperative_id=cid,
            actor_user_id=current.user_id,
            action="payment_transfer_recorded",
            entity_type="payment",
            entity_id=str(payment_id),
            role=actor_role,
            summary={"amount_kobo": amount_kobo, "member_id": str(member_id), "purpose": purpose},
            channel="api",
            ip=ip,
        )
        row = (
            (
                await session.execute(
                    text("SELECT * FROM payments WHERE cooperative_id=:cid AND id=:id"),
                    {"cid": cid, "id": payment_id},
                )
            )
            .mappings()
            .one()
        )
        return _payment_dict(row)


async def _replay_action(
    session: AsyncSession,
    *,
    cooperative_id: uuid.UUID,
    idempotency_key: str,
    payload_hash: bytes,
) -> dict[str, Any] | None:
    existing = (
        (
            await session.execute(
                text("""SELECT request_hash,result_payload FROM payment_action_idempotency
                WHERE cooperative_id=:cid AND idempotency_key=:key"""),
                {"cid": cooperative_id, "key": idempotency_key},
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is None:
        return None
    if not hmac.compare_digest(bytes(existing["request_hash"]), payload_hash):
        raise Conflict("Idempotency key was reused with a different payload")
    return dict(existing["result_payload"])


async def _post_confirmed_payment(
    session: AsyncSession,
    current: CurrentSession,
    payment: Any,
    *,
    reason: str | None,
    ip: str | None,
) -> uuid.UUID:
    payment_id = payment["id"]
    posting_key = f"payment:{payment_id}:confirm"
    if payment["purpose"] == "loan_repayment":
        repayment_id = await post_repayment(
            current,
            loan_id=payment["loan_id"],
            member_id=payment["member_id"],
            amount_kobo=int(payment["amount_kobo"]),
            source="bank_transfer",
            received_on=payment["received_on"],
            reference=payment["provider_reference"],
            idempotency_key=posting_key,
            ip=ip,
            reason=reason,
            db_session=session,
        )
        entry_id = await session.scalar(
            text("SELECT journal_entry_id FROM repayments WHERE cooperative_id=:cid AND id=:id"),
            {"cid": current.cooperative_id, "id": repayment_id},
        )
        if entry_id is None:
            raise RuntimeError("Repayment posting did not produce a ledger entry")
        return uuid.UUID(str(entry_id))
    return await record_contribution(
        current,
        payment["member_id"],
        payment["purpose"],
        int(payment["amount_kobo"]),
        posting_key,
        effective_date=payment["received_on"],
        reference=payment["provider_reference"],
        payment_method="bank_transfer",
        db_session=session,
    )


async def _decide_payment(
    current: CurrentSession,
    payment_id: uuid.UUID,
    idempotency_key: str,
    *,
    action: str,
    reason: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    role = _role(current, _PAYMENT_WRITE_ROLES)
    normalized_reason = reason.strip() if reason and reason.strip() else None
    if action == "reject" and normalized_reason is None:
        raise ValueError("A rejection reason is required")
    if not idempotency_key.strip():
        raise ValueError("Idempotency key is required")
    cid = current.cooperative_id
    payload = {
        "operation": f"payment_{action}",
        "payment_id": str(payment_id),
        "reason": normalized_reason,
    }
    async with transaction(cid) as session:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 33302))"),
            {"key": f"{cid}:payment-action:{idempotency_key}"},
        )
        payload_hash = await _request_hash(session, payload)
        replay = await _replay_action(
            session,
            cooperative_id=cid,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
        if replay is not None:
            return replay
        payment = (
            (
                await session.execute(
                    text("""SELECT id,member_id,loan_id,channel,purpose,amount_kobo,provider_reference,
                    status,recorded_by,received_on FROM payments
                    WHERE cooperative_id=:cid AND id=:id FOR UPDATE"""),
                    {"cid": cid, "id": payment_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if payment is None:
            raise NotFound("Payment not found")
        if payment["status"] != "pending":
            raise Conflict("Payment is no longer awaiting a decision")
        if payment["recorded_by"] == current.user_id:
            raise Conflict("A different user must decide a payment")

        ledger_entry_id: uuid.UUID | None = None
        if action == "confirm":
            if payment["purpose"] == "loan_repayment":
                loan = (
                    (
                        await session.execute(
                            text(
                                "SELECT status,deductions_paused FROM loans WHERE cooperative_id=:cid AND id=:id"
                            ),
                            {"cid": cid, "id": payment["loan_id"]},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if loan is None:
                    raise NotFound("Loan not found")
                if loan["status"] == "disputed" or loan["deductions_paused"]:
                    if role != "treasurer":
                        raise Forbidden(
                            "Only a treasurer may confirm a disputed or paused loan repayment"
                        )
                    if normalized_reason is None:
                        raise Conflict(
                            "A reason is required for a disputed or paused loan repayment"
                        )
            ledger_entry_id = await _post_confirmed_payment(
                session, current, payment, reason=normalized_reason, ip=ip
            )
            status = "confirmed"
            audit_action = "payment_transfer_confirmed"
            await session.execute(
                text("""UPDATE payments SET status='confirmed',confirmed_by=:actor,
                    confirmed_at=now(),ledger_entry_id=:entry
                    WHERE cooperative_id=:cid AND id=:id"""),
                {"cid": cid, "id": payment_id, "actor": current.user_id, "entry": ledger_entry_id},
            )
        else:
            status = "rejected"
            audit_action = "payment_transfer_rejected"
            await session.execute(
                text("""UPDATE payments SET status='rejected',confirmed_by=:actor,
                    confirmed_at=now(),rejected_reason=:reason
                    WHERE cooperative_id=:cid AND id=:id"""),
                {
                    "cid": cid,
                    "id": payment_id,
                    "actor": current.user_id,
                    "reason": normalized_reason,
                },
            )
        result = {
            "id": str(payment_id),
            "status": status,
            "ledger_entry_id": str(ledger_entry_id) if ledger_entry_id else None,
        }
        await session.execute(
            text("""INSERT INTO payment_action_idempotency
                (cooperative_id,payment_id,action,idempotency_key,request_hash,result_payload)
                VALUES(:cid,:id,:action,:key,:hash,CAST(:result AS jsonb))"""),
            {
                "cid": cid,
                "id": payment_id,
                "action": action,
                "key": idempotency_key,
                "hash": payload_hash,
                "result": json.dumps(result),
            },
        )
        await append_audit(
            session,
            cooperative_id=cid,
            actor_user_id=current.user_id,
            action=audit_action,
            entity_type="payment",
            entity_id=str(payment_id),
            role=role,
            summary={"reason": normalized_reason} if normalized_reason else {},
            channel="api",
            ip=ip,
        )
        return result


async def confirm_payment(
    current: CurrentSession,
    payment_id: uuid.UUID,
    idempotency_key: str,
    *,
    reason: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    return await _decide_payment(
        current,
        payment_id,
        idempotency_key,
        action="confirm",
        reason=reason,
        ip=ip,
    )


async def reject_payment(
    current: CurrentSession,
    payment_id: uuid.UUID,
    reason: str,
    idempotency_key: str,
    *,
    ip: str | None = None,
) -> dict[str, Any]:
    return await _decide_payment(
        current,
        payment_id,
        idempotency_key,
        action="reject",
        reason=reason,
        ip=ip,
    )


async def get_payment(current: CurrentSession, payment_id: uuid.UUID) -> dict[str, Any]:
    _role(current, _PAYMENT_READ_ROLES)
    async with transaction(current.cooperative_id) as session:
        row = (
            (
                await session.execute(
                    text("SELECT * FROM payments WHERE cooperative_id=:cid AND id=:id"),
                    {"cid": current.cooperative_id, "id": payment_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise NotFound("Payment not found")
        return _payment_dict(row)
