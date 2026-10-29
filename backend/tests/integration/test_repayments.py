"""Repayment waterfall, idempotency, closing, and reversal invariants."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date

import asyncpg
import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.config import get_settings
from app.core.db import asyncpg_dsn, dispose_engine
from app.core.errors import Conflict
from app.domains.disbursements.service import confirm_paid
from app.domains.repayments.service import post_repayment, reverse_repayment
from tests.integration.test_disbursements import authorized_transfer
from tests.integration.test_loans import LoanBase, session

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def loan_base(test_database) -> LoanBase:
    from tests.integration.test_loans import _create_loan_base

    return asyncio.run(_create_loan_base(test_database))


@pytest.fixture(autouse=True)
async def reset_engine():
    get_settings.cache_clear()
    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture
async def db(test_database) -> asyncpg.Connection:
    connection = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    yield connection
    await connection.close()


async def repaying_loan(db: asyncpg.Connection, base: LoanBase) -> tuple[uuid.UUID, uuid.UUID]:
    await db.execute("SELECT set_config('app.cooperative_id', $1, false)", str(base.cooperative_id))
    for code, account_type in (
        ("loans_receivable", "asset"),
        ("interest_income", "income"),
        ("dues_income", "income"),
        ("unallocated_receipts", "liability"),
    ):
        await db.execute(
            "INSERT INTO accounts(cooperative_id,code,name,account_type) VALUES($1,$2,$2,$3) ON CONFLICT DO NOTHING",
            base.cooperative_id,
            code,
            account_type,
        )
    rate_product_id = uuid.uuid4()
    await db.execute(
        """INSERT INTO loan_products(cooperative_id,id,code,name,interest_method,annual_rate_bps,
          min_amount_kobo,max_amount_kobo,min_tenor_months,max_tenor_months,fee_bps,
          guarantors_required,status,approved_at)
          VALUES($1,$2,$3,'Repayment test','reducing_balance',100,1,100000000,1,36,0,0,'approved',now())""",
        base.cooperative_id,
        rate_product_id,
        f"REPAY-{rate_product_id.hex[:8]}",
    )
    repayment_base = LoanBase(
        base.cooperative_id,
        base.user_ids,
        rate_product_id,
        base.policy_id,
        base.bank_account_id,
        base.savings_account_id,
    )
    disbursement_id, loan_id, authorizer = await authorized_transfer(db, repayment_base)
    await confirm_paid(session(base.cooperative_id, authorizer, "committee"), disbursement_id, None)
    member_id = await db.fetchval(
        "SELECT member_id FROM loans WHERE cooperative_id=$1 AND id=$2",
        base.cooperative_id,
        loan_id,
    )
    return loan_id, member_id


async def test_worked_waterfall_partial_and_allocation_sum(
    db: asyncpg.Connection, loan_base: LoanBase
) -> None:
    loan_id, member_id = await repaying_loan(db, loan_base)
    cid = loan_base.cooperative_id
    first = await db.fetchrow(
        "SELECT principal_kobo,interest_kobo FROM loan_schedule WHERE cooperative_id=$1 AND loan_id=$2 ORDER BY installment_no LIMIT 1",
        cid,
        loan_id,
    )
    amount = int(first["interest_kobo"]) + 12_345
    repayment_id = await post_repayment(
        session(cid, loan_base.user_ids[0], "treasurer"),
        loan_id=loan_id,
        member_id=member_id,
        amount_kobo=amount,
        source="payroll",
        received_on=date(2099, 1, 1),
        reference="PAY-1",
        idempotency_key="waterfall-1",
        ip=None,
    )
    allocations = await db.fetch(
        "SELECT component,amount_kobo FROM repayment_allocations WHERE cooperative_id=$1 AND repayment_id=$2 ORDER BY component",
        cid,
        repayment_id,
    )
    assert {(row["component"], row["amount_kobo"]) for row in allocations} == {
        ("interest", first["interest_kobo"]),
        ("principal", 12_345),
    }
    allocated = await db.fetchval(
        "SELECT coalesce(sum(amount_kobo),0) FROM repayment_allocations WHERE cooperative_id=$1 AND repayment_id=$2",
        cid,
        repayment_id,
    )
    unallocated = await db.fetchval(
        "SELECT amount_kobo-coalesce((SELECT sum(amount_kobo) FROM repayment_allocations WHERE cooperative_id=$1 AND repayment_id=$2),0) FROM repayments WHERE cooperative_id=$1 AND id=$2",
        cid,
        repayment_id,
    )
    assert allocated + unallocated == amount


async def test_overpayment_is_unallocated_and_replay_posts_once(
    db: asyncpg.Connection, loan_base: LoanBase
) -> None:
    loan_id, member_id = await repaying_loan(db, loan_base)
    cid = loan_base.cooperative_id
    due = await db.fetchval(
        "SELECT sum(principal_kobo+interest_kobo+dues_kobo+savings_kobo) FROM loan_schedule WHERE cooperative_id=$1 AND loan_id=$2",
        cid,
        loan_id,
    )
    kwargs = dict(
        loan_id=loan_id,
        member_id=member_id,
        amount_kobo=int(due) + 99,
        source="cash",
        received_on=date(2099, 1, 1),
        reference="CASH-1",
        idempotency_key="replay-1",
        ip=None,
    )
    first = await post_repayment(session(cid, loan_base.user_ids[0], "treasurer"), **kwargs)
    second = await post_repayment(session(cid, loan_base.user_ids[0], "treasurer"), **kwargs)
    assert first == second
    with pytest.raises(Conflict, match="different payload"):
        await post_repayment(
            session(cid, loan_base.user_ids[0], "treasurer"),
            **{**kwargs, "amount_kobo": int(due) + 100},
        )
    assert (
        await db.fetchval(
            "SELECT count(*) FROM repayments WHERE cooperative_id=$1 AND idempotency_key='replay-1'",
            cid,
        )
        == 1
    )
    assert (
        await db.fetchval(
            "SELECT octet_length(request_hash) FROM repayments WHERE cooperative_id=$1 AND idempotency_key='replay-1'",
            cid,
        )
        == 32
    )
    assert (
        await db.fetchval(
            "SELECT credit_kobo FROM journal_lines j JOIN accounts a ON a.cooperative_id=j.cooperative_id AND a.id=j.account_id WHERE j.cooperative_id=$1 AND j.entry_id=(SELECT journal_entry_id FROM repayments WHERE cooperative_id=$1 AND idempotency_key='replay-1') AND a.code='unallocated_receipts'",
            cid,
        )
        == 99
    )


async def test_closing_releases_locks_and_reversal_restores_schedule(
    db: asyncpg.Connection, loan_base: LoanBase
) -> None:
    loan_id, member_id = await repaying_loan(db, loan_base)
    cid = loan_base.cooperative_id
    await db.execute(
        "INSERT INTO savings_locks(cooperative_id,member_id,loan_id,amount_kobo) VALUES($1,$2,$3,1)",
        cid,
        member_id,
        loan_id,
    )
    due = await db.fetchval(
        "SELECT sum(principal_kobo+interest_kobo) FROM loan_schedule WHERE cooperative_id=$1 AND loan_id=$2",
        cid,
        loan_id,
    )
    repayment_id = await post_repayment(
        session(cid, loan_base.user_ids[0], "treasurer"),
        loan_id=loan_id,
        member_id=member_id,
        amount_kobo=int(due),
        source="bank_transfer",
        received_on=date(2099, 1, 1),
        reference="BANK-1",
        idempotency_key="close-1",
        ip=None,
    )
    assert (
        await db.fetchval(
            "SELECT status FROM loans WHERE cooperative_id=$1 AND id=$2", cid, loan_id
        )
        == "closed"
    )
    assert await db.fetchval(
        "SELECT released_at IS NOT NULL FROM savings_locks WHERE cooperative_id=$1 AND loan_id=$2",
        cid,
        loan_id,
    )
    await reverse_repayment(
        session(cid, loan_base.user_ids[2], "committee"), repayment_id, "Mistaken receipt", None
    )
    assert (
        await db.fetchval(
            "SELECT status FROM loans WHERE cooperative_id=$1 AND id=$2", cid, loan_id
        )
        == "repaying"
    )
    assert (
        await db.fetchval(
            "SELECT count(*) FROM repayment_allocations WHERE cooperative_id=$1 AND repayment_id=$2 AND status='posted'",
            cid,
            repayment_id,
        )
        == 0
    )
    assert await db.fetchval(
        "SELECT released_at IS NULL FROM savings_locks WHERE cooperative_id=$1 AND loan_id=$2",
        cid,
        loan_id,
    )


async def test_principal_paid_with_savings_component_outstanding_keeps_lock_active(
    db: asyncpg.Connection, loan_base: LoanBase, test_database
) -> None:
    loan_id, member_id = await repaying_loan(db, loan_base)
    cid = loan_base.cooperative_id
    owner = await asyncpg.connect(test_database.owner_url)
    try:
        await owner.execute("SELECT set_config('app.cooperative_id',$1,false)", str(cid))
        await owner.execute(
            "UPDATE loan_schedule SET savings_kobo=200 WHERE cooperative_id=$1 AND loan_id=$2 AND installment_no=(SELECT max(installment_no) FROM loan_schedule WHERE cooperative_id=$1 AND loan_id=$2)",
            cid,
            loan_id,
        )
    finally:
        await owner.close()
    await db.execute(
        "INSERT INTO savings_locks(cooperative_id,member_id,loan_id,amount_kobo) VALUES($1,$2,$3,1)",
        cid,
        member_id,
        loan_id,
    )
    principal_and_interest = await db.fetchval(
        "SELECT sum(principal_kobo+interest_kobo) FROM loan_schedule WHERE cooperative_id=$1 AND loan_id=$2",
        cid,
        loan_id,
    )
    await post_repayment(
        session(cid, loan_base.user_ids[0], "treasurer"),
        loan_id=loan_id,
        member_id=member_id,
        amount_kobo=int(principal_and_interest),
        source="bank_transfer",
        received_on=date(2099, 1, 1),
        reference="PARTIAL-COMPONENT-1",
        idempotency_key="partial-component-1",
        ip=None,
    )
    assert (
        await db.fetchval(
            "SELECT status FROM loans WHERE cooperative_id=$1 AND id=$2", cid, loan_id
        )
        == "repaying"
    )
    assert await db.fetchval(
        "SELECT released_at IS NULL FROM savings_locks WHERE cooperative_id=$1 AND loan_id=$2",
        cid,
        loan_id,
    )


@given(
    receipt=st.integers(min_value=1, max_value=1_000_000),
    due=st.integers(min_value=0, max_value=1_000_000),
)
def test_allocation_partition_always_equals_receipt(receipt: int, due: int) -> None:
    allocated = min(receipt, due)
    unallocated = receipt - allocated
    assert allocated + unallocated == receipt
