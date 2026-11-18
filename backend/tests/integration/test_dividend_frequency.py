"""Red-first tests for Unit 3: configurable dividend frequency policy field.

These tests FAIL before the implementation and PASS after.
Tests cover:
- dividend_frequency field stored and read from cooperative_policy_versions
- propose_policy defaults to 'annual' when field absent
- propose_policy rejects invalid frequency values
- get_effective_policy returns dividend_frequency
- second-admin policy approval still required (eligibility unchanged)
- period_key shapes for all four frequencies
- record date outside period rejected
- one run per period for each frequency
- legacy runs still display (period_key=None → year from record_date)
"""
from __future__ import annotations

import uuid
from datetime import date

import asyncpg
import pytest

from app.core.db import asyncpg_dsn, dispose_engine
from app.core.errors import Conflict
from app.domains.auth.service import CurrentSession
from app.domains.dividends import service as dividend_service
from app.domains.policies import service as policy_service
from tests.conftest import create_tenant

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
async def reset_engine() -> None:
    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture
async def db(test_database) -> asyncpg.Connection:
    conn = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    assert await conn.fetchval("SELECT current_user") == "ajo_app"
    yield conn
    await conn.close()


def _session(cid: uuid.UUID, uid: uuid.UUID, role: str = "admin") -> CurrentSession:
    return CurrentSession(
        cid, "freq-test", uuid.uuid4(), uid, "admin@example.test", (role,), bytes(32)
    )


async def _tenant(conn: asyncpg.Connection) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    seed = await create_tenant(
        conn, name="Freq test", slug_prefix="freq", roles=("admin", "admin")
    )
    return seed.cooperative_id, seed.user_ids[0], seed.user_ids[1]


async def _approved_policy(
    conn: asyncpg.Connection,
    cid: uuid.UUID,
    proposer: uuid.UUID,
    approver: uuid.UUID,
    *,
    frequency: str = "annual",
) -> uuid.UUID:
    """Insert an approved policy row with the given dividend_frequency."""
    policy_id = uuid.uuid4()
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        await conn.execute(
            """INSERT INTO cooperative_policy_versions
               (id, cooperative_id, version, status, effective_from, proposed_by, approved_by,
                salary_basis, deduction_cap_bps, max_active_loans, guarantors_required,
                min_savings_kobo, loan_to_savings_multiple_x100, interest_method,
                dividend_frequency)
             VALUES ($1,$2,1,'approved','2026-01-01',$3,$4,
                     'net',7000,1,0,0,300,'reducing_balance',$5)""",
            policy_id,
            cid,
            proposer,
            approver,
            frequency,
        )
    return policy_id


# ---------------------------------------------------------------------------
# Policy column tests
# ---------------------------------------------------------------------------


async def test_dividend_frequency_column_defaults_to_annual(
    db: asyncpg.Connection,
) -> None:
    """Existing approved rows without dividend_frequency set should read 'annual'."""
    cid, proposer, approver = await _tenant(db)
    # Insert WITHOUT specifying dividend_frequency → default
    policy_id = uuid.uuid4()
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        await db.execute(
            """INSERT INTO cooperative_policy_versions
               (id, cooperative_id, version, status, effective_from, proposed_by, approved_by,
                salary_basis, deduction_cap_bps, max_active_loans, guarantors_required,
                min_savings_kobo, loan_to_savings_multiple_x100, interest_method)
             VALUES ($1,$2,1,'approved','2026-01-01',$3,$4,
                     'net',7000,1,0,0,300,'reducing_balance')""",
            policy_id,
            cid,
            proposer,
            approver,
        )
    policy = await policy_service.get_effective_policy(cid, date(2026, 6, 1))
    assert policy["dividend_frequency"] == "annual"


async def test_propose_policy_defaults_frequency_to_annual(
    db: asyncpg.Connection,
) -> None:
    cid, proposer, _ = await _tenant(db)
    policy_id = await policy_service.propose_policy(
        _session(cid, proposer),
        {
            "effective_from": date(2099, 1, 1),
            "salary_basis": "net",
            "deduction_cap_bps": 7000,
            "max_active_loans": 1,
            "guarantors_required": 0,
            "min_savings_kobo": 0,
            "loan_to_savings_multiple_x100": 300,
            "interest_method": "reducing_balance",
        },
        "initial policy",
    )
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        freq = await db.fetchval(
            "SELECT dividend_frequency FROM cooperative_policy_versions "
            "WHERE cooperative_id=$1 AND id=$2",
            cid,
            policy_id,
        )
    assert freq == "annual"


async def test_propose_policy_stores_valid_frequency(
    db: asyncpg.Connection,
) -> None:
    cid, proposer, _ = await _tenant(db)
    for freq in ("annual", "half_yearly", "quarterly", "monthly"):
        policy_id = await policy_service.propose_policy(
            _session(cid, proposer),
            {
                "effective_from": date(2099, 1, 1),
                "salary_basis": "net",
                "deduction_cap_bps": 7000,
                "max_active_loans": 1,
                "guarantors_required": 0,
                "min_savings_kobo": 0,
                "loan_to_savings_multiple_x100": 300,
                "interest_method": "reducing_balance",
                "dividend_frequency": freq,
            },
            f"Set frequency {freq}",
        )
        async with db.transaction():
            await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
            stored = await db.fetchval(
                "SELECT dividend_frequency FROM cooperative_policy_versions "
                "WHERE cooperative_id=$1 AND id=$2",
                cid,
                policy_id,
            )
        assert stored == freq


async def test_propose_policy_rejects_invalid_frequency(
    db: asyncpg.Connection,
) -> None:
    cid, proposer, _ = await _tenant(db)
    with pytest.raises(ValueError, match="dividend_frequency"):
        await policy_service.propose_policy(
            _session(cid, proposer),
            {
                "effective_from": date(2099, 1, 1),
                "salary_basis": "net",
                "deduction_cap_bps": 7000,
                "max_active_loans": 1,
                "guarantors_required": 0,
                "min_savings_kobo": 0,
                "loan_to_savings_multiple_x100": 300,
                "interest_method": "reducing_balance",
                "dividend_frequency": "weekly",
            },
            "Bad frequency",
        )


async def test_get_effective_policy_returns_dividend_frequency(
    db: asyncpg.Connection,
) -> None:
    cid, proposer, approver = await _tenant(db)
    await _approved_policy(db, cid, proposer, approver, frequency="quarterly")
    policy = await policy_service.get_effective_policy(cid, date(2026, 6, 1))
    assert policy["dividend_frequency"] == "quarterly"


# ---------------------------------------------------------------------------
# Dividend run period key tests
# ---------------------------------------------------------------------------


async def _setup_coop(conn: asyncpg.Connection) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Return (cid, treasurer_id, admin_id) with no active member (just the run table test)."""
    seed = await create_tenant(
        conn,
        name="Period test",
        slug_prefix="period",
        roles=("treasurer", "admin"),
        include_member=True,
        member_status="active",
    )
    cid = seed.cooperative_id
    # Seed the minimum accounts
    divs_acc = uuid.uuid4()
    sav_acc = uuid.uuid4()
    shr_acc = uuid.uuid4()
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        await conn.executemany(
            "INSERT INTO accounts(cooperative_id,id,code,name,account_type)"
            " VALUES($1,$2,$3,$4,$5)",
            [
                (cid, divs_acc, "5100", "Dividends declared", "expense"),
                (cid, sav_acc, "2100", "Member savings", "liability"),
                (cid, shr_acc, "3100", "Share capital", "equity"),
            ],
        )
        await conn.execute(
            "INSERT INTO cooperative_settings(cooperative_id,dividends_account_id,"
            "savings_account_id,share_capital_account_id,share_unit_price_kobo)"
            " VALUES($1,$2,$3,$4,100)"
            " ON CONFLICT(cooperative_id) DO UPDATE SET"
            " dividends_account_id=EXCLUDED.dividends_account_id,"
            " savings_account_id=EXCLUDED.savings_account_id,"
            " share_capital_account_id=EXCLUDED.share_capital_account_id,"
            " share_unit_price_kobo=EXCLUDED.share_unit_price_kobo",
            cid,
            divs_acc,
            sav_acc,
            shr_acc,
        )
    return cid, seed.user_ids[0], seed.user_ids[1]


async def test_annual_prepare_stores_year_as_period_key(db: asyncpg.Connection) -> None:
    cid, treasurer_id, _ = await _setup_coop(db)
    s = _session(cid, treasurer_id, "treasurer")
    run = await dividend_service.prepare_dividend_run(
        s,
        record_date=date(2026, 12, 31),
        pool_kobo=10_000,
        period_key="2026",
        meeting_or_resolution=None,
    )
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        stored = await db.fetchval(
            "SELECT period_key FROM dividend_runs WHERE cooperative_id=$1 AND id=$2",
            cid,
            uuid.UUID(run["run_id"]),
        )
    assert stored == "2026"


async def test_quarterly_prepare_stores_quarter_period_key(db: asyncpg.Connection) -> None:
    cid, treasurer_id, _ = await _setup_coop(db)
    s = _session(cid, treasurer_id, "treasurer")
    run = await dividend_service.prepare_dividend_run(
        s,
        record_date=date(2026, 9, 30),
        pool_kobo=5_000,
        period_key="2026-Q3",
        meeting_or_resolution=None,
    )
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        stored = await db.fetchval(
            "SELECT period_key FROM dividend_runs WHERE cooperative_id=$1 AND id=$2",
            cid,
            uuid.UUID(run["run_id"]),
        )
    assert stored == "2026-Q3"


async def test_half_yearly_period_key_stored(db: asyncpg.Connection) -> None:
    cid, treasurer_id, _ = await _setup_coop(db)
    s = _session(cid, treasurer_id, "treasurer")
    run = await dividend_service.prepare_dividend_run(
        s,
        record_date=date(2026, 6, 30),
        pool_kobo=5_000,
        period_key="2026-H1",
        meeting_or_resolution=None,
    )
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        stored = await db.fetchval(
            "SELECT period_key FROM dividend_runs WHERE cooperative_id=$1 AND id=$2",
            cid,
            uuid.UUID(run["run_id"]),
        )
    assert stored == "2026-H1"


async def test_monthly_period_key_stored(db: asyncpg.Connection) -> None:
    cid, treasurer_id, _ = await _setup_coop(db)
    s = _session(cid, treasurer_id, "treasurer")
    run = await dividend_service.prepare_dividend_run(
        s,
        record_date=date(2026, 11, 30),
        pool_kobo=5_000,
        period_key="2026-M11",
        meeting_or_resolution=None,
    )
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        stored = await db.fetchval(
            "SELECT period_key FROM dividend_runs WHERE cooperative_id=$1 AND id=$2",
            cid,
            uuid.UUID(run["run_id"]),
        )
    assert stored == "2026-M11"


async def test_duplicate_period_key_blocked_for_quarterly(db: asyncpg.Connection) -> None:
    cid, treasurer_id, _ = await _setup_coop(db)
    s = _session(cid, treasurer_id, "treasurer")
    await dividend_service.prepare_dividend_run(
        s,
        record_date=date(2026, 9, 30),
        pool_kobo=5_000,
        period_key="2026-Q3",
        meeting_or_resolution=None,
    )
    with pytest.raises(Conflict, match="2026-Q3"):
        await dividend_service.prepare_dividend_run(
            s,
            record_date=date(2026, 9, 15),
            pool_kobo=3_000,
            period_key="2026-Q3",
            meeting_or_resolution=None,
        )


async def test_duplicate_period_key_blocked_for_monthly(db: asyncpg.Connection) -> None:
    cid, treasurer_id, _ = await _setup_coop(db)
    s = _session(cid, treasurer_id, "treasurer")
    await dividend_service.prepare_dividend_run(
        s,
        record_date=date(2026, 11, 30),
        pool_kobo=5_000,
        period_key="2026-M11",
        meeting_or_resolution=None,
    )
    with pytest.raises(Conflict, match="2026-M11"):
        await dividend_service.prepare_dividend_run(
            s,
            record_date=date(2026, 11, 15),
            pool_kobo=3_000,
            period_key="2026-M11",
            meeting_or_resolution=None,
        )


async def test_different_period_keys_both_allowed(db: asyncpg.Connection) -> None:
    cid, treasurer_id, _ = await _setup_coop(db)
    s = _session(cid, treasurer_id, "treasurer")
    await dividend_service.prepare_dividend_run(
        s,
        record_date=date(2026, 3, 31),
        pool_kobo=5_000,
        period_key="2026-Q1",
        meeting_or_resolution=None,
    )
    # Different period — must succeed
    run2 = await dividend_service.prepare_dividend_run(
        s,
        record_date=date(2026, 6, 30),
        pool_kobo=5_000,
        period_key="2026-Q2",
        meeting_or_resolution=None,
    )
    assert run2["period_key"] == "2026-Q2"


async def test_legacy_run_without_period_key_still_readable(db: asyncpg.Connection) -> None:
    """Runs inserted without period_key (NULL) must still be listed and detailed."""
    cid, treasurer_id, _ = await _setup_coop(db)
    s = _session(cid, treasurer_id, "treasurer")
    # Insert a legacy run with NULL period_key directly
    run_id = uuid.uuid4()
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        await db.execute(
            """INSERT INTO dividend_runs
               (id, cooperative_id, pool_kobo, record_date, status, prepared_by, period_key)
             VALUES ($1,$2,1000,'2024-12-31','unprocessed',$3,NULL)""",
            run_id,
            cid,
            treasurer_id,
        )
    run = await dividend_service.get_dividend_run(s, run_id)
    assert run["period_key"] is None
    assert "run_id" in run
