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
from app.domains.auth.display_names import staff_actor_display_name
from app.domains.auth.service import CurrentSession
from app.domains.members.service import record_contribution
from app.domains.notifications import eligibility
from app.domains.payments.providers import MANUAL_PROVIDER
from app.domains.repayments.service import post_repayment

_PAYMENT_READ_ROLES = {"officer", "treasurer", "admin", "auditor"}
_PAYMENT_WRITE_ROLES = {"officer", "treasurer"}

_PAYMENT_LIST_QUERY = text(
    """WITH payment_action_candidates AS (
        __PAYMENT_ACTION_CANDIDATES__
    ), filtered AS (
        SELECT p.id, p.member_id, m.member_no, m.full_name AS member_name,
               p.loan_id, loan_product.name AS loan_name, p.purpose,
               p.amount_kobo, p.channel, p.status, p.recorded_at, p.received_on,
               p.receipt_no, recorder.full_name AS recorder_full_name,
               (recorder.id IS NOT NULL) AS recorder_exists,
               candidate.event_type AS action_event_type,
               candidate.entity_id AS action_entity_id,
               candidate.excluded_user_ids AS action_excluded_user_ids
          FROM payments p
          JOIN members m ON m.cooperative_id=p.cooperative_id AND m.id=p.member_id
          LEFT JOIN loans loan
            ON loan.cooperative_id=p.cooperative_id AND loan.id=p.loan_id
          LEFT JOIN loan_products loan_product
            ON loan_product.cooperative_id=loan.cooperative_id
           AND loan_product.id=loan.product_id
          LEFT JOIN users recorder
            ON recorder.cooperative_id=p.cooperative_id AND recorder.id=p.recorded_by
          LEFT JOIN payment_action_candidates candidate ON candidate.entity_id=p.id
         WHERE p.cooperative_id=:cooperative_id
           AND (CAST(:status AS text) IS NULL OR p.status=CAST(:status AS text))
           AND (CAST(:channel AS text) IS NULL OR p.channel=CAST(:channel AS text))
           AND (CAST(:purpose AS text) IS NULL OR p.purpose=CAST(:purpose AS text))
           AND (CAST(:member_id AS uuid) IS NULL OR p.member_id=CAST(:member_id AS uuid))
           AND (NOT :mine OR p.recorded_by=:user_id)
           AND (NOT :action_needed OR (
                candidate.event_type=ANY(CAST(:action_event_types AS text[]))
                AND NOT (:user_id=ANY(COALESCE(
                    candidate.excluded_user_ids, ARRAY[]::uuid[]))))
           )
    ), page AS (
        SELECT * FROM filtered
         ORDER BY recorded_at DESC, id DESC
         LIMIT :page_size OFFSET :offset
    ), total AS (
        SELECT count(*) AS total FROM filtered
    )
    SELECT page.*, total.total
      FROM total LEFT JOIN page ON true
     ORDER BY page.recorded_at DESC NULLS LAST, page.id DESC NULLS LAST""".replace(
        "__PAYMENT_ACTION_CANDIDATES__", eligibility.payment_action_candidates_sql()
    )  # noqa: S608 - candidate SQL is built only from fixed action constants.
)


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


async def _next_receipt_no(session: AsyncSession, cooperative_id: uuid.UUID) -> int:
    value = await session.scalar(
        text("""INSERT INTO payment_receipt_counters (cooperative_id,last_receipt_no)
            VALUES(:cid,1)
            ON CONFLICT (cooperative_id) DO UPDATE
              SET last_receipt_no=payment_receipt_counters.last_receipt_no+1
            RETURNING last_receipt_no"""),
        {"cid": cooperative_id},
    )
    if value is None:
        raise RuntimeError("Could not allocate a payment receipt number")
    return int(value)


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
    reason: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    actor_role = _role(current, _PAYMENT_WRITE_ROLES)
    if channel not in {"cash", "bank_transfer"}:
        raise ValueError("Manual provider supports cash and bank transfers only")
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
        "reason": reason.strip() if reason and reason.strip() else None,
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
            if (loan["status"] == "disputed" or loan["deductions_paused"]) and not (
                reason and reason.strip()
            ):
                raise Conflict("A reason is required for a disputed or paused loan repayment")
        if reference is not None:
            duplicate = await session.scalar(
                text("""SELECT id FROM payments WHERE cooperative_id=:cid
                    AND channel=:channel AND provider_reference=:reference"""),
                {"cid": cid, "channel": channel, "reference": reference},
            )
            if duplicate is not None:
                raise Conflict("A payment with this bank reference already exists")

        payment_id = uuid.uuid4()
        receipt_no = await _next_receipt_no(session, cid) if channel == "cash" else None
        status = "received" if channel == "cash" else "pending"
        try:
            async with session.begin_nested():
                await session.execute(
                    text("""INSERT INTO payments (
                        cooperative_id,id,member_id,channel,provider,provider_reference,purpose,
                        loan_id,amount_kobo,fee_kobo,fee_bearer,status,recorded_by,received_on,
                        receipt_no,idempotency_key,request_hash)
                        VALUES(:cid,:id,:member,:channel,:provider,:reference,:purpose,
                        :loan,:amount,:fee,:fee_bearer,:status,:actor,:received,:receipt,:key,:hash)"""),
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
                        "status": status,
                        "actor": current.user_id,
                        "received": received_on,
                        "receipt": receipt_no,
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
            action="payment_cash_received" if channel == "cash" else "payment_transfer_recorded",
            entity_type="payment",
            entity_id=str(payment_id),
            role=actor_role,
            summary={"amount_kobo": amount_kobo, "member_id": str(member_id), "purpose": purpose},
            channel="api",
            ip=ip,
        )
        if channel == "cash":
            ledger_entry_id = await _post_confirmed_payment(
                session,
                current,
                {
                    "id": payment_id,
                    "member_id": member_id,
                    "loan_id": loan_id,
                    "purpose": purpose,
                    "amount_kobo": amount_kobo,
                    "provider_reference": reference or "cash",
                    "received_on": received_on,
                    "channel": channel,
                },
                reason=reason,
                ip=ip,
            )
            await session.execute(
                text(
                    "UPDATE payments SET ledger_entry_id=:entry WHERE cooperative_id=:cid AND id=:id"
                ),
                {"cid": cid, "id": payment_id, "entry": ledger_entry_id},
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
    source = payment["channel"]
    funding_account_code = "cash_on_hand" if source == "cash" else "bank"
    posting_key = f"payment:{payment_id}:post"
    if payment["purpose"] == "loan_repayment":
        repayment_id = await post_repayment(
            current,
            loan_id=payment["loan_id"],
            member_id=payment["member_id"],
            amount_kobo=int(payment["amount_kobo"]),
            source=source,
            received_on=payment["received_on"],
            reference=payment["provider_reference"],
            idempotency_key=posting_key,
            ip=ip,
            reason=reason,
            db_session=session,
            funding_account_code=funding_account_code,
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
        payment_method=source,
        db_session=session,
        funding_account_code=funding_account_code,
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


async def list_payments(
    current: CurrentSession,
    *,
    page: int,
    page_size: int,
    status: str | None = None,
    channel: str | None = None,
    purpose: str | None = None,
    member_id: uuid.UUID | None = None,
    mine: bool = False,
    action: str | None = None,
) -> dict[str, Any]:
    _role(current, _PAYMENT_READ_ROLES)
    if page < 1 or not 1 <= page_size <= 100:
        raise ValueError("Page must be positive and page size must be between 1 and 100")
    if action not in {None, "needed"}:
        raise ValueError("Unsupported payment list action filter")

    roles = set(current.roles)
    eligible_action_types = eligibility.eligible_action_types_for_user(
        current.user_id, roles, eligibility.PAYMENT_ACTION_TYPES
    )
    async with transaction(current.cooperative_id) as session:
        result = await session.execute(
            _PAYMENT_LIST_QUERY,
            {
                "cooperative_id": current.cooperative_id,
                "entity_id": None,
                "status": status,
                "channel": channel,
                "purpose": purpose,
                "member_id": member_id,
                "mine": mine,
                "action_needed": action == "needed",
                "action_event_types": list(eligible_action_types),
                "user_id": current.user_id,
                "page_size": page_size,
                "offset": (page - 1) * page_size,
            },
        )
        result_rows = list(result.mappings().all())

    total = int(result_rows[0]["total"]) if result_rows else 0
    rows = [row for row in result_rows if row["id"] is not None]
    if action == "needed":
        candidate_rows = [
            {
                "event_type": row["action_event_type"],
                "entity_id": row["action_entity_id"],
                "excluded_user_ids": row["action_excluded_user_ids"],
            }
            for row in rows
            if row["action_event_type"] is not None
        ]
        eligible_ids_by_action = eligibility.action_entity_ids_for_candidate_rows(
            candidate_rows, current.user_id, roles
        )
        eligible_ids = {entity_id for ids in eligible_ids_by_action.values() for entity_id in ids}
        rows = [row for row in rows if row["id"] in eligible_ids]

    items = []
    for row in rows:
        items.append(
            {
                "id": str(row["id"]),
                "member_id": str(row["member_id"]),
                "member_no": row["member_no"],
                "member_name": row["member_name"],
                "loan_id": str(row["loan_id"]) if row["loan_id"] is not None else None,
                "loan_name": row["loan_name"],
                "purpose": row["purpose"],
                "amount_kobo": int(row["amount_kobo"]),
                "channel": row["channel"],
                "status": row["status"],
                "recorded_by_name": staff_actor_display_name(
                    row["recorder_full_name"],
                    None,
                    user_exists=bool(row["recorder_exists"]),
                    include_email=False,
                ),
                "recorded_at": row["recorded_at"],
                "received_on": row["received_on"],
                "receipt_no": row["receipt_no"],
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def cash_on_hand_balance(cooperative_id: uuid.UUID) -> int:
    async with transaction(cooperative_id) as session:
        balance = await session.scalar(
            text("""SELECT coalesce(sum(lines.debit_kobo-lines.credit_kobo),0)::bigint
                FROM journal_lines lines JOIN accounts accounts
                  ON accounts.cooperative_id=lines.cooperative_id
                 AND accounts.id=lines.account_id
                WHERE lines.cooperative_id=:cid AND accounts.code='cash_on_hand'"""),
            {"cid": cooperative_id},
        )
        return int(balance or 0)


async def list_cash_awaiting_deposit(current: CurrentSession) -> list[dict[str, Any]]:
    _role(current, _PAYMENT_READ_ROLES)
    async with transaction(current.cooperative_id) as session:
        result = await session.execute(
            text("""SELECT p.id,p.member_id,p.receipt_no,p.purpose,p.amount_kobo,
                p.provider_reference,p.received_on,p.recorded_at,p.recorded_by,
                coalesce(policy.cash_banking_days,2) AS cash_banking_days,
                (p.received_on + coalesce(policy.cash_banking_days,2) <
                    (now() AT TIME ZONE 'Africa/Lagos')::date) AS overdue
              FROM payments p
              LEFT JOIN LATERAL (
                SELECT v.cash_banking_days FROM cooperative_policy_versions v
                 WHERE v.cooperative_id=p.cooperative_id AND v.status='approved'
                   AND v.effective_from <= p.received_on
                   AND (v.effective_to IS NULL OR p.received_on < v.effective_to)
                 ORDER BY v.effective_from DESC LIMIT 1
              ) policy ON true
             WHERE p.cooperative_id=:cid AND p.channel='cash' AND p.status='received'
             ORDER BY p.received_on,p.recorded_at,p.id"""),
            {"cid": current.cooperative_id},
        )
        rows = []
        for row in result.mappings().all():
            item = dict(row)
            for field in ("id", "member_id", "recorded_by"):
                item[field] = str(item[field])
            rows.append(item)
        return rows


async def record_cash_deposit(
    current: CurrentSession,
    *,
    slip_reference: str,
    deposited_on: date,
    payment_ids: list[uuid.UUID],
    idempotency_key: str,
    ip: str | None = None,
) -> dict[str, Any]:
    role = _role(current, {"treasurer"})
    slip = slip_reference.strip()
    if not slip or len(slip) > 160:
        raise ValueError("Bank deposit slip reference must be 1–160 characters")
    if not idempotency_key.strip():
        raise ValueError("Idempotency key is required")
    if not payment_ids:
        raise ValueError("Select at least one cash receipt")
    if len(payment_ids) != len(set(payment_ids)):
        raise Conflict("A cash receipt cannot be included twice in one deposit")

    cid = current.cooperative_id
    ordered_ids = sorted(payment_ids, key=str)
    payload = {
        "operation": "cash_deposit_confirm",
        "slip_reference": slip,
        "deposited_on": deposited_on.isoformat(),
        "payment_ids": [str(payment_id) for payment_id in ordered_ids],
    }
    async with transaction(cid) as session:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 33303))"),
            {"key": f"{cid}:cash-deposit-key:{idempotency_key}"},
        )
        request_hash = await _request_hash(session, payload)
        existing = (
            (
                await session.execute(
                    text("""SELECT id,request_hash,amount_kobo,ledger_entry_id
                    FROM cash_deposits WHERE cooperative_id=:cid AND idempotency_key=:key"""),
                    {"cid": cid, "key": idempotency_key},
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            if not hmac.compare_digest(bytes(existing["request_hash"]), request_hash):
                raise Conflict("Idempotency key was reused with a different payload")
            return {
                "id": str(existing["id"]),
                "status": "banked",
                "slip_reference": slip,
                "deposited_on": deposited_on,
                "amount_kobo": int(existing["amount_kobo"]),
                "payment_ids": [str(payment_id) for payment_id in ordered_ids],
                "ledger_entry_id": str(existing["ledger_entry_id"]),
            }

        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 33304))"),
            {"key": f"{cid}:cash-deposit-slip:{slip}"},
        )
        duplicate_slip = await session.scalar(
            text("SELECT id FROM cash_deposits WHERE cooperative_id=:cid AND slip_reference=:slip"),
            {"cid": cid, "slip": slip},
        )
        if duplicate_slip is not None:
            raise Conflict("A deposit with this slip reference already exists")

        rows = (
            (
                await session.execute(
                    text("""SELECT id,recorded_by,received_on,amount_kobo,status
                    FROM payments WHERE cooperative_id=:cid AND id=ANY(:ids)
                    ORDER BY id FOR UPDATE"""),
                    {"cid": cid, "ids": ordered_ids},
                )
            )
            .mappings()
            .all()
        )
        if len(rows) != len(ordered_ids):
            raise NotFound("One or more cash receipts were not found")
        if any(row["status"] != "received" for row in rows):
            raise Conflict("Only cash receipts awaiting deposit can be banked")
        if any(row["recorded_by"] == current.user_id for row in rows):
            raise Conflict(
                "The treasurer confirming a deposit must differ from every receipt recorder"
            )
        if any(row["received_on"] > deposited_on for row in rows):
            raise Conflict("Deposit date cannot be earlier than a cash receipt date")
        amount_kobo = sum(int(row["amount_kobo"]) for row in rows)
        if amount_kobo <= 0:
            raise Conflict("Cash deposit amount must be greater than zero")
        cash_balance = await session.scalar(
            text("""SELECT coalesce(sum(lines.debit_kobo-lines.credit_kobo),0)::bigint
                FROM journal_lines lines JOIN accounts accounts
                  ON accounts.cooperative_id=lines.cooperative_id
                 AND accounts.id=lines.account_id
                WHERE lines.cooperative_id=:cid AND accounts.code='cash_on_hand'"""),
            {"cid": cid},
        )
        if int(cash_balance or 0) < amount_kobo:
            raise Conflict("Selected receipts exceed the cash-on-hand ledger balance")

        account_rows = (
            (
                await session.execute(
                    text(
                        "SELECT code,id FROM accounts WHERE cooperative_id=:cid AND code=ANY(:codes)"
                    ),
                    {"cid": cid, "codes": ["bank", "cash_on_hand"]},
                )
            )
            .mappings()
            .all()
        )
        account_ids = {row["code"]: row["id"] for row in account_rows}
        if set(account_ids) != {"bank", "cash_on_hand"}:
            raise Conflict("Required cash deposit ledger accounts are not configured")

        deposit_id = uuid.uuid4()
        lines = [
            {
                "account_id": str(account_ids["bank"]),
                "debit_kobo": amount_kobo,
                "credit_kobo": 0,
            },
            {
                "account_id": str(account_ids["cash_on_hand"]),
                "debit_kobo": 0,
                "credit_kobo": amount_kobo,
            },
        ]
        entry_id = await session.scalar(
            text("""SELECT post_journal_entry_with_payload(
                :cid,:date,'cash_deposit',:source,'Cash bank deposit',:actor,:key,
                CAST(:lines AS jsonb),CAST(:payload AS jsonb),NULL)"""),
            {
                "cid": cid,
                "date": deposited_on,
                "source": deposit_id,
                "actor": current.user_id,
                "key": f"cash-deposit:{deposit_id}",
                "lines": json.dumps(lines),
                "payload": json.dumps(payload, separators=(",", ":")),
            },
        )
        if entry_id is None:
            raise RuntimeError("Cash deposit did not produce a ledger entry")
        await session.execute(
            text("""INSERT INTO cash_deposits
                (cooperative_id,id,slip_reference,deposited_on,amount_kobo,confirmed_by,
                 ledger_entry_id,idempotency_key,request_hash)
                VALUES(:cid,:id,:slip,:date,:amount,:actor,:entry,:key,:hash)"""),
            {
                "cid": cid,
                "id": deposit_id,
                "slip": slip,
                "date": deposited_on,
                "amount": amount_kobo,
                "actor": current.user_id,
                "entry": entry_id,
                "key": idempotency_key,
                "hash": request_hash,
            },
        )
        for payment_id in ordered_ids:
            await session.execute(
                text("""INSERT INTO cash_deposit_receipts
                    (cooperative_id,deposit_id,payment_id) VALUES(:cid,:deposit,:payment)"""),
                {"cid": cid, "deposit": deposit_id, "payment": payment_id},
            )
        await session.execute(
            text("""UPDATE payments SET status='banked',confirmed_by=:actor,confirmed_at=now()
                WHERE cooperative_id=:cid AND id=ANY(:ids)"""),
            {"cid": cid, "actor": current.user_id, "ids": ordered_ids},
        )
        await append_audit(
            session,
            cooperative_id=cid,
            actor_user_id=current.user_id,
            action="cash_deposit_confirmed",
            entity_type="cash_deposit",
            entity_id=str(deposit_id),
            role=role,
            summary={
                "amount_kobo": amount_kobo,
                "receipt_count": len(ordered_ids),
                "slip_reference": slip,
            },
            channel="api",
            ip=ip,
        )
        return {
            "id": str(deposit_id),
            "status": "banked",
            "slip_reference": slip,
            "deposited_on": deposited_on,
            "amount_kobo": amount_kobo,
            "payment_ids": [str(payment_id) for payment_id in ordered_ids],
            "ledger_entry_id": str(entry_id),
        }
