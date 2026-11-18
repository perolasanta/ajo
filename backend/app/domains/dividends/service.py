"""Dividend run service: prepare, approve, reject, cancel, and post dividend allocations."""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import transaction
from app.core.errors import Conflict, Forbidden, NotFound
from app.domains.auth.service import CurrentSession
from app.domains.notifications.eligibility import DIVIDEND_APPROVAL
from app.domains.notifications.outbox import enqueue_action_notifications_safely


def _role(current: CurrentSession, allowed: set[str]) -> None:
    """Check that current user has one of the allowed roles."""
    if not allowed.intersection(current.roles):
        raise Forbidden("This role cannot perform this action")


async def prepare_dividend_run(
    current: CurrentSession,
    *,
    record_date: date,
    declared_by_meeting_id: uuid.UUID,
    pool_kobo: int,
) -> dict[str, Any]:
    """Prepare a dividend run with share snapshot at record_date.

    Only treasurer and admin roles are allowed.
    Creates dividend_lines for all active members with shares as of record_date.
    Allocates from pool_kobo proportionally to shares held.

    Args:
        current: Current session (user/cooperative context)
        record_date: Effective date for share snapshot (cutoff for membership)
        declared_by_meeting_id: Meeting that declared the dividend
        pool_kobo: Total dividend pool in integer kobo

    Returns:
        Dividend run details with status='unprocessed'

    Raises:
        Forbidden: If user lacks treasurer or admin role
    """
    _role(current, {"treasurer", "admin"})

    cid = current.cooperative_id
    uid = current.user_id

    async with transaction(cid) as session:
        run_id = uuid.uuid4()

        # Insert the dividend run header
        await session.execute(
            text(
                """INSERT INTO dividend_runs (
                    id, cooperative_id, record_date, declared_by_meeting_id,
                    pool_kobo, prepared_by, status
                ) VALUES (
                    :run_id, :cid, :record_date, :meeting_id,
                    :pool_kobo, :uid, 'unprocessed'
                )"""
            ),
            {
                "run_id": run_id,
                "cid": cid,
                "record_date": record_date,
                "meeting_id": declared_by_meeting_id,
                "pool_kobo": pool_kobo,
                "uid": uid,
            },
        )

        # Get share snapshot at record_date for active members
        # Query: sum of shares per member where member_shares entries were recorded before/at record_date
        shares_result = await session.execute(
            text(
                """WITH member_snapshots AS (
                    SELECT
                        l.member_id,
                        SUM(l.credit_kobo) as shares_kobo
                    FROM journal_lines l
                    JOIN accounts a ON a.id = l.account_id
                    JOIN journal_entries e ON e.id = l.entry_id
                    WHERE l.cooperative_id = :cid
                      AND a.code = 'member_shares'
                      AND e.effective_date <= :record_date
                    GROUP BY l.member_id
                    HAVING SUM(l.credit_kobo) > 0
                )
                SELECT member_id, shares_kobo FROM member_snapshots"""
            ),
            {"cid": cid, "record_date": record_date},
        )

        member_shares = list(shares_result)

        # Calculate total shares and allocate proportionally
        total_shares_kobo = sum(row[1] for row in member_shares)

        if total_shares_kobo > 0:
            # Allocate dividend to each member proportionally
            for member_id, member_shares_kobo in member_shares:
                # Calculate: (member_shares / total_shares) * pool_kobo
                # Using integer arithmetic: (member_shares * pool_kobo) // total_shares
                amount_kobo = (member_shares_kobo * pool_kobo) // total_shares_kobo

                if amount_kobo > 0:
                    await session.execute(
                        text(
                            """INSERT INTO dividend_lines (
                                id, cooperative_id, run_id, member_id,
                                shares_held, amount_kobo
                            ) VALUES (
                                :line_id, :cid, :run_id, :member_id,
                                :shares_held, :amount_kobo
                            )"""
                        ),
                        {
                            "line_id": uuid.uuid4(),
                            "cid": cid,
                            "run_id": run_id,
                            "member_id": member_id,
                            "shares_held": member_shares_kobo,
                            "amount_kobo": amount_kobo,
                        },
                    )

        result = await _get_dividend_run_dict(session, cid, run_id)
        if not result:
            raise RuntimeError(f"Failed to retrieve created dividend run {run_id}")
        await enqueue_action_notifications_safely(session, cid, DIVIDEND_APPROVAL, run_id)
        return result


async def list_dividend_runs(
    current: CurrentSession, *, page: int = 1, page_size: int = 25
) -> dict[str, Any]:
    """List dividend runs for the cooperative.

    Treasurer, admin, and officer roles are allowed read access.

    Args:
        current: Current session (user/cooperative context)
        page: Page number (1-indexed)
        page_size: Number of items per page

    Returns:
        Paginated list of dividend runs

    Raises:
        Forbidden: If user lacks required role
    """
    _role(current, {"treasurer", "admin", "officer", "auditor", "committee"})

    cid = current.cooperative_id
    offset = (page - 1) * page_size

    async with transaction(cid) as session:
        # Count total
        count_result = await session.execute(
            text("SELECT COUNT(*) FROM dividend_runs WHERE cooperative_id = :cid"),
            {"cid": cid},
        )
        total = count_result.scalar() or 0

        # Fetch paginated runs
        runs_result = await session.execute(
            text(
                """SELECT
                    id, record_date, declared_by_meeting_id, pool_kobo,
                    prepared_by, prepared_at, approved_by, approved_at,
                    rejected_by, rejected_at, rejection_reason,
                    posted_by, posted_at, status, created_at, updated_at
                FROM dividend_runs
                WHERE cooperative_id = :cid
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset"""
            ),
            {"cid": cid, "limit": page_size, "offset": offset},
        )

        items = [_row_to_dividend_run_dict(row) for row in runs_result]

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }


async def get_dividend_run(current: CurrentSession, run_id: uuid.UUID) -> dict[str, Any]:
    """Get a single dividend run with its allocated lines.

    Args:
        current: Current session (user/cooperative context)
        run_id: ID of the dividend run

    Returns:
        Dividend run details including allocated lines

    Raises:
        NotFound: If run does not exist in this cooperative
        Forbidden: If user lacks read permission
    """
    _role(current, {"treasurer", "admin", "officer", "auditor", "committee"})

    cid = current.cooperative_id

    async with transaction(cid) as session:
        run_dict = await _get_dividend_run_dict(session, cid, run_id)

        if not run_dict:
            raise NotFound(f"Dividend run {run_id} not found")

        # Fetch dividend lines
        lines_result = await session.execute(
            text(
                """SELECT
                    id, member_id, shares_held, amount_kobo, created_at
                FROM dividend_lines
                WHERE run_id = :run_id AND cooperative_id = :cid
                ORDER BY created_at ASC"""
            ),
            {"run_id": run_id, "cid": cid},
        )

        items = [
            {
                "line_id": str(row[0]),
                "member_id": str(row[1]),
                "shares_held": row[2],
                "amount_kobo": row[3],
                "created_at": row[4].isoformat(),
            }
            for row in lines_result
        ]

        run_dict["items"] = items
        return run_dict


async def approve_dividend_run(current: CurrentSession, run_id: uuid.UUID) -> dict[str, Any]:
    """Approve a dividend run for posting.

    Only admin role is allowed.
    Can only approve a run with status='unprocessed'.

    Args:
        current: Current session (admin user)
        run_id: ID of the dividend run

    Returns:
        Updated dividend run with status='approved'

    Raises:
        Forbidden: If user is not admin
        NotFound: If run does not exist
        Conflict: If run is not in 'unprocessed' status
    """
    _role(current, {"admin"})

    cid = current.cooperative_id
    uid = current.user_id

    async with transaction(cid) as session:
        # Check current status
        status_result = await session.execute(
            text("SELECT status FROM dividend_runs WHERE id = :run_id AND cooperative_id = :cid"),
            {"run_id": run_id, "cid": cid},
        )
        row = status_result.first()

        if not row:
            raise NotFound(f"Dividend run {run_id} not found")

        status = row[0]
        if status != "unprocessed":
            raise Conflict(f"Cannot approve run with status={status}")

        # Update status to approved
        await session.execute(
            text(
                """UPDATE dividend_runs
                   SET status = 'approved', approved_by = :uid, approved_at = CURRENT_TIMESTAMP
                   WHERE id = :run_id AND cooperative_id = :cid"""
            ),
            {"run_id": run_id, "uid": uid, "cid": cid},
        )

        run_dict = await _get_dividend_run_dict(session, cid, run_id)
        if not run_dict:
            raise RuntimeError(f"Failed to retrieve dividend run {run_id}")
        return run_dict


async def reject_dividend_run(
    current: CurrentSession, run_id: uuid.UUID, reason: str
) -> dict[str, Any]:
    """Reject a dividend run.

    Only admin role is allowed.
    Can only reject a run with status='unprocessed'.

    Args:
        current: Current session (admin user)
        run_id: ID of the dividend run
        reason: Reason for rejection

    Returns:
        Updated dividend run with status='rejected'

    Raises:
        Forbidden: If user is not admin
        NotFound: If run does not exist
        Conflict: If run is not in 'unprocessed' status
    """
    _role(current, {"admin"})

    cid = current.cooperative_id
    uid = current.user_id

    async with transaction(cid) as session:
        # Check current status
        status_result = await session.execute(
            text("SELECT status FROM dividend_runs WHERE id = :run_id AND cooperative_id = :cid"),
            {"run_id": run_id, "cid": cid},
        )
        row = status_result.first()

        if not row:
            raise NotFound(f"Dividend run {run_id} not found")

        status = row[0]
        if status != "unprocessed":
            raise Conflict(f"Cannot reject run with status={status}")

        # Update status to rejected
        await session.execute(
            text(
                """UPDATE dividend_runs
                   SET status = 'rejected', rejected_by = :uid, rejected_at = CURRENT_TIMESTAMP,
                       rejection_reason = :reason
                   WHERE id = :run_id AND cooperative_id = :cid"""
            ),
            {"run_id": run_id, "uid": uid, "cid": cid, "reason": reason},
        )

        run_dict = await _get_dividend_run_dict(session, cid, run_id)
        if not run_dict:
            raise RuntimeError(f"Failed to retrieve dividend run {run_id}")
        return run_dict


async def cancel_dividend_run(current: CurrentSession, run_id: uuid.UUID) -> dict[str, Any]:
    """Cancel a prepared dividend run.

    Can be cancelled by the preparer or admin.
    Can only cancel a run with status='unprocessed'.

    Args:
        current: Current session (user)
        run_id: ID of the dividend run

    Returns:
        Updated dividend run with status='cancelled'

    Raises:
        Forbidden: If user is not preparer or admin
        NotFound: If run does not exist
        Conflict: If run is not in 'unprocessed' status
    """
    _role(current, {"treasurer", "admin"})

    cid = current.cooperative_id

    async with transaction(cid) as session:
        # Check current status and preparer
        status_result = await session.execute(
            text(
                "SELECT status, prepared_by FROM dividend_runs WHERE id = :run_id AND cooperative_id = :cid"
            ),
            {"run_id": run_id, "cid": cid},
        )
        row = status_result.first()

        if not row:
            raise NotFound(f"Dividend run {run_id} not found")

        status, prepared_by = row

        if status != "unprocessed":
            raise Conflict(f"Cannot cancel run with status={status}")

        # Only preparer or admin can cancel
        if current.user_id != prepared_by and "admin" not in current.roles:
            raise Forbidden("Only preparer or admin can cancel a dividend run")

        # Update status to cancelled
        await session.execute(
            text(
                """UPDATE dividend_runs
                   SET status = 'cancelled'
                   WHERE id = :run_id AND cooperative_id = :cid"""
            ),
            {"run_id": run_id, "cid": cid},
        )

        run_dict = await _get_dividend_run_dict(session, cid, run_id)
        if not run_dict:
            raise RuntimeError(f"Failed to retrieve dividend run {run_id}")
        return run_dict


async def post_dividend_run(current: CurrentSession, run_id: uuid.UUID) -> dict[str, Any]:
    """Post approved dividend run to ledger.

    Creates balanced journal entries:
    - Debit dividends_declared for total pool_kobo
    - Credit member_savings for each member's allocation

    Only admin and treasurer can post.
    Can only post a run with status='approved'.

    Args:
        current: Current session (admin/treasurer user)
        run_id: ID of the dividend run

    Returns:
        Updated dividend run with status='posted'

    Raises:
        Forbidden: If user is not admin or treasurer
        NotFound: If run does not exist
        Conflict: If run is not in 'approved' status
    """
    _role(current, {"admin", "treasurer"})

    cid = current.cooperative_id
    uid = current.user_id

    async with transaction(cid) as session:
        # Get the run and check status
        status_result = await session.execute(
            text(
                """SELECT
                    status, pool_kobo, id, record_date
                FROM dividend_runs
                WHERE id = :run_id AND cooperative_id = :cid"""
            ),
            {"run_id": run_id, "cid": cid},
        )
        row = status_result.first()

        if not row:
            raise NotFound(f"Dividend run {run_id} not found")

        status, pool_kobo, run_id_check, record_date = row

        if status != "approved":
            raise Conflict(f"Cannot post run with status={status}")

        # Get the account IDs for dividends_declared and member_savings
        accounts_result = await session.execute(
            text(
                """SELECT code, id FROM accounts
                   WHERE cooperative_id = :cid AND code IN ('dividends_declared', 'member_savings')"""
            ),
            {"cid": cid},
        )

        accounts = {row[0]: row[1] for row in accounts_result}

        if "dividends_declared" not in accounts or "member_savings" not in accounts:
            raise Conflict("Required accounts (dividends_declared, member_savings) not found")

        # Get all dividend lines
        lines_result = await session.execute(
            text(
                """SELECT member_id, amount_kobo FROM dividend_lines
                   WHERE run_id = :run_id AND cooperative_id = :cid"""
            ),
            {"run_id": run_id, "cid": cid},
        )

        lines = list(lines_result)

        if not lines:
            raise Conflict("Dividend run has no allocated lines")

        # Build lines for post_journal_entry: debit dividends_declared, credit each member_savings
        financial_year = record_date.year
        journal_lines = [
            {
                "account_id": str(accounts["dividends_declared"]),
                "debit_kobo": pool_kobo,
                "credit_kobo": 0,
            }
        ] + [
            {
                "account_id": str(accounts["member_savings"]),
                "member_id": str(member_id),
                "debit_kobo": 0,
                "credit_kobo": int(amount_kobo),
            }
            for member_id, amount_kobo in lines
        ]
        await session.scalar(
            text(
                "SELECT post_journal_entry(:cid, :date, 'dividend', :source,"
                " :description, :actor, :key, CAST(:lines AS jsonb), NULL)"
            ),
            {
                "cid": cid,
                "date": record_date,
                "source": run_id,
                "description": f"Dividend {financial_year}",
                "actor": uid,
                "key": f"dividend:{run_id}",
                "lines": json.dumps(journal_lines),
            },
        )

        # Update run status to posted
        await session.execute(
            text(
                """UPDATE dividend_runs
                   SET status = 'posted', posted_by = :uid, posted_at = CURRENT_TIMESTAMP
                   WHERE id = :run_id AND cooperative_id = :cid"""
            ),
            {"run_id": run_id, "uid": uid, "cid": cid},
        )

        run_dict = await _get_dividend_run_dict(session, cid, run_id)
        if not run_dict:
            raise RuntimeError(f"Failed to retrieve dividend run {run_id}")
        return run_dict


async def _get_dividend_run_dict(
    session: AsyncSession, cid: uuid.UUID, run_id: uuid.UUID
) -> dict[str, Any] | None:
    """Fetch a dividend run and return as dict."""
    result = await session.execute(
        text(
            """SELECT
                id, record_date, declared_by_meeting_id, pool_kobo,
                prepared_by, prepared_at, approved_by, approved_at,
                rejected_by, rejected_at, rejection_reason,
                posted_by, posted_at, status, created_at, updated_at
            FROM dividend_runs
            WHERE id = :run_id AND cooperative_id = :cid"""
        ),
        {"run_id": run_id, "cid": cid},
    )

    row = result.first()
    return _row_to_dividend_run_dict(row) if row else None


def _row_to_dividend_run_dict(row: Any) -> dict[str, Any]:
    """Convert a database row to a dividend run dict."""
    return {
        "run_id": str(row[0]),
        "record_date": row[1].isoformat(),
        "declared_by_meeting_id": str(row[2]),
        "pool_kobo": row[3],
        "prepared_by": str(row[4]),
        "prepared_at": row[5].isoformat(),
        "approved_by": str(row[6]) if row[6] else None,
        "approved_at": row[7].isoformat() if row[7] else None,
        "rejected_by": str(row[8]) if row[8] else None,
        "rejected_at": row[9].isoformat() if row[9] else None,
        "rejection_reason": row[10],
        "posted_by": str(row[11]) if row[11] else None,
        "posted_at": row[12].isoformat() if row[12] else None,
        "status": row[13],
        "created_at": row[14].isoformat(),
        "updated_at": row[15].isoformat(),
    }
