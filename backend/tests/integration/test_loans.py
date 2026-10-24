"""Loan calculations and workflow invariants on local PostgreSQL as ajo_app."""

from __future__ import annotations

import asyncio
import json
import uuid
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


def session(cid: uuid.UUID, uid: uuid.UUID, *roles: str) -> CurrentSession:
    return CurrentSession(
        cid, "loan-test", uuid.uuid4(), uid, "staff@example.test", roles, bytes(32)
    )


async def setup_loan_tenant(
    db: asyncpg.Connection, roles: tuple[str, ...] = ("officer", "committee", "committee")
):
    seed = await create_tenant(
        db, name="Loan test", slug_prefix="loan", roles=roles, include_member=True
    )
    assert seed.member_id is not None
    cid, member = seed.cooperative_id, seed.member_id
    proposer = seed.user_ids[0]
    approver = seed.user_ids[1] if len(seed.user_ids) > 1 else proposer
    policy_id, product_id = uuid.uuid4(), uuid.uuid4()
    today = date.today()
    await db.execute(
        """INSERT INTO cooperative_policy_versions(cooperative_id,id,version,status,effective_from,
          proposed_by,approved_by,salary_basis,deduction_cap_bps,max_active_loans,guarantors_required,
          min_savings_kobo,loan_to_savings_multiple_x100,interest_method)
          VALUES($1,$2,1,'approved',$3,$4,$5,'net',7000,1,0,0,100000,'reducing_balance')""",
        cid,
        policy_id,
        today,
        proposer,
        approver,
    )
    await db.execute(
        """INSERT INTO loan_products(cooperative_id,id,code,name,interest_method,annual_rate_bps,
          min_amount_kobo,max_amount_kobo,min_tenor_months,max_tenor_months,fee_bps,
          guarantors_required,status,approved_at)
          VALUES($1,$2,'TEST','Test','reducing_balance',0,1,100000000,1,36,0,0,'approved',now())""",
        cid,
        product_id,
    )
    await db.execute(
        """INSERT INTO salary_records(cooperative_id,member_id,effective_from,basis,amount_kobo,source,recorded_by)
          VALUES($1,$2,$3,'net',100000000,'declared',$4)""",
        cid,
        member,
        today,
        proposer,
    )
    bank_id, savings_id = uuid.uuid4(), uuid.uuid4()
    await db.executemany(
        "INSERT INTO accounts(cooperative_id,id,code,name,account_type) VALUES($1,$2,$3,$4,$5)",
        [
            (cid, bank_id, "bank", "Bank", "asset"),
            (cid, savings_id, "member_savings", "Savings", "liability"),
        ],
    )
    await db.fetchval(
        """SELECT post_journal_entry($1,current_date,'seed',$2,'test savings',$3,$4,$5::jsonb,NULL)""",
        cid,
        uuid.uuid4(),
        proposer,
        f"seed-{uuid.uuid4()}",
        json.dumps(
            [
                {"account_id": str(bank_id), "member_id": str(member), "debit_kobo": 100000000},
                {"account_id": str(savings_id), "member_id": str(member), "credit_kobo": 100000000},
            ]
        ),
    )
    return cid, member, seed.user_ids, product_id, policy_id


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


async def test_concurrent_approvals_for_member_cannot_both_succeed(db: asyncpg.Connection) -> None:
    cid, member, users, product, _ = await setup_loan_tenant(db)
    application = await submit_application(
        session(cid, users[0], "officer"),
        member_id=member,
        product_id=product,
        principal_kobo=500_000,
        tenor_months=6,
    )
    results = await asyncio.gather(
        decide_application(session(cid, users[1], "committee"), application, "approved", "review"),
        decide_application(session(cid, users[2], "committee"), application, "approved", "review"),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, Conflict) for result in results) == 1


async def test_snapshot_invalidated_after_term_change(db: asyncpg.Connection) -> None:
    cid, member, users, product, _ = await setup_loan_tenant(db)
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
    for index, changes in enumerate(term_changes):
        application = await submit_application(
            session(cid, users[0], "officer"),
            member_id=member,
            product_id=product,
            principal_kobo=500_000,
            tenor_months=6,
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


async def test_approver_cannot_equal_submitter(db: asyncpg.Connection) -> None:
    cid, member, users, product, _ = await setup_loan_tenant(db)
    application = await submit_application(
        session(cid, users[0], "officer"),
        member_id=member,
        product_id=product,
        principal_kobo=500_000,
        tenor_months=6,
    )
    with pytest.raises(Conflict, match="submitter"):
        await decide_application(
            session(cid, users[0], "committee"), application, "approved", "review"
        )


async def test_policy_version_is_stored_on_approval_and_loan(db: asyncpg.Connection) -> None:
    cid, member, users, product, policy_id = await setup_loan_tenant(db)
    application = await submit_application(
        session(cid, users[0], "officer"),
        member_id=member,
        product_id=product,
        principal_kobo=500_000,
        tenor_months=6,
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
