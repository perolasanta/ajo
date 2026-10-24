"""Policy, product, and module invariants exercised as ajo_app on dev."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from app.core.db import asyncpg_dsn, dispose_engine
from app.core.errors import Conflict
from app.domains.auth.service import CurrentSession
from app.domains.policies import service
from tests.conftest import create_tenant

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
async def reset_application_engine() -> None:
    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture
async def db(test_database) -> asyncpg.Connection:
    conn = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    assert await conn.fetchval("SELECT current_user") == "ajo_app"
    yield conn
    await conn.close()


async def tenant(conn: asyncpg.Connection) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    seed = await create_tenant(
        conn, name="Policy test", slug_prefix="policy", roles=("admin", "admin")
    )
    return seed.cooperative_id, seed.user_ids[0], seed.user_ids[1]


async def policy_row(
    conn: asyncpg.Connection,
    cid: uuid.UUID,
    proposer: uuid.UUID,
    *,
    status: str = "proposed",
    start: date = date(2026, 1, 1),
    end: date | None = None,
    approved_by: uuid.UUID | None = None,
    version: int = 1,
) -> uuid.UUID:
    policy_id = uuid.uuid4()
    await conn.execute(
        """INSERT INTO cooperative_policy_versions
      (id,cooperative_id,version,status,effective_from,effective_to,proposed_by,approved_by,
       salary_basis,deduction_cap_bps,max_active_loans,guarantors_required,min_savings_kobo,
       loan_to_savings_multiple_x100,interest_method)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,'net',7000,1,2,0,300,'reducing_balance')""",
        policy_id,
        cid,
        version,
        status,
        start,
        end,
        proposer,
        approved_by,
    )
    return policy_id


def session(cid: uuid.UUID, uid: uuid.UUID) -> CurrentSession:
    return CurrentSession(
        cid, "policy-test", uuid.uuid4(), uid, "admin@example.test", ("admin",), bytes(32)
    )


async def test_same_user_approval_rejected(db: asyncpg.Connection) -> None:
    cid, proposer, _ = await tenant(db)
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        policy_id = await policy_row(db, cid, proposer)
    with pytest.raises(Conflict, match="different administrator"):
        await service.approve_policy(session(cid, proposer), policy_id, "approval")


async def test_overlapping_approved_dates_rejected(db: asyncpg.Connection) -> None:
    cid, proposer, approver = await tenant(db)
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        await policy_row(db, cid, proposer, status="approved", approved_by=approver)
        with pytest.raises(asyncpg.PostgresError, match="overlap"):
            await policy_row(
                db,
                cid,
                proposer,
                status="approved",
                approved_by=approver,
                start=date(2026, 6, 1),
                version=2,
            )


async def test_approved_policy_is_immutable(db: asyncpg.Connection) -> None:
    cid, proposer, approver = await tenant(db)
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        policy_id = await policy_row(db, cid, proposer, status="approved", approved_by=approver)
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        with pytest.raises(asyncpg.PostgresError, match="immutable"):
            await db.execute(
                "UPDATE cooperative_policy_versions SET deduction_cap_bps=5000 "
                "WHERE cooperative_id=$1 AND id=$2",
                cid,
                policy_id,
            )
    with pytest.raises(Conflict, match="fewer guarantors"):
        await service.validate_product_safeguards(
            cid,
            {"guarantors_required": 1, "interest_method": "reducing_balance"},
            date(2026, 6, 1),
        )
    product = {
        "code": "STANDARD",
        "name": "Standard loan",
        "interest_method": "reducing_balance",
        "annual_rate_bps": 1000,
        "min_amount_kobo": 10000,
        "max_amount_kobo": 1000000,
        "min_tenor_months": 1,
        "max_tenor_months": 12,
        "fee_bps": 0,
        "guarantors_required": 2,
        "supersedes_id": None,
    }
    current = session(cid, approver)
    product_id = await service.create_loan_product(
        current, product, date(2026, 6, 1), "initial product"
    )
    await service.approve_loan_product(current, product_id, date(2026, 6, 1), "approved")
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        assert (
            await db.fetchval(
                "SELECT count(*) FROM audit_logs WHERE cooperative_id=$1 AND entity_id=$2 "
                "AND action='loan_product_approved'",
                cid,
                str(product_id),
            )
            == 1
        )
        with pytest.raises(asyncpg.PostgresError, match="immutable"):
            await db.execute(
                "UPDATE loan_products SET annual_rate_bps=2000 WHERE cooperative_id=$1 AND id=$2",
                cid,
                product_id,
            )


async def test_effective_policy_uses_wat_calendar_boundaries(db: asyncpg.Connection) -> None:
    cid, proposer, approver = await tenant(db)
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        await policy_row(
            db,
            cid,
            proposer,
            status="approved",
            approved_by=approver,
            start=date(2026, 10, 24),
            end=date(2026, 10, 25),
        )
        await policy_row(
            db,
            cid,
            proposer,
            status="approved",
            approved_by=approver,
            start=date(2026, 10, 25),
            version=2,
        )
    wat = ZoneInfo("Africa/Lagos")
    before = datetime(2026, 10, 24, 23, 59, tzinfo=wat).date()
    boundary = datetime(2026, 10, 25, 0, 0, tzinfo=wat).date()
    assert (await service.get_effective_policy(cid, before))["version"] == 1
    assert (await service.get_effective_policy(cid, boundary))["version"] == 2


async def test_module_disable_keeps_financial_history(db: asyncpg.Connection) -> None:
    cid, user_id, _ = await tenant(db)
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        a, b = uuid.uuid4(), uuid.uuid4()
        await db.executemany(
            "INSERT INTO accounts(id,cooperative_id,code,name,account_type) "
            "VALUES($1,$2,$3,$3,'asset')",
            [(a, cid, "a"), (b, cid, "b")],
        )
        entry_id = await db.fetchval(
            "SELECT post_journal_entry($1,current_date,'test',$2,'saved',NULL,$3,$4::jsonb,NULL)",
            cid,
            user_id,
            str(uuid.uuid4()),
            '[{"account_id":"' + str(a) + '","debit_kobo":25,"credit_kobo":0},'
            '{"account_id":"' + str(b) + '","debit_kobo":0,"credit_kobo":25}]',
        )
        await db.execute(
            "INSERT INTO cooperative_modules(cooperative_id,module,enabled) VALUES($1,'loans',true)",
            cid,
        )
        await db.execute(
            "UPDATE cooperative_modules SET enabled=false,disabled_at=now() "
            "WHERE cooperative_id=$1 AND module='loans'",
            cid,
        )
        assert (
            await db.fetchval(
                "SELECT count(*) FROM journal_entries WHERE cooperative_id=$1 AND id=$2",
                cid,
                entry_id,
            )
            == 1
        )
        assert (
            await db.fetchval(
                "SELECT enabled FROM cooperative_modules WHERE cooperative_id=$1 AND module='loans'",
                cid,
            )
            is False
        )
