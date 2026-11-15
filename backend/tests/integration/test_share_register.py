"""Tenant isolation, search, and pagination for the share register."""

from __future__ import annotations

import uuid
from datetime import date

import asyncpg
import pytest

from app.core.db import asyncpg_dsn, dispose_engine
from app.domains.share_purchases.reads import list_share_register
from app.domains.share_purchases.service import approve_purchase, request_purchase
from tests.conftest import create_tenant
from tests.integration.test_share_purchases import ShareContext, post_savings_change, session

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
async def reset_engine():
    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture
async def db(test_database) -> asyncpg.Connection:
    connection = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    yield connection
    await connection.close()


@pytest.fixture
async def share_context(db: asyncpg.Connection) -> ShareContext:
    seed = await create_tenant(
        db,
        name="Share register test",
        slug_prefix="share-register",
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


async def test_register_searches_and_pages_ledger_holdings_without_cross_tenant_data(
    db: asyncpg.Connection, share_context: ShareContext
) -> None:
    await post_savings_change(db, share_context, 1_000)
    request_id = await request_purchase(
        session(share_context.cooperative_id, share_context.user_ids[0], "officer"),
        share_context.member_id,
        3,
    )
    await approve_purchase(
        session(share_context.cooperative_id, share_context.user_ids[1], "treasurer"),
        request_id,
    )
    await db.execute(
        "UPDATE cooperatives SET share_unit_price_naira=2 WHERE id=$1",
        share_context.cooperative_id,
    )

    first_page = await list_share_register(
        session(share_context.cooperative_id, share_context.user_ids[0], "officer"),
        search="Test Member",
        page=1,
        page_size=1,
    )
    second_page = await list_share_register(
        session(share_context.cooperative_id, share_context.user_ids[0], "officer"),
        search="Test Member",
        page=2,
        page_size=1,
    )

    assert first_page["total"] == 1
    assert first_page["items"][0]["member_name"] == "Test Member"
    assert first_page["items"][0]["shares_held"] == 3
    assert first_page["items"][0]["value_kobo"] == 600
    assert first_page["items"][0]["last_purchase_date"] == date.today()
    assert first_page["total_shares"] == 3
    assert first_page["members_holding_shares"] == 1
    assert first_page["current_unit_price_naira"] == 2
    assert second_page["items"] == [] and second_page["total"] == 1

    other = await create_tenant(
        db,
        name="Other cooperative",
        slug_prefix="share-register-other",
        roles=("admin",),
        include_member=True,
    )
    isolated = await list_share_register(
        session(other.cooperative_id, other.user_ids[0], "admin"),
        search=None,
        page=1,
        page_size=10,
    )
    assert isolated["items"] == []
    assert isolated["total"] == 0
    assert isolated["total_shares"] == 0
    assert isolated["members_holding_shares"] == 0
