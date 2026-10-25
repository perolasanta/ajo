"""Seed-demo invariants: expected counts, balanced journals, and production refusal."""

from __future__ import annotations

import asyncpg
import pytest

from app.cli import create_cooperative
from app.core.config import get_settings
from app.core.db import asyncpg_dsn, dispose_engine
from app.seed_demo import seed_demo

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


async def test_seed_demo_counts_and_balanced_journals(db: asyncpg.Connection) -> None:
    slug = "seedtest"
    await create_cooperative(slug, "Seed test", "admin@seed.test")
    await seed_demo(slug, yes_fake_data=True)

    cooperative_id = await db.fetchval(
        "SELECT cooperative_id FROM tenant_directory WHERE slug = $1", slug
    )
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id', $1, true)", str(cooperative_id))
        assert (
            await db.fetchval(
                "SELECT count(*) FROM members WHERE cooperative_id = $1", cooperative_id
            )
            == 25
        )
        assert (
            await db.fetchval(
                "SELECT count(*) FROM loan_applications WHERE cooperative_id = $1",
                cooperative_id,
            )
            == 14
        )
        assert (
            await db.fetchval(
                "SELECT count(*) FROM loans WHERE cooperative_id = $1", cooperative_id
            )
            == 8
        )
        application_counts = {
            row["status"]: row["count"]
            for row in await db.fetch(
                "SELECT status, count(*) AS count FROM loan_applications "
                "WHERE cooperative_id = $1 GROUP BY status",
                cooperative_id,
            )
        }
        assert application_counts == {
            "awaiting_applicant_confirmation": 3,
            "under_review": 2,
            "awaiting_borrower_final_confirmation": 2,
            "ready_for_disbursement": 1,
            "repaying": 5,
            "disputed": 1,
        }
        loan_counts = {
            row["status"]: row["count"]
            for row in await db.fetch(
                "SELECT status, count(*) AS count FROM loans "
                "WHERE cooperative_id = $1 GROUP BY status",
                cooperative_id,
            )
        }
        assert loan_counts == {
            "approved": 2,
            "ready_for_disbursement": 1,
            "repaying": 4,
            "closed": 1,
        }
        assert (
            await db.fetchval(
                "SELECT count(DISTINCT user_id) FROM user_roles WHERE cooperative_id = $1",
                cooperative_id,
            )
            == 5
        )
        unbalanced = await db.fetchval(
            "SELECT count(*) FROM (SELECT entry_id, sum(debit_kobo) AS debits, "
            "sum(credit_kobo) AS credits FROM journal_lines WHERE cooperative_id = $1 "
            "GROUP BY entry_id) t WHERE debits <> credits",
            cooperative_id,
        )
        assert unbalanced == 0
        totals = await db.fetchrow(
            "SELECT coalesce(sum(debit_kobo), 0) AS debits, "
            "coalesce(sum(credit_kobo), 0) AS credits FROM journal_lines WHERE cooperative_id = $1",
            cooperative_id,
        )
        assert totals["debits"] == totals["credits"]


async def test_seed_demo_refuses_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="production"):
            await seed_demo("any-slug", yes_fake_data=True)
    finally:
        get_settings.cache_clear()
