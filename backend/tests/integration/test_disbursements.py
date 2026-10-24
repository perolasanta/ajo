"""Manual loan disbursement separation and ledger invariants on local PostgreSQL."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date

import asyncpg
import pytest

from app.core.db import asyncpg_dsn, dispose_engine
from app.core.errors import Conflict
from app.domains.disbursements.service import (
    authorize,
    confirm_paid,
    fail_transfer,
    prepare,
    record_transfer,
)
from app.domains.loans.service import decide_application, submit_application
from tests.integration.test_loans import (
    LoanBase,
    _create_loan_base,
    session,
    setup_loan_member,
)

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def loan_base(test_database) -> LoanBase:
    return asyncio.run(_create_loan_base(test_database))


@pytest.fixture(autouse=True)
async def reset_engine():
    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture
async def db(test_database) -> asyncpg.Connection:
    connection = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    yield connection
    await connection.close()


async def ready_loan(db: asyncpg.Connection, base: LoanBase) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    member = await setup_loan_member(db, base)
    cid, preparer, approver = base.cooperative_id, *base.user_ids[:2]
    bank_account = uuid.uuid4()
    await db.execute("""INSERT INTO bank_accounts(cooperative_id,id,member_id,bank_code,
      account_number_encrypted,last4,account_name,status,verified_at)
      VALUES($1,$2,$3,'001','ciphertext','4321','Member','verified',now())""",
      cid, bank_account, member)
    application = await submit_application(session(cid, preparer, "officer"), member_id=member,
      product_id=base.product_id, principal_kobo=500_000, tenor_months=6,
      destination_bank_account_id=bank_account)
    await db.execute("UPDATE loan_applications SET status='under_review' WHERE cooperative_id=$1 AND id=$2", cid, application)
    await decide_application(session(cid, approver, "committee"), application, "approved", "review")
    loan_id = await db.fetchval("SELECT id FROM loans WHERE cooperative_id=$1 AND application_id=$2", cid, application)
    await db.execute("""UPDATE member_consents SET status='confirmed',decided_at=now()
      WHERE cooperative_id=$1 AND application_id=$2 AND kind='final_terms'""", cid, application)
    await db.execute("UPDATE loan_applications SET status='ready_for_disbursement' WHERE cooperative_id=$1 AND id=$2", cid, application)
    await db.execute("UPDATE loans SET status='ready_for_disbursement' WHERE cooperative_id=$1 AND id=$2", cid, loan_id)
    return application, loan_id, bank_account


def staff(cid: uuid.UUID, uid: uuid.UUID, *roles: str):
    return session(cid, uid, *roles)


async def prepared_disbursement(db: asyncpg.Connection, base: LoanBase) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    _, loan_id, _ = await ready_loan(db, base)
    cid, preparer = base.cooperative_id, base.user_ids[0]
    result = await prepare(staff(cid, preparer, "treasurer"), loan_id, "127.0.0.1")
    assert result is not None
    return result, loan_id, preparer


async def authorized_transfer(db: asyncpg.Connection, base: LoanBase) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    identifier, loan_id, preparer = await prepared_disbursement(db, base)
    cid, authorizer = base.cooperative_id, base.user_ids[2]
    await authorize(staff(cid, authorizer, "committee"), identifier, "127.0.0.1")
    await record_transfer(staff(cid, preparer, "treasurer"), identifier, "BANK-REF-1", date.today(), "127.0.0.1")
    return identifier, loan_id, authorizer


async def test_same_user_cannot_prepare_and_authorize(db: asyncpg.Connection, loan_base: LoanBase) -> None:
    identifier, _, preparer = await prepared_disbursement(db, loan_base)
    with pytest.raises(Conflict, match="independent"):
        await authorize(staff(loan_base.cooperative_id, preparer, "admin", "treasurer"), identifier, None)


async def test_loan_approver_cannot_authorize(db: asyncpg.Connection, loan_base: LoanBase) -> None:
    identifier, _, _ = await prepared_disbursement(db, loan_base)
    with pytest.raises(Conflict, match="independent"):
        await authorize(staff(loan_base.cooperative_id, loan_base.user_ids[1], "committee"), identifier, None)


async def test_missing_or_mismatched_consent_blocks_preparation(db: asyncpg.Connection, loan_base: LoanBase) -> None:
    _, loan_id, _ = await ready_loan(db, loan_base)
    cid = loan_base.cooperative_id
    await db.execute("UPDATE member_consents SET status='pending' WHERE cooperative_id=$1 AND application_id=(SELECT application_id FROM loans WHERE cooperative_id=$1 AND id=$2) AND kind='final_terms'", cid, loan_id)
    with pytest.raises(Conflict, match="consent"):
        await prepare(staff(cid, loan_base.user_ids[0], "treasurer"), loan_id, None)
    await db.execute("UPDATE member_consents SET status='confirmed',terms_hash=$3 WHERE cooperative_id=$1 AND application_id=(SELECT application_id FROM loans WHERE cooperative_id=$1 AND id=$2) AND kind='final_terms'", cid, loan_id, bytes(32))
    with pytest.raises(Conflict, match="consent"):
        await prepare(staff(cid, loan_base.user_ids[0], "treasurer"), loan_id, None)


async def test_destination_change_blocks_and_raises_alert(db: asyncpg.Connection, loan_base: LoanBase) -> None:
    application, loan_id, _old_account = await ready_loan(db, loan_base)
    cid = loan_base.cooperative_id
    replacement = uuid.uuid4()
    member = await db.fetchval("SELECT member_id FROM loans WHERE cooperative_id=$1 AND id=$2", cid, loan_id)
    await db.execute("""INSERT INTO bank_accounts(cooperative_id,id,member_id,bank_code,account_number_encrypted,last4,account_name,status,verified_at)
      VALUES($1,$2,$3,'002','ciphertext','9876','Member','verified',now())""", cid, replacement, member)
    await db.execute("UPDATE loan_applications SET destination_bank_account_id=$3 WHERE cooperative_id=$1 AND id=$2", cid, application, replacement)
    assert await prepare(staff(cid, loan_base.user_ids[0], "treasurer"), loan_id, None) is None
    assert await db.fetchval("SELECT count(*) FROM fraud_alerts WHERE cooperative_id=$1 AND entity_id=$2 AND rule_code='destination_changed'", cid, application) == 1


async def test_double_confirm_paid_posts_once(db: asyncpg.Connection, loan_base: LoanBase) -> None:
    identifier, loan_id, authorizer = await authorized_transfer(db, loan_base)
    cid = loan_base.cooperative_id
    first = await confirm_paid(staff(cid, authorizer, "committee"), identifier, None)
    assert first is not None
    with pytest.raises(Conflict):
        await confirm_paid(staff(cid, authorizer, "committee"), identifier, None)
    assert await db.fetchval("SELECT count(*) FROM journal_entries WHERE cooperative_id=$1 AND source_id=$2", cid, identifier) == 1


async def test_posted_entry_balances_with_expected_accounts_and_fee(db: asyncpg.Connection, loan_base: LoanBase) -> None:
    await db.execute("UPDATE loan_products SET fee_bps=100 WHERE cooperative_id=$1 AND id=$2", loan_base.cooperative_id, loan_base.product_id)
    identifier, loan_id, authorizer = await authorized_transfer(db, loan_base)
    cid = loan_base.cooperative_id
    entry = await confirm_paid(staff(cid, authorizer, "committee"), identifier, None)
    assert entry is not None
    totals = await db.fetchrow("""SELECT sum(j.debit_kobo) AS debits,sum(j.credit_kobo) AS credits,
      sum(j.debit_kobo) FILTER (WHERE a.code='loans_receivable') AS principal_debit,
      sum(j.credit_kobo) FILTER (WHERE a.code='bank') AS bank_credit,
      sum(j.credit_kobo) FILTER (WHERE a.code='fee_income') AS fee_credit
      FROM journal_lines j JOIN accounts a ON a.cooperative_id=j.cooperative_id AND a.id=j.account_id
      WHERE j.cooperative_id=$1 AND j.entry_id=$2""", cid, entry)
    assert totals["debits"] == totals["credits"] == 500_000
    assert totals["principal_debit"] == 500_000
    assert totals["bank_credit"] == 495_000
    assert totals["fee_credit"] == 5_000
    assert await db.fetchval("SELECT status FROM loans WHERE cooperative_id=$1 AND id=$2", cid, loan_id) == "repaying"


async def test_concurrent_confirmations_cannot_both_succeed(db: asyncpg.Connection, loan_base: LoanBase) -> None:
    identifier, _, authorizer = await authorized_transfer(db, loan_base)
    cid = loan_base.cooperative_id
    results = await asyncio.gather(
        confirm_paid(staff(cid, authorizer, "committee"), identifier, None),
        confirm_paid(staff(cid, authorizer, "committee"), identifier, None),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, Conflict) for result in results) == 1


async def test_failed_transfer_posts_nothing_and_restores_ready_state(db: asyncpg.Connection, loan_base: LoanBase) -> None:
    identifier, loan_id, preparer = await prepared_disbursement(db, loan_base)
    cid = loan_base.cooperative_id
    await authorize(staff(cid, loan_base.user_ids[2], "committee"), identifier, None)
    await fail_transfer(staff(cid, preparer, "treasurer"), identifier, "Bank rejected transfer", None)
    assert await db.fetchval("SELECT count(*) FROM journal_entries WHERE cooperative_id=$1 AND source_id=$2", cid, identifier) == 0
    assert await db.fetchval("SELECT status FROM loans WHERE cooperative_id=$1 AND id=$2", cid, loan_id) == "ready_for_disbursement"
