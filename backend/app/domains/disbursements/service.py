"""Manually authorized disbursement workflow and final ledger posting."""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

from sqlalchemy import text

from app.core.db import lock_cooperative_member, transaction
from app.core.errors import Conflict, Forbidden, NotFound
from app.domains.audit.service import append_audit
from app.domains.auth.service import CurrentSession
from app.domains.loans.service import _effective_policy, _eligibility, _load_application, _today_wat


def _role(current: CurrentSession, allowed: set[str]) -> None:
    if not allowed.intersection(current.roles):
        raise Forbidden("This role cannot perform the disbursement action")


async def _load(session: Any, cid: uuid.UUID, loan_id: uuid.UUID) -> Any:
    row = (
        (
            await session.execute(
                text("""SELECT l.id,l.member_id,l.status AS loan_status,l.application_id,
      a.status AS application_status,a.submitted_by,a.policy_version_id,a.product_id,a.current_terms_snapshot_id,
      a.destination_bank_account_id,a.beneficiary_member_id,a.principal_kobo,s.terms,s.terms_sha256,p.fee_bps,
      b.bank_code,b.last4,b.account_name,b.status AS bank_status,b.verified_at
      FROM loans l JOIN loan_applications a ON a.cooperative_id=l.cooperative_id AND a.id=l.application_id
      JOIN loan_terms_snapshots s ON s.cooperative_id=a.cooperative_id AND s.id=a.current_terms_snapshot_id
      JOIN loan_products p ON p.cooperative_id=a.cooperative_id AND p.id=a.product_id
      LEFT JOIN bank_accounts b ON b.cooperative_id=a.cooperative_id AND b.id=a.destination_bank_account_id
      WHERE l.cooperative_id=:cid AND l.id=:loan FOR UPDATE OF l,a"""),
                {"cid": cid, "loan": loan_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise NotFound("Loan not found")
    return row


async def _validate(session: Any, cid: uuid.UUID, loan: Any) -> bool:
    await lock_cooperative_member(session, cid, loan["member_id"])
    if loan["application_status"] != "ready_for_disbursement":
        raise Conflict("Loan is not ready for disbursement")
    consent = (
        await session.execute(
            text("""SELECT terms_hash FROM member_consents
      WHERE cooperative_id=:cid AND application_id=:app AND member_id=:borrower
        AND kind='final_terms' AND status='confirmed'
      ORDER BY decided_at DESC LIMIT 1"""),
            {"cid": cid, "app": loan["application_id"], "borrower": loan["member_id"]},
        )
    ).scalar_one_or_none()
    if consent is None or bytes(consent) != bytes(loan["terms_sha256"]):
        raise Conflict("Borrower final-terms confirmation is missing or no longer current")
    beneficiary_id = loan["beneficiary_member_id"]
    if beneficiary_id is not None and beneficiary_id != loan["member_id"]:
        beneficiary_consent = (
            await session.execute(
                text("""SELECT terms_hash FROM member_consents
                  WHERE cooperative_id=:cid AND application_id=:app AND member_id=:beneficiary
                    AND kind='beneficiary' AND status='confirmed'
                  ORDER BY decided_at DESC LIMIT 1"""),
                {"cid": cid, "app": loan["application_id"], "beneficiary": beneficiary_id},
            )
        ).scalar_one_or_none()
        if beneficiary_consent is None or bytes(beneficiary_consent) != bytes(loan["terms_sha256"]):
            raise Conflict("Beneficiary confirmation is missing or no longer current")
    open_dispute = await session.scalar(
        text("""SELECT EXISTS(SELECT 1 FROM disputes WHERE cooperative_id=:cid
      AND application_id=:app AND status IN ('open','under_investigation'))"""),
        {"cid": cid, "app": loan["application_id"]},
    )
    if open_dispute:
        raise Conflict("An open dispute blocks disbursement")
    expected_destination = loan["terms"].get("destination")
    actual_destination = {
        "bank_code": loan["bank_code"],
        "last4": loan["last4"],
        "account_name": loan["account_name"],
        "status": loan["bank_status"],
        "verified": loan["verified_at"] is not None,
    }
    if (
        loan["bank_status"] != "verified"
        or loan["verified_at"] is None
        or loan["destination_bank_account_id"] is None
        or str(loan["terms"].get("destination_bank_account_id") or "")
        != str(loan["destination_bank_account_id"])
        or expected_destination != actual_destination
    ):
        await session.execute(
            text("""INSERT INTO fraud_alerts(cooperative_id,rule_code,entity_type,entity_id,summary)
          VALUES(:cid,'destination_changed','loan_application',:app,'{"stage":"disbursement"}'::jsonb)"""),
            {"cid": cid, "app": loan["application_id"]},
        )
        return False
    application = await _load_application(session, cid, loan["application_id"])
    policy = await _effective_policy(session, cid, _today_wat())
    eligibility = await _eligibility(session, application, policy, exclude_loan_id=loan["id"])
    if not eligibility["eligible"]:
        raise Conflict("Loan no longer meets current eligibility rules")
    approval = (
        (
            await session.execute(
                text("""SELECT id,savings_backing_bps,savings_backing_amount_kobo
                  FROM approvals WHERE cooperative_id=:cid AND application_id=:app AND decision='approved'
                  ORDER BY created_at DESC LIMIT 1"""),
                {"cid": cid, "app": loan["application_id"]},
            )
        )
        .mappings()
        .one_or_none()
    )
    if approval is None:
        raise Conflict("Approved loan decision record is missing")
    existing_lock = (
        (
            await session.execute(
                text("""SELECT id,amount_kobo FROM savings_locks
                  WHERE cooperative_id=:cid AND loan_id=:loan AND released_at IS NULL FOR UPDATE"""),
                {"cid": cid, "loan": loan["id"]},
            )
        )
        .mappings()
        .one_or_none()
    )
    bps = approval["savings_backing_bps"]
    amount = approval["savings_backing_amount_kobo"]
    if bps is None or amount is None:
        raise Conflict("Approved loan decision is missing its savings backing snapshot")
    amount = int(amount)
    # Preserve a larger legacy lock, but never rewrite the approval-time snapshot.
    existing_amount = int(existing_lock["amount_kobo"]) if existing_lock else 0
    lock_amount = max(amount, existing_amount)
    additional_lock = max(0, lock_amount - existing_amount)
    if additional_lock > int(eligibility["available_savings_kobo"]):
        raise Conflict("Insufficient unlocked savings to back this loan")
    if lock_amount > 0 and existing_lock is None:
        await session.execute(
            text("""INSERT INTO savings_locks(cooperative_id,member_id,loan_id,amount_kobo)
          VALUES(:cid,:member,:loan,:amount)"""),
            {
                "cid": cid,
                "member": loan["member_id"],
                "loan": loan["id"],
                "amount": lock_amount,
            },
        )
    elif existing_lock is not None and int(existing_lock["amount_kobo"]) < amount:
        await session.execute(
            text(
                "UPDATE savings_locks SET amount_kobo=:amount WHERE cooperative_id=:cid AND id=:id"
            ),
            {"cid": cid, "id": existing_lock["id"], "amount": amount},
        )
    return True


async def prepare(current: CurrentSession, loan_id: uuid.UUID, ip: str | None) -> uuid.UUID | None:
    _role(current, {"treasurer"})
    cid = current.cooperative_id
    async with transaction(cid) as session:
        loan = await _load(session, cid, loan_id)
        if not await _validate(session, cid, loan):
            return None
        fee = int(loan["principal_kobo"]) * int(loan["fee_bps"]) // 10_000
        net = int(loan["principal_kobo"]) - fee
        if net <= 0:
            raise Conflict("Disbursement fee must be lower than principal")
        disbursement_id = uuid.uuid4()
        await session.execute(
            text("""INSERT INTO disbursements(cooperative_id,id,loan_id,amount_kobo,
          fee_kobo,net_kobo,bank_code,last4,account_name,bank_account_id,prepared_by)
          VALUES(:cid,:id,:loan,:amount,:fee,:net,:bank,:last4,:name,:account,:actor)"""),
            {
                "cid": cid,
                "id": disbursement_id,
                "loan": loan_id,
                "amount": loan["principal_kobo"],
                "fee": fee,
                "net": net,
                "bank": loan["bank_code"],
                "last4": loan["last4"],
                "name": loan["account_name"],
                "account": loan["destination_bank_account_id"],
                "actor": current.user_id,
            },
        )
        await append_audit(
            session,
            cooperative_id=cid,
            actor_user_id=current.user_id,
            role="treasurer",
            action="disbursement_prepared",
            entity_type="disbursement",
            entity_id=str(disbursement_id),
            summary={"loan_id": str(loan_id), "amount_kobo": int(loan["principal_kobo"])},
            channel="api",
            ip=ip,
        )
    return disbursement_id


async def authorize(current: CurrentSession, disbursement_id: uuid.UUID, ip: str | None) -> bool:
    _role(current, {"admin"})
    cid = current.cooperative_id
    async with transaction(cid) as session:
        row = (
            (
                await session.execute(
                    text("""SELECT d.*,l.member_id,l.application_id,a.submitted_by
          FROM disbursements d JOIN loans l ON l.cooperative_id=d.cooperative_id AND l.id=d.loan_id
          JOIN loan_applications a ON a.cooperative_id=l.cooperative_id AND a.id=l.application_id
          WHERE d.cooperative_id=:cid AND d.id=:id FOR UPDATE OF d"""),
                    {"cid": cid, "id": disbursement_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise NotFound("Disbursement not found")
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(CAST(CAST(:member AS uuid) AS text)))"),
            {"member": row["member_id"]},
        )
        if not await _validate(session, cid, await _load(session, cid, row["loan_id"])):
            return False
        if row["status"] != "pending_authorization":
            raise Conflict("Disbursement is not awaiting authorization")
        approver_ids = (
            (
                await session.execute(
                    text(
                        "SELECT approver_id FROM approvals WHERE cooperative_id=:cid AND application_id=:app"
                    ),
                    {"cid": cid, "app": row["application_id"]},
                )
            )
            .scalars()
            .all()
        )
        if current.user_id in {row["prepared_by"], row["submitted_by"], *approver_ids}:
            raise Conflict(
                "Authorizer must be independent of preparation, application and loan approval"
            )
        await session.execute(
            text("""UPDATE disbursements SET status='authorized',authorized_by=:actor,
          authorized_at=now() WHERE cooperative_id=:cid AND id=:id"""),
            {"cid": cid, "id": disbursement_id, "actor": current.user_id},
        )
        await append_audit(
            session,
            cooperative_id=cid,
            actor_user_id=current.user_id,
            role="disbursement_authorizer",
            action="disbursement_authorized",
            entity_type="disbursement",
            entity_id=str(disbursement_id),
            summary={"loan_id": str(row["loan_id"])},
            channel="api",
            ip=ip,
        )
    return True


async def record_transfer(
    current: CurrentSession,
    disbursement_id: uuid.UUID,
    reference: str,
    transfer_date: date,
    ip: str | None,
) -> bool:
    _role(current, {"treasurer"})
    cid = current.cooperative_id
    async with transaction(cid) as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM disbursements WHERE cooperative_id=:cid AND id=:id FOR UPDATE"
                    ),
                    {"cid": cid, "id": disbursement_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise NotFound("Disbursement not found")
        loan = await _load(session, cid, row["loan_id"])
        if not await _validate(session, cid, loan):
            return False
        if row["status"] != "authorized" or row["authorized_by"] == current.user_id:
            raise Conflict("An independent treasurer must record the authorized transfer")
        await session.execute(
            text("""UPDATE disbursements SET status='transfer_recorded',transfer_reference=:reference,
          transfer_date=:day,transfer_recorded_by=:actor,transfer_recorded_at=now()
          WHERE cooperative_id=:cid AND id=:id"""),
            {
                "cid": cid,
                "id": disbursement_id,
                "reference": reference,
                "day": transfer_date,
                "actor": current.user_id,
            },
        )
        await append_audit(
            session,
            cooperative_id=cid,
            actor_user_id=current.user_id,
            role="treasurer",
            action="disbursement_transfer_recorded",
            entity_type="disbursement",
            entity_id=str(disbursement_id),
            summary={"transfer_date": transfer_date.isoformat()},
            channel="api",
            ip=ip,
        )
    return True


async def confirm_paid(
    current: CurrentSession, disbursement_id: uuid.UUID, ip: str | None
) -> uuid.UUID | None:
    _role(current, {"admin"})
    cid = current.cooperative_id
    async with transaction(cid) as session:
        row = (
            (
                await session.execute(
                    text("""SELECT d.*,l.member_id,l.application_id,a.submitted_by,
          l.product_id FROM disbursements d JOIN loans l ON l.cooperative_id=d.cooperative_id AND l.id=d.loan_id
          JOIN loan_applications a ON a.cooperative_id=l.cooperative_id AND a.id=l.application_id
          WHERE d.cooperative_id=:cid AND d.id=:id FOR UPDATE OF d"""),
                    {"cid": cid, "id": disbursement_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise NotFound("Disbursement not found")
        loan = await _load(session, cid, row["loan_id"])
        if not await _validate(session, cid, loan):
            return None
        if row["status"] != "transfer_recorded" or current.user_id == row["authorized_by"]:
            raise Conflict("A different admin must confirm a recorded transfer")
        approvers = (
            (
                await session.execute(
                    text(
                        "SELECT approver_id FROM approvals WHERE cooperative_id=:cid AND application_id=:app"
                    ),
                    {"cid": cid, "app": row["application_id"]},
                )
            )
            .scalars()
            .all()
        )
        if current.user_id in {row["prepared_by"], row["submitted_by"], *approvers}:
            raise Conflict("Paid confirmation requires an independent authorizer")
        accounts = (
            (
                await session.execute(
                    text(
                        "SELECT code,id FROM accounts WHERE cooperative_id=:cid AND code=ANY(:codes)"
                    ),
                    {"cid": cid, "codes": ["loans_receivable", "bank", "fee_income"]},
                )
            )
            .mappings()
            .all()
        )
        account_ids = {item["code"]: item["id"] for item in accounts}
        if (
            "loans_receivable" not in account_ids
            or "bank" not in account_ids
            or (row["fee_kobo"] and "fee_income" not in account_ids)
        ):
            raise Conflict("Required ledger accounts are not configured")
        lines = [
            {
                "account_id": str(account_ids["loans_receivable"]),
                "member_id": str(row["member_id"]),
                "loan_id": str(row["loan_id"]),
                "debit_kobo": row["amount_kobo"],
                "credit_kobo": 0,
            },
            {
                "account_id": str(account_ids["bank"]),
                "member_id": None,
                "loan_id": str(row["loan_id"]),
                "debit_kobo": 0,
                "credit_kobo": row["net_kobo"],
            },
        ]
        if row["fee_kobo"]:
            lines.append(
                {
                    "account_id": str(account_ids["fee_income"]),
                    "member_id": None,
                    "loan_id": str(row["loan_id"]),
                    "debit_kobo": 0,
                    "credit_kobo": row["fee_kobo"],
                }
            )
        entry_id = await session.scalar(
            text("""SELECT post_journal_entry(:cid,:date,'loan_disbursement',:source,
          :description,:actor,:key,CAST(:lines AS jsonb),NULL)"""),
            {
                "cid": cid,
                "date": row["transfer_date"],
                "source": disbursement_id,
                "description": "Loan disbursement",
                "actor": current.user_id,
                "key": f"disbursement:{disbursement_id}",
                "lines": json.dumps(lines),
            },
        )
        await session.execute(
            text("""UPDATE disbursements SET status='paid',paid_confirmed_by=:actor,
          paid_at=now(),journal_entry_id=:entry WHERE cooperative_id=:cid AND id=:id"""),
            {"cid": cid, "actor": current.user_id, "entry": entry_id, "id": disbursement_id},
        )
        await session.execute(
            text("UPDATE loans SET status='repaying' WHERE cooperative_id=:cid AND id=:id"),
            {"cid": cid, "id": row["loan_id"]},
        )
        await session.execute(
            text(
                "UPDATE loan_schedule SET activated_at=now() WHERE cooperative_id=:cid AND loan_id=:loan"
            ),
            {"cid": cid, "loan": row["loan_id"]},
        )
        await session.execute(
            text(
                "UPDATE loan_applications SET status='repaying',updated_at=now() WHERE cooperative_id=:cid AND id=:app"
            ),
            {"cid": cid, "app": row["application_id"]},
        )
        await append_audit(
            session,
            cooperative_id=cid,
            actor_user_id=current.user_id,
            role="disbursement_authorizer",
            action="disbursement_paid_confirmed",
            entity_type="disbursement",
            entity_id=str(disbursement_id),
            summary={"journal_entry_id": str(entry_id), "loan_id": str(row["loan_id"])},
            channel="api",
            ip=ip,
        )
    return uuid.UUID(str(entry_id))


async def reverse_paid(
    current: CurrentSession, disbursement_id: uuid.UUID, reason: str, ip: str | None
) -> uuid.UUID:
    """Record a separately approved, append-only reversal of a paid disbursement."""
    _role(current, {"admin"})
    cid = current.cooperative_id
    async with transaction(cid) as session:
        row = (
            (
                await session.execute(
                    text("""SELECT d.*,l.member_id,l.application_id
          FROM disbursements d JOIN loans l ON l.cooperative_id=d.cooperative_id AND l.id=d.loan_id
          WHERE d.cooperative_id=:cid AND d.id=:id FOR UPDATE OF d"""),
                    {"cid": cid, "id": disbursement_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise NotFound("Disbursement not found")
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(CAST(CAST(:member AS uuid) AS text)))"),
            {"member": row["member_id"]},
        )
        if row["status"] != "paid" or row["paid_confirmed_by"] == current.user_id:
            raise Conflict("A different approver must reverse a paid disbursement")
        original_lines = (
            (
                await session.execute(
                    text("""SELECT account_id,member_id,loan_id,debit_kobo,credit_kobo
          FROM journal_lines WHERE cooperative_id=:cid AND entry_id=:entry ORDER BY line_number"""),
                    {"cid": cid, "entry": row["journal_entry_id"]},
                )
            )
            .mappings()
            .all()
        )
        if not original_lines:
            raise Conflict("Paid disbursement has no journal entry")
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
            text("""SELECT post_journal_entry(:cid,current_date,'loan_disbursement_reversal',:source,
          :description,:actor,:key,CAST(:lines AS jsonb),:reverses)"""),
            {
                "cid": cid,
                "source": disbursement_id,
                "description": f"Loan disbursement reversal: {reason}",
                "actor": current.user_id,
                "key": f"disbursement-reversal:{disbursement_id}",
                "lines": json.dumps(lines),
                "reverses": row["journal_entry_id"],
            },
        )
        await session.execute(
            text("""UPDATE disbursements SET status='cancelled',reversed_by=:actor,
          reversed_at=now(),reversal_reason=:reason,reversal_entry_id=:entry
          WHERE cooperative_id=:cid AND id=:id"""),
            {
                "cid": cid,
                "id": disbursement_id,
                "actor": current.user_id,
                "reason": reason,
                "entry": entry_id,
            },
        )
        await session.execute(
            text(
                "UPDATE loans SET status='ready_for_disbursement' WHERE cooperative_id=:cid AND id=:id"
            ),
            {"cid": cid, "id": row["loan_id"]},
        )
        await session.execute(
            text(
                "UPDATE loan_applications SET status='ready_for_disbursement',updated_at=now() WHERE cooperative_id=:cid AND id=:app"
            ),
            {"cid": cid, "app": row["application_id"]},
        )
        await append_audit(
            session,
            cooperative_id=cid,
            actor_user_id=current.user_id,
            role="disbursement_authorizer",
            action="disbursement_reversed",
            entity_type="disbursement",
            entity_id=str(disbursement_id),
            summary={"journal_entry_id": str(entry_id)},
            channel="api",
            ip=ip,
            reason=reason,
        )
    return uuid.UUID(str(entry_id))


async def fail_transfer(
    current: CurrentSession, disbursement_id: uuid.UUID, reason: str, ip: str | None
) -> None:
    _role(current, {"treasurer", "admin"})
    cid = current.cooperative_id
    async with transaction(cid) as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM disbursements WHERE cooperative_id=:cid AND id=:id FOR UPDATE"
                    ),
                    {"cid": cid, "id": disbursement_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise NotFound("Disbursement not found")
        if (
            row["status"] not in {"authorized", "transfer_recorded"}
            or row["authorized_by"] == current.user_id
        ):
            raise Conflict("An independent treasurer must record transfer failure")
        await session.execute(
            text(
                "UPDATE disbursements SET status='failed',failed_at=now() WHERE cooperative_id=:cid AND id=:id"
            ),
            {"cid": cid, "id": disbursement_id},
        )
        await session.execute(
            text(
                "UPDATE loans SET status='ready_for_disbursement' WHERE cooperative_id=:cid AND id=:id"
            ),
            {"cid": cid, "id": row["loan_id"]},
        )
        await session.execute(
            text(
                "UPDATE loan_applications SET status='ready_for_disbursement',updated_at=now() WHERE cooperative_id=:cid AND id=(SELECT application_id FROM loans WHERE cooperative_id=:cid AND id=:loan)"
            ),
            {"cid": cid, "loan": row["loan_id"]},
        )
        await append_audit(
            session,
            cooperative_id=cid,
            actor_user_id=current.user_id,
            role="treasurer",
            action="disbursement_failed",
            entity_type="disbursement",
            entity_id=str(disbursement_id),
            summary={"reason": reason},
            channel="api",
            ip=ip,
            reason=reason,
        )
