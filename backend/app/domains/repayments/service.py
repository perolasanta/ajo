"""Append-only repayment receipt posting, waterfall allocation, and reversals."""

from __future__ import annotations

import hmac
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import lock_cooperative_member, transaction
from app.core.errors import Conflict, Forbidden, NotFound
from app.domains.audit.service import append_audit
from app.domains.auth.service import CurrentSession

_COMPONENT_COLUMNS = {
    "principal": "principal_kobo",
    "interest": "interest_kobo",
    "dues": "dues_kobo",
    "savings": "savings_kobo",
}
_ACCOUNT_CODES = {
    "principal": "loans_receivable",
    "interest": "interest_income",
    "dues": "dues_income",
    "savings": "member_savings",
}


def _role(current: CurrentSession, allowed: set[str]) -> str:
    role = next((role for role in current.roles if role in allowed), None)
    if role is None:
        raise Forbidden("This role cannot record repayments")
    return role


@asynccontextmanager
async def _transaction_or_session(
    cooperative_id: uuid.UUID, db_session: AsyncSession | None
) -> AsyncIterator[AsyncSession]:
    if db_session is None:
        async with transaction(cooperative_id) as session:
            yield session
    else:
        yield db_session


async def post_repayment(
    current: CurrentSession,
    *,
    loan_id: uuid.UUID,
    member_id: uuid.UUID,
    amount_kobo: int,
    source: str,
    received_on: date,
    reference: str,
    idempotency_key: str,
    ip: str | None,
    reason: str | None = None,
    db_session: AsyncSession | None = None,
) -> uuid.UUID:
    role = _role(current, {"officer", "treasurer", "admin"})
    if amount_kobo <= 0 or source not in {"payroll", "bank_transfer", "cash"}:
        raise ValueError("Repayment amount and source are invalid")
    if not reference.strip() or not idempotency_key.strip():
        raise ValueError("Repayment reference and idempotency key are required")
    cid = current.cooperative_id
    normalized_reason = reason.strip() if reason and reason.strip() else None
    request_payload = {
        "operation": "loan_repayment",
        "loan_id": str(loan_id),
        "member_id": str(member_id),
        "amount_kobo": amount_kobo,
        "source": source,
        "received_on": received_on.isoformat(),
        "reference": reference,
        "reason": normalized_reason,
    }
    payload_json = json.dumps(request_payload, separators=(",", ":"))
    async with _transaction_or_session(cid, db_session) as session:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 9009))"),
            {"key": f"{cid}:repayment:{idempotency_key}"},
        )
        request_hash = await session.scalar(
            text("SELECT canonical_request_hash(CAST(:payload AS jsonb))"),
            {"payload": payload_json},
        )
        existing = (
            (
                await session.execute(
                    text(
                        "SELECT id,request_hash FROM repayments WHERE cooperative_id=:cid AND idempotency_key=:key"
                    ),
                    {"cid": cid, "key": idempotency_key},
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            if not hmac.compare_digest(bytes(existing["request_hash"]), bytes(request_hash)):
                raise Conflict("Idempotency key was reused with a different payload")
            return uuid.UUID(str(existing["id"]))
        loan = (
            (
                await session.execute(
                    text("""SELECT l.id,l.member_id,l.status,l.deductions_paused,
          p.payroll_allocation_order FROM loans l JOIN cooperative_policy_versions p
          ON p.cooperative_id=l.cooperative_id AND p.id=l.policy_version_id
          WHERE l.cooperative_id=:cid AND l.id=:loan FOR UPDATE OF l"""),
                    {"cid": cid, "loan": loan_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if loan is None or loan["member_id"] != member_id:
            raise NotFound("Loan for member not found")
        if loan["status"] not in {"repaying", "disputed"}:
            raise Conflict("Loan is not accepting repayments")
        if loan["status"] == "disputed" or loan["deductions_paused"]:
            if source == "payroll":
                raise Conflict(
                    "Disputed or deduction-paused loans cannot be repaid through payroll"
                )
            if role != "treasurer":
                raise Forbidden("Only a treasurer may manually repay a disputed or paused loan")
            if not reason or not reason.strip():
                raise Conflict(
                    "A reason is required for manual repayment of a disputed or paused loan"
                )
        await lock_cooperative_member(session, cid, member_id)
        schedules = (
            (
                await session.execute(
                    text("""SELECT s.id,s.installment_no,s.principal_kobo,s.interest_kobo,
          s.dues_kobo,s.savings_kobo,
          coalesce(sum(ra.amount_kobo) FILTER (WHERE ra.component='principal' AND ra.status='posted'),0) allocated_principal,
          coalesce(sum(ra.amount_kobo) FILTER (WHERE ra.component='interest' AND ra.status='posted'),0) allocated_interest,
          coalesce(sum(ra.amount_kobo) FILTER (WHERE ra.component='dues' AND ra.status='posted'),0) allocated_dues,
          coalesce(sum(ra.amount_kobo) FILTER (WHERE ra.component='savings' AND ra.status='posted'),0) allocated_savings
          FROM loan_schedule s LEFT JOIN repayment_allocations ra
            ON ra.cooperative_id=s.cooperative_id AND ra.schedule_id=s.id
          WHERE s.cooperative_id=:cid AND s.loan_id=:loan
            AND (s.due_at AT TIME ZONE 'Africa/Lagos')::date <= :received
          GROUP BY s.id,s.installment_no,s.principal_kobo,s.interest_kobo,s.dues_kobo,s.savings_kobo
          ORDER BY s.installment_no"""),
                    {"cid": cid, "loan": loan_id, "received": received_on},
                )
            )
            .mappings()
            .all()
        )
        remaining = amount_kobo
        allocations: list[dict[str, Any]] = []
        for schedule in schedules:
            for component in loan["payroll_allocation_order"]:
                due = int(schedule[_COMPONENT_COLUMNS[component]]) - int(
                    schedule[f"allocated_{component}"]
                )
                allocated = min(remaining, max(due, 0))
                if allocated:
                    allocations.append(
                        {
                            "schedule_id": schedule["id"],
                            "component": component,
                            "amount_kobo": allocated,
                        }
                    )
                    remaining -= allocated
                if not remaining:
                    break
            if not remaining:
                break
        accounts = (
            (
                await session.execute(
                    text(
                        "SELECT code,id FROM accounts WHERE cooperative_id=:cid AND code=ANY(:codes)"
                    ),
                    {
                        "cid": cid,
                        "codes": ["bank", "unallocated_receipts", *_ACCOUNT_CODES.values()],
                    },
                )
            )
            .mappings()
            .all()
        )
        ids = {row["code"]: row["id"] for row in accounts}
        missing = {"bank", "unallocated_receipts", *_ACCOUNT_CODES.values()} - ids.keys()
        if missing:
            raise Conflict("Required repayment ledger accounts are not configured")
        repayment_id = uuid.uuid4()
        lines: list[dict[str, Any]] = [
            {
                "account_id": str(ids["bank"]),
                "member_id": str(member_id),
                "loan_id": str(loan_id),
                "debit_kobo": amount_kobo,
                "credit_kobo": 0,
            }
        ]
        for allocation in allocations:
            lines.append(
                {
                    "account_id": str(ids[_ACCOUNT_CODES[allocation["component"]]]),
                    "member_id": str(member_id),
                    "loan_id": str(loan_id),
                    "debit_kobo": 0,
                    "credit_kobo": allocation["amount_kobo"],
                }
            )
        if remaining:
            lines.append(
                {
                    "account_id": str(ids["unallocated_receipts"]),
                    "member_id": str(member_id),
                    "loan_id": str(loan_id),
                    "debit_kobo": 0,
                    "credit_kobo": remaining,
                }
            )
        entry_id = await session.scalar(
            text("""SELECT post_journal_entry_with_payload(:cid,:date,'loan_repayment',:source,
          'Loan repayment receipt',:actor,:key,CAST(:lines AS jsonb),CAST(:request_payload AS jsonb),NULL)"""),
            {
                "cid": cid,
                "date": received_on,
                "source": repayment_id,
                "actor": current.user_id,
                "key": f"repayment:{idempotency_key}",
                "lines": json.dumps(lines),
                "request_payload": payload_json,
            },
        )
        await session.execute(
            text("""INSERT INTO repayments(cooperative_id,id,loan_id,member_id,amount_kobo,source,
          received_on,reference,journal_entry_id,idempotency_key,request_hash,created_by,reason)
          VALUES(:cid,:id,:loan,:member,:amount,:source,:received,:reference,:entry,:key,:request_hash,:actor,:reason)"""),
            {
                "cid": cid,
                "id": repayment_id,
                "loan": loan_id,
                "member": member_id,
                "amount": amount_kobo,
                "source": source,
                "received": received_on,
                "reference": reference,
                "entry": entry_id,
                "key": idempotency_key,
                "request_hash": request_hash,
                "actor": current.user_id,
                "reason": normalized_reason,
            },
        )
        for allocation in allocations:
            await session.execute(
                text("""INSERT INTO repayment_allocations(cooperative_id,repayment_id,schedule_id,component,amount_kobo)
              VALUES(:cid,:repayment,:schedule,:component,:amount)"""),
                {
                    "cid": cid,
                    "repayment": repayment_id,
                    "schedule": allocation["schedule_id"],
                    "component": allocation["component"],
                    "amount": allocation["amount_kobo"],
                },
            )
        if remaining:
            await session.execute(
                text("""INSERT INTO fraud_alerts(cooperative_id,rule_code,entity_type,entity_id,summary)
              VALUES(:cid,'unallocated_receipt','repayment',:id,CAST(:summary AS jsonb))"""),
                {"cid": cid, "id": repayment_id, "summary": json.dumps({"amount_kobo": remaining})},
            )
        if loan["deductions_paused"]:
            await session.execute(
                text("""INSERT INTO fraud_alerts(cooperative_id,rule_code,entity_type,entity_id,summary)
              VALUES(:cid,'paused_deduction_receipt','repayment',:id,'{}'::jsonb)"""),
                {"cid": cid, "id": repayment_id},
            )
        outstanding_principal = await session.scalar(
            text("""SELECT coalesce(sum(s.principal_kobo-a.allocated),0)
          FROM loan_schedule s LEFT JOIN LATERAL (SELECT coalesce(sum(amount_kobo),0) AS allocated
            FROM repayment_allocations WHERE cooperative_id=s.cooperative_id AND schedule_id=s.id
              AND component='principal' AND status='posted') a ON true
          WHERE s.cooperative_id=:cid AND s.loan_id=:loan"""),
            {"cid": cid, "loan": loan_id},
        )
        if int(outstanding_principal or 0) == 0:
            await session.execute(
                text("UPDATE loans SET status='closed' WHERE cooperative_id=:cid AND id=:loan"),
                {"cid": cid, "loan": loan_id},
            )
            await session.execute(
                text("""UPDATE loan_applications SET status='closed',updated_at=now()
                  WHERE cooperative_id=:cid AND id=(SELECT application_id FROM loans
                    WHERE cooperative_id=:cid AND id=:loan)
                    AND status IN ('repaying','disbursed','restructured')"""),
                {"cid": cid, "loan": loan_id},
            )
            await session.execute(
                text(
                    "UPDATE savings_locks SET released_at=now() WHERE cooperative_id=:cid AND loan_id=:loan AND released_at IS NULL"
                ),
                {"cid": cid, "loan": loan_id},
            )
            await append_audit(
                session,
                cooperative_id=cid,
                actor_user_id=current.user_id,
                role=role,
                action="loan_closed_by_repayment",
                entity_type="loan",
                entity_id=str(loan_id),
                summary={"repayment_id": str(repayment_id)},
                channel="api",
                ip=ip,
            )
        await append_audit(
            session,
            cooperative_id=cid,
            actor_user_id=current.user_id,
            role=role,
            action="repayment_posted",
            entity_type="repayment",
            entity_id=str(repayment_id),
            summary={
                "loan_id": str(loan_id),
                "amount_kobo": amount_kobo,
                "unallocated_kobo": remaining,
            },
            channel="api",
            ip=ip,
            reason=reason,
        )
    return repayment_id


async def reverse_repayment(
    current: CurrentSession, repayment_id: uuid.UUID, reason: str, ip: str | None
) -> uuid.UUID:
    role = _role(current, {"committee", "admin"})
    if not reason.strip():
        raise ValueError("A reversal reason is required")
    cid = current.cooperative_id
    async with transaction(cid) as session:
        repayment = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM repayments WHERE cooperative_id=:cid AND id=:id FOR UPDATE"
                    ),
                    {"cid": cid, "id": repayment_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if repayment is None:
            raise NotFound("Repayment not found")
        if repayment["status"] != "posted" or repayment["created_by"] == current.user_id:
            raise Conflict("A different approver must reverse a posted repayment")
        await lock_cooperative_member(session, cid, repayment["member_id"])
        original_lines = (
            (
                await session.execute(
                    text("""SELECT account_id,member_id,loan_id,debit_kobo,credit_kobo FROM journal_lines
          WHERE cooperative_id=:cid AND entry_id=:entry ORDER BY line_number"""),
                    {"cid": cid, "entry": repayment["journal_entry_id"]},
                )
            )
            .mappings()
            .all()
        )
        lines = [
            {
                "account_id": str(line["account_id"]),
                "member_id": str(line["member_id"] or ""),
                "loan_id": str(line["loan_id"] or ""),
                "debit_kobo": line["credit_kobo"],
                "credit_kobo": line["debit_kobo"],
            }
            for line in original_lines
        ]
        entry_id = await session.scalar(
            text("""SELECT post_journal_entry(:cid,current_date,'loan_repayment_reversal',:source,
          :description,:actor,:key,CAST(:lines AS jsonb),:reverses)"""),
            {
                "cid": cid,
                "source": repayment_id,
                "description": f"Loan repayment reversal: {reason}",
                "actor": current.user_id,
                "key": f"repayment-reversal:{repayment_id}",
                "lines": json.dumps(lines),
                "reverses": repayment["journal_entry_id"],
            },
        )
        await session.execute(
            text("""UPDATE repayments SET status='reversed',reversed_by=:actor,reversed_at=now(),
          reversal_reason=:reason,reversal_entry_id=:entry WHERE cooperative_id=:cid AND id=:id"""),
            {
                "cid": cid,
                "id": repayment_id,
                "actor": current.user_id,
                "reason": reason,
                "entry": entry_id,
            },
        )
        await session.execute(
            text(
                "UPDATE repayment_allocations SET status='reversed' WHERE cooperative_id=:cid AND repayment_id=:id"
            ),
            {"cid": cid, "id": repayment_id},
        )
        await session.execute(
            text(
                "UPDATE loans SET status='repaying' WHERE cooperative_id=:cid AND id=:loan AND status='closed'"
            ),
            {"cid": cid, "loan": repayment["loan_id"]},
        )
        await session.execute(
            text("""UPDATE loan_applications SET status='repaying',updated_at=now()
              WHERE cooperative_id=:cid AND id=(SELECT application_id FROM loans
                WHERE cooperative_id=:cid AND id=:loan) AND status='closed'"""),
            {"cid": cid, "loan": repayment["loan_id"]},
        )
        await session.execute(
            text(
                "UPDATE savings_locks SET released_at=NULL WHERE cooperative_id=:cid AND loan_id=:loan"
            ),
            {"cid": cid, "loan": repayment["loan_id"]},
        )
        await append_audit(
            session,
            cooperative_id=cid,
            actor_user_id=current.user_id,
            role=role,
            action="repayment_reversed",
            entity_type="repayment",
            entity_id=str(repayment_id),
            summary={"reversal_entry_id": str(entry_id)},
            channel="api",
            ip=ip,
            reason=reason,
        )
    return uuid.UUID(str(entry_id))
