"""Payroll deduction, ledger posting, and state-machine integration tests."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date

import asyncpg
import pytest

from app.core.db import asyncpg_dsn, dispose_engine
from app.core.errors import Conflict, Forbidden
from app.domains.auth.service import CurrentSession
from app.domains.payroll import service
from app.domains.repayments.service import post_repayment
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


def session(cid: uuid.UUID, uid: uuid.UUID, role: str) -> CurrentSession:
    return CurrentSession(
        cid, "payroll-test", uuid.uuid4(), uid, "staff@example.test", (role,), bytes(32)
    )


async def setup_payroll_fixture(
    db: asyncpg.Connection,
) -> tuple[uuid.UUID, tuple[uuid.UUID, ...], list[tuple[uuid.UUID, uuid.UUID, str]]]:
    seed = await create_tenant(
        db,
        name="Payroll test",
        slug_prefix="payroll",
        roles=("officer", "committee", "treasurer", "admin"),
    )
    cid, users = seed.cooperative_id, seed.user_ids
    policy_id, product_id = uuid.uuid4(), uuid.uuid4()
    await db.execute(
        """INSERT INTO cooperative_policy_versions(cooperative_id,id,version,status,effective_from,
          proposed_by,approved_by,salary_basis,deduction_cap_bps,max_active_loans,guarantors_required,
          min_savings_kobo,loan_to_savings_multiple_x100,interest_method)
          VALUES($1,$2,1,'approved',$3,$4,$5,'net',7000,1,0,0,100000,'reducing_balance')""",
        cid,
        policy_id,
        date.today(),
        users[0],
        users[1],
    )
    await db.execute(
        """INSERT INTO loan_products(cooperative_id,id,code,name,interest_method,annual_rate_bps,
          min_amount_kobo,max_amount_kobo,min_tenor_months,max_tenor_months,fee_bps,
          guarantors_required,status,approved_at)
          VALUES($1,$2,'PAYROLL','Payroll','reducing_balance',0,1,1000000,1,12,0,0,'approved',now())""",
        cid,
        product_id,
    )
    accounts = (
        ("bank", "asset"),
        ("loans_receivable", "asset"),
        ("interest_income", "income"),
        ("dues_income", "income"),
        ("member_savings", "liability"),
        ("unallocated_receipts", "liability"),
    )
    for code, account_type in accounts:
        await db.execute(
            "INSERT INTO accounts(cooperative_id,code,name,account_type) VALUES($1,$2,$2,$3)",
            cid,
            code,
            account_type,
        )

    loans: list[tuple[uuid.UUID, uuid.UUID, str]] = []
    for index, (status, paused, staff_no) in enumerate(
        (
            ("disputed", False, "STAFF-D"),
            ("repaying", True, "STAFF-P"),
            ("repaying", False, "STAFF-A"),
        )
    ):
        member_id, application_id, snapshot_id, loan_id = (uuid.uuid4() for _ in range(4))
        await db.execute(
            """INSERT INTO members(id,cooperative_id,member_no,full_name,phone_e164,staff_no)
               VALUES($1,$2,$3,$4,'+2348000000000',$5)""",
            member_id,
            cid,
            f"M-{index}",
            f"Member {index}",
            staff_no,
        )
        await db.execute(
            """INSERT INTO loan_applications(cooperative_id,id,member_id,product_id,policy_version_id,
              submitted_by,principal_kobo,tenor_months,annual_rate_bps,status)
              VALUES($1,$2,$3,$4,$5,$6,100,1,0,$7)""",
            cid,
            application_id,
            member_id,
            product_id,
            policy_id,
            users[0],
            status,
        )
        await db.execute(
            """INSERT INTO loan_terms_snapshots(cooperative_id,id,application_id,terms,terms_sha256,created_by)
              VALUES($1,$2,$3,$4::jsonb,$5,$6)""",
            cid,
            snapshot_id,
            application_id,
            json.dumps({"principal_kobo": 100}),
            bytes(32),
            users[0],
        )
        await db.execute(
            """INSERT INTO loans(cooperative_id,id,application_id,member_id,product_id,policy_version_id,
              terms_snapshot_id,status,deductions_paused)
              VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
            cid,
            loan_id,
            application_id,
            member_id,
            product_id,
            policy_id,
            snapshot_id,
            status,
            paused,
        )
        await db.execute(
            """INSERT INTO loan_schedule(cooperative_id,loan_id,installment_no,due_at,principal_kobo,
              interest_kobo,payment_kobo,balance_kobo,dues_kobo,savings_kobo)
              VALUES($1,$2,1,'2099-01-01T00:00:00Z',100,10,110,0,20,30)""",
            cid,
            loan_id,
        )
        loans.append((loan_id, member_id, staff_no))
    return cid, users, loans


async def test_disputed_and_paused_loans_are_excluded_from_payroll_deductions_and_posting(
    db: asyncpg.Connection,
) -> None:
    cid, users, loans = await setup_payroll_fixture(db)
    profile_id = await service.create_profile(
        session(cid, users[3], "admin"),
        {"staff_no_column": "Staff", "amount_column": "Amount"},
    )
    run_id = await service.create_run(session(cid, users[0], "officer"), profile_id, 2099, 1)

    expected_csv = await service.export_expected(session(cid, users[0], "officer"), run_id)
    exported_rows = list(csv.DictReader(io.StringIO(expected_csv.decode())))
    assert [(row["Staff"], row["Amount"]) for row in exported_rows] == [("STAFF-A", "160")]

    await db.execute(
        "UPDATE payroll_runs SET status='imported',expected_total_kobo=160,file_total_kobo=480,imported_by=$3,imported_at=now() WHERE cooperative_id=$1 AND id=$2",
        cid,
        run_id,
        users[0],
    )
    for index, (_loan_id, member_id, staff_no) in enumerate(loans):
        expected = (110, 30, 20, "matched") if index == 2 else (0, 0, 0, "unknown_member")
        await db.execute(
            """INSERT INTO payroll_items(cooperative_id,run_id,member_id,staff_no_raw,expected_loan_kobo,
              expected_savings_kobo,expected_dues_kobo,actual_kobo,classification)
              VALUES($1,$2,$3,$4,$5,$6,$7,160,$8)""",
            cid,
            run_id,
            member_id,
            staff_no,
            *expected,
        )

    assert await service.post_run(session(cid, users[2], "treasurer"), run_id) == 1
    posted_loans = await db.fetch(
        """SELECT l.id,l.status,l.deductions_paused,r.amount_kobo
           FROM repayments r JOIN loans l ON l.cooperative_id=r.cooperative_id AND l.id=r.loan_id
           WHERE r.cooperative_id=$1""",
        cid,
    )
    assert [(row["id"], row["amount_kobo"]) for row in posted_loans] == [(loans[2][0], 160)]
    assert (
        await db.fetchval(
            "SELECT count(*) FROM journal_entries WHERE cooperative_id=$1 AND source_type='loan_repayment'",
            cid,
        )
        == 1
    )
    ledger_lines = await db.fetch(
        """SELECT a.code,sum(l.debit_kobo)::bigint debit_kobo,sum(l.credit_kobo)::bigint credit_kobo
           FROM journal_lines l JOIN accounts a
             ON a.cooperative_id=l.cooperative_id AND a.id=l.account_id
           WHERE l.cooperative_id=$1 AND l.entry_id=(
             SELECT journal_entry_id FROM repayments WHERE cooperative_id=$1 AND loan_id=$2)
           GROUP BY a.code ORDER BY a.code""",
        cid,
        loans[2][0],
    )
    assert {(row["code"], row["debit_kobo"], row["credit_kobo"]) for row in ledger_lines} == {
        ("bank", 160, 0),
        ("dues_income", 0, 20),
        ("interest_income", 0, 10),
        ("loans_receivable", 0, 100),
        ("member_savings", 0, 30),
    }


async def test_paused_repayment_is_manual_treasurer_only_and_records_reason(
    db: asyncpg.Connection,
) -> None:
    cid, users, loans = await setup_payroll_fixture(db)
    disputed_id, disputed_member, _ = loans[0]
    paused_id, paused_member, _ = loans[1]

    for loan_id, member_id in ((disputed_id, disputed_member), (paused_id, paused_member)):
        with pytest.raises(Conflict, match="payroll"):
            await post_repayment(
                session(cid, users[2], "treasurer"),
                loan_id=loan_id,
                member_id=member_id,
                amount_kobo=1,
                source="payroll",
                received_on=date(2099, 1, 31),
                reference="AUTO-1",
                idempotency_key=f"auto-{loan_id}",
                ip=None,
            )

    with pytest.raises(Forbidden, match="treasurer"):
        await post_repayment(
            session(cid, users[0], "officer"),
            loan_id=paused_id,
            member_id=paused_member,
            amount_kobo=1,
            source="cash",
            received_on=date(2099, 1, 31),
            reference="CASH-1",
            idempotency_key="paused-officer",
            ip=None,
            reason="Manual collection",
        )
    with pytest.raises(Conflict, match="reason"):
        await post_repayment(
            session(cid, users[2], "treasurer"),
            loan_id=paused_id,
            member_id=paused_member,
            amount_kobo=1,
            source="cash",
            received_on=date(2099, 1, 31),
            reference="CASH-2",
            idempotency_key="paused-no-reason",
            ip=None,
        )

    repayment_id = await post_repayment(
        session(cid, users[2], "treasurer"),
        loan_id=paused_id,
        member_id=paused_member,
        amount_kobo=1,
        source="cash",
        received_on=date(2099, 1, 31),
        reference="CASH-3",
        idempotency_key="paused-with-reason",
        ip=None,
        reason="Manual cash receipt approved by treasurer",
    )
    assert (
        await db.fetchval(
            "SELECT reason FROM repayments WHERE cooperative_id=$1 AND id=$2", cid, repayment_id
        )
        == "Manual cash receipt approved by treasurer"
    )
    assert (
        await db.fetchval(
            "SELECT reason FROM audit_logs WHERE cooperative_id=$1 AND action='repayment_posted' AND entity_id=$2",
            cid,
            str(repayment_id),
        )
        == "Manual cash receipt approved by treasurer"
    )


async def test_payroll_component_totals_reconcile_before_ledger_posting(
    db: asyncpg.Connection,
) -> None:
    cid, users, loans = await setup_payroll_fixture(db)
    profile_id = await service.create_profile(
        session(cid, users[3], "admin"),
        {"staff_no_column": "Staff", "amount_column": "Amount"},
    )
    run_id = await service.create_run(session(cid, users[0], "officer"), profile_id, 2099, 1)
    await service.export_expected(session(cid, users[0], "officer"), run_id)
    await db.execute(
        "UPDATE payroll_runs SET status='imported',file_total_kobo=159,imported_by=$3,imported_at=now() WHERE cooperative_id=$1 AND id=$2",
        cid,
        run_id,
        users[0],
    )
    await db.execute(
        """INSERT INTO payroll_items(cooperative_id,run_id,member_id,staff_no_raw,expected_loan_kobo,
          expected_savings_kobo,expected_dues_kobo,actual_kobo,classification)
          VALUES($1,$2,$3,$4,110,30,20,160,'matched')""",
        cid,
        run_id,
        loans[2][1],
        loans[2][2],
    )

    with pytest.raises(Conflict, match="reconcil"):
        await service.post_run(session(cid, users[2], "treasurer"), run_id)

    assert await db.fetchval("SELECT count(*) FROM repayments WHERE cooperative_id=$1", cid) == 0
    assert (
        await db.fetchval(
            "SELECT count(*) FROM journal_entries WHERE cooperative_id=$1 AND source_type='loan_repayment'",
            cid,
        )
        == 0
    )


async def test_payroll_run_rolls_back_prior_component_entries_if_later_item_fails(
    db: asyncpg.Connection,
) -> None:
    cid, users, loans = await setup_payroll_fixture(db)
    await db.execute(
        "UPDATE loans SET deductions_paused=false WHERE cooperative_id=$1 AND id=$2",
        cid,
        loans[1][0],
    )
    profile_id = await service.create_profile(
        session(cid, users[3], "admin"),
        {"staff_no_column": "Staff", "amount_column": "Amount"},
    )
    run_id = await service.create_run(session(cid, users[0], "officer"), profile_id, 2099, 1)
    await service.export_expected(session(cid, users[0], "officer"), run_id)
    await db.execute(
        "UPDATE payroll_runs SET status='imported',file_total_kobo=160,imported_by=$3,imported_at=now() WHERE cooperative_id=$1 AND id=$2",
        cid,
        run_id,
        users[0],
    )
    await db.executemany(
        """INSERT INTO payroll_items(id,cooperative_id,run_id,member_id,staff_no_raw,expected_loan_kobo,
          expected_savings_kobo,expected_dues_kobo,actual_kobo,classification)
          VALUES($1,$2,$3,$4,$5,110,30,20,$6,'matched')""",
        [
            (uuid.UUID(int=1), cid, run_id, loans[2][1], loans[2][2], 160),
            (uuid.UUID(int=2), cid, run_id, loans[1][1], loans[1][2], 0),
        ],
    )

    with pytest.raises(ValueError, match="invalid"):
        await service.post_run(session(cid, users[2], "treasurer"), run_id)

    assert await db.fetchval("SELECT count(*) FROM repayments WHERE cooperative_id=$1", cid) == 0
    assert (
        await db.fetchval(
            "SELECT count(*) FROM journal_entries WHERE cooperative_id=$1 AND source_type='loan_repayment'",
            cid,
        )
        == 0
    )
