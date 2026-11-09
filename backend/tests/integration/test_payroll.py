"""Payroll deduction, ledger posting, and state-machine integration tests."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date

import asyncpg
import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import asyncpg_dsn, dispose_engine, transaction
from app.core.errors import Conflict, Forbidden
from app.domains.auth.service import CurrentSession
from app.domains.notifications import eligibility
from app.domains.notifications.reads import get_action_counts
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
    db: asyncpg.Connection, *, extra_admins: int = 0
) -> tuple[uuid.UUID, tuple[uuid.UUID, ...], list[tuple[uuid.UUID, uuid.UUID, str]]]:
    seed = await create_tenant(
        db,
        name="Payroll test",
        slug_prefix="payroll",
        roles=("officer", "committee", "treasurer", "admin") + ("admin",) * extra_admins,
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


async def test_payroll_profiles_are_role_limited_and_tenant_scoped(
    db: asyncpg.Connection,
) -> None:
    roles = ("officer", "committee", "treasurer", "admin", "auditor")
    first = await create_tenant(
        db,
        name="Payroll profile tenant one",
        slug_prefix="payroll-profile-one",
        roles=roles,
    )
    second = await create_tenant(
        db,
        name="Payroll profile tenant two",
        slug_prefix="payroll-profile-two",
        roles=roles,
    )
    first_users = dict(zip(roles, first.user_ids, strict=True))
    second_users = dict(zip(roles, second.user_ids, strict=True))
    values = {
        "staff_no_column": "Staff No",
        "amount_column": "Amount",
        "name_column": "Full Name",
    }
    first_profile = await service.create_profile(
        session(first.cooperative_id, first_users["admin"], "admin"), values
    )
    await service.create_profile(
        session(second.cooperative_id, second_users["admin"], "admin"), values
    )

    expected_keys = {
        "id",
        "name",
        "staff_no_column",
        "amount_column",
        "name_column",
        "created_at",
    }
    for role in ("officer", "treasurer", "admin"):
        result = await service.list_profiles(session(first.cooperative_id, first_users[role], role))
        assert len(result["items"]) == 1
        profile = result["items"][0]
        assert set(profile) == expected_keys
        assert profile["id"] == str(first_profile)
        assert profile["name"] == "Payroll profile v1"
        assert profile["staff_no_column"] == "Staff No"
        assert profile["amount_column"] == "Amount"
        assert profile["name_column"] == "Full Name"

    for role in ("committee", "auditor", "member"):
        user_id = first_users.get(role, uuid.uuid4())
        with pytest.raises(Forbidden):
            await service.list_profiles(session(first.cooperative_id, user_id, role))


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


async def test_signoff_requires_posted_run_and_cannot_leave_it_unpostable(
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
        "UPDATE payroll_runs SET status='imported',file_total_kobo=160,imported_by=$3,imported_at=now() WHERE cooperative_id=$1 AND id=$2",
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

    with pytest.raises(Conflict, match="posted"):
        await service.sign_off(session(cid, users[3], "admin"), run_id, 160)
    assert (
        await db.fetchval(
            "SELECT status FROM payroll_runs WHERE cooperative_id=$1 AND id=$2", cid, run_id
        )
        == "imported"
    )

    assert await service.post_run(session(cid, users[2], "treasurer"), run_id) == 1
    posted = await db.fetchrow(
        """SELECT p.posted_repayment_id,r.journal_entry_id FROM payroll_items p
           JOIN repayments r ON r.cooperative_id=p.cooperative_id AND r.id=p.posted_repayment_id
           WHERE p.cooperative_id=$1 AND p.run_id=$2""",
        cid,
        run_id,
    )
    assert posted is not None and posted["posted_repayment_id"] is not None
    assert posted["journal_entry_id"] is not None
    await service.sign_off(session(cid, users[3], "admin"), run_id, 160)
    assert (
        await db.fetchval(
            "SELECT status FROM payroll_runs WHERE cooperative_id=$1 AND id=$2", cid, run_id
        )
        == "signed_off"
    )


@pytest.mark.parametrize("trigger", ["=", "+", "-", "@", "\t", "\r"])
async def test_expected_csv_escapes_text_cells_and_preserves_amount(
    db: asyncpg.Connection, test_database, trigger: str
) -> None:
    cid, users, loans = await setup_payroll_fixture(db)
    staff_no = f"{trigger}staff"
    full_name = f"{trigger}Member"
    owner = await asyncpg.connect(asyncpg_dsn(test_database.owner_url))
    try:
        await owner.execute(
            "UPDATE members SET staff_no=$1,full_name=$2 WHERE cooperative_id=$3 AND id=$4",
            staff_no,
            full_name,
            cid,
            loans[2][1],
        )
    finally:
        await owner.close()
    profile_id = await service.create_profile(
        session(cid, users[3], "admin"),
        {"staff_no_column": "Staff", "amount_column": "Amount", "name_column": "Name"},
    )
    run_id = await service.create_run(session(cid, users[0], "officer"), profile_id, 2099, 1)

    csv_bytes = await service.export_expected(session(cid, users[0], "officer"), run_id)
    reader = csv.DictReader(io.StringIO(csv_bytes.decode()))
    rows = list(reader)

    assert reader.fieldnames == ["Staff", "Amount", "Name"]
    assert len(rows) == 1
    assert rows[0]["Staff"] == f"'{staff_no}"
    assert rows[0]["Name"] == f"'{full_name}"
    assert rows[0]["Amount"] == "160"


async def test_each_expected_csv_download_has_its_own_audit_entry(
    db: asyncpg.Connection,
) -> None:
    cid, users, _ = await setup_payroll_fixture(db)
    profile_id = await service.create_profile(
        session(cid, users[3], "admin"),
        {"staff_no_column": "Staff", "amount_column": "Amount"},
    )
    run_id = await service.create_run(session(cid, users[0], "officer"), profile_id, 2099, 1)
    current = session(cid, users[0], "officer")

    await service.export_expected(current, run_id)
    await service.export_expected(current, run_id)

    assert (
        await db.fetchval(
            "SELECT count(*) FROM audit_logs WHERE cooperative_id=$1 AND action='payroll_expected_exported' AND entity_id=$2",
            cid,
            str(run_id),
        )
        == 2
    )


async def test_signoff_caps_and_sanitizes_exported_resolution_note(
    db: asyncpg.Connection,
) -> None:
    cid, users, loans = await setup_payroll_fixture(db)
    profile_id = await service.create_profile(
        session(cid, users[3], "admin"),
        {"staff_no_column": "Staff", "amount_column": "Amount"},
    )
    run_id = await service.create_run(session(cid, users[0], "officer"), profile_id, 2099, 1)
    note = "+" + ("n" * 600)
    await db.execute(
        "UPDATE payroll_runs SET status='posted',expected_total_kobo=160,file_total_kobo=160,imported_by=$3 WHERE cooperative_id=$1 AND id=$2",
        cid,
        run_id,
        users[0],
    )
    await db.execute(
        """INSERT INTO payroll_items(cooperative_id,run_id,member_id,staff_no_raw,
          expected_loan_kobo,expected_savings_kobo,expected_dues_kobo,actual_kobo,
          classification,resolution_note)
          VALUES($1,$2,$3,$4,110,30,20,160,'short',$5)""",
        cid,
        run_id,
        loans[2][1],
        "STAFF-A",
        note,
    )

    csv_report, json_report = await service.sign_off(session(cid, users[3], "admin"), run_id, 160)
    csv_note = next(csv.DictReader(io.StringIO(csv_report.decode())))["resolution_note"]
    json_note = json.loads(json_report)["exceptions"][0]["resolution_note"]

    assert csv_note == ("'" + note)[:500]
    assert len(csv_note) == 500
    assert json_note == note[:500]
    assert len(json_note) == 500


async def test_posted_by_is_recorded_and_cannot_sign_off_own_posted_run(
    db: asyncpg.Connection,
) -> None:
    cid, users, loans = await setup_payroll_fixture(db)
    profile_id = await service.create_profile(
        session(cid, users[3], "admin"),
        {"staff_no_column": "Staff", "amount_column": "Amount"},
    )
    run_id = await service.create_run(session(cid, users[0], "officer"), profile_id, 2099, 1)
    await db.execute(
        "UPDATE payroll_runs SET status='imported',expected_total_kobo=160,file_total_kobo=160,imported_by=$3,imported_at=now() WHERE cooperative_id=$1 AND id=$2",
        cid,
        run_id,
        users[0],
    )
    await db.execute(
        """INSERT INTO payroll_items(cooperative_id,run_id,member_id,staff_no_raw,
          expected_loan_kobo,expected_savings_kobo,expected_dues_kobo,actual_kobo,classification)
          VALUES($1,$2,$3,$4,110,30,20,160,'matched')""",
        cid,
        run_id,
        loans[2][1],
        loans[2][2],
    )

    assert await service.post_run(session(cid, users[2], "treasurer"), run_id) == 1
    assert (
        await db.fetchval(
            "SELECT posted_by FROM payroll_runs WHERE cooperative_id=$1 AND id=$2", cid, run_id
        )
        == users[2]
    )
    with pytest.raises(Conflict, match="different administrator"):
        await service.sign_off(session(cid, users[2], "admin"), run_id, 160)
    await service.sign_off(session(cid, users[3], "admin"), run_id, 160)


async def test_legacy_posted_run_falls_back_to_importer_for_signoff_separation(
    db: asyncpg.Connection,
) -> None:
    cid, users, _ = await setup_payroll_fixture(db)
    profile_id = await service.create_profile(
        session(cid, users[3], "admin"),
        {"staff_no_column": "Staff", "amount_column": "Amount"},
    )
    run_id = await service.create_run(session(cid, users[0], "officer"), profile_id, 2099, 1)
    await db.execute(
        """UPDATE payroll_runs SET status='posted',file_total_kobo=10,
          imported_by=$3,posted_by=NULL WHERE cooperative_id=$1 AND id=$2""",
        cid,
        run_id,
        users[3],
    )

    with pytest.raises(Conflict, match="different administrator"):
        await service.sign_off(session(cid, users[3], "admin"), run_id, 10)


async def test_notification_domain_queries_accept_null_optional_entity_filter(
    db: asyncpg.Connection,
) -> None:
    seed = await create_tenant(db, roles=("admin", "treasurer"))
    cid, admin_id = seed.cooperative_id, seed.user_ids[0]

    summary = await get_action_counts(session(cid, admin_id, "admin"))
    assert summary["payroll"]["awaiting_sign_off"] == 0

    async with transaction(cid) as session_db:
        for event_type in eligibility.ACTION_SPECS:
            candidates = await eligibility.get_action_candidates(
                session_db, cid, event_type, entity_id=None
            )
            assert candidates == []
        paused_candidates = await eligibility.get_action_candidates(
            session_db, cid, "loan_disputed", entity_id=None, include_paused_disputed=True
        )
        assert paused_candidates == []


async def test_treasurer_post_queues_signoff_for_every_admin_and_updates_badges(
    db: asyncpg.Connection, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "email_enabled", True)
    monkeypatch.setattr(settings, "email_from_address", "test@example.test")
    monkeypatch.setattr(settings, "email_from_name", "Ajo Test")
    monkeypatch.setattr(settings, "brevo_api_key", "dummy-brevo-key")
    cid, users, _ = await setup_payroll_fixture(db, extra_admins=1)
    officer_id, treasurer_id, first_admin_id, second_admin_id = (
        users[0],
        users[2],
        users[3],
        users[4],
    )
    profile_id = await service.create_profile(
        session(cid, first_admin_id, "admin"),
        {"staff_no_column": "Staff", "amount_column": "Amount"},
    )
    run_id = await service.create_run(session(cid, officer_id, "officer"), profile_id, 2099, 1)
    payroll_csv = await service.export_expected(session(cid, officer_id, "officer"), run_id)
    await service.import_file(
        session(cid, treasurer_id, "treasurer"), run_id, payroll_csv, "payroll.csv"
    )

    assert await service.post_run(session(cid, treasurer_id, "treasurer"), run_id) == 1
    enqueue_errors = [
        record
        for record in caplog.records
        if record.name == "app.domains.notifications.outbox"
        and record.getMessage().startswith("action_notification_enqueue_failed")
    ]
    assert not enqueue_errors, [
        (record.exception_class, record.exception_message) for record in enqueue_errors
    ]
    posted_run = await db.fetchrow(
        "SELECT status,posted_by,imported_by,signed_off_at FROM payroll_runs WHERE cooperative_id=$1 AND id=$2",
        cid,
        run_id,
    )
    assert posted_run is not None
    assert posted_run["status"] == "posted"
    assert posted_run["posted_by"] == posted_run["imported_by"] == treasurer_id
    assert posted_run["signed_off_at"] is None

    for admin_id in (first_admin_id, second_admin_id):
        summary = await get_action_counts(session(cid, admin_id, "admin"))
        assert summary["payroll"]["awaiting_sign_off"] >= 1

    async with transaction(cid) as session_db:
        rows = (
            (
                await session_db.execute(
                    text("""SELECT recipient_user_id,kind,entity_id,channel,status
                  FROM notifications WHERE cooperative_id=:cid AND entity_id=:run_id
                    AND kind='payroll_sign_off'"""),
                    {"cid": cid, "run_id": run_id},
                )
            )
            .mappings()
            .all()
        )

    assert len(rows) == 2
    assert {
        (
            row["recipient_user_id"],
            row["kind"],
            row["entity_id"],
            row["channel"],
            row["status"],
        )
        for row in rows
    } == {
        (first_admin_id, "payroll_sign_off", run_id, "email", "pending"),
        (second_admin_id, "payroll_sign_off", run_id, "email", "pending"),
    }
    assert treasurer_id not in {row["recipient_user_id"] for row in rows}


async def test_treasurer_post_succeeds_without_queued_actions_when_channels_disabled(
    db: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "email_enabled", False)
    monkeypatch.setattr(settings, "telegram_enabled", False)
    cid, users, _ = await setup_payroll_fixture(db, extra_admins=1)
    profile_id = await service.create_profile(
        session(cid, users[3], "admin"),
        {"staff_no_column": "Staff", "amount_column": "Amount"},
    )
    run_id = await service.create_run(session(cid, users[0], "officer"), profile_id, 2099, 1)
    payroll_csv = await service.export_expected(session(cid, users[0], "officer"), run_id)
    await service.import_file(
        session(cid, users[2], "treasurer"), run_id, payroll_csv, "payroll.csv"
    )

    assert await service.post_run(session(cid, users[2], "treasurer"), run_id) == 1
    assert (
        await db.fetchval(
            "SELECT count(*) FROM notifications WHERE cooperative_id=$1 AND entity_id=$2",
            cid,
            run_id,
        )
        == 0
    )
