"""Red-first tests for Unit 2: dividend display fixes.

These verify:
- dividend_lines.shares_held stores a share COUNT, not kobo value.
- The count in dividend_lines matches what list_share_register reports for the same member.
- meeting_or_resolution is text; no UUID-looking value is accepted as a bare UUID.
- Pro-rata maths is unchanged: pool distributes proportionally.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

import asyncpg
import pytest

from app.core.db import asyncpg_dsn, dispose_engine
from app.domains.auth.service import CurrentSession
from app.domains.dividends import service as dividend_service
from app.domains.share_purchases import reads as share_reads
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


async def _setup_tenant(
    conn: asyncpg.Connection,
    *,
    shares_kobo: int = 100_000,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Return (cid, treasurer_id, admin_id, member_id).

    Cooperative has default share_unit_price_naira=1 (1 naira = 100 kobo per share).
    shares_kobo=100_000 means the member holds 1,000 shares.
    """
    seed = await create_tenant(
        conn,
        name="Display test",
        slug_prefix="disp",
        roles=("treasurer", "admin"),
        include_member=True,
        member_status="active",
    )
    cid = seed.cooperative_id
    treasurer_id = seed.user_ids[0]
    admin_id = seed.user_ids[1]
    assert seed.member_id is not None
    member_id = seed.member_id

    divs_acc_id = uuid.uuid4()
    savings_acc_id = uuid.uuid4()
    shares_acc_id = uuid.uuid4()
    bank_acc_id = uuid.uuid4()
    await conn.executemany(
        "INSERT INTO accounts(cooperative_id,id,code,name,account_type) VALUES($1,$2,$3,$4,$5)",
        [
            (cid, divs_acc_id, "dividends_declared", "Dividends declared", "equity"),
            (cid, savings_acc_id, "member_savings", "Member savings", "liability"),
            (cid, shares_acc_id, "member_shares", "Member shares", "equity"),
            (cid, bank_acc_id, "bank", "Bank", "asset"),
        ],
    )

    # Seed a share purchase so the member holds shares.
    # Uses post_journal_entry with source_type='share_purchase' so the share
    # register query (which JOINs share_purchase_requests) can find it.
    purchase_id = uuid.uuid4()
    await conn.execute(
        """INSERT INTO share_purchase_requests
           (id, cooperative_id, member_id, shares_count, unit_price_naira, amount_kobo, requested_by, status)
           VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending')""",
        purchase_id,
        cid,
        member_id,
        shares_kobo // 100,  # shares_count = kobo / (1 naira * 100)
        1,  # unit_price_naira = 1
        shares_kobo,
        treasurer_id,
    )
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
        purchase_id,
        treasurer_id,
        f"share-disp:{cid}",
        lines_json,
    )
    return cid, treasurer_id, admin_id, member_id


def _treasurer(cid: uuid.UUID, uid: uuid.UUID) -> CurrentSession:
    return CurrentSession(
        cid, "disp-test", uuid.uuid4(), uid, "treasurer@test", ("treasurer",), bytes(32)
    )


def _admin(cid: uuid.UUID, uid: uuid.UUID) -> CurrentSession:
    return CurrentSession(cid, "disp-test", uuid.uuid4(), uid, "admin@test", ("admin",), bytes(32))


def _register_session(cid: uuid.UUID, uid: uuid.UUID) -> CurrentSession:
    return CurrentSession(cid, "disp-test", uuid.uuid4(), uid, "admin@test", ("admin",), bytes(32))


class TestShareCountStoredNotKobo:
    """shares_held in dividend_lines must be a count, never a kobo value."""

    async def test_shares_held_is_count_not_kobo(self, db: asyncpg.Connection) -> None:
        """With 1,000 shares at 1 naira each (100_000 kobo), shares_held must be 1000."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant(db, shares_kobo=100_000)
        run = await dividend_service.prepare_dividend_run(
            _treasurer(cid, treasurer_id),
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=1_000,
            financial_year=2025,
        )
        run_detail = await dividend_service.get_dividend_run(
            _treasurer(cid, treasurer_id), uuid.UUID(run["run_id"])
        )
        lines = run_detail["items"]
        assert lines, "No dividend lines were created"
        member_line = next((li for li in lines if li["member_id"] == str(member_id)), None)
        assert member_line is not None, "No line for the test member"
        # 100_000 kobo / (1 naira * 100) = 1_000 shares
        assert member_line["shares_held"] == 1_000, (
            f"shares_held should be 1000 (share count) but got {member_line['shares_held']}; "
            "did the service store kobo value instead?"
        )

    async def test_shares_held_matches_share_register(self, db: asyncpg.Connection) -> None:
        """dividend_lines.shares_held agrees with list_share_register for the same member."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant(db, shares_kobo=100_000)
        run = await dividend_service.prepare_dividend_run(
            _treasurer(cid, treasurer_id),
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM 2025",
            pool_kobo=5_000,
            financial_year=2025,
        )
        run_detail = await dividend_service.get_dividend_run(
            _treasurer(cid, treasurer_id), uuid.UUID(run["run_id"])
        )
        dividend_lines = run_detail["items"]
        assert dividend_lines, "No dividend lines"
        dividend_shares = {li["member_id"]: li["shares_held"] for li in dividend_lines}

        register = await share_reads.list_share_register(
            _register_session(cid, admin_id),
            search=None,
            page=1,
            page_size=100,
        )
        register_shares = {item["member_id"]: item["shares_held"] for item in register["items"]}

        for mid, div_count in dividend_shares.items():
            assert mid in register_shares, f"Member {mid} in dividend lines but not in register"
            assert div_count == register_shares[mid], (
                f"Member {mid}: dividend shares_held={div_count} "
                f"but register shows {register_shares[mid]}"
            )

    async def test_pro_rata_allocation_unchanged(self, db: asyncpg.Connection) -> None:
        """With equal shares, pool divides evenly (largest-remainder for rounding).
        1,1,1 share → pool 1000 kobo → 334, 333, 333.
        """
        seed = await create_tenant(
            db,
            name="Pro-rata test",
            slug_prefix="prt",
            roles=("treasurer", "admin"),
            include_member=False,
        )
        cid = seed.cooperative_id
        treasurer_id = seed.user_ids[0]

        divs_acc_id = uuid.uuid4()
        savings_acc_id = uuid.uuid4()
        shares_acc_id = uuid.uuid4()
        bank_acc_id = uuid.uuid4()
        await db.executemany(
            "INSERT INTO accounts(cooperative_id,id,code,name,account_type) VALUES($1,$2,$3,$4,$5)",
            [
                (cid, divs_acc_id, "dividends_declared", "Dividends declared", "equity"),
                (cid, savings_acc_id, "member_savings", "Member savings", "liability"),
                (cid, shares_acc_id, "member_shares", "Member shares", "equity"),
                (cid, bank_acc_id, "bank", "Bank", "asset"),
            ],
        )

        # Create 3 members, each with 100 kobo = 1 share
        member_ids: list[uuid.UUID] = []
        for i in range(3):
            mid = uuid.uuid4()
            member_ids.append(mid)
            await db.execute(
                """INSERT INTO members(id,cooperative_id,member_no,full_name,phone_e164,status)
                   VALUES($1,$2,$3,$4,'+234800000000'||$5,'active')""",
                mid,
                cid,
                f"M-{i}",
                f"Member {i}",
                str(i),
            )
            # 1 share = 100 kobo (unit_price_naira=1)
            purchase_id = uuid.uuid4()
            await db.execute(
                """INSERT INTO share_purchase_requests
                   (id,cooperative_id,member_id,shares_count,unit_price_naira,amount_kobo,requested_by,status)
                   VALUES($1,$2,$3,1,1,100,$4,'pending')""",
                purchase_id,
                cid,
                mid,
                treasurer_id,
            )
            lines_json = json.dumps(
                [
                    {"account_id": str(bank_acc_id), "debit_kobo": 100, "credit_kobo": 0},
                    {
                        "account_id": str(shares_acc_id),
                        "member_id": str(mid),
                        "debit_kobo": 0,
                        "credit_kobo": 100,
                    },
                ]
            )
            await db.execute("SELECT set_config('app.cooperative_id', $1, false)", str(cid))
            await db.fetchval(
                "SELECT post_journal_entry($1,$2,'share_purchase',$3,'Shares',$4,$5,$6::jsonb,NULL)",
                cid,
                date(2025, 1, 1),
                purchase_id,
                treasurer_id,
                f"share-prt:{cid}:{i}",
                lines_json,
            )

        treasurer = _treasurer(cid, treasurer_id)
        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            meeting_or_resolution=None,
            pool_kobo=1_000,
            financial_year=2025,
        )
        run_detail = await dividend_service.get_dividend_run(treasurer, uuid.UUID(run["run_id"]))
        lines = run_detail["items"]
        assert lines
        amounts = sorted(li["amount_kobo"] for li in lines)
        assert sum(amounts) == 1_000, "Lines must sum to pool"
        assert amounts == [333, 333, 334], f"Expected [333, 333, 334] got {amounts}"
        # Each member has exactly 1 share
        for line in lines:
            assert line["shares_held"] == 1, (
                f"Each member has 1 share but got {line['shares_held']}"
            )


class TestMeetingOrResolution:
    """meeting_or_resolution is optional text; UUIDs are stored as text, not UUID type."""

    async def test_meeting_or_resolution_stores_text(self, db: asyncpg.Connection) -> None:
        """meeting_or_resolution is stored and returned as-is."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant(db)
        run = await dividend_service.prepare_dividend_run(
            _treasurer(cid, treasurer_id),
            record_date=date(2025, 12, 31),
            meeting_or_resolution="AGM November 2025",
            pool_kobo=500,
            financial_year=2025,
        )
        assert run["meeting_or_resolution"] == "AGM November 2025"

    async def test_meeting_or_resolution_can_be_none(self, db: asyncpg.Connection) -> None:
        """meeting_or_resolution is optional (can be None)."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant(db)
        run = await dividend_service.prepare_dividend_run(
            _treasurer(cid, treasurer_id),
            record_date=date(2025, 12, 31),
            meeting_or_resolution=None,
            pool_kobo=500,
            financial_year=2025,
        )
        assert run["meeting_or_resolution"] is None

    async def test_uuid_looking_string_stored_as_text(self, db: asyncpg.Connection) -> None:
        """A UUID-formatted string passed as meeting_or_resolution is stored as text, not UUID."""
        cid, treasurer_id, admin_id, member_id = await _setup_tenant(db)
        uuid_str = str(uuid.uuid4())
        run = await dividend_service.prepare_dividend_run(
            _treasurer(cid, treasurer_id),
            record_date=date(2025, 12, 31),
            meeting_or_resolution=uuid_str,
            pool_kobo=500,
            financial_year=2025,
        )
        # The API should return it as text; the frontend must NOT display it as a bare UUID label.
        # This test documents that the backend stores/returns whatever string is given.
        assert run["meeting_or_resolution"] == uuid_str
        # Verify the returned dict key name: no 'declared_by_meeting_id' key
        assert "declared_by_meeting_id" not in run, (
            "Old field 'declared_by_meeting_id' must not appear in service response"
        )
