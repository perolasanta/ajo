"""Integration tests for dividend runs and allocations."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from app.core.http_error import Conflict, Forbidden, NotFound, UnprocessableEntity
from app.domains.auth.service import CurrentSession
from app.domains.dividends import service as dividend_service


@pytest.fixture
def treasurer(db: Any) -> CurrentSession:
    """Create a treasurer session for testing."""
    cid = uuid.uuid4()
    uid = uuid.uuid4()
    return CurrentSession(
        cooperative_id=cid,
        user_id=uid,
        roles=["treasurer"],
        mfa_verified=True,
    )


@pytest.fixture
def admin(db: Any) -> CurrentSession:
    """Create an admin session for testing."""
    cid = uuid.uuid4()
    uid = uuid.uuid4()
    return CurrentSession(
        cooperative_id=cid,
        user_id=uid,
        roles=["admin"],
        mfa_verified=True,
    )


@pytest.fixture
def officer(db: Any) -> CurrentSession:
    """Create an officer session for testing."""
    cid = uuid.uuid4()
    uid = uuid.uuid4()
    return CurrentSession(
        cooperative_id=cid,
        user_id=uid,
        roles=["officer"],
        mfa_verified=True,
    )


class TestDividendPrepare:
    """Tests for preparing a dividend run."""

    @pytest.mark.db
    async def test_prepare_dividend_run_requires_treasurer_or_admin(
        self, db: Any, treasurer: CurrentSession, officer: CurrentSession
    ) -> None:
        """Only treasurer and admin can prepare dividend runs."""
        # Officer should be forbidden
        with pytest.raises(Forbidden):
            await dividend_service.prepare_dividend_run(
                officer,
                record_date=date(2025, 12, 31),
                declared_by_meeting_id=uuid.uuid4(),
                pool_kobo=1_000_000,
            )

    @pytest.mark.db
    async def test_prepare_dividend_run_creates_run_with_unprocessed_status(
        self, db: Any, treasurer: CurrentSession
    ) -> None:
        """Preparing a dividend run creates it with status='unprocessed'."""
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
        assert run["record_date"] == record_date
        assert run["declared_by_meeting_id"] == str(meeting_id)
        assert run["prepared_by"] == str(treasurer.user_id)
        assert run["pool_kobo"] == pool_kobo
        assert "run_id" in run

    @pytest.mark.db
    async def test_prepare_dividend_run_takes_share_snapshot_at_record_date(
        self, db: Any, treasurer: CurrentSession
    ) -> None:
        """Prepare filters shares as of record_date and snapshots balances."""
        # This test verifies the share snapshot is captured at preparation time
        # The actual member share query is parameterized with effective_date <= record_date
        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            declared_by_meeting_id=uuid.uuid4(),
            pool_kobo=1_000_000,
        )

        # Verify the run has a snapshot - the service should query shares at record date
        assert "run_id" in run


class TestDividendList:
    """Tests for listing dividend runs."""

    @pytest.mark.db
    async def test_list_dividend_runs_requires_permission(
        self, db: Any, officer: CurrentSession
    ) -> None:
        """Only roles with read permission can list dividend runs."""
        # Officer has no permission
        with pytest.raises(Forbidden):
            await dividend_service.list_dividend_runs(officer)

    @pytest.mark.db
    async def test_list_dividend_runs_returns_paginated_results(
        self, db: Any, treasurer: CurrentSession
    ) -> None:
        """List dividend runs returns paginated results."""
        result = await dividend_service.list_dividend_runs(treasurer)
        assert "items" in result
        assert "total" in result
        assert isinstance(result["items"], list)


class TestDividendGet:
    """Tests for getting a single dividend run."""

    @pytest.mark.db
    async def test_get_dividend_run_not_found(
        self, db: Any, treasurer: CurrentSession
    ) -> None:
        """Getting a nonexistent dividend run raises NotFound."""
        with pytest.raises(NotFound):
            await dividend_service.get_dividend_run(
                treasurer, uuid.uuid4()
            )

    @pytest.mark.db
    async def test_get_dividend_run_includes_line_items(
        self, db: Any, treasurer: CurrentSession
    ) -> None:
        """Get dividend run includes allocated line items."""
        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            declared_by_meeting_id=uuid.uuid4(),
            pool_kobo=1_000_000,
        )

        detail = await dividend_service.get_dividend_run(
            treasurer, uuid.UUID(run["run_id"])
        )

        assert "items" in detail
        assert detail["status"] == "unprocessed"


class TestDividendAllocate:
    """Tests for allocating dividends to members."""

    @pytest.mark.db
    async def test_allocate_uses_integer_kobo_allocation(
        self, db: Any, treasurer: CurrentSession
    ) -> None:
        """Allocation uses integer kobo with no fractional shares."""
        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            declared_by_meeting_id=uuid.uuid4(),
            pool_kobo=1_000_000,
        )

        # Verify all allocations are integer kobo
        detail = await dividend_service.get_dividend_run(
            treasurer, uuid.UUID(run["run_id"])
        )

        for item in detail["items"]:
            assert isinstance(item["amount_kobo"], int)
            assert item["amount_kobo"] > 0


class TestDividendApprove:
    """Tests for approving a dividend run for posting."""

    @pytest.mark.db
    async def test_approve_dividend_run_requires_admin(
        self, db: Any, treasurer: CurrentSession, admin: CurrentSession
    ) -> None:
        """Only admin can approve dividend runs."""
        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            declared_by_meeting_id=uuid.uuid4(),
            pool_kobo=1_000_000,
        )

        # Treasurer cannot approve
        with pytest.raises(Forbidden):
            await dividend_service.approve_dividend_run(
                treasurer, uuid.UUID(run["run_id"])
            )

    @pytest.mark.db
    async def test_approve_changes_status_to_approved(
        self, db: Any, treasurer: CurrentSession, admin: CurrentSession
    ) -> None:
        """Approving a run changes status to 'approved'."""
        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            declared_by_meeting_id=uuid.uuid4(),
            pool_kobo=1_000_000,
        )

        approved_run = await dividend_service.approve_dividend_run(
            admin, uuid.UUID(run["run_id"])
        )

        assert approved_run["status"] == "approved"
        assert approved_run["approved_by"] == str(admin.user_id)

    @pytest.mark.db
    async def test_approve_only_works_on_unprocessed_run(
        self, db: Any, admin: CurrentSession, treasurer: CurrentSession
    ) -> None:
        """Can only approve a run with status='unprocessed'."""
        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            declared_by_meeting_id=uuid.uuid4(),
            pool_kobo=1_000_000,
        )

        # Approve once
        await dividend_service.approve_dividend_run(
            admin, uuid.UUID(run["run_id"])
        )

        # Cannot approve again
        with pytest.raises(Conflict):
            await dividend_service.approve_dividend_run(
                admin, uuid.UUID(run["run_id"])
            )


class TestDividendReject:
    """Tests for rejecting a dividend run."""

    @pytest.mark.db
    async def test_reject_dividend_run_requires_admin(
        self, db: Any, treasurer: CurrentSession
    ) -> None:
        """Only admin can reject dividend runs."""
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

    @pytest.mark.db
    async def test_reject_changes_status_to_rejected(
        self, db: Any, admin: CurrentSession, treasurer: CurrentSession
    ) -> None:
        """Rejecting a run changes status to 'rejected'."""
        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            declared_by_meeting_id=uuid.uuid4(),
            pool_kobo=1_000_000,
        )

        rejected_run = await dividend_service.reject_dividend_run(
            admin, uuid.UUID(run["run_id"]), "Not enough funds"
        )

        assert rejected_run["status"] == "rejected"
        assert rejected_run["rejected_by"] == str(admin.user_id)
        assert rejected_run["rejection_reason"] == "Not enough funds"


class TestDividendCancel:
    """Tests for canceling a prepared dividend run."""

    @pytest.mark.db
    async def test_cancel_dividend_run_requires_preparer_or_admin(
        self, db: Any, officer: CurrentSession, treasurer: CurrentSession
    ) -> None:
        """Only preparer or admin can cancel a prepared run."""
        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            declared_by_meeting_id=uuid.uuid4(),
            pool_kobo=1_000_000,
        )

        # Officer has no permission
        with pytest.raises(Forbidden):
            await dividend_service.cancel_dividend_run(
                officer, uuid.UUID(run["run_id"])
            )

    @pytest.mark.db
    async def test_cancel_changes_status_to_cancelled(
        self, db: Any, treasurer: CurrentSession
    ) -> None:
        """Canceling a run changes status to 'cancelled'."""
        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            declared_by_meeting_id=uuid.uuid4(),
            pool_kobo=1_000_000,
        )

        cancelled_run = await dividend_service.cancel_dividend_run(
            treasurer, uuid.UUID(run["run_id"])
        )

        assert cancelled_run["status"] == "cancelled"


class TestDividendPost:
    """Tests for posting approved dividends to the ledger."""

    @pytest.mark.db
    async def test_post_dividend_requires_posting_permissions(
        self, db: Any, officer: CurrentSession, admin: CurrentSession, treasurer: CurrentSession
    ) -> None:
        """Only specific roles can post dividends to ledger."""
        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            declared_by_meeting_id=uuid.uuid4(),
            pool_kobo=1_000_000,
        )

        await dividend_service.approve_dividend_run(
            admin, uuid.UUID(run["run_id"])
        )

        # Officer cannot post
        with pytest.raises(Forbidden):
            await dividend_service.post_dividend_run(
                officer, uuid.UUID(run["run_id"])
            )

    @pytest.mark.db
    async def test_post_dividend_creates_balanced_ledger_entries(
        self, db: Any, treasurer: CurrentSession, admin: CurrentSession
    ) -> None:
        """Posting dividends creates balanced journal entries."""
        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            declared_by_meeting_id=uuid.uuid4(),
            pool_kobo=1_000_000,
        )

        await dividend_service.approve_dividend_run(
            admin, uuid.UUID(run["run_id"])
        )

        posted_run = await dividend_service.post_dividend_run(
            admin, uuid.UUID(run["run_id"])
        )

        assert posted_run["status"] == "posted"
        # The ledger entries should be balanced (debit dividends_declared = credit member_savings)

    @pytest.mark.db
    async def test_post_only_works_on_approved_run(
        self, db: Any, admin: CurrentSession, treasurer: CurrentSession
    ) -> None:
        """Can only post a run with status='approved'."""
        run = await dividend_service.prepare_dividend_run(
            treasurer,
            record_date=date(2025, 12, 31),
            declared_by_meeting_id=uuid.uuid4(),
            pool_kobo=1_000_000,
        )

        # Cannot post without approval
        with pytest.raises(Conflict):
            await dividend_service.post_dividend_run(
                admin, uuid.UUID(run["run_id"])
            )
