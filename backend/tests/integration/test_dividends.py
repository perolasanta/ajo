"""Integration tests for dividend runs and allocations."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import asyncpg
import pytest

from app.core.db import asyncpg_dsn, dispose_engine
from app.core.errors import Conflict, Forbidden
from app.domains.auth.service import CurrentSession
from app.domains.dividends import service as dividend_service
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


async def setup_dividend_tenant(
    conn: asyncpg.Connection,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Set up a test tenant with treasurer, admin, officer, and auditor roles."""
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
    auditor_id = seed.user_ids[3] if len(seed.user_ids) > 3 else treasurer_id

    return cid, treasurer_id, admin_id, officer_id, auditor_id


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
                declared_by_meeting_id=uuid.uuid4(),
                pool_kobo=1_000_000,
            )

    async def test_prepare_dividend_run_creates_run_with_unprocessed_status(
        self, db: asyncpg.Connection
    ) -> None:
        """Preparing a dividend run creates it with status='unprocessed'."""
        cid, treasurer_id, admin_id, officer_id = await setup_dividend_tenant(db)
        treasurer = treasurer_session(cid, treasurer_id)

        record_date = date(2025, 12, 31)
        pool_kobo = 1_000_000
        meeting_id = uuid.uuid4()

        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=record_date,
            declared_by_meeting_id=meeting_id,
            pool_kobo=pool_kobo,
        )

        assert run["status"] == "unprocessed"
        assert run["record_date"] == record_date.isoformat()
        assert run["declared_by_meeting_id"] == str(meeting_id)
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
            declared_by_meeting_id=uuid.uuid4(),
            pool_kobo=1_000_000,
        )

        # Treasurer cannot approve
        with pytest.raises(Forbidden):
            await dividend_service.approve_dividend_run(treasurer, uuid.UUID(run["run_id"]))

    async def test_approve_changes_status_to_approved(self, db: asyncpg.Connection) -> None:
        """Approving a run changes status from 'unprocessed' to 'approved'."""
        cid, treasurer_id, admin_id, officer_id = await setup_dividend_tenant(db)
        treasurer = treasurer_session(cid, treasurer_id)
        admin = admin_session(cid, admin_id)

        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            declared_by_meeting_id=uuid.uuid4(),
            pool_kobo=1_000_000,
        )

        approved_run = await dividend_service.approve_dividend_run(admin, uuid.UUID(run["run_id"]))

        assert approved_run["status"] == "approved"
        assert approved_run["approved_by"] == str(admin_id)

    async def test_approve_only_works_on_unprocessed_run(self, db: asyncpg.Connection) -> None:
        """Can only approve a run with status='unprocessed'."""
        cid, treasurer_id, admin_id, officer_id = await setup_dividend_tenant(db)
        treasurer = treasurer_session(cid, treasurer_id)
        admin = admin_session(cid, admin_id)

        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            declared_by_meeting_id=uuid.uuid4(),
            pool_kobo=1_000_000,
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
            declared_by_meeting_id=uuid.uuid4(),
            pool_kobo=1_000_000,
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
            declared_by_meeting_id=uuid.uuid4(),
            pool_kobo=1_000_000,
        )

        # Officer has no permission
        with pytest.raises(Forbidden):
            await dividend_service.cancel_dividend_run(officer, uuid.UUID(run["run_id"]))
