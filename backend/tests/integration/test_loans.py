"""Loan calculations and workflow invariants on local PostgreSQL as ajo_app."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

import asyncpg
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.db import asyncpg_dsn, dispose_engine
from app.core.errors import Conflict
from app.domains.auth.service import CurrentSession
from app.domains.loans.calculations import build_schedule, deduction_capacity
from app.domains.loans.service import (
    change_application_terms,
    decide_application,
    submit_application,
)
from tests.conftest import create_tenant

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
async def reset_engine():
    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture
async def db(test_database) -> asyncpg.Connection:
    connection = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    assert await connection.fetchval("SELECT current_user") == "ajo_app"
    yield connection
    await connection.close()


@dataclass(frozen=True)
class LoanBase:
    cooperative_id: uuid.UUID
    user_ids: tuple[uuid.UUID, ...]
    product_id: uuid.UUID
    policy_id: uuid.UUID
    bank_account_id: uuid.UUID
    savings_account_id: uuid.UUID


def session(cid: uuid.UUID, uid: uuid.UUID, *roles: str) -> CurrentSession:
    return CurrentSession(
        cid, "loan-test", uuid.uuid4(), uid, "staff@example.test", roles, bytes(32)
    )


async def _create_loan_base(
    test_database,
    *,
    savings_backing_bps: int | None = None,
    min_savings_kobo: int = 0,
    fee_bps: int = 0,
) -> LoanBase:
    connection = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    assert await connection.fetchval("SELECT current_user") == "ajo_app"
    seed = await create_tenant(
        connection,
        name="Loan test",
        slug_prefix="loan",
        roles=("officer", "committee", "committee"),
    )
    cid, proposer, approver = seed.cooperative_id, seed.user_ids[0], seed.user_ids[1]
    policy_id, product_id = uuid.uuid4(), uuid.uuid4()
    today = date.today()
    await connection.execute(
        """INSERT INTO cooperative_policy_versions(cooperative_id,id,version,status,effective_from,
          proposed_by,approved_by,salary_basis,deduction_cap_bps,max_active_loans,guarantors_required,
          min_savings_kobo,loan_to_savings_multiple_x100,interest_method,settings)
          VALUES($1,$2,1,'approved',$3,$4,$5,'net',7000,1,0,$6,100000,'reducing_balance',$7::jsonb)""",
        cid,
        policy_id,
        today,
        proposer,
        approver,
        min_savings_kobo,
        json.dumps(
            {} if savings_backing_bps is None else {"savings_backing_bps": savings_backing_bps}
        ),
    )
    await connection.execute(
        """INSERT INTO loan_products(cooperative_id,id,code,name,interest_method,annual_rate_bps,
          min_amount_kobo,max_amount_kobo,min_tenor_months,max_tenor_months,fee_bps,
          guarantors_required,status,approved_at)
          VALUES($1,$2,'TEST','Test','reducing_balance',0,1,100000000,1,36,$3,0,'approved',now())""",
        cid,
        product_id,
        fee_bps,
    )
    bank_id, savings_id, receivable_id, fee_income_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    await connection.executemany(
        "INSERT INTO accounts(cooperative_id,id,code,name,account_type) VALUES($1,$2,$3,$4,$5)",
        [
            (cid, bank_id, "bank", "Bank", "asset"),
            (cid, savings_id, "member_savings", "Savings", "liability"),
            (cid, receivable_id, "loans_receivable", "Loans receivable", "asset"),
            (cid, fee_income_id, "fee_income", "Fee income", "income"),
        ],
    )
    await connection.close()
    return LoanBase(cid, seed.user_ids, product_id, policy_id, bank_id, savings_id)


@pytest.fixture(scope="module")
def loan_base(test_database) -> LoanBase:
    return asyncio.run(_create_loan_base(test_database))


async def setup_loan_member(db: asyncpg.Connection, base: LoanBase) -> uuid.UUID:
    cid, member, proposer = base.cooperative_id, uuid.uuid4(), base.user_ids[0]
    await db.execute("SELECT set_config('app.cooperative_id',$1,false)", str(cid))
    await db.execute(
        """INSERT INTO members(id,cooperative_id,member_no,full_name,phone_e164)
          VALUES($1,$2,$3,'Loan test member','+2348000000000')""",
        member,
        cid,
        f"M-{member.hex}",
    )
    await db.execute(
        """INSERT INTO salary_records(cooperative_id,member_id,effective_from,basis,amount_kobo,source,recorded_by)
          VALUES($1,$2,$3,'net',100000000,'declared',$4)""",
        cid,
        member,
        date.today(),
        proposer,
    )
    await db.fetchval(
        """SELECT post_journal_entry($1,current_date,'seed',$2,'test savings',$3,$4,$5::jsonb,NULL)""",
        cid,
        uuid.uuid4(),
        proposer,
        f"seed-{uuid.uuid4()}",
        json.dumps(
            [
                {
                    "account_id": str(base.bank_account_id),
                    "member_id": str(member),
                    "debit_kobo": 100000000,
                },
                {
                    "account_id": str(base.savings_account_id),
                    "member_id": str(member),
                    "credit_kobo": 100000000,
                },
            ]
        ),
    )
    return member


def test_deduction_capacity_worked_example() -> None:
    capacity = deduction_capacity(25_000_000, 7000, 8_100_000, 0)
    assert capacity.capacity_kobo == 9_400_000
    assert capacity.partial_check is False


def test_deduction_capacity_marks_unknown_external_deductions_partial() -> None:
    assert deduction_capacity(25_000_000, 7000, 8_100_000, None).partial_check is True


@settings(max_examples=10, deadline=None)
@given(
    principal=st.integers(min_value=1, max_value=100_000),
    rate=st.integers(min_value=0, max_value=5_000),
    tenor=st.integers(min_value=1, max_value=24),
    method=st.sampled_from(["flat", "reducing_balance"]),
)
def test_schedule_principal_sums_and_finishes_at_zero(
    principal: int, rate: int, tenor: int, method: str
) -> None:
    schedule = build_schedule(principal, rate, tenor, method, date(2026, 1, 31))
    assert sum(item.principal_kobo for item in schedule) == principal
    assert schedule[-1].balance_kobo == 0
    assert all(item.balance_kobo >= 0 for item in schedule)


def test_schedule_month_end_is_calculated_in_wat() -> None:
    schedule = build_schedule(30_000, 0, 3, "flat", datetime(2026, 1, 31, 14, 59, tzinfo=UTC))
    assert [item.due_at.date() for item in schedule] == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
    ]
    assert schedule[1].due_at.tzinfo is not None


async def test_concurrent_approvals_for_member_cannot_both_succeed(
    db: asyncpg.Connection, loan_base: LoanBase
) -> None:
    member = await setup_loan_member(db, loan_base)
    cid, users, product = loan_base.cooperative_id, loan_base.user_ids, loan_base.product_id
    application = await submit_application(
        session(cid, users[0], "officer"),
        member_id=member,
        product_id=product,
        principal_kobo=500_000,
        tenor_months=6,
    )
    await db.execute(
        "UPDATE loan_applications SET status='under_review' WHERE cooperative_id=$1 AND id=$2",
        cid,
        application,
    )
    results = await asyncio.gather(
        decide_application(session(cid, users[1], "committee"), application, "approved", "review"),
        decide_application(session(cid, users[2], "committee"), application, "approved", "review"),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, Conflict) for result in results) == 1


async def test_snapshot_invalidated_after_term_change(
    db: asyncpg.Connection, loan_base: LoanBase
) -> None:
    cid, users = loan_base.cooperative_id, loan_base.user_ids
    product = uuid.uuid4()
    await db.execute("SELECT set_config('app.cooperative_id',$1,false)", str(cid))
    await db.execute(
        """INSERT INTO loan_products(cooperative_id,id,code,name,interest_method,annual_rate_bps,
          min_amount_kobo,max_amount_kobo,min_tenor_months,max_tenor_months,fee_bps,
          guarantors_required,status,approved_at)
          VALUES($1,$2,'TEST-RATE','Test rate','reducing_balance',100,1,100000000,1,36,0,0,'approved',now())""",
        cid,
        product,
    )
    for index in range(5):
        member = await setup_loan_member(db, loan_base)
        bank_account_id = uuid.uuid4()
        await db.execute(
            """INSERT INTO bank_accounts(cooperative_id,id,member_id,bank_code,account_number_encrypted,last4,account_name)
               VALUES($1,$2,$3,'001','test-ciphertext','1234','Test member')""",
            cid,
            bank_account_id,
            member,
        )
        term_changes = (
            {"principal_kobo": 600_000},
            {"tenor_months": 7},
            {"annual_rate_bps": 100},
            {"beneficiary_member_id": member},
            {"destination_bank_account_id": bank_account_id},
        )
        changes = term_changes[index]
        application = await submit_application(
            session(cid, users[0], "officer"),
            member_id=member,
            product_id=product,
            principal_kobo=500_000,
            tenor_months=6,
        )
        await db.execute(
            "UPDATE loan_applications SET status='under_review' WHERE cooperative_id=$1 AND id=$2",
            cid,
            application,
        )
        await decide_application(
            session(cid, users[1], "committee"), application, "approved", "review"
        )
        snapshot = await db.fetchval(
            "SELECT current_terms_snapshot_id FROM loan_applications WHERE cooperative_id=$1 AND id=$2",
            cid,
            application,
        )
        await change_application_terms(
            session(cid, users[0], "officer"), application, changes, f"updated request {index}"
        )
        row = await db.fetchrow(
            "SELECT status,current_terms_snapshot_id FROM loan_applications WHERE cooperative_id=$1 AND id=$2",
            cid,
            application,
        )
        assert row["status"] == "under_review" and row["current_terms_snapshot_id"] is None
        assert (
            await db.fetchval(
                "SELECT count(*) FROM loan_terms_snapshots WHERE cooperative_id=$1 AND id=$2",
                cid,
                snapshot,
            )
            == 1
        )


async def test_approver_cannot_equal_submitter(db: asyncpg.Connection, loan_base: LoanBase) -> None:
    member = await setup_loan_member(db, loan_base)
    cid, users, product = loan_base.cooperative_id, loan_base.user_ids, loan_base.product_id
    application = await submit_application(
        session(cid, users[0], "officer"),
        member_id=member,
        product_id=product,
        principal_kobo=500_000,
        tenor_months=6,
    )
    await db.execute(
        "UPDATE loan_applications SET status='under_review' WHERE cooperative_id=$1 AND id=$2",
        cid,
        application,
    )
    with pytest.raises(Conflict, match="submitter"):
        await decide_application(
            session(cid, users[0], "committee"), application, "approved", "review"
        )


async def test_policy_version_is_stored_on_approval_and_loan(
    db: asyncpg.Connection, loan_base: LoanBase
) -> None:
    member = await setup_loan_member(db, loan_base)
    cid, users, product, policy_id = (
        loan_base.cooperative_id,
        loan_base.user_ids,
        loan_base.product_id,
        loan_base.policy_id,
    )
    application = await submit_application(
        session(cid, users[0], "officer"),
        member_id=member,
        product_id=product,
        principal_kobo=500_000,
        tenor_months=6,
    )
    await db.execute(
        "UPDATE loan_applications SET status='under_review' WHERE cooperative_id=$1 AND id=$2",
        cid,
        application,
    )
    await decide_application(session(cid, users[1], "committee"), application, "approved", "review")
    assert (
        await db.fetchval(
            "SELECT policy_version_id FROM approvals WHERE cooperative_id=$1 AND application_id=$2",
            cid,
            application,
        )
        == policy_id
    )
    assert (
        await db.fetchval(
            "SELECT policy_version_id FROM loans WHERE cooperative_id=$1 AND application_id=$2",
            cid,
            application,
        )
        == policy_id
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"principal_kobo": 100_000_001},
        {"tenor_months": 37},
        {"annual_rate_bps": 1},
    ],
    ids=["amount", "tenor", "rate"],
)
async def test_term_edit_rejects_values_outside_product_limits(
    db: asyncpg.Connection, loan_base: LoanBase, changes: dict[str, int]
) -> None:
    member = await setup_loan_member(db, loan_base)
    cid, users, product = loan_base.cooperative_id, loan_base.user_ids, loan_base.product_id
    application = await submit_application(
        session(cid, users[0], "officer"),
        member_id=member,
        product_id=product,
        principal_kobo=500_000,
        tenor_months=6,
    )

    with pytest.raises(Conflict, match="product limits"):
        await change_application_terms(
            session(cid, users[0], "officer"), application, changes, "out-of-range edit"
        )
    row = await db.fetchrow(
        "SELECT principal_kobo,tenor_months,annual_rate_bps FROM loan_applications WHERE cooperative_id=$1 AND id=$2",
        cid,
        application,
    )
    assert (row["principal_kobo"], row["tenor_months"], row["annual_rate_bps"]) == (
        500_000,
        6,
        0,
    )


@pytest.mark.parametrize(
    ("principal_kobo", "tenor_months", "annual_rate_bps"),
    [
        (100_000_001, 6, 0),
        (500_000, 37, 0),
        (500_000, 6, 1),
    ],
    ids=["amount", "tenor", "rate"],
)
async def test_approval_rejects_terms_outside_product_limits(
    db: asyncpg.Connection,
    loan_base: LoanBase,
    principal_kobo: int,
    tenor_months: int,
    annual_rate_bps: int,
) -> None:
    member = await setup_loan_member(db, loan_base)
    cid, users, product = loan_base.cooperative_id, loan_base.user_ids, loan_base.product_id
    application = await submit_application(
        session(cid, users[0], "officer"),
        member_id=member,
        product_id=product,
        principal_kobo=500_000,
        tenor_months=6,
    )
    await db.execute(
        """UPDATE loan_applications SET principal_kobo=$3,tenor_months=$4,
          annual_rate_bps=$5,status='under_review' WHERE cooperative_id=$1 AND id=$2""",
        cid,
        application,
        principal_kobo,
        tenor_months,
        annual_rate_bps,
    )

    with pytest.raises(Conflict, match="product limits"):
        await decide_application(
            session(cid, users[1], "committee"), application, "approved", "limit check"
        )
    assert (
        await db.fetchval(
            "SELECT count(*) FROM loans WHERE cooperative_id=$1 AND application_id=$2",
            cid,
            application,
        )
        == 0
    )


async def test_simultaneous_submissions_allow_only_one_open_application(
    db: asyncpg.Connection, loan_base: LoanBase
) -> None:
    member = await setup_loan_member(db, loan_base)
    cid, users, product = loan_base.cooperative_id, loan_base.user_ids, loan_base.product_id

    results = await asyncio.gather(
        *(
            submit_application(
                session(cid, users[0], "officer"),
                member_id=member,
                product_id=product,
                principal_kobo=500_000,
                tenor_months=6,
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, Conflict) for result in results) == 1
    assert (
        await db.fetchval(
            """SELECT count(*) FROM loan_applications WHERE cooperative_id=$1 AND member_id=$2
           AND status NOT IN ('rejected','withdrawn','expired','disbursement_failed','closed','written_off')""",
            cid,
            member,
        )
        == 1
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            """INSERT INTO loan_applications(cooperative_id,id,member_id,product_id,policy_version_id,
              submitted_by,staff_submitted,principal_kobo,tenor_months,annual_rate_bps,status)
              VALUES($1,$2,$3,$4,$5,$6,true,500000,6,0,'under_review')""",
            cid,
            uuid.uuid4(),
            member,
            product,
            loan_base.policy_id,
            users[0],
        )
