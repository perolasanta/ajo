"""Red-first regression tests for dividend ledger posting and duplicate-run guard.

Unit 1 of fix/dividends-ledger-and-periods.
These tests FAIL on main (before the fix) and PASS after.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.auth import current_session
from app.core.db import asyncpg_dsn, dispose_engine, transaction
from app.core.errors import Conflict
from app.domains.auth.service import CurrentSession
from app.domains.dividends import service as dividend_service
from app.domains.members import reads as member_reads
from app.domains.members.service import member_balances
from app.main import create_app
from tests.conftest import create_tenant

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


# ---------------------------------------------------------------------------
# Shared setup helpers
# ---------------------------------------------------------------------------


async def _setup_tenant_with_shares(
    conn: asyncpg.Connection,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Return (cid, treasurer_id, admin_id, member_id).

    Creates a cooperative with treasurer + admin users, one active member,
    and seeds the required accounts + a share purchase journal entry so the
    member has 1_000_00 kobo (1,000 shares at 100 kobo each) worth of shares.
    """
    seed = await create_tenant(
        conn,
        name="Posting test",
        slug_prefix="post-test",
        roles=("treasurer", "admin"),
        include_member=True,
        member_status="active",
    )
    cid = seed.cooperative_id
    treasurer_id = seed.user_ids[0]
    admin_id = seed.user_ids[1]
    assert seed.member_id is not None
    member_id = seed.member_id

    # Seed required accounts
    divs_acc_id = uuid.uuid4()
    savings_acc_id = uuid.uuid4()
    shares_acc_id = uuid.uuid4()
    await conn.executemany(
        "INSERT INTO accounts(cooperative_id,id,code,name,account_type) VALUES($1,$2,$3,$4,$5)",
        [
            (cid, divs_acc_id, "dividends_declared", "Dividends declared", "equity"),
            (cid, savings_acc_id, "member_savings", "Member savings", "liability"),
            (cid, shares_acc_id, "member_shares", "Member shares", "equity"),
        ],
    )

    # Post a share purchase so the member has shares at record date
    # Use the post_journal_entry DB function (same path as real share purchases)
    shares_kobo = 100_000  # 1,000 shares at 100 kobo each
    bank_acc_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO accounts(cooperative_id,id,code,name,account_type) VALUES($1,$2,$3,$4,$5)",
        cid,
        bank_acc_id,
        "bank",
        "Bank",
        "asset",
    )
    # post_journal_entry(cid, date, source_type, source_id, description, actor,
    #                    idempotency_key, lines jsonb, reverses_entry_id)
    lines_json = json.dumps(
        [
            {"account_id": str(bank_acc_id), "debit_kobo": shares_kobo, "credit_kobo": 0},
            {
                "account_id": str(shares_acc_id),
                "member_id": str(member_id),
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
        f"share-setup:{cid}",
        lines_json,
    )

    return cid, treasurer_id, admin_id, member_id


def _treasurer(cid: uuid.UUID, uid: uuid.UUID) -> CurrentSession:
    return CurrentSession(
        cid, "post-test", uuid.uuid4(), uid, "treasurer@test", ("treasurer",), bytes(32)
    )


def _admin(cid: uuid.UUID, uid: uuid.UUID) -> CurrentSession:
    return CurrentSession(cid, "post-test", uuid.uuid4(), uid, "admin@test", ("admin",), bytes(32))


def _officer(cid: uuid.UUID, uid: uuid.UUID) -> CurrentSession:
    return CurrentSession(
        cid, "post-test", uuid.uuid4(), uid, "officer@test", ("officer",), bytes(32)
    )


# ---------------------------------------------------------------------------
# Bug A: approval must post the ledger entry atomically
# ---------------------------------------------------------------------------


class TestApprovePostsLedger:
    """Approving a run must create a balanced journal entry and raise the member balance."""

    async def test_savings_balance_rises_after_approval(self, db: asyncpg.Connection) -> None:
        """Member savings balance increases by their allocation after approval."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant_with_shares(db)

        pool_kobo = 10_000
        run = await dividend_service.prepare_dividend_run(
            _treasurer(cid, treasurer_id),
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=pool_kobo,
            financial_year=2025,
        )
        run_id = uuid.UUID(run["run_id"])

        before = await member_balances(cid, member_id)
        savings_before = before["savings_kobo"]

        await dividend_service.approve_dividend_run(_admin(cid, admin_id), run_id)

        after = await member_balances(cid, member_id)
        savings_after = after["savings_kobo"]

        assert savings_after == savings_before + pool_kobo

    async def test_approve_sets_status_to_posted(self, db: asyncpg.Connection) -> None:
        """After approval the run status is 'posted' (ledger entry was written)."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant_with_shares(db)

        run = await dividend_service.prepare_dividend_run(
            _treasurer(cid, treasurer_id),
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=5_000,
            financial_year=2025,
        )
        run_id = uuid.UUID(run["run_id"])

        result = await dividend_service.approve_dividend_run(_admin(cid, admin_id), run_id)

        assert result["status"] == "posted"
        assert result["journal_entry_id"] is not None

    async def test_approve_credits_sum_to_pool(self, db: asyncpg.Connection) -> None:
        """Sum of credit lines on the journal entry equals pool_kobo."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant_with_shares(db)

        pool_kobo = 7_777
        run = await dividend_service.prepare_dividend_run(
            _treasurer(cid, treasurer_id),
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=pool_kobo,
            financial_year=2025,
        )
        run_id = uuid.UUID(run["run_id"])
        result = await dividend_service.approve_dividend_run(_admin(cid, admin_id), run_id)

        entry_id = uuid.UUID(result["journal_entry_id"])

        await db.execute("SELECT set_config('app.cooperative_id', $1, false)", str(cid))
        total_credits = await db.fetchval(
            "SELECT SUM(credit_kobo) FROM journal_lines WHERE entry_id=$1",
            entry_id,
        )
        assert int(total_credits) == pool_kobo

    async def test_double_approval_is_idempotent(self, db: asyncpg.Connection) -> None:
        """Calling approve twice does not create a second journal entry."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant_with_shares(db)

        run = await dividend_service.prepare_dividend_run(
            _treasurer(cid, treasurer_id),
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=3_000,
            financial_year=2025,
        )
        run_id = uuid.UUID(run["run_id"])

        await dividend_service.approve_dividend_run(_admin(cid, admin_id), run_id)

        # Second call must raise Conflict (run is already posted)
        with pytest.raises(Conflict):
            await dividend_service.approve_dividend_run(_admin(cid, admin_id), run_id)

        # Only one journal entry for this run
        await db.execute("SELECT set_config('app.cooperative_id', $1, false)", str(cid))
        entry_count = await db.fetchval(
            "SELECT COUNT(*) FROM journal_entries "
            "WHERE cooperative_id=$1 AND source_type='dividend' AND source_id=$2",
            cid,
            run_id,
        )
        assert int(entry_count) == 1

    async def test_statement_shows_dividend_line(self, db: asyncpg.Connection) -> None:
        """Member statement includes a 'Dividend <year>' line after approval."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant_with_shares(db)

        run = await dividend_service.prepare_dividend_run(
            _treasurer(cid, treasurer_id),
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=5_000,
            financial_year=2025,
        )
        run_id = uuid.UUID(run["run_id"])
        await dividend_service.approve_dividend_run(_admin(cid, admin_id), run_id)

        sess = CurrentSession(
            cid, "post-test", uuid.uuid4(), admin_id, "admin@test", ("admin",), bytes(32)
        )
        stmt = await member_reads.list_statement(sess, member_id, page=1, page_size=50)
        types = [item["type"] for item in stmt["items"]]
        assert any("Dividend" in t and "2025" in t for t in types), (
            f"No dividend line in statement; types={types}"
        )

    async def test_end_to_end_via_http_as_app_role(self, db: asyncpg.Connection) -> None:
        """Full HTTP round-trip: prepare → approve → member balance correct."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant_with_shares(db)

        selected: dict[str, CurrentSession] = {}
        app = create_app()
        app.dependency_overrides[current_session] = lambda: selected["current"]

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://test.local"
        ) as client:
            # Prepare
            selected["current"] = _treasurer(cid, treasurer_id)
            resp = await client.post(
                "/api/v1/dividends/runs",
                json={
                    "record_date": "2025-12-31",
                    "meeting_or_resolution": "AGM 2025",
                    "pool_kobo": 6_000,
                    "financial_year": 2025,
                },
            )
            assert resp.status_code == 200, resp.text
            run_id = resp.json()["run_id"]

            # Approve
            selected["current"] = _admin(cid, admin_id)
            resp = await client.post(f"/api/v1/dividends/runs/{run_id}/approve")
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "posted"

        app.dependency_overrides.clear()

        # Verify balance via service layer (same code the overview uses)
        bal = await member_balances(cid, member_id)
        assert bal["savings_kobo"] == 6_000


# ---------------------------------------------------------------------------
# Bug B: duplicate-run guard
# ---------------------------------------------------------------------------


class TestDuplicateRunGuard:
    """A second run for the same period must be blocked."""

    async def test_second_prepare_for_same_year_is_rejected(self, db: asyncpg.Connection) -> None:
        """Preparing a second run for the same financial year raises Conflict."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant_with_shares(db)
        t = _treasurer(cid, treasurer_id)

        await dividend_service.prepare_dividend_run(
            t,
            record_date=date(2025, 6, 30),
            meeting_or_resolution="AGM 2025",
            pool_kobo=5_000,
            financial_year=2025,
        )

        with pytest.raises(Conflict, match="2025"):
            await dividend_service.prepare_dividend_run(
                t,
                record_date=date(2025, 12, 31),
                meeting_or_resolution="AGM 2025",
                pool_kobo=4_000,
                financial_year=2025,
            )

    async def test_different_record_date_same_year_still_blocked(
        self, db: asyncpg.Connection
    ) -> None:
        """Different record date in the same year is still blocked."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant_with_shares(db)
        t = _treasurer(cid, treasurer_id)

        await dividend_service.prepare_dividend_run(
            t,
            record_date=date(2025, 3, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=5_000,
            financial_year=2025,
        )

        with pytest.raises(Conflict, match="2025"):
            await dividend_service.prepare_dividend_run(
                t,
                record_date=date(2025, 9, 30),
                meeting_or_resolution="AGM 2025",
                pool_kobo=4_000,
                financial_year=2025,
            )

    async def test_allowed_after_first_run_is_rejected(self, db: asyncpg.Connection) -> None:
        """A new run is allowed once the previous one is rejected."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant_with_shares(db)
        t = _treasurer(cid, treasurer_id)
        a = _admin(cid, admin_id)

        first = await dividend_service.prepare_dividend_run(
            t,
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=5_000,
            financial_year=2025,
        )
        await dividend_service.reject_dividend_run(a, uuid.UUID(first["run_id"]), "Board decision")

        # Now a new run for 2025 should succeed
        second = await dividend_service.prepare_dividend_run(
            t,
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=5_000,
            financial_year=2025,
        )
        assert second["status"] == "unprocessed"

    async def test_allowed_after_first_run_is_cancelled(self, db: asyncpg.Connection) -> None:
        """A new run is allowed once the previous one is cancelled."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant_with_shares(db)
        t = _treasurer(cid, treasurer_id)

        first = await dividend_service.prepare_dividend_run(
            t,
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=5_000,
            financial_year=2025,
        )
        await dividend_service.cancel_dividend_run(t, uuid.UUID(first["run_id"]))

        second = await dividend_service.prepare_dividend_run(
            t,
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=5_000,
            financial_year=2025,
        )
        assert second["status"] == "unprocessed"

    async def test_different_years_both_allowed(self, db: asyncpg.Connection) -> None:
        """Runs for different years do not block each other."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant_with_shares(db)
        t = _treasurer(cid, treasurer_id)

        r2024 = await dividend_service.prepare_dividend_run(
            t,
            record_date=date(2024, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=5_000,
            financial_year=2024,
        )
        r2025 = await dividend_service.prepare_dividend_run(
            t,
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=5_000,
            financial_year=2025,
        )
        assert r2024["status"] == "unprocessed"
        assert r2025["status"] == "unprocessed"

    async def test_partial_unique_index_exists_on_dividend_runs(
        self, db: asyncpg.Connection
    ) -> None:
        """The partial unique index (cooperative_id, period_key) WHERE active must exist."""
        index_def = await db.fetchval(
            """SELECT indexdef FROM pg_indexes
               WHERE tablename = 'dividend_runs'
                 AND indexname = 'uq_dividend_runs_coop_period_active'"""
        )
        assert index_def is not None, (
            "Partial unique index uq_dividend_runs_coop_period_active is missing"
        )


# ---------------------------------------------------------------------------
# Repair helper (test-internal, mirrors the repair command logic using
# transaction() so it works with the ajo_app test role and RLS context)
# ---------------------------------------------------------------------------


async def _repair_single_cooperative(cid: uuid.UUID, *, dry_run: bool = False) -> dict[str, int]:
    """Repair broken runs for a single cooperative (test helper).

    The production repair command uses DATABASE_URL_DIRECT to list runs across
    all cooperatives.  In tests we already know the cid, so we query directly
    through transaction() without the cross-tenant discovery step.
    """
    from app.domains.dividends.service import _post_entry

    async with transaction(cid) as session:
        rows = await session.execute(
            text(
                """SELECT id, pool_kobo, record_date, approved_by
                   FROM dividend_runs
                   WHERE cooperative_id = :cid
                     AND status = 'approved'
                     AND journal_entry_id IS NULL
                   FOR UPDATE"""
            ),
            {"cid": cid},
        )
        broken = [
            (
                uuid.UUID(str(r[0])),
                int(r[1]),
                r[2],
                uuid.UUID(str(r[3])) if r[3] else uuid.UUID(int=0),
            )
            for r in rows
        ]

        if dry_run:
            return {"would_post": len(broken), "skipped": 0}

        posted = 0
        for run_id, pool_kobo, record_date, actor_id in broken:
            entry_id = await _post_entry(
                session,
                cid=cid,
                run_id=run_id,
                pool_kobo=pool_kobo,
                record_date=record_date,
                actor_id=actor_id,
            )
            await session.execute(
                text(
                    """UPDATE dividend_runs
                       SET status = 'posted',
                           posted_by = :actor,
                           posted_at = CURRENT_TIMESTAMP,
                           journal_entry_id = :entry_id
                       WHERE id = :run_id AND cooperative_id = :cid"""
                ),
                {
                    "actor": actor_id,
                    "entry_id": entry_id,
                    "run_id": run_id,
                    "cid": cid,
                },
            )
            posted += 1

    return {"posted": posted, "skipped": 0}


# ---------------------------------------------------------------------------
# Repair command
# ---------------------------------------------------------------------------


class TestRepairCommand:
    """_repair_single_cooperative posts missing entries for approved-but-unposted runs."""

    async def test_repair_posts_missing_entry(self, db: asyncpg.Connection) -> None:
        """Repair posts ledger entry for a run that was approved without posting."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant_with_shares(db)

        # Simulate an already-approved-but-unposted run (staging legacy state):
        # prepare and then manually flip status to 'approved' without posting.
        run = await dividend_service.prepare_dividend_run(
            _treasurer(cid, treasurer_id),
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=4_000,
            financial_year=2025,
        )
        run_id = uuid.UUID(run["run_id"])

        await db.execute("SELECT set_config('app.cooperative_id', $1, false)", str(cid))
        await db.execute(
            "UPDATE dividend_runs SET status='approved', approved_by=$1,"
            " approved_at=CURRENT_TIMESTAMP WHERE id=$2",
            admin_id,
            run_id,
        )

        bal_before = await member_balances(cid, member_id)

        counts = await _repair_single_cooperative(cid, dry_run=False)
        assert counts["posted"] == 1

        bal_after = await member_balances(cid, member_id)
        assert bal_after["savings_kobo"] == bal_before["savings_kobo"] + 4_000

    async def test_repair_dry_run_posts_nothing(self, db: asyncpg.Connection) -> None:
        """dry_run counts but does not alter the database."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant_with_shares(db)

        run = await dividend_service.prepare_dividend_run(
            _treasurer(cid, treasurer_id),
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=4_000,
            financial_year=2025,
        )
        run_id = uuid.UUID(run["run_id"])

        await db.execute("SELECT set_config('app.cooperative_id', $1, false)", str(cid))
        await db.execute(
            "UPDATE dividend_runs SET status='approved', approved_by=$1,"
            " approved_at=CURRENT_TIMESTAMP WHERE id=$2",
            admin_id,
            run_id,
        )

        bal_before = await member_balances(cid, member_id)
        counts = await _repair_single_cooperative(cid, dry_run=True)
        assert counts["would_post"] == 1

        bal_after = await member_balances(cid, member_id)
        assert bal_after["savings_kobo"] == bal_before["savings_kobo"]

    async def test_repair_is_idempotent(self, db: asyncpg.Connection) -> None:
        """Running repair twice posts nothing on the second run."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant_with_shares(db)

        run = await dividend_service.prepare_dividend_run(
            _treasurer(cid, treasurer_id),
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=4_000,
            financial_year=2025,
        )
        run_id = uuid.UUID(run["run_id"])

        await db.execute("SELECT set_config('app.cooperative_id', $1, false)", str(cid))
        await db.execute(
            "UPDATE dividend_runs SET status='approved', approved_by=$1,"
            " approved_at=CURRENT_TIMESTAMP WHERE id=$2",
            admin_id,
            run_id,
        )

        first = await _repair_single_cooperative(cid, dry_run=False)
        second = await _repair_single_cooperative(cid, dry_run=False)
        assert first["posted"] == 1
        assert second["posted"] == 0
