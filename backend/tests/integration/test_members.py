"""Member financial invariants via the ajo_app role on local PostgreSQL."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.db import asyncpg_dsn, dispose_engine
from app.core.errors import Conflict
from app.domains.auth.service import CurrentSession
from app.domains.members import service
from tests.conftest import create_tenant

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
async def reset_engine():
    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture
async def db(test_database) -> asyncpg.Connection:
    conn = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    assert await conn.fetchval("SELECT current_user") == "ajo_app"
    yield conn
    await conn.close()


async def setup_tenant(conn: asyncpg.Connection):
    seed = await create_tenant(conn, name="Member test", slug_prefix="member", include_member=True)
    cid, uid, other = seed.cooperative_id, seed.user_ids[0], seed.user_ids[1]
    ids = {}
    for code, name, account_type in (
        ("bank", "Bank", "asset"),
        ("member_savings", "Savings", "liability"),
        ("member_shares", "Shares", "equity"),
        ("dues_income", "Dues", "income"),
    ):
        aid = uuid.uuid4()
        ids[code] = aid
        await conn.execute(
            "INSERT INTO accounts(id,cooperative_id,code,name,account_type) VALUES($1,$2,$3,$4,$5)",
            aid,
            cid,
            code,
            name,
            account_type,
        )
    assert seed.member_id is not None
    return cid, uid, other, seed.member_id, ids


def session(cid: uuid.UUID, uid: uuid.UUID, role: str = "officer") -> CurrentSession:
    return CurrentSession(
        cid, "member-test", uuid.uuid4(), uid, "staff@example.test", (role,), bytes(32)
    )


async def test_balance_derived_from_ledger_after_contribution_and_withdrawal(
    db: asyncpg.Connection,
) -> None:
    cid, uid, treasurer, mid, _ = await setup_tenant(db)
    posted = session(cid, uid)
    await service.record_contribution(posted, mid, "savings", 10000, "contrib-one")
    request_id = await service.request_withdrawal(posted, mid, 3000, "withdraw-one")
    await service.approve_withdrawal(session(cid, treasurer, "treasurer"), request_id)
    assert await service.member_balances(cid, mid) == {
        "savings_kobo": 7000,
        "shares_kobo": 0,
        "dues_paid_kobo": 0,
        "locked_kobo": 0,
        "available_kobo": 7000,
    }


async def test_withdrawal_beyond_available_rejected(db: asyncpg.Connection) -> None:
    cid, uid, _, mid, _ = await setup_tenant(db)
    with pytest.raises(Conflict, match="exceeds available"):
        await service.request_withdrawal(session(cid, uid), mid, 1, "too-much")


async def test_same_user_withdrawal_approval_rejected(db: asyncpg.Connection) -> None:
    cid, uid, _, mid, _ = await setup_tenant(db)
    posted = session(cid, uid)
    await service.record_contribution(posted, mid, "savings", 1000, "same-approval-contribution")
    request_id = await service.request_withdrawal(posted, mid, 1000, "same-approval-request")
    with pytest.raises(Conflict, match="different user"):
        await service.approve_withdrawal(session(cid, uid, "admin"), request_id)


async def test_concurrent_withdrawal_approvals_cannot_overdraw_member_savings(
    db: asyncpg.Connection,
) -> None:
    cid, officer, treasurer, member, _ = await setup_tenant(db)
    await service.record_contribution(
        session(cid, officer), member, "savings", 10_000, "seed-savings"
    )
    requests = [
        await service.request_withdrawal(session(cid, officer), member, 7_000, f"withdraw-{i}")
        for i in range(2)
    ]

    results = await asyncio.gather(
        *(
            service.approve_withdrawal(session(cid, treasurer, "treasurer"), request_id)
            for request_id in requests
        ),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, Conflict) for result in results) == 1
    assert (await service.member_balances(cid, member))["savings_kobo"] == 3_000
    entries = await db.fetch(
        """SELECT e.id,e.source_type,a.code,l.debit_kobo,l.credit_kobo
           FROM journal_entries e JOIN journal_lines l
             ON l.cooperative_id=e.cooperative_id AND l.entry_id=e.id
           JOIN accounts a ON a.cooperative_id=l.cooperative_id AND a.id=l.account_id
           WHERE e.cooperative_id=$1 AND e.source_type='withdrawal'
           ORDER BY e.id,l.line_number""",
        cid,
    )
    assert {(row["code"], row["debit_kobo"], row["credit_kobo"]) for row in entries} == {
        ("member_savings", 7_000, 0),
        ("bank", 0, 7_000),
    }
    assert len({row["id"] for row in entries}) == 1


async def test_contribution_replay_posts_once(db: asyncpg.Connection) -> None:
    cid, uid, _, mid, _ = await setup_tenant(db)
    current = session(cid, uid)
    first = await service.record_contribution(current, mid, "savings", 500, "replay-key")
    second = await service.record_contribution(current, mid, "savings", 500, "replay-key")
    assert first == second
    assert (await service.member_balances(cid, mid))["savings_kobo"] == 500


async def test_rls_isolates_members(db: asyncpg.Connection) -> None:
    cid, _, _, mid, _ = await setup_tenant(db)
    other_tenant = await create_tenant(db, name="Other tenant", slug_prefix="other", roles=())
    other_cid = other_tenant.cooperative_id
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(other_cid))
        assert (
            await db.fetchval(
                "SELECT count(*) FROM members WHERE cooperative_id=$1 AND id=$2", cid, mid
            )
            == 0
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await db.execute(
                "INSERT INTO members(cooperative_id,member_no,full_name,phone_e164) VALUES($1,'x','X','+12345678')",
                cid,
            )


async def test_bank_account_number_never_appears_in_api_output_or_logs(
    db: asyncpg.Connection, caplog
) -> None:
    cid, uid, _, mid, _ = await setup_tenant(db)
    marker = "encrypted-marker-must-stay-private"
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        await db.execute(
            "INSERT INTO bank_accounts(cooperative_id,member_id,bank_code,account_number_encrypted,last4,account_name) VALUES($1,$2,'001',$3,'4321','Member')",
            cid,
            mid,
            marker,
        )
        await db.execute("UPDATE members SET staff_no='STAFF-PREFIX' WHERE id=$1", mid)
    from app.api.members import current_session
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[current_session] = lambda: session(cid, uid)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/members/{mid}")
        search_response = await client.get("/api/v1/members?search=staff-pre")
    assert response.status_code == 200
    assert response.json()["bank_accounts"][0]["last4"] == "4321"
    assert marker not in response.text and marker not in caplog.text
    assert search_response.json()[0]["id"] == str(mid)


async def test_csv_dry_run_writes_nothing(db: asyncpg.Connection) -> None:
    cid, uid, _, _, _ = await setup_tenant(db)
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        before = await db.fetchval("SELECT count(*) FROM members WHERE cooperative_id=$1", cid)
    result = await service.import_members(
        session(cid, uid), "member_no,full_name,phone_e164\nM-2,Second,+2348012345678\n", False
    )
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id',$1,true)", str(cid))
        after = await db.fetchval("SELECT count(*) FROM members WHERE cooperative_id=$1", cid)
    assert result["valid_rows"] == 1 and result["imported"] == 0
    assert before == after
