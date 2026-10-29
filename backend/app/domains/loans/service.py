"""Tenant-scoped loan application, eligibility and committee decision services."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.core.db import lock_cooperative_member, transaction
from app.core.errors import Conflict, Forbidden, NotFound
from app.domains.audit.service import append_audit
from app.domains.auth.service import CurrentSession
from app.domains.consent.service import create_consent
from app.domains.loans.calculations import build_schedule, deduction_capacity, max_loan_from_savings

WAT = ZoneInfo("Africa/Lagos")
ACTIVE_LOAN_STATUSES = ("approved", "disbursed", "repaying")


def _role(current: CurrentSession, allowed: set[str]) -> str:
    role = next((value for value in current.roles if value in allowed), None)
    if role is None:
        raise Forbidden("This role cannot perform the loan action")
    return role


def _today_wat() -> date:
    return datetime.now(WAT).date()


async def _effective_policy(session: Any, cooperative_id: uuid.UUID, on_date: date) -> Any:
    row = (
        (
            await session.execute(
                text("""SELECT id,salary_basis,deduction_cap_bps,max_active_loans,
              guarantors_required,min_savings_kobo,loan_to_savings_multiple_x100
              FROM cooperative_policy_versions WHERE cooperative_id=:cid AND status='approved'
              AND effective_from<=:day AND (effective_to IS NULL OR :day<effective_to)
              ORDER BY effective_from DESC LIMIT 1"""),
                {"cid": cooperative_id, "day": on_date},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Conflict("No approved policy is effective for this application")
    return row


async def _load_application(
    session: Any, cooperative_id: uuid.UUID, application_id: uuid.UUID
) -> Any:
    result = await session.execute(
        text(
            """SELECT a.*, p.interest_method FROM loan_applications a
              JOIN loan_products p ON p.cooperative_id=a.cooperative_id AND p.id=a.product_id
              WHERE a.cooperative_id=:cid AND a.id=:id FOR UPDATE OF a"""
        ),
        {"cid": cooperative_id, "id": application_id},
    )
    return result.mappings().one_or_none()


async def _eligibility(
    session: Any, application: Any, policy: Any, exclude_loan_id: uuid.UUID | None = None
) -> dict[str, Any]:
    cid, member_id = application["cooperative_id"], application["member_id"]
    today = _today_wat()
    salary = (
        (
            await session.execute(
                text("""SELECT basis,amount_kobo FROM salary_records WHERE cooperative_id=:cid
              AND member_id=:member AND effective_from<=:day ORDER BY effective_from DESC,created_at DESC LIMIT 1"""),
                {"cid": cid, "member": member_id, "day": today},
            )
        )
        .mappings()
        .one_or_none()
    )
    savings = int(
        (
            await session.execute(
                text("""SELECT coalesce(sum(l.credit_kobo-l.debit_kobo),0) FROM journal_lines l
              JOIN accounts a ON a.cooperative_id=l.cooperative_id AND a.id=l.account_id
              WHERE l.cooperative_id=:cid AND l.member_id=:member AND a.code='member_savings'"""),
                {"cid": cid, "member": member_id},
            )
        ).scalar_one()
    )
    locked = int(
        (
            await session.execute(
                text("""SELECT coalesce(sum(amount_kobo),0) FROM savings_locks
              WHERE cooperative_id=:cid AND member_id=:member AND released_at IS NULL
              AND (CAST(:exclude_loan AS uuid) IS NULL OR loan_id IS NULL OR loan_id<>CAST(:exclude_loan AS uuid))"""),
                {"cid": cid, "member": member_id, "exclude_loan": exclude_loan_id},
            )
        ).scalar_one()
    )
    scheduled = int(
        (
            await session.execute(
                text("""SELECT coalesce(sum(s.payment_kobo),0) FROM loan_schedule s
              JOIN loans l ON l.cooperative_id=s.cooperative_id AND l.id=s.loan_id
              WHERE l.cooperative_id=:cid AND l.member_id=:member AND l.status=ANY(:statuses)
              AND s.due_at > now() AND s.due_at <= now()+interval '1 month'"""),
                {"cid": cid, "member": member_id, "statuses": list(ACTIVE_LOAN_STATUSES)},
            )
        ).scalar_one()
    )
    guarantee_monthly = int(
        (
            await session.execute(
                text("""SELECT coalesce(sum(s.payment_kobo),0) FROM guarantees g
              JOIN loan_applications a ON a.cooperative_id=g.cooperative_id AND a.id=g.application_id
              JOIN loans l ON l.cooperative_id=a.cooperative_id AND l.application_id=a.id
              JOIN loan_schedule s ON s.cooperative_id=l.cooperative_id AND s.loan_id=l.id
              WHERE g.cooperative_id=:cid AND g.guarantor_member_id=:member AND g.status='accepted'
              AND l.status=ANY(:statuses) AND s.due_at>now() AND s.due_at<=now()+interval '1 month'"""),
                {"cid": cid, "member": member_id, "statuses": list(ACTIVE_LOAN_STATUSES)},
            )
        ).scalar_one()
    )
    capacity = deduction_capacity(
        int(salary["amount_kobo"]) if salary and salary["basis"] == policy["salary_basis"] else 0,
        int(policy["deduction_cap_bps"]),
        scheduled + guarantee_monthly,
        None,
    )
    available_savings = savings - locked
    savings_limit = max_loan_from_savings(
        available_savings, int(policy["loan_to_savings_multiple_x100"])
    )
    schedule = build_schedule(
        int(application["principal_kobo"]),
        int(application["annual_rate_bps"]),
        int(application["tenor_months"]),
        application["interest_method"],
        datetime.combine(today + timedelta(days=32), time.min, WAT),
    )
    installment = schedule[0].payment_kobo
    reasons: list[str] = []
    if salary is None or salary["basis"] != policy["salary_basis"]:
        reasons.append("current_salary_missing_or_wrong_basis")
    if installment > capacity.capacity_kobo:
        reasons.append("instalment_exceeds_capacity")
    if (
        available_savings < int(policy["min_savings_kobo"])
        or int(application["principal_kobo"]) > savings_limit
    ):
        reasons.append("savings_safeguard_failed")
    guarantee_count = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM guarantees WHERE cooperative_id=:cid AND application_id=:app AND status<>'released'"
                ),
                {"cid": cid, "app": application["id"]},
            )
        ).scalar_one()
    )
    product_guarantors = int(
        (
            await session.execute(
                text(
                    "SELECT guarantors_required FROM loan_products WHERE cooperative_id=:cid AND id=:id"
                ),
                {"cid": cid, "id": application["product_id"]},
            )
        ).scalar_one()
    )
    if guarantee_count < max(int(policy["guarantors_required"]), product_guarantors):
        reasons.append("required_guarantors_missing")
    active_count = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM loans WHERE cooperative_id=:cid AND member_id=:member AND status=ANY(:statuses)"
                ),
                {"cid": cid, "member": member_id, "statuses": list(ACTIVE_LOAN_STATUSES)},
            )
        ).scalar_one()
    )
    if active_count >= int(policy["max_active_loans"]):
        reasons.append("max_active_loans_reached")
    return {
        "eligible": not reasons,
        "partial_check": capacity.partial_check,
        "capacity_kobo": capacity.capacity_kobo,
        "available_savings_kobo": available_savings,
        "guarantor_monthly_exposure_kobo": guarantee_monthly,
        "reasons": reasons,
    }


async def _fraud_alert(
    session: Any,
    cooperative_id: uuid.UUID,
    rule: str,
    entity_id: uuid.UUID,
    summary: dict[str, Any],
) -> None:
    await session.execute(
        text("""INSERT INTO fraud_alerts(cooperative_id,rule_code,entity_type,entity_id,summary)
          VALUES(:cid,:rule,'loan_application',:entity,CAST(:summary AS jsonb))"""),
        {
            "cid": cooperative_id,
            "rule": rule,
            "entity": entity_id,
            "summary": json.dumps(summary, sort_keys=True, separators=(",", ":")),
        },
    )


async def _validate_product_terms(
    session: Any,
    cooperative_id: uuid.UUID,
    product_id: uuid.UUID,
    principal_kobo: int,
    tenor_months: int,
    annual_rate_bps: int,
) -> Any:
    product = (
        (
            await session.execute(
                text("""SELECT interest_method,annual_rate_bps,min_amount_kobo,max_amount_kobo,
                  min_tenor_months,max_tenor_months FROM loan_products
                  WHERE cooperative_id=:cid AND id=:id"""),
                {"cid": cooperative_id, "id": product_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if product is None:
        raise NotFound("Loan product not found")
    if not product["min_amount_kobo"] <= principal_kobo <= product["max_amount_kobo"]:
        raise Conflict("Loan amount is outside product limits")
    if not product["min_tenor_months"] <= tenor_months <= product["max_tenor_months"]:
        raise Conflict("Loan tenor is outside product limits")
    # A product stores one fixed annual rate, not a rate band.
    if annual_rate_bps != product["annual_rate_bps"]:
        raise Conflict("Loan rate is outside product limits")
    return product


async def submit_application(
    current: CurrentSession,
    *,
    member_id: uuid.UUID,
    product_id: uuid.UUID,
    principal_kobo: int,
    tenor_months: int,
    beneficiary_member_id: uuid.UUID | None = None,
    destination_bank_account_id: uuid.UUID | None = None,
    guarantors: list[tuple[uuid.UUID, int]] | None = None,
    consent_delivery_observer: Callable[[str], None] | None = None,
) -> uuid.UUID:
    _role(current, {"officer", "admin"})
    if principal_kobo <= 0 or tenor_months <= 0:
        raise ValueError("loan amount and tenor must be positive")
    cid, today, application_id = current.cooperative_id, _today_wat(), uuid.uuid4()
    async with transaction(cid) as session:
        await lock_cooperative_member(session, cid, member_id)
        has_open_application = await session.scalar(
            text("""SELECT EXISTS(SELECT 1 FROM loan_applications WHERE cooperative_id=:cid
              AND member_id=:member AND status NOT IN
              ('rejected','withdrawn','expired','disbursement_failed','closed','written_off'))"""),
            {"cid": cid, "member": member_id},
        )
        if has_open_application:
            raise Conflict("Member already has an active loan application")
        policy = await _effective_policy(session, cid, today)
        product = (
            (
                await session.execute(
                    text("""SELECT interest_method,annual_rate_bps,min_amount_kobo,max_amount_kobo,
                  min_tenor_months,max_tenor_months,guarantors_required FROM loan_products
                  WHERE cooperative_id=:cid AND id=:id AND status='approved'"""),
                    {"cid": cid, "id": product_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if product is None:
            raise NotFound("Approved loan product not found")
        if not (product["min_amount_kobo"] <= principal_kobo <= product["max_amount_kobo"]):
            raise Conflict("Loan amount is outside product limits")
        if not (product["min_tenor_months"] <= tenor_months <= product["max_tenor_months"]):
            raise Conflict("Loan tenor is outside product limits")
        await session.execute(
            text("""INSERT INTO loan_applications(cooperative_id,id,member_id,beneficiary_member_id,
              product_id,policy_version_id,submitted_by,staff_submitted,destination_bank_account_id,
              principal_kobo,tenor_months,annual_rate_bps,status)
              VALUES(:cid,:id,:member,:beneficiary,:product,:policy,:actor,true,:account,
                :principal,:tenor,:rate,'submitted')"""),
            {
                "cid": cid,
                "id": application_id,
                "member": member_id,
                "beneficiary": beneficiary_member_id,
                "product": product_id,
                "policy": policy["id"],
                "actor": current.user_id,
                "account": destination_bank_account_id,
                "principal": principal_kobo,
                "tenor": tenor_months,
                "rate": product["annual_rate_bps"],
            },
        )
        for guarantor_id, amount in guarantors or []:
            await session.execute(
                text("""INSERT INTO guarantees(cooperative_id,application_id,guarantor_member_id,guaranteed_amount_kobo)
                  VALUES(:cid,:app,:member,:amount)"""),
                {"cid": cid, "app": application_id, "member": guarantor_id, "amount": amount},
            )
        application = await _load_application(session, cid, application_id)
        if application is None:
            raise NotFound("Loan application not found")
        eligibility = await _eligibility(session, application, policy)
        await session.execute(
            text(
                "UPDATE loan_applications SET partial_check=:partial,status=:status WHERE cooperative_id=:cid AND id=:id"
            ),
            {
                "partial": eligibility["partial_check"],
                "status": "awaiting_applicant_confirmation",
                "cid": cid,
                "id": application_id,
            },
        )
        if int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM loans WHERE cooperative_id=:cid AND member_id=:member AND status=ANY(:statuses)"
                    ),
                    {"cid": cid, "member": member_id, "statuses": list(ACTIVE_LOAN_STATUSES)},
                )
            ).scalar_one()
        ):
            await _fraud_alert(
                session, cid, "parallel_loan", application_id, {"member_id": str(member_id)}
            )
        if beneficiary_member_id is not None and beneficiary_member_id != member_id:
            await _fraud_alert(
                session,
                cid,
                "third_party_beneficiary",
                application_id,
                {"member_id": str(member_id), "beneficiary_member_id": str(beneficiary_member_id)},
            )
        await append_audit(
            session,
            cooperative_id=cid,
            actor_user_id=current.user_id,
            role="officer",
            action="loan_application_submitted",
            entity_type="loan_application",
            entity_id=str(application_id),
            summary={
                "policy_version_id": str(policy["id"]),
                "product_id": str(product_id),
                "eligibility": eligibility,
            },
            channel="api",
            ip=None,
        )
        await create_consent(
            session,
            cooperative_id=cid,
            application_id=application_id,
            member_id=member_id,
            kind="applicant_ack",
            terms_hash=None,
            slug=current.slug,
            delivery_observer=consent_delivery_observer,
        )
    return application_id


async def run_eligibility(cooperative_id: uuid.UUID, application_id: uuid.UUID) -> dict[str, Any]:
    async with transaction(cooperative_id) as session:
        application = await _load_application(session, cooperative_id, application_id)
        if application is None:
            raise NotFound("Loan application not found")
        policy = await _effective_policy(session, cooperative_id, _today_wat())
        return await _eligibility(session, application, policy)


async def decide_application(
    current: CurrentSession,
    application_id: uuid.UUID,
    decision: str,
    reason: str,
    consent_delivery_observer: Callable[[str], None] | None = None,
) -> uuid.UUID | None:
    role = _role(current, {"committee"})
    if decision not in {"approved", "rejected"}:
        raise ValueError("decision must be approved or rejected")
    cid, same_actor = current.cooperative_id, False
    async with transaction(cid) as session:
        member_id = (
            await session.execute(
                text(
                    "SELECT member_id FROM loan_applications WHERE cooperative_id=:cid AND id=:id"
                ),
                {"cid": cid, "id": application_id},
            )
        ).scalar_one_or_none()
        if member_id is None:
            raise NotFound("Loan application not found")
        await lock_cooperative_member(session, cid, member_id)
        application = await _load_application(session, cid, application_id)
        if application is None:
            raise NotFound("Loan application not found")
        if application["status"] not in {"submitted", "under_review"}:
            raise Conflict("Application is not awaiting committee review")
        if application["submitted_by"] == current.user_id:
            same_actor = True
            await _fraud_alert(
                session,
                cid,
                "same_user_authorization",
                application_id,
                {"user_id": str(current.user_id)},
            )
        else:
            policy = await _effective_policy(session, cid, _today_wat())
            product_terms = None
            if decision == "approved":
                product_terms = await _validate_product_terms(
                    session,
                    cid,
                    application["product_id"],
                    int(application["principal_kobo"]),
                    int(application["tenor_months"]),
                    int(application["annual_rate_bps"]),
                )
            eligibility = await _eligibility(session, application, policy)
            if decision == "approved" and not eligibility["eligible"]:
                raise Conflict("Application does not meet current eligibility rules")
            await session.execute(
                text("""INSERT INTO approvals(cooperative_id,application_id,approver_id,
              policy_version_id,product_id,decision,reason) VALUES(:cid,:app,:actor,:policy,:product,:decision,:reason)"""),
                {
                    "cid": cid,
                    "app": application_id,
                    "actor": current.user_id,
                    "policy": policy["id"],
                    "product": application["product_id"],
                    "decision": decision,
                    "reason": reason,
                },
            )
            loan_id: uuid.UUID | None = None
            if decision == "approved":
                if product_terms is None:
                    raise Conflict("Loan product terms are unavailable")
                destination = (
                    (
                        await session.execute(
                            text("""SELECT id,bank_code,last4,account_name,status,verified_at
                          FROM bank_accounts WHERE cooperative_id=:cid
                          AND id=:id"""),
                            {"cid": cid, "id": application["destination_bank_account_id"]},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                terms = {
                    "principal_kobo": application["principal_kobo"],
                    "tenor_months": application["tenor_months"],
                    "annual_rate_bps": application["annual_rate_bps"],
                    "beneficiary_member_id": str(
                        application["beneficiary_member_id"] or application["member_id"]
                    ),
                    "destination_bank_account_id": str(
                        application["destination_bank_account_id"] or ""
                    ),
                    "destination": {
                        "bank_code": destination["bank_code"],
                        "last4": destination["last4"],
                        "account_name": destination["account_name"],
                        "status": destination["status"],
                        "verified": destination["verified_at"] is not None,
                    }
                    if destination is not None
                    else None,
                    "product_id": str(application["product_id"]),
                    "policy_version_id": str(policy["id"]),
                }
                canonical = json.dumps(
                    terms, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                )
                snapshot_id = uuid.uuid4()
                await session.execute(
                    text("""INSERT INTO loan_terms_snapshots
                  (cooperative_id,id,application_id,terms,terms_sha256,created_by)
                  VALUES(:cid,:id,:app,CAST(:terms AS jsonb),:hash,:actor)"""),
                    {
                        "cid": cid,
                        "id": snapshot_id,
                        "app": application_id,
                        "terms": canonical,
                        "hash": hashlib.sha256(canonical.encode()).digest(),
                        "actor": current.user_id,
                    },
                )
                await session.execute(
                    text("""UPDATE loan_applications SET status='awaiting_borrower_final_confirmation',
                  current_terms_snapshot_id=:snapshot,policy_version_id=:policy,partial_check=:partial,
                  updated_at=now() WHERE cooperative_id=:cid AND id=:app"""),
                    {
                        "snapshot": snapshot_id,
                        "policy": policy["id"],
                        "partial": eligibility["partial_check"],
                        "cid": cid,
                        "app": application_id,
                    },
                )
                loan_id = uuid.uuid4()
                await session.execute(
                    text("""INSERT INTO loans(cooperative_id,id,application_id,member_id,
                  product_id,policy_version_id,terms_snapshot_id,status)
                  VALUES(:cid,:id,:app,:member,:product,:policy,:snapshot,'approved')"""),
                    {
                        "cid": cid,
                        "id": loan_id,
                        "app": application_id,
                        "member": member_id,
                        "product": application["product_id"],
                        "policy": policy["id"],
                        "snapshot": snapshot_id,
                    },
                )
                first_day = datetime.now(WAT).date() + timedelta(days=32)
                schedule = build_schedule(
                    int(application["principal_kobo"]),
                    int(application["annual_rate_bps"]),
                    int(application["tenor_months"]),
                    product_terms["interest_method"],
                    first_day,
                )
                for number, installment in enumerate(schedule, start=1):
                    await session.execute(
                        text("""INSERT INTO loan_schedule(cooperative_id,loan_id,installment_no,
                      due_at,principal_kobo,interest_kobo,payment_kobo,balance_kobo)
                      VALUES(:cid,:loan,:number,:due,:principal,:interest,:payment,:balance)"""),
                        {
                            "cid": cid,
                            "loan": loan_id,
                            "number": number,
                            "due": installment.due_at,
                            "principal": installment.principal_kobo,
                            "interest": installment.interest_kobo,
                            "payment": installment.payment_kobo,
                            "balance": installment.balance_kobo,
                        },
                    )
                await create_consent(
                    session,
                    cooperative_id=cid,
                    application_id=application_id,
                    member_id=application["beneficiary_member_id"] or member_id,
                    kind="final_terms",
                    terms_hash=hashlib.sha256(canonical.encode()).digest(),
                    slug=current.slug,
                    delivery_observer=consent_delivery_observer,
                )
            else:
                await session.execute(
                    text(
                        "UPDATE loan_applications SET status='rejected',updated_at=now() WHERE cooperative_id=:cid AND id=:id"
                    ),
                    {"cid": cid, "id": application_id},
                )
            await append_audit(
                session,
                cooperative_id=cid,
                actor_user_id=current.user_id,
                role=role,
                action=f"loan_application_{decision}",
                entity_type="loan_application",
                entity_id=str(application_id),
                summary={
                    "policy_version_id": str(policy["id"]),
                    "product_id": str(application["product_id"]),
                    "eligibility": eligibility,
                },
                channel="api",
                ip=None,
                reason=reason,
            )
    if same_actor:
        raise Conflict("Application submitter cannot approve or reject it")
    return loan_id if decision == "approved" else None


async def change_application_terms(
    current: CurrentSession, application_id: uuid.UUID, changes: dict[str, Any], reason: str
) -> None:
    role = _role(current, {"officer", "admin"})
    allowed = {
        "principal_kobo",
        "tenor_months",
        "annual_rate_bps",
        "beneficiary_member_id",
        "destination_bank_account_id",
    }
    if not changes or set(changes) - allowed:
        raise ValueError("unsupported application term changes")
    async with transaction(current.cooperative_id) as session:
        old = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM loan_applications WHERE cooperative_id=:cid AND id=:id FOR UPDATE"
                    ),
                    {"cid": current.cooperative_id, "id": application_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if old is None:
            raise NotFound("Loan application not found")
        if old["status"] in {"disbursed", "repaying", "closed", "written_off"}:
            raise Conflict("Contracted loan terms cannot be changed")
        values = dict(changes)
        if "principal_kobo" in values and values["principal_kobo"] <= 0:
            raise ValueError("principal must be positive")
        if "tenor_months" in values and values["tenor_months"] <= 0:
            raise ValueError("tenor must be positive")
        if "annual_rate_bps" in values and values["annual_rate_bps"] < 0:
            raise ValueError("rate cannot be negative")
        await _validate_product_terms(
            session,
            current.cooperative_id,
            old["product_id"],
            int(values.get("principal_kobo", old["principal_kobo"])),
            int(values.get("tenor_months", old["tenor_months"])),
            int(values.get("annual_rate_bps", old["annual_rate_bps"])),
        )
        await session.execute(
            text("""UPDATE loan_applications SET
              principal_kobo=coalesce(:principal,principal_kobo),
              tenor_months=coalesce(:tenor,tenor_months),
              annual_rate_bps=coalesce(:rate,annual_rate_bps),
              beneficiary_member_id=CASE WHEN :change_beneficiary THEN :beneficiary ELSE beneficiary_member_id END,
              destination_bank_account_id=CASE WHEN :change_account THEN :account ELSE destination_bank_account_id END,
              status='under_review',current_terms_snapshot_id=NULL,updated_at=now()
              WHERE cooperative_id=:cid AND id=:id"""),
            {
                "principal": values.get("principal_kobo"),
                "tenor": values.get("tenor_months"),
                "rate": values.get("annual_rate_bps"),
                "change_beneficiary": "beneficiary_member_id" in values,
                "beneficiary": values.get("beneficiary_member_id"),
                "change_account": "destination_bank_account_id" in values,
                "account": values.get("destination_bank_account_id"),
                "cid": current.cooperative_id,
                "id": application_id,
            },
        )
        await session.execute(
            text(
                "UPDATE loans SET status='withdrawn' WHERE cooperative_id=:cid AND application_id=:id AND status='approved'"
            ),
            {"cid": current.cooperative_id, "id": application_id},
        )
        await session.execute(
            text(
                "UPDATE member_consents SET status='invalidated' WHERE cooperative_id=:cid AND application_id=:id AND status='pending'"
            ),
            {"cid": current.cooperative_id, "id": application_id},
        )
        if "destination_bank_account_id" in changes and old["status"] == "approved":
            await _fraud_alert(
                session,
                current.cooperative_id,
                "destination_changed",
                application_id,
                {"application_id": str(application_id)},
            )
        await append_audit(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            role=role,
            action="loan_terms_changed",
            entity_type="loan_application",
            entity_id=str(application_id),
            summary={"changed_fields": sorted(changes)},
            channel="api",
            ip=None,
            reason=reason,
        )
