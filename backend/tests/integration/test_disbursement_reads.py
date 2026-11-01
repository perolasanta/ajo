"""Tenant isolation, filtering and pagination for disbursement read projections."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest

from app.core.db import asyncpg_dsn, dispose_engine
from app.core.errors import NotFound
from app.domains.disbursements.reads import get_disbursement, list_disbursements, list_ready_loans
from app.domains.disbursements.service import prepare
from tests.integration.test_loans import LoanBase, _create_loan_base, session, setup_loan_member

pytestmark = pytest.mark.db


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


async def _make_disbursement(db: asyncpg.Connection, base: LoanBase) -> str:
    member = await setup_loan_member(db, base)
    cid = base.cooperative_id
    preparer, approver = base.user_ids[:2]
    bank_id = uuid.uuid4()
    await db.execute(
        """INSERT INTO bank_accounts(cooperative_id,id,member_id,bank_code,account_number_encrypted,
          last4,account_name,status,verified_at) VALUES($1,$2,$3,'001','ciphertext','4321','Member','verified',now())""",
        cid,
        bank_id,
        member,
    )
    from app.domains.loans.service import decide_application, submit_application

    application = await submit_application(
        session(cid, preparer, "officer"),
        member_id=member,
        product_id=base.product_id,
        principal_kobo=500_000,
        tenor_months=6,
        destination_bank_account_id=bank_id,
    )
    await db.execute(
        "UPDATE loan_applications SET status='under_review' WHERE cooperative_id=$1 AND id=$2",
        cid,
        application,
    )
    await decide_application(session(cid, approver, "committee"), application, "approved", "review")
    loan = await db.fetchval(
        "SELECT id FROM loans WHERE cooperative_id=$1 AND application_id=$2", cid, application
    )
    await db.execute(
        "UPDATE member_consents SET status='confirmed',decided_at=now() WHERE cooperative_id=$1 AND application_id=$2 AND kind='final_terms'",
        cid,
        application,
    )
    await db.execute(
        "UPDATE loan_applications SET status='ready_for_disbursement' WHERE cooperative_id=$1 AND id=$2",
        cid,
        application,
    )
    await db.execute(
        "UPDATE loans SET status='ready_for_disbursement' WHERE cooperative_id=$1 AND id=$2",
        cid,
        loan,
    )
    ready = await list_ready_loans(
        session(cid, preparer, "treasurer"), search="Loan test member", page=1, page_size=10
    )
    assert any(item["loan_id"] == str(loan) for item in ready["items"])
    identifier = await prepare(session(cid, preparer, "treasurer"), loan, None)
    assert identifier is not None
    no_longer_ready = await list_ready_loans(
        session(cid, preparer, "treasurer"), search="Loan test member", page=1, page_size=10
    )
    assert all(item["loan_id"] != str(loan) for item in no_longer_ready["items"])
    return str(identifier)


@pytest.fixture(scope="module")
def base(test_database) -> LoanBase:
    return asyncio.run(_create_loan_base(test_database))


async def test_search_pagination_status_and_tenant_scope(db, test_database, base) -> None:
    other = await _create_loan_base(test_database)
    await _make_disbursement(db, base)
    await _make_disbursement(db, base)
    await _make_disbursement(db, other)
    first = await list_disbursements(
        session(base.cooperative_id, base.user_ids[0], "treasurer"),
        status="pending_authorization",
        search="M-",
        page=1,
        page_size=1,
    )
    assert first["total"] == 2 and len(first["items"]) == 1
    second = await list_disbursements(
        session(base.cooperative_id, base.user_ids[0], "treasurer"),
        status="pending_authorization",
        search="M-",
        page=2,
        page_size=1,
    )
    assert second["total"] == 2 and first["items"][0]["id"] != second["items"][0]["id"]
    missing = await list_disbursements(
        session(base.cooperative_id, base.user_ids[0], "treasurer"),
        status="paid",
        search="not-a-member",
        page=1,
        page_size=10,
    )
    assert missing["items"] == [] and missing["total"] == 0
    isolated = await list_disbursements(
        session(other.cooperative_id, other.user_ids[0], "treasurer"),
        status=None,
        search=None,
        page=1,
        page_size=10,
    )
    assert isolated["total"] == 1 and len(isolated["items"]) == 1


async def test_detail_is_tenant_scoped_and_auditor_omits_bank_fields(
    db, test_database, base
) -> None:
    other = await _create_loan_base(test_database)
    identifier = uuid.UUID(await _make_disbursement(db, base))
    auditor_detail = await get_disbursement(
        session(base.cooperative_id, base.user_ids[0], "auditor"), identifier
    )
    assert auditor_detail["id"] == str(identifier)
    assert "bank_code" not in auditor_detail and "last4" not in auditor_detail
    with pytest.raises(NotFound):
        await get_disbursement(
            session(other.cooperative_id, other.user_ids[0], "admin"), identifier
        )


async def test_auditor_list_omits_bank_fields(db, base) -> None:
    identifier = await _make_disbursement(db, base)
    page = await list_disbursements(
        session(base.cooperative_id, base.user_ids[0], "auditor"),
        status=None,
        search=None,
        page=1,
        page_size=100,
    )
    auditor_item = next(item for item in page["items"] if item["id"] == identifier)
    assert "status" in auditor_item and "full_name" in auditor_item
    assert "bank_code" not in auditor_item and "last4" not in auditor_item
