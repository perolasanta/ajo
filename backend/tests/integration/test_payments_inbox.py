"""Payment inbox workflow, separation-of-duties, and ledger invariants."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from app.core.db import asyncpg_dsn, dispose_engine
from app.core.errors import Conflict, Forbidden, NotFound
from app.domains.members.service import member_balances
from app.domains.notifications.eligibility import ACTION_SPECS, CASH_DEPOSIT, PAYMENT_CONFIRMATION
from app.domains.payments import service
from tests.integration.test_members import session, setup_tenant

pytestmark = pytest.mark.db


def today_wat() -> date:
    return datetime.now(ZoneInfo("Africa/Lagos")).date()


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


async def payment_tenant(db: asyncpg.Connection):
    cooperative_id, officer, treasurer, member_id, accounts = await setup_tenant(db)
    cash_id = uuid.uuid4()
    await db.execute(
        "INSERT INTO accounts(id,cooperative_id,code,name,account_type) "
        "VALUES($1,$2,'cash_on_hand','Cash held by officers','asset') ON CONFLICT DO NOTHING",
        cash_id,
        cooperative_id,
    )
    return cooperative_id, officer, treasurer, member_id, accounts


async def record_transfer(cooperative_id, actor_id, member_id, reference, key="transfer-key"):
    return await service.record_payment(
        session(cooperative_id, actor_id, "officer"),
        member_id=member_id,
        channel="bank_transfer",
        purpose="savings",
        amount_kobo=12500,
        provider="manual",
        provider_reference=reference,
        received_on=today_wat(),
        idempotency_key=key,
    )


async def test_notifications_constraint_matches_action_specs(db: asyncpg.Connection) -> None:
    definition = await db.fetchval(
        "SELECT pg_get_constraintdef(oid, true) FROM pg_constraint "
        "WHERE conname = 'notifications_action_fields_check'"
    )
    assert definition is not None
    match = re.search(r"kind\s*=\s*ANY\s*\(\s*\(?ARRAY\[(.*?)\]", definition, re.S)
    assert match is not None, definition
    kinds = set(re.findall(r"'([^']+)'", match.group(1)))
    specs = set(ACTION_SPECS)
    assert kinds == specs, (
        f"in constraint only: {sorted(kinds - specs)}; "
        f"in ACTION_SPECS only: {sorted(specs - kinds)}"
    )


async def test_transfer_requires_a_different_confirmer(db: asyncpg.Connection) -> None:
    cid, officer, treasurer, member_id, _ = await payment_tenant(db)
    pending = await record_transfer(cid, officer, member_id, "BANK-SEP-1")
    with pytest.raises(Conflict, match="different user"):
        await service.confirm_payment(
            session(cid, officer, "officer"), pending["id"], "confirm-self"
        )
    confirmed = await service.confirm_payment(
        session(cid, treasurer, "treasurer"), pending["id"], "confirm-other"
    )
    assert confirmed["status"] == "confirmed"
    assert confirmed["ledger_entry_id"] is not None


async def test_duplicate_bank_reference_is_rejected(db: asyncpg.Connection) -> None:
    cid, officer, _, member_id, _ = await payment_tenant(db)
    await record_transfer(cid, officer, member_id, "BANK-DUP-1", "dup-key-1")
    with pytest.raises(Conflict, match="A payment with this bank reference already exists"):
        await record_transfer(cid, officer, member_id, "BANK-DUP-1", "dup-key-2")


async def test_idempotent_confirmation_posts_once(db: asyncpg.Connection) -> None:
    cid, officer, treasurer, member_id, _ = await payment_tenant(db)
    first = await record_transfer(cid, officer, member_id, "BANK-REPLAY-1", "replay-record")
    replay = await record_transfer(cid, officer, member_id, "BANK-REPLAY-1", "replay-record")
    assert replay["id"] == first["id"]
    original = await service.confirm_payment(
        session(cid, treasurer, "treasurer"), first["id"], "replay-confirm"
    )
    again = await service.confirm_payment(
        session(cid, treasurer, "treasurer"), first["id"], "replay-confirm"
    )
    assert again["ledger_entry_id"] == original["ledger_entry_id"]
    assert (
        await db.fetchval(
            "SELECT count(*) FROM journal_entries WHERE cooperative_id=$1 AND source_type='member_contribution'",
            cid,
        )
        == 1
    )


async def test_cash_on_hand_equals_unbanked_receipts(db: asyncpg.Connection) -> None:
    cid, officer, treasurer, member_id, _ = await payment_tenant(db)
    received = await service.record_payment(
        session(cid, officer, "officer"),
        member_id=member_id,
        channel="cash",
        purpose="shares",
        amount_kobo=24500,
        provider="manual",
        provider_reference=None,
        received_on=today_wat(),
        idempotency_key="cash-unbanked-1",
    )
    assert received["status"] == "received"
    assert await service.cash_on_hand_balance(cid) == 24500
    await service.record_cash_deposit(
        session(cid, treasurer, "treasurer"),
        slip_reference="SLIP-CASH-1",
        deposited_on=today_wat(),
        payment_ids=[received["id"]],
        idempotency_key="deposit-cash-1",
    )
    assert await service.cash_on_hand_balance(cid) == 0
    assert (
        await db.fetchval(
            "SELECT status FROM payments WHERE cooperative_id=$1 AND id=$2", cid, received["id"]
        )
        == "banked"
    )


async def test_deposit_cannot_include_a_receipt_twice(db: asyncpg.Connection) -> None:
    cid, officer, treasurer, member_id, _ = await payment_tenant(db)
    received = await service.record_payment(
        session(cid, officer, "officer"),
        member_id=member_id,
        channel="cash",
        purpose="dues",
        amount_kobo=1000,
        provider="manual",
        provider_reference=None,
        received_on=today_wat(),
        idempotency_key="cash-dup-set",
    )
    with pytest.raises(Conflict, match="twice|duplicate"):
        await service.record_cash_deposit(
            session(cid, treasurer, "treasurer"),
            slip_reference="SLIP-DUP-SET",
            deposited_on=today_wat(),
            payment_ids=[received["id"], received["id"]],
            idempotency_key="deposit-dup-set",
        )


async def test_deposit_cannot_cover_a_receipt_twice_across_deposits(db: asyncpg.Connection) -> None:
    cid, officer, treasurer, member_id, _ = await payment_tenant(db)
    received = await service.record_payment(
        session(cid, officer, "officer"),
        member_id=member_id,
        channel="cash",
        purpose="dues",
        amount_kobo=1000,
        provider="manual",
        provider_reference=None,
        received_on=today_wat(),
        idempotency_key="cash-bank-once",
    )
    await service.record_cash_deposit(
        session(cid, treasurer, "treasurer"),
        slip_reference="SLIP-BANK-ONCE-1",
        deposited_on=today_wat(),
        payment_ids=[received["id"]],
        idempotency_key="deposit-bank-once-1",
    )
    with pytest.raises(Conflict, match="awaiting deposit"):
        await service.record_cash_deposit(
            session(cid, treasurer, "treasurer"),
            slip_reference="SLIP-BANK-ONCE-2",
            deposited_on=today_wat(),
            payment_ids=[received["id"]],
            idempotency_key="deposit-bank-once-2",
        )


async def test_treasurer_cannot_confirm_deposit_with_any_own_receipt(
    db: asyncpg.Connection,
) -> None:
    cid, officer, treasurer, member_id, _ = await payment_tenant(db)
    receipts = []
    for actor_id, role, key in (
        (officer, "officer", "cash-mixed-officer"),
        (treasurer, "treasurer", "cash-mixed-treasurer"),
    ):
        receipts.append(
            await service.record_payment(
                session(cid, actor_id, role),
                member_id=member_id,
                channel="cash",
                purpose="savings",
                amount_kobo=1000,
                provider="manual",
                provider_reference=None,
                received_on=today_wat(),
                idempotency_key=key,
            )
        )
    with pytest.raises(Conflict, match="differ from every receipt recorder"):
        await service.record_cash_deposit(
            session(cid, treasurer, "treasurer"),
            slip_reference="SLIP-MIXED-RECORDER",
            deposited_on=today_wat(),
            payment_ids=[receipt["id"] for receipt in receipts],
            idempotency_key="deposit-mixed-recorder",
        )


async def test_cash_becomes_overdue_after_the_configured_calendar_day_limit(
    db: asyncpg.Connection,
) -> None:
    cid, officer, _, member_id, _ = await payment_tenant(db)
    current = session(cid, officer, "officer")
    for days_ago, key in ((2, "cash-at-window"), (3, "cash-past-window")):
        await service.record_payment(
            current,
            member_id=member_id,
            channel="cash",
            purpose="savings",
            amount_kobo=100,
            provider="manual",
            provider_reference=None,
            received_on=today_wat() - timedelta(days=days_ago),
            idempotency_key=key,
        )
    rows = await service.list_cash_awaiting_deposit(current)
    assert len(rows) == 2
    overdue_by_receipt_date = {row["received_on"]: row["overdue"] for row in rows}
    assert overdue_by_receipt_date[today_wat() - timedelta(days=2)] is False
    assert overdue_by_receipt_date[today_wat() - timedelta(days=3)] is True


async def test_cash_on_hand_summary_by_officer_reconciles_to_ledger(db: asyncpg.Connection) -> None:
    cid, officer, treasurer, member_id, _ = await payment_tenant(db)
    received_on = today_wat() - timedelta(days=2)
    await service.record_payment(
        session(cid, officer, "officer"),
        member_id=member_id,
        channel="cash",
        purpose="savings",
        amount_kobo=24500,
        provider="manual",
        provider_reference=None,
        received_on=received_on,
        idempotency_key="cash-officer-summary",
    )
    display_name = await db.fetchval(
        "SELECT full_name FROM users WHERE cooperative_id=$1 AND id=$2", cid, officer
    )

    for role in ("treasurer", "admin", "auditor"):
        summary = await service.cash_on_hand_summary(session(cid, treasurer, role))
        assert summary["ledger_balance_kobo"] == await service.cash_on_hand_balance(cid)
        assert (
            sum(item["amount_kobo"] for item in summary["officers"])
            == summary["ledger_balance_kobo"]
        )
        assert summary["officers"] == [
            {
                "user_id": str(officer),
                "display_name": display_name or "Unnamed user",
                "amount_kobo": 24500,
                "oldest_age_days": 2,
            }
        ]


async def test_member_balance_matches_cash_contribution_ledger(db: asyncpg.Connection) -> None:
    cid, officer, _, member_id, _ = await payment_tenant(db)
    await service.record_payment(
        session(cid, officer, "officer"),
        member_id=member_id,
        channel="cash",
        purpose="savings",
        amount_kobo=9876,
        provider="manual",
        provider_reference=None,
        received_on=today_wat(),
        idempotency_key="cash-balance-1",
    )
    assert (await member_balances(cid, member_id))["savings_kobo"] == 9876


async def test_payment_reads_are_tenant_isolated(db: asyncpg.Connection) -> None:
    tenant_a, officer_a, _, member_a, _ = await payment_tenant(db)
    tenant_b, officer_b, _, member_b, _ = await payment_tenant(db)
    payment = await record_transfer(tenant_a, officer_a, member_a, "BANK-TENANT-A")
    with pytest.raises(NotFound):
        await service.get_payment(session(tenant_b, officer_b, "officer"), payment["id"])


async def test_admin_is_read_only_for_payment_writes(db: asyncpg.Connection) -> None:
    cid, officer, admin, member_id, _ = await payment_tenant(db)
    pending = await record_transfer(cid, officer, member_id, "BANK-ADMIN-WRITE")
    received = await service.record_payment(
        session(cid, officer, "officer"),
        member_id=member_id,
        channel="cash",
        purpose="savings",
        amount_kobo=100,
        provider="manual",
        provider_reference=None,
        received_on=today_wat(),
        idempotency_key="admin-deposit-write",
    )
    with pytest.raises(Forbidden):
        await service.record_payment(
            session(cid, admin, "admin"),
            member_id=member_id,
            channel="cash",
            purpose="savings",
            amount_kobo=100,
            provider="manual",
            provider_reference=None,
            received_on=today_wat(),
            idempotency_key="admin-write-denied",
        )
    with pytest.raises(Forbidden):
        await service.confirm_payment(
            session(cid, admin, "admin"), pending["id"], "admin-confirm-denied"
        )
    with pytest.raises(Forbidden):
        await service.reject_payment(
            session(cid, admin, "admin"),
            pending["id"],
            "not authorized",
            "admin-reject-denied",
        )
    with pytest.raises(Forbidden):
        await service.record_cash_deposit(
            session(cid, admin, "admin"),
            slip_reference="ADMIN-SLIP",
            deposited_on=today_wat(),
            payment_ids=[received["id"]],
            idempotency_key="admin-deposit-denied",
        )


def test_payment_alerts_name_only_the_action_kind() -> None:
    assert ACTION_SPECS[PAYMENT_CONFIRMATION].sentence == "A payment needs your confirmation"
    assert ACTION_SPECS[CASH_DEPOSIT].sentence == "Cash needs to be banked"
    for kind in (PAYMENT_CONFIRMATION, CASH_DEPOSIT):
        sentence = ACTION_SPECS[kind].sentence
        assert "₦" not in sentence
        assert not any(character.isdigit() for character in sentence)
        assert "Test Member" not in sentence
        assert "BANK-PRIVATE-REF" not in sentence


async def test_payment_list_role_visibility_and_read_only_roles(db: asyncpg.Connection) -> None:
    cid, officer, treasurer, member_id, _ = await payment_tenant(db)
    await record_transfer(cid, officer, member_id, "BANK-LIST-ROLES")

    for role in ("admin", "auditor"):
        result = await service.list_payments(session(cid, treasurer, role), page=1, page_size=20)
        assert result["total"] == 1
        assert result["items"][0]["channel"] == "bank_transfer"
        with pytest.raises(Forbidden):
            await service.record_payment(
                session(cid, treasurer, role),
                member_id=member_id,
                channel="cash",
                purpose="savings",
                amount_kobo=100,
                provider="manual",
                provider_reference=None,
                received_on=today_wat(),
                idempotency_key=f"{role}-list-read-only",
            )

    with pytest.raises(Forbidden):
        await service.list_payments(session(cid, officer, "committee"), page=1, page_size=20)


async def test_payment_list_is_tenant_scoped_named_and_never_returns_bank_numbers(
    db: asyncpg.Connection,
) -> None:
    tenant_a, officer_a, _, member_a, _ = await payment_tenant(db)
    tenant_b, officer_b, _, member_b, _ = await payment_tenant(db)
    payment_a = await record_transfer(tenant_a, officer_a, member_a, "BANK-LIST-TENANT-A")
    await record_transfer(tenant_b, officer_b, member_b, "BANK-LIST-TENANT-B")
    result = await service.list_payments(
        session(tenant_a, officer_a, "officer"), page=1, page_size=20
    )
    assert result["total"] == 1
    assert [row["id"] for row in result["items"]] == [payment_a["id"]]
    listed = result["items"][0]
    assert listed["member_id"] == str(member_a)
    assert isinstance(listed["member_name"], str) and listed["member_name"]
    assert isinstance(listed["member_no"], str) and listed["member_no"]
    assert listed["recorded_by_name"]
    assert listed["purpose"] == "savings"
    assert listed["amount_kobo"] == 12500
    assert listed["received_on"] == today_wat()
    assert listed["recorded_at"] is not None
    assert "receipt_no" in listed

    cash = await service.record_payment(
        session(tenant_a, officer_a, "officer"),
        member_id=member_a,
        channel="cash",
        purpose="dues",
        amount_kobo=500,
        provider="manual",
        provider_reference=None,
        received_on=today_wat(),
        idempotency_key="cash-list-safe-response",
    )
    responses = [
        result,
        await service.get_payment(session(tenant_a, officer_a, "officer"), payment_a["id"]),
        await service.list_cash_awaiting_deposit(session(tenant_a, officer_a, "officer")),
    ]
    assert cash["status"] == "received"

    def assert_no_bank_account_numbers(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = key.lower()
                assert "account_number" not in lowered
                assert "account_no" not in lowered
                assert "encrypted_account" not in lowered
                assert "bank_account" not in lowered
                assert_no_bank_account_numbers(child)
        elif isinstance(value, list):
            for child in value:
                assert_no_bank_account_numbers(child)

    for response in responses:
        assert_no_bank_account_numbers(response)
