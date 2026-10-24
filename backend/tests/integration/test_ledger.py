"""Ledger invariants exercised through the ajo_app role on a disposable dev branch."""

import asyncio
import json
import uuid

import asyncpg
import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.db import asyncpg_dsn
from tests.conftest import TEST_APP_URL
from tests.conftest import create_tenant as seed_tenant

pytestmark = pytest.mark.db


@pytest.fixture
async def db(test_database) -> asyncpg.Connection:
    conn = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    assert await conn.fetchval("SELECT current_user") == "ajo_app"
    yield conn
    await conn.close()


async def set_tenant(conn: asyncpg.Connection, cooperative_id: uuid.UUID) -> None:
    await conn.execute("SELECT set_config('app.cooperative_id', $1, true)", str(cooperative_id))


async def create_tenant(
    conn: asyncpg.Connection, cooperative_id: uuid.UUID | None = None
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    seed = await seed_tenant(
        conn,
        cooperative_id=cooperative_id,
        name="Test cooperative",
        slug_prefix="ledger",
        roles=(),
    )
    cooperative_id = seed.cooperative_id
    debit_account, credit_account = uuid.uuid4(), uuid.uuid4()
    await conn.executemany(
        """
        INSERT INTO accounts (id, cooperative_id, code, name, account_type)
        VALUES ($1, $2, $3, $4, $5)
        """,
        [
            (debit_account, cooperative_id, "debit", "Debit account", "asset"),
            (credit_account, cooperative_id, "credit", "Credit account", "liability"),
        ],
    )
    return cooperative_id, debit_account, credit_account


async def post_entry(
    conn: asyncpg.Connection,
    cooperative_id: uuid.UUID,
    debit_account: uuid.UUID,
    credit_account: uuid.UUID,
    amount: int,
    *,
    idempotency_key: str | None = None,
    reverses_entry_id: uuid.UUID | None = None,
    lines: list[dict[str, object]] | None = None,
) -> uuid.UUID:
    if lines is None:
        lines = [
            {"account_id": str(debit_account), "debit_kobo": amount, "credit_kobo": 0},
            {"account_id": str(credit_account), "debit_kobo": 0, "credit_kobo": amount},
        ]
    return await conn.fetchval(
        """
        SELECT post_journal_entry(
            $1, current_date, 'test', $2, 'integration test', NULL,
            $3, $4::jsonb, $5
        )
        """,
        cooperative_id,
        uuid.uuid4(),
        idempotency_key or str(uuid.uuid4()),
        json.dumps(lines),
        reverses_entry_id,
    )


async def test_ledger_unbalanced_entry_fails_at_commit(db: asyncpg.Connection) -> None:
    tx = db.transaction()
    await tx.start()
    try:
        cooperative_id, debit_account, credit_account = await create_tenant(db)
        await post_entry(
            db,
            cooperative_id,
            debit_account,
            credit_account,
            0,
            lines=[
                {"account_id": str(debit_account), "debit_kobo": 10, "credit_kobo": 0},
                {"account_id": str(credit_account), "debit_kobo": 0, "credit_kobo": 9},
            ],
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await tx.commit()
    finally:
        if db.is_in_transaction():
            await tx.rollback()


async def test_single_line_entry_rejected_at_commit(db: asyncpg.Connection) -> None:
    tx = db.transaction()
    await tx.start()
    try:
        cooperative_id, debit_account, _ = await create_tenant(db)
        await post_entry(
            db,
            cooperative_id,
            debit_account,
            debit_account,
            0,
            lines=[{"account_id": str(debit_account), "debit_kobo": 10, "credit_kobo": 0}],
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await tx.commit()
    finally:
        if db.is_in_transaction():
            await tx.rollback()


async def test_update_delete_and_truncate_fail_as_ajo_app(db: asyncpg.Connection) -> None:
    tx = db.transaction()
    await tx.start()
    try:
        cooperative_id, debit_account, credit_account = await create_tenant(db)
        entry_id = await post_entry(db, cooperative_id, debit_account, credit_account, 50)
        await db.execute("SET CONSTRAINTS ALL IMMEDIATE")
        statements = (
            (
                "UPDATE journal_entries SET description = 'changed' WHERE id = $1",
                "DELETE FROM journal_entries WHERE id = $1",
                "TRUNCATE journal_entries",
                entry_id,
            ),
            (
                "UPDATE journal_lines SET debit_kobo = debit_kobo WHERE entry_id = $1",
                "DELETE FROM journal_lines WHERE entry_id = $1",
                "TRUNCATE journal_lines",
                entry_id,
            ),
            (
                "UPDATE audit_logs SET action = action",
                "DELETE FROM audit_logs",
                "TRUNCATE audit_logs",
                None,
            ),
            (
                "UPDATE policy_change_log SET reason = reason",
                "DELETE FROM policy_change_log",
                "TRUNCATE policy_change_log",
                None,
            ),
        )
        for update_sql, delete_sql, truncate_sql, target_id in statements:
            for sql in (update_sql, delete_sql):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with db.transaction():
                        if target_id is None:
                            await db.execute(sql)
                        else:
                            await db.execute(sql, target_id)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with db.transaction():
                    await db.execute(truncate_sql)
    finally:
        if db.is_in_transaction():
            await tx.rollback()


async def test_tenant_a_cannot_read_or_insert_tenant_b(db: asyncpg.Connection) -> None:
    tx = db.transaction()
    await tx.start()
    try:
        tenant_a, debit_a, credit_a = await create_tenant(db)
        tenant_b, debit_b, credit_b = await create_tenant(db)
        entry_b = await post_entry(db, tenant_b, debit_b, credit_b, 20)
        await db.execute("SET CONSTRAINTS ALL IMMEDIATE")

        rls_rows = await db.fetch(
            """SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, p.qual, p.with_check
                 FROM pg_class AS c JOIN pg_namespace AS n ON n.oid=c.relnamespace
                 JOIN pg_policies AS p ON p.schemaname=n.nspname AND p.tablename=c.relname
                WHERE n.nspname=current_schema() AND c.relname=ANY($1::text[])""",
            ["accounts", "journal_entries", "journal_lines"],
        )
        assert {row["relname"] for row in rls_rows} == {
            "accounts",
            "journal_entries",
            "journal_lines",
        }
        for row in rls_rows:
            assert row["relrowsecurity"] and row["relforcerowsecurity"]
            assert "app_current_cooperative_id()" in row["qual"]
            assert "app_current_cooperative_id()" in row["with_check"]

        await set_tenant(db, tenant_a)
        assert (
            await db.fetchval(
                "SELECT count(*) FROM journal_entries WHERE cooperative_id = $1 AND id = $2",
                tenant_b,
                entry_b,
            )
            == 0
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with db.transaction():
                await db.execute(
                    """
                    INSERT INTO journal_entries (
                        cooperative_id, effective_date, source_type, description,
                        idempotency_key
                    ) VALUES ($1, current_date, 'test', 'cross-tenant', 'cross-tenant')
                    """,
                    tenant_b,
                )
        assert debit_a != credit_a
    finally:
        if db.is_in_transaction():
            await tx.rollback()


async def test_idempotent_replay_returns_existing_entry(db: asyncpg.Connection) -> None:
    tx = db.transaction()
    await tx.start()
    try:
        cooperative_id, debit_account, credit_account = await create_tenant(db)
        key = str(uuid.uuid4())
        original = await post_entry(
            db, cooperative_id, debit_account, credit_account, 42, idempotency_key=key
        )
        replay = await post_entry(
            db, cooperative_id, debit_account, credit_account, 42, idempotency_key=key
        )
        assert replay == original
        assert (
            await db.fetchval(
                "SELECT count(*) FROM journal_lines WHERE cooperative_id = $1 AND entry_id = $2",
                cooperative_id,
                original,
            )
            == 2
        )
        await db.execute("SET CONSTRAINTS ALL IMMEDIATE")
    finally:
        if db.is_in_transaction():
            await tx.rollback()


async def test_reversal_is_a_new_entry_and_nets_to_zero(db: asyncpg.Connection) -> None:
    tx = db.transaction()
    await tx.start()
    try:
        cooperative_id, debit_account, credit_account = await create_tenant(db)
        original = await post_entry(db, cooperative_id, debit_account, credit_account, 37)
        reversal = await post_entry(
            db,
            cooperative_id,
            debit_account,
            credit_account,
            37,
            reverses_entry_id=original,
            lines=[
                {"account_id": str(debit_account), "debit_kobo": 0, "credit_kobo": 37},
                {"account_id": str(credit_account), "debit_kobo": 37, "credit_kobo": 0},
            ],
        )
        assert reversal != original
        assert (
            await db.fetchval(
                """
            SELECT sum(debit_kobo) - sum(credit_kobo)
              FROM journal_lines
             WHERE cooperative_id = $1 AND entry_id IN ($2, $3)
            """,
                cooperative_id,
                original,
                reversal,
            )
            == 0
        )
        await db.execute("SET CONSTRAINTS ALL IMMEDIATE")
    finally:
        if db.is_in_transaction():
            await tx.rollback()


@pytest.mark.property
@given(st.lists(st.integers(min_value=1, max_value=10**9), min_size=1, max_size=5))
def test_hypothesis_valid_posted_entries_remain_balanced(amounts: list[int]) -> None:
    asyncio.run(_assert_entries_balance(amounts))


async def _assert_entries_balance(amounts: list[int]) -> None:
    conn = await asyncpg.connect(asyncpg_dsn(TEST_APP_URL))
    tx = conn.transaction()
    await tx.start()
    try:
        assert await conn.fetchval("SELECT current_user") == "ajo_app"
        cooperative_id, debit_account, credit_account = await create_tenant(conn)
        for amount in amounts:
            await post_entry(conn, cooperative_id, debit_account, credit_account, amount)
        await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        totals = await conn.fetchrow(
            """
            SELECT sum(debit_kobo) AS debits, sum(credit_kobo) AS credits
              FROM journal_lines WHERE cooperative_id = $1
            """,
            cooperative_id,
        )
        assert totals["debits"] == totals["credits"]
    finally:
        if conn.is_in_transaction():
            await tx.rollback()
        await conn.close()
