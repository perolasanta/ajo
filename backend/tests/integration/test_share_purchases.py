"""Share-purchase request and approval safeguards on local PostgreSQL."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import asyncpg
import pytest

from app.core.db import asyncpg_dsn, dispose_engine
from app.core.errors import Conflict, UnprocessableEntity
from app.domains.auth.service import CurrentSession
from app.domains.share_purchases.service import approve_purchase, request_purchase
from tests.conftest import create_tenant

pytestmark = pytest.mark.db


@dataclass(frozen=True)
class ShareContext:
    cooperative_id: uuid.UUID
    user_ids: tuple[uuid.UUID, ...]
    member_id: uuid.UUID
    bank_account_id: uuid.UUID
    savings_account_id: uuid.UUID
    shares_account_id: uuid.UUID


def session(cid: uuid.UUID, uid: uuid.UUID, *roles: str) -> CurrentSession:
    return CurrentSession(
        cid, "share-test", uuid.uuid4(), uid, "staff@example.test", roles, bytes(32)
    )


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


@pytest.fixture
async def share_context(db: asyncpg.Connection) -> ShareContext:
    seed = await create_tenant(
        db,
        name="Share purchase test",
        slug_prefix="share-purchase",
        roles=("officer", "treasurer"),
        include_member=True,
        member_status="active",
    )
    assert seed.member_id is not None
    cid = seed.cooperative_id
    bank_account_id, savings_account_id, shares_account_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    await db.executemany(
        "INSERT INTO accounts(cooperative_id,id,code,name,account_type) VALUES($1,$2,$3,$4,$5)",
        [
            (cid, bank_account_id, "bank", "Bank", "asset"),
            (cid, savings_account_id, "member_savings", "Member savings", "liability"),
            (cid, shares_account_id, "member_shares", "Member shares", "equity"),
        ],
    )
    return ShareContext(
        cid,
        seed.user_ids,
        seed.member_id,
        bank_account_id,
        savings_account_id,
        shares_account_id,
    )


async def post_savings_change(
    db: asyncpg.Connection,
    base: ShareContext,
    amount_kobo: int,
    *,
    withdrawal: bool = False,
) -> None:
    source_id = uuid.uuid4()
    lines = (
        [
            {
                "account_id": str(base.savings_account_id),
                "member_id": str(base.member_id),
                "debit_kobo": amount_kobo,
            },
            {
                "account_id": str(base.bank_account_id),
                "member_id": str(base.member_id),
                "credit_kobo": amount_kobo,
            },
        ]
        if withdrawal
        else [
            {
                "account_id": str(base.bank_account_id),
                "member_id": str(base.member_id),
                "debit_kobo": amount_kobo,
            },
            {
                "account_id": str(base.savings_account_id),
                "member_id": str(base.member_id),
                "credit_kobo": amount_kobo,
            },
        ]
    )
    await db.fetchval(
        """SELECT post_journal_entry($1,current_date,$2,$3,'Share purchase test savings',$4,$5,$6::jsonb,NULL)""",
        base.cooperative_id,
        "test_savings_withdrawal" if withdrawal else "test_savings",
        source_id,
        base.user_ids[0],
        f"share-test-{source_id}",
        json.dumps(lines),
    )


@pytest.mark.parametrize("shares_count", [0, -1])
async def test_request_rejects_non_positive_share_counts(shares_count: int) -> None:
    with pytest.raises(UnprocessableEntity, match="positive") as error:
        await request_purchase(
            session(uuid.uuid4(), uuid.uuid4(), "officer"), uuid.uuid4(), shares_count
        )
    assert error.value.status_code == 422


async def test_request_rejects_total_above_current_savings(
    db: asyncpg.Connection, share_context: ShareContext
) -> None:
    await post_savings_change(db, share_context, 100)

    with pytest.raises(UnprocessableEntity, match="exceeds.*available savings") as error:
        await request_purchase(
            session(share_context.cooperative_id, share_context.user_ids[0], "officer"),
            share_context.member_id,
            2,
        )

    assert error.value.status_code == 422
    assert (
        await db.fetchval(
            "SELECT count(*) FROM share_purchase_requests WHERE cooperative_id=$1",
            share_context.cooperative_id,
        )
        == 0
    )


async def test_request_allows_total_exactly_equal_to_current_savings(
    db: asyncpg.Connection, share_context: ShareContext
) -> None:
    await post_savings_change(db, share_context, 100)

    request_id = await request_purchase(
        session(share_context.cooperative_id, share_context.user_ids[0], "officer"),
        share_context.member_id,
        1,
    )

    row = await db.fetchrow(
        "SELECT amount_kobo,status FROM share_purchase_requests WHERE cooperative_id=$1 AND id=$2",
        share_context.cooperative_id,
        request_id,
    )
    assert row is not None
    assert row["amount_kobo"] == 100
    assert row["status"] == "pending"


async def test_approval_rechecks_savings_after_request(
    db: asyncpg.Connection, share_context: ShareContext
) -> None:
    await post_savings_change(db, share_context, 500)
    request_id = await request_purchase(
        session(share_context.cooperative_id, share_context.user_ids[0], "officer"),
        share_context.member_id,
        3,
    )
    await post_savings_change(db, share_context, 201, withdrawal=True)

    with pytest.raises(Conflict, match="savings are insufficient"):
        await approve_purchase(
            session(share_context.cooperative_id, share_context.user_ids[1], "treasurer"),
            request_id,
        )

    assert (
        await db.fetchval(
            "SELECT status FROM share_purchase_requests WHERE cooperative_id=$1 AND id=$2",
            share_context.cooperative_id,
            request_id,
        )
        == "pending"
    )
    assert (
        await db.fetchval(
            "SELECT count(*) FROM journal_entries WHERE cooperative_id=$1 AND source_type='share_purchase' AND source_id=$2",
            share_context.cooperative_id,
            request_id,
        )
        == 0
    )
