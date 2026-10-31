"""Deterministic fake-data seeder for a single cooperative.

Every state change goes through the real domain services inside
``app.core.db.transaction(cooperative_id)`` (tenant role ``ajo_app`` with RLS via SET LOCAL).
The seeder never uses the owner/direct connection and writes no raw SQL. It refuses in
production, without the explicit fake-data flag, for an unknown slug, or for a cooperative
that already has members. The only secrets it ever prints are the one-time temporary staff
passwords; bank account numbers are never printed (only ``last4`` ever leaves the database).
"""

from __future__ import annotations

import random
import secrets
import uuid
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import transaction
from app.domains.auth.service import CurrentSession, resolve_tenant_slug
from app.domains.auth.staff import create_staff_user, get_bootstrap_admin
from app.domains.consent.service import decide, send_otp
from app.domains.disbursements.service import authorize, confirm_paid, prepare, record_transfer
from app.domains.loans.calculations import build_schedule
from app.domains.loans.service import decide_application, submit_application
from app.domains.members.bank_accounts import add_bank_account
from app.domains.members.salary import add_salary_record
from app.domains.members.service import import_members, record_contribution
from app.domains.policies.service import (
    approve_loan_product,
    approve_policy,
    create_loan_product,
    propose_policy,
)
from app.domains.repayments.service import post_repayment

WAT = ZoneInfo("Africa/Lagos")
DEMO_SEED = 20240501
MEMBER_COUNT = 25
ANNUAL_RATE_BPS = 2400
SALARY_KOBO = 50_000_000
SAVINGS_KOBO = 5_000_000
SHARES_KOBO = 1_000_000
DUES_KOBO = 500_000

# 14 loans, one per borrowing member (M-001..M-014), each driven to an exact end state.
# (member_index, target_state, repayments_for_repaying_loans)
LOAN_PLAN: list[tuple[int, str, int]] = [
    (0, "awaiting_applicant_confirmation", 0),
    (1, "awaiting_applicant_confirmation", 0),
    (2, "awaiting_applicant_confirmation", 0),
    (3, "under_review", 0),
    (4, "under_review", 0),
    (5, "approved_awaiting_final", 0),
    (6, "approved_awaiting_final", 0),
    (7, "ready_for_disbursement", 0),
    (8, "repaying", 2),
    (9, "repaying", 2),
    (10, "repaying", 3),
    (11, "repaying", 3),
    (12, "closed", 0),
    (13, "disputed", 0),
]

_MEMBER_NAMES = [
    "Adaobi Eze",
    "Chinedu Okafor",
    "Funke Adeyemi",
    "Ibrahim Musa",
    "Ngozi Obi",
    "Tunde Bakare",
    "Bukola Soyinka",
    "Emeka Nwosu",
    "Halima Bello",
    "Yetunde Williams",
    "Kunle Ajayi",
    "Zainab Ibrahim",
    "Tope Olawale",
    "Sani Garba",
    "Amara Nwankwo",
    "Femi Ogunleye",
    "Lola Akande",
    "Yakubu Aliyu",
    "Chioma Okeke",
    "Dauda Suleiman",
    "Bisi Adewale",
    "Musa Danjuma",
    "Nneka Obiekwe",
    "Rabiu Lawal",
    "Titilope Oni",
]


def _today_wat() -> date:
    return datetime.now(WAT).date()


def _phone(k: int) -> str:
    """Member k (1-based) -> +234 800 555 01xx, the demo phone range."""
    return f"+234800555{100 + k:04d}"


def _account_number(k: int) -> str:
    """Distinct 10-digit account number per member (never printed by the seeder)."""
    return f"{1_000_000_000 + k}"


def _session(
    cooperative_id: uuid.UUID, slug: str, user_id: uuid.UUID, email: str, role: str
) -> CurrentSession:
    return CurrentSession(cooperative_id, slug, uuid.uuid4(), user_id, email, (role,), bytes(32))


class _CapturingOtp:
    """Captures OTP codes delivered to a member phone so the seeder can confirm consents."""

    def __init__(self) -> None:
        self.codes: dict[str, str] = {}

    def __call__(self, destination: str, code: str) -> None:
        self.codes[destination] = code

    async def send(self, destination: str, code: str) -> None:
        self.codes[destination] = code


async def _confirm(
    slug: str,
    token: str,
    member_phone: str,
    sender: _CapturingOtp,
    decision: str = "confirm",
) -> None:
    """Run the real OTP + consent-decide path for one consent token."""
    ip = f"127.0.1.{int(member_phone[-3:])}"  # distinct per member, stays under the per-IP limit
    await send_otp(slug, token, ip, sender)
    code = sender.codes[member_phone]
    await decide(slug, token, code, decision, ip, "seed-demo")

async def _submit(
    officer: CurrentSession,
    *,
    member_id: uuid.UUID,
    product_id: uuid.UUID,
    principal_kobo: int,
    tenor_months: int,
    bank_account_id: uuid.UUID,
) -> tuple[uuid.UUID, list[str]]:
    """Submit a loan application; return (application_id, captured consent tokens).

    The consent-delivery observer closure lives inside this function (not the seeding
    loop), so the captured ``tokens`` list is bound at definition time per call.
    """
    tokens: list[str] = []

    def capture(token: str) -> None:
        tokens.append(token)

    application_id = await submit_application(
        officer,
        member_id=member_id,
        product_id=product_id,
        principal_kobo=principal_kobo,
        tenor_months=tenor_months,
        destination_bank_account_id=bank_account_id,
        guarantors=None,
        consent_delivery_observer=capture,
    )
    return application_id, tokens


async def _drive_to_ready(
    *,
    slug: str,
    officer: CurrentSession,
    committee: CurrentSession,
    member_id: uuid.UUID,
    product_id: uuid.UUID,
    principal_kobo: int,
    tenor_months: int,
    bank_account_id: uuid.UUID,
    member_phone: str,
    sender: _CapturingOtp,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Submit, confirm the applicant ack, approve, and confirm final terms -> ready for disbursement.

    Returns (application_id, loan_id).
    """
    tokens: list[str] = []

    def capture(token: str) -> None:
        tokens.append(token)

    application_id = await submit_application(
        officer,
        member_id=member_id,
        product_id=product_id,
        principal_kobo=principal_kobo,
        tenor_months=tenor_months,
        destination_bank_account_id=bank_account_id,
        guarantors=None,
        consent_delivery_observer=capture,
    )
    await _confirm(slug, tokens[0], member_phone, sender)  # applicant_ack -> under_review
    loan_id = await decide_application(
        committee, application_id, "approved", "demo approval", consent_delivery_observer=capture
    )
    assert loan_id is not None
    await _confirm(slug, tokens[1], member_phone, sender)  # final_terms -> ready_for_disbursement
    return application_id, loan_id


async def _disburse(
    *,
    treasurer: CurrentSession,
    admin: CurrentSession,
    loan_id: uuid.UUID,
    reference: str,
) -> None:
    """Run the full manual disbursement chain: prepare, authorize, record, confirm paid."""
    disbursement_id = await prepare(treasurer, loan_id, "127.0.0.1")
    assert disbursement_id is not None
    await authorize(admin, disbursement_id, "127.0.0.1")
    await record_transfer(treasurer, disbursement_id, reference, _today_wat(), "127.0.0.1")
    await confirm_paid(admin, disbursement_id, "127.0.0.1")


def _schedule_payments(principal_kobo: int, tenor_months: int, first_due: date) -> list[int]:
    schedule = build_schedule(
        principal_kobo, ANNUAL_RATE_BPS, tenor_months, "reducing_balance", first_due
    )
    return [installment.payment_kobo for installment in schedule]


async def seed_demo(slug: str, yes_fake_data: bool) -> dict[str, Any]:
    """Seed one cooperative with a fixed policy, product, staff, members and 14 loans.

    Refuses in production, without the fake-data flag, for an unknown slug, or when the
    cooperative already has members. Returns a counts summary; prints the one-time staff
    passwords once and a short summary. Never prints account numbers or other secrets.
    """
    if get_settings().is_production:
        raise RuntimeError("refusing to seed in production")
    if not yes_fake_data:
        raise RuntimeError("pass --yes-fake-data to confirm fake demo data")

    tenant = await resolve_tenant_slug(slug)
    if tenant is None:
        raise RuntimeError("slug does not exist")
    cooperative_id = tenant[0]

    async with transaction(cooperative_id) as session:
        existing = await session.scalar(
            text("SELECT count(*) FROM members WHERE cooperative_id = :cid"),
            {"cid": cooperative_id},
        )
    if int(existing or 0) > 0:
        raise RuntimeError("slug already has members")

    bootstrap = await get_bootstrap_admin(cooperative_id)
    if bootstrap is None:
        raise RuntimeError("cooperative has no administrator to seed from")
    admin_id, admin_email = bootstrap
    admin = _session(cooperative_id, slug, admin_id, admin_email, "admin")

    rng = random.Random(DEMO_SEED)  # noqa: S311 - deterministic, non-cryptographic demo data

    # Staff users: officer, committee, treasurer, auditor (admin already exists).
    staff_passwords: list[tuple[str, str]] = []
    staff_ids: dict[str, uuid.UUID] = {}
    for role in ("officer", "committee", "treasurer", "auditor"):
        password = secrets.token_urlsafe(18)
        email = f"{role}@{slug}.demo"
        user_id = await create_staff_user(admin, email=email, password=password, roles=(role,))
        staff_ids[role] = user_id
        staff_passwords.append((role, password))

    officer = _session(
        cooperative_id, slug, staff_ids["officer"], f"officer@{slug}.demo", "officer"
    )
    committee = _session(
        cooperative_id, slug, staff_ids["committee"], f"committee@{slug}.demo", "committee"
    )
    treasurer = _session(
        cooperative_id, slug, staff_ids["treasurer"], f"treasurer@{slug}.demo", "treasurer"
    )

    # One approved policy version: officer proposes, admin approves (different users).
    today = _today_wat()
    policy_values: dict[str, Any] = {
        "effective_from": today,
        "salary_basis": "net",
        "deduction_cap_bps": 7000,
        "max_active_loans": 1,
        "guarantors_required": 0,
        "min_savings_kobo": 0,
        "loan_to_savings_multiple_x100": 10000,
        "interest_method": "reducing_balance",
        "payroll_allocation_order": ["dues", "interest", "principal", "savings"],
        "confirmation_expiry_minutes": 1440,
        "otp_validity_seconds": 600,
        "settings": {},
    }
    policy_id = await propose_policy(officer, policy_values, "demo policy")
    await approve_policy(admin, policy_id, "demo approval")

    # One approved loan product: officer proposes, admin approves.
    product: dict[str, Any] = {
        "code": "demo-staff",
        "name": "Demo staff loan",
        "interest_method": "reducing_balance",
        "annual_rate_bps": ANNUAL_RATE_BPS,
        "min_amount_kobo": 5_000_000,
        "max_amount_kobo": 50_000_000,
        "min_tenor_months": 3,
        "max_tenor_months": 24,
        "fee_bps": 0,
        "guarantors_required": 0,
        "supersedes_id": None,
    }
    product_id = await create_loan_product(officer, product, today, "demo product")
    await approve_loan_product(admin, product_id, today, "demo approval")

    # 25 fictional members via the real CSV import (returns each member id).
    rows: list[str] = ["member_no,full_name,phone_e164,email,staff_no,employer,joined_on"]
    for k in range(1, MEMBER_COUNT + 1):
        rows.append(
            ",".join(
                [
                    f"M-{k:03d}",
                    _MEMBER_NAMES[k - 1],
                    _phone(k),
                    f"member{k}@{slug}.demo",
                    f"STAFF-{k:03d}",
                    "Demo Employer Ltd",
                    today.isoformat(),
                ]
            )
        )
    imported = await import_members(officer, "\n".join(rows), confirm=True)
    if imported["imported"] != MEMBER_COUNT:
        raise RuntimeError("member import did not create the expected members")
    member_ids: dict[str, uuid.UUID] = {
        entry["member_no"]: uuid.UUID(entry["id"]) for entry in imported["members"]
    }

    # Per member: a salary record, a bank account (verified for the 14 borrowers), and
    # savings/shares/dues contributions. All via the real services, all as the officer.
    borrowing_member_count = len(LOAN_PLAN)
    member_bank_accounts: dict[int, uuid.UUID] = {}
    for k in range(1, MEMBER_COUNT + 1):
        member_no = f"M-{k:03d}"
        member_id = member_ids[member_no]
        await add_salary_record(
            officer,
            member_id,
            effective_from=today,
            basis="net",
            amount_kobo=SALARY_KOBO,
            source="declared",
        )
        member_bank_accounts[k] = await add_bank_account(
            officer,
            member_id,
            bank_code="001",
            account_number=_account_number(k),
            account_name=_MEMBER_NAMES[k - 1],
            mark_verified=k <= borrowing_member_count,
        )
        await record_contribution(
            officer, member_id, "savings", SAVINGS_KOBO, f"seed-sav-{member_no}"
        )
        await record_contribution(
            officer, member_id, "shares", SHARES_KOBO, f"seed-sha-{member_no}"
        )
        await record_contribution(officer, member_id, "dues", DUES_KOBO, f"seed-due-{member_no}")

    # 14 loans driven to their exact states through the real services.
    sender = _CapturingOtp()
    first_due = today + timedelta(days=32)
    for plan_index, (member_index, target_state, repayments) in enumerate(LOAN_PLAN):
        k = member_index + 1
        member_no = f"M-{k:03d}"
        member_id = member_ids[member_no]
        bank_account_id = member_bank_accounts[k]
        principal_kobo = rng.randint(10, 30) * 1_000_000
        tenor_months = rng.randint(6, 12)

        if target_state == "awaiting_applicant_confirmation":
            await _submit(
                officer,
                member_id=member_id,
                product_id=product_id,
                principal_kobo=principal_kobo,
                tenor_months=tenor_months,
                bank_account_id=bank_account_id,
            )
            continue

        if target_state == "under_review":
            _, tokens = await _submit(
                officer,
                member_id=member_id,
                product_id=product_id,
                principal_kobo=principal_kobo,
                tenor_months=tenor_months,
                bank_account_id=bank_account_id,
            )
            await _confirm(slug, tokens[0], _phone(k), sender)
            continue

        if target_state == "disputed":
            # Last reachable disputed state: reject the applicant_ack consent. A loan-level
            # dispute is unreachable because consent.decide reject sets both application_id
            # and loan_id on a final-terms reject, violating the disputes CHECK (exactly one
            # of application_id / loan_id must be non-null). See the status report.
            _, tokens = await _submit(
                officer,
                member_id=member_id,
                product_id=product_id,
                principal_kobo=principal_kobo,
                tenor_months=tenor_months,
                bank_account_id=bank_account_id,
            )
            await _confirm(slug, tokens[0], _phone(k), sender, decision="reject")
            continue

        # approved_awaiting_final, ready_for_disbursement, repaying, closed all require the
        # loan to reach at least "approved" first.
        _, loan_id = await _drive_to_ready(
            slug=slug,
            officer=officer,
            committee=committee,
            member_id=member_id,
            product_id=product_id,
            principal_kobo=principal_kobo,
            tenor_months=tenor_months,
            bank_account_id=bank_account_id,
            member_phone=_phone(k),
            sender=sender,
        )

        if target_state == "approved_awaiting_final":
            continue  # loan approved, application awaiting_borrower_final_confirmation

        if target_state == "ready_for_disbursement":
            continue  # loan + application ready_for_disbursement

        # repaying and closed: disburse, then post repayments via the real service.
        await _disburse(
            treasurer=treasurer, admin=admin, loan_id=loan_id, reference=f"BANK-REF-{plan_index}"
        )
        payments = _schedule_payments(principal_kobo, tenor_months, first_due)

        if target_state == "repaying":
            for payment_index in range(repayments):
                await post_repayment(
                    officer,
                    loan_id=loan_id,
                    member_id=member_id,
                    amount_kobo=payments[payment_index],
                    source="cash",
                    received_on=first_due + timedelta(days=5 + 35 * payment_index),
                    reference=f"PAY-{plan_index}-{payment_index}",
                    idempotency_key=f"seed-pay-{plan_index}-{payment_index}",
                    ip="127.0.0.1",
                )
        elif target_state == "closed":
            await post_repayment(
                officer,
                loan_id=loan_id,
                member_id=member_id,
                amount_kobo=sum(payments),
                source="bank_transfer",
                received_on=date(2099, 1, 1),
                reference=f"CLOSE-{plan_index}",
                idempotency_key=f"seed-close-{plan_index}",
                ip="127.0.0.1",
            )

    _print_output(staff_passwords, slug)
    return {
        "members": MEMBER_COUNT,
        "loan_applications": len(LOAN_PLAN),
        "loans": 8,
        "staff_users": 5,
        "disputed_loan_reachable": False,
    }


def _print_output(staff_passwords: list[tuple[str, str]], slug: str) -> None:
    """Print the one-time staff passwords and a short summary. No account numbers or other secrets."""
    print("One-time temporary staff passwords (shown once, store securely):")
    for role, password in staff_passwords:
        print(f"  {role}@{slug}.demo: {password}")
    print(
        "Seed complete: members=25, loan_applications=14, loans=8, staff_users=5. "
        "Disputed loan state unreachable through services (disputed application produced instead)."
    )
