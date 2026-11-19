"""Integration tests for dividend runs and allocations."""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

import asyncpg
import pytest

from app.core.config import get_settings
from app.core.db import asyncpg_dsn, dispose_engine, transaction
from app.core.errors import Conflict, Forbidden
from app.domains.auth.service import CurrentSession
from app.domains.dividends import service as dividend_service
from app.domains.notifications.eligibility import ACTION_SPECS, DIVIDEND_APPROVAL
from app.domains.notifications.outbox import (
    decrypt_payload,
    enqueue_action_notifications_safely,
)
from tests.conftest import create_tenant
from tests.helpers.postgres import check_constraint_array_values

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
async def reset_engine():
    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture
async def db(test_database: Any) -> asyncpg.Connection:
    conn = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    assert await conn.fetchval("SELECT current_user") == "ajo_app"
    yield conn
    await conn.close()


async def setup_dividend_tenant(
    conn: asyncpg.Connection,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Set up a test tenant with accounts and shares seeded for approval+posting."""
    seed = await create_tenant(
        conn,
        name="Dividend test",
        slug_prefix="div",
        roles=("treasurer", "admin", "officer", "auditor"),
        include_member=True,
    )
    cid = seed.cooperative_id
    treasurer_id = seed.user_ids[0]
    admin_id = seed.user_ids[1]
    officer_id = seed.user_ids[2] if len(seed.user_ids) > 2 else treasurer_id
    bank_acc_id = uuid.uuid4()
    savings_acc_id = uuid.uuid4()
    shares_acc_id = uuid.uuid4()
    divs_acc_id = uuid.uuid4()
    await conn.executemany(
        "INSERT INTO accounts(cooperative_id,id,code,name,account_type) VALUES($1,$2,$3,$4,$5)",
        [
            (cid, bank_acc_id, "bank", "Bank", "asset"),
            (cid, savings_acc_id, "member_savings", "Member savings", "liability"),
            (cid, shares_acc_id, "member_shares", "Member shares", "equity"),
            (cid, divs_acc_id, "dividends_declared", "Dividends declared", "equity"),
        ],
    )
    # Seed a share purchase so the member has shares at record date.
    if seed.member_id:
        shares_kobo = 100_000
        lines_json = json.dumps(
            [
                {"account_id": str(bank_acc_id), "debit_kobo": shares_kobo, "credit_kobo": 0},
                {
                    "account_id": str(shares_acc_id),
                    "member_id": str(seed.member_id),
                    "debit_kobo": 0,
                    "credit_kobo": shares_kobo,
                },
            ]
        )
        await conn.execute("SELECT set_config('app.cooperative_id', $1, false)", str(cid))
        await conn.fetchval(
            "SELECT post_journal_entry($1,$2,'share_purchase',$3,'Initial shares',$4,$5,$6::jsonb,NULL)",
            cid,
            date(2025, 1, 1),
            uuid.uuid4(),
            treasurer_id,
            f"share-setup-div:{cid}",
            lines_json,
        )
    return cid, treasurer_id, admin_id, officer_id


def treasurer_session(cid: uuid.UUID, uid: uuid.UUID) -> CurrentSession:
    return CurrentSession(
        cid, "div-test", uuid.uuid4(), uid, "treasury@example.test", ("treasurer",), bytes(32)
    )


def admin_session(cid: uuid.UUID, uid: uuid.UUID) -> CurrentSession:
    return CurrentSession(
        cid, "div-test", uuid.uuid4(), uid, "admin@example.test", ("admin",), bytes(32)
    )


def officer_session(cid: uuid.UUID, uid: uuid.UUID) -> CurrentSession:
    return CurrentSession(
        cid, "div-test", uuid.uuid4(), uid, "officer@example.test", ("officer",), bytes(32)
    )


class TestDividendPrepare:
    """Tests for preparing a dividend run."""

    async def test_prepare_dividend_run_requires_treasurer_or_admin(
        self, db: asyncpg.Connection
    ) -> None:
        """Only treasurer and admin can prepare dividend runs."""
        cid, treasurer_id, admin_id, officer_id = await setup_dividend_tenant(db)

        # Officer should be forbidden
        officer = officer_session(cid, officer_id)
        with pytest.raises(Forbidden):
            await dividend_service.prepare_dividend_run(
                officer,
                record_date=date(2025, 12, 31),
                meeting_or_resolution="AGM 2025",
                pool_kobo=1_000_000,
                financial_year=2025,
            )

    async def test_prepare_dividend_run_creates_run_with_unprocessed_status(
        self, db: asyncpg.Connection
    ) -> None:
        """Preparing a dividend run creates it with status='unprocessed'."""
        cid, treasurer_id, admin_id, officer_id = await setup_dividend_tenant(db)
        treasurer = treasurer_session(cid, treasurer_id)

        record_date = date(2025, 12, 31)
        pool_kobo = 1_000_000

        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=record_date,
            meeting_or_resolution="AGM 2025",
            pool_kobo=pool_kobo,
            financial_year=2025,
        )

        assert run["status"] == "unprocessed"
        assert run["record_date"] == record_date.isoformat()
        assert run["meeting_or_resolution"] == "AGM 2025"
        assert run["prepared_by"] == str(treasurer_id)
        assert run["pool_kobo"] == pool_kobo
        assert "run_id" in run


class TestDividendApprove:
    """Tests for approving a dividend run for posting."""

    async def test_approve_dividend_run_requires_admin(self, db: asyncpg.Connection) -> None:
        """Only admin can approve dividend runs."""
        cid, treasurer_id, admin_id, officer_id = await setup_dividend_tenant(db)
        treasurer = treasurer_session(cid, treasurer_id)

        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=1_000_000,
            financial_year=2025,
        )

        # Treasurer cannot approve
        with pytest.raises(Forbidden):
            await dividend_service.approve_dividend_run(treasurer, uuid.UUID(run["run_id"]))

    async def test_approve_changes_status_to_posted(self, db: asyncpg.Connection) -> None:
        """Approving a run changes status from 'unprocessed' to 'posted' (posts ledger atomically)."""
        cid, treasurer_id, admin_id, officer_id = await setup_dividend_tenant(db)
        treasurer = treasurer_session(cid, treasurer_id)
        admin = admin_session(cid, admin_id)

        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=1_000_000,
            financial_year=2025,
        )

        approved_run = await dividend_service.approve_dividend_run(admin, uuid.UUID(run["run_id"]))

        assert approved_run["status"] == "posted"
        assert approved_run["approved_by"] == str(admin_id)

    async def test_approve_only_works_on_unprocessed_run(self, db: asyncpg.Connection) -> None:
        """Can only approve a run with status='unprocessed'."""
        cid, treasurer_id, admin_id, officer_id = await setup_dividend_tenant(db)
        treasurer = treasurer_session(cid, treasurer_id)
        admin = admin_session(cid, admin_id)

        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=1_000_000,
            financial_year=2025,
        )

        # Approve once
        await dividend_service.approve_dividend_run(admin, uuid.UUID(run["run_id"]))

        # Cannot approve again
        with pytest.raises(Conflict):
            await dividend_service.approve_dividend_run(admin, uuid.UUID(run["run_id"]))


class TestDividendReject:
    """Tests for rejecting a dividend run."""

    async def test_reject_dividend_run_requires_admin(self, db: asyncpg.Connection) -> None:
        """Only admin can reject dividend runs."""
        cid, treasurer_id, admin_id, officer_id = await setup_dividend_tenant(db)
        treasurer = treasurer_session(cid, treasurer_id)

        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=1_000_000,
            financial_year=2025,
        )

        # Treasurer cannot reject
        with pytest.raises(Forbidden):
            await dividend_service.reject_dividend_run(
                treasurer, uuid.UUID(run["run_id"]), "Not enough funds"
            )


class TestDividendCancel:
    """Tests for canceling a prepared dividend run."""

    async def test_cancel_dividend_run_requires_preparer_or_admin(
        self, db: asyncpg.Connection
    ) -> None:
        """Only preparer or admin can cancel a prepared run."""
        cid, treasurer_id, admin_id, officer_id = await setup_dividend_tenant(db)
        treasurer = treasurer_session(cid, treasurer_id)
        officer = officer_session(cid, officer_id)

        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=1_000_000,
            financial_year=2025,
        )

        # Officer has no permission
        with pytest.raises(Forbidden):
            await dividend_service.cancel_dividend_run(officer, uuid.UUID(run["run_id"]))


async def _notification_tenant(
    conn: asyncpg.Connection, *, roles: tuple[str, ...] = ("treasurer", "admin", "admin")
) -> tuple[uuid.UUID, tuple[uuid.UUID, ...]]:
    seed = await create_tenant(
        conn,
        name="Dividend notification test",
        slug_prefix="divnotif",
        roles=roles,
        include_member=True,
    )
    return seed.cooperative_id, seed.user_ids


async def _dividend_rows(
    conn: asyncpg.Connection, cid: uuid.UUID, run_id: uuid.UUID
) -> list[asyncpg.Record]:
    await conn.execute("SELECT set_config('app.cooperative_id', $1, false)", str(cid))
    return await conn.fetch(
        "SELECT recipient_user_id, channel, payload_encrypted "
        "FROM notifications WHERE cooperative_id=$1 AND kind=$2 AND entity_id=$3 "
        "ORDER BY channel, recipient_user_id",
        cid,
        DIVIDEND_APPROVAL,
        run_id,
    )


class TestDividendNotifications:
    """Shared dividend approval eligibility, channel-aware enqueue, and dedupe."""

    async def test_prepare_enqueues_one_email_per_eligible_admin_excluding_preparer(
        self, db: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = get_settings()
        monkeypatch.setattr(settings, "email_enabled", True)
        monkeypatch.setattr(settings, "telegram_enabled", False)

        cid, user_ids = await _notification_tenant(db)
        treasurer_id, admin1_id, admin2_id = user_ids
        treasurer = treasurer_session(cid, treasurer_id)

        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=1_000_000,
            financial_year=2025,
        )
        run_id = uuid.UUID(run["run_id"])

        rows = await _dividend_rows(db, cid, run_id)
        assert len(rows) == 2
        assert {row["recipient_user_id"] for row in rows} == {admin1_id, admin2_id}
        assert all(row["channel"] == "email" for row in rows)
        assert treasurer_id not in {row["recipient_user_id"] for row in rows}

        payload = decrypt_payload(rows[0]["payload_encrypted"])
        assert set(payload) == {"section", "action_kind", "message", "url"}
        assert payload["action_kind"] == "dividend approval"
        assert payload["message"] == "A dividend run needs your approval."
        assert payload["url"].endswith(f"/dividends/{run_id}")
        rendered = str(payload)
        assert "Test Member" not in rendered
        assert "1000000" not in rendered

    async def test_admin_who_is_also_preparer_gets_no_notification(
        self, db: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = get_settings()
        monkeypatch.setattr(settings, "email_enabled", True)
        monkeypatch.setattr(settings, "telegram_enabled", False)

        cid, user_ids = await _notification_tenant(db, roles=("treasurer", "admin"))
        treasurer_id, admin_id = user_ids
        async with db.transaction():
            await db.execute("SELECT set_config('app.cooperative_id', $1, true)", str(cid))
            await db.execute(
                "INSERT INTO user_roles(cooperative_id,user_id,role) VALUES($1,$2,$3)",
                cid,
                treasurer_id,
                "admin",
            )
        treasurer = treasurer_session(cid, treasurer_id)

        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=500_000,
            financial_year=2025,
        )
        run_id = uuid.UUID(run["run_id"])

        rows = await _dividend_rows(db, cid, run_id)
        assert len(rows) == 1
        assert rows[0]["recipient_user_id"] == admin_id
        assert treasurer_id not in {row["recipient_user_id"] for row in rows}

    async def test_prepare_enqueues_telegram_for_linked_admins_and_dedupes_on_reenqueue(
        self, db: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = get_settings()
        monkeypatch.setattr(settings, "email_enabled", True)
        monkeypatch.setattr(settings, "telegram_enabled", True)

        cid, user_ids = await _notification_tenant(db, roles=("treasurer", "admin"))
        treasurer_id, admin_id = user_ids
        async with db.transaction():
            await db.execute("SELECT set_config('app.cooperative_id', $1, true)", str(cid))
            await db.execute(
                "INSERT INTO telegram_links(cooperative_id,user_id,chat_id) VALUES($1,$2,$3)",
                cid,
                admin_id,
                f"chat-{uuid.uuid4()}",
            )
        treasurer = treasurer_session(cid, treasurer_id)

        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=2_000_000,
            financial_year=2025,
        )
        run_id = uuid.UUID(run["run_id"])

        rows = await _dividend_rows(db, cid, run_id)
        assert len(rows) == 2
        assert {row["channel"] for row in rows} == {"email", "telegram"}
        assert {row["recipient_user_id"] for row in rows} == {admin_id}

        # Re-enqueueing the same run must not duplicate (dedupe index includes channel).
        async with transaction(cid) as session:
            await enqueue_action_notifications_safely(session, cid, DIVIDEND_APPROVAL, run_id)

        await db.execute("SELECT set_config('app.cooperative_id', $1, false)", str(cid))
        after = await db.fetchval(
            "SELECT count(*) FROM notifications "
            "WHERE cooperative_id=$1 AND kind=$2 AND entity_id=$3",
            cid,
            DIVIDEND_APPROVAL,
            run_id,
        )
        assert after == 2

    async def test_dividend_notifications_are_tenant_isolated(
        self, db: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = get_settings()
        monkeypatch.setattr(settings, "email_enabled", True)
        monkeypatch.setattr(settings, "telegram_enabled", False)

        cid_a, user_ids_a = await _notification_tenant(db, roles=("treasurer", "admin"))
        treasurer_a = user_ids_a[0]
        cid_b, _ = await _notification_tenant(db, roles=("treasurer", "admin"))

        treasurer = treasurer_session(cid_a, treasurer_a)
        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=1_000_000,
            financial_year=2025,
        )
        run_id = uuid.UUID(run["run_id"])

        await db.execute("SELECT set_config('app.cooperative_id', $1, false)", str(cid_b))
        count_b = await db.fetchval(
            "SELECT count(*) FROM notifications WHERE cooperative_id=$1 AND kind=$2",
            cid_b,
            DIVIDEND_APPROVAL,
        )
        assert count_b == 0

        await db.execute("SELECT set_config('app.cooperative_id', $1, false)", str(cid_a))
        rows = await _dividend_rows(db, cid_a, run_id)
        assert len(rows) == 1

    async def test_notifications_constraint_includes_dividend_approval(
        self, db: asyncpg.Connection
    ) -> None:
        definition = await db.fetchval(
            "SELECT pg_get_constraintdef(oid, true) FROM pg_constraint "
            "WHERE conname = 'notifications_action_fields_check'"
        )
        assert definition is not None
        kinds = check_constraint_array_values(definition, column="kind")
        assert DIVIDEND_APPROVAL in kinds
        assert kinds == set(ACTION_SPECS)
