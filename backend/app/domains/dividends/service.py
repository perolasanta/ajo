"""Dividend run service: prepare, approve, reject, cancel, and post dividend allocations.
Change log (fix/dividends-ledger-and-periods):
- approve_dividend_run now posts the balanced journal entry atomically in the same
  transaction; it no longer merely sets status='approved'.  Status after approval
  is 'posted'.  The separate post_dividend_run endpoint is kept for idempotent
  re-posting and for staging runs that were approved without an entry.
- prepare_dividend_run accepts financial_year (int) and stores it as period_key.
  It rejects a prepare when an unprocessed, approved, or posted run already exists
  for the same (cooperative_id, period_key) using an advisory lock + explicit query
  so the error message is clear before the DB index fires.
- Unit 2: declared_by_meeting_id UUID replaced by meeting_or_resolution text (optional).
  shares_held now stores the share COUNT (kobo / (unit_price_naira * 100)) so the
  dividend detail page agrees with the share register.
"""

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
    """Raise Forbidden if the caller lacks all allowed roles."""
    if not allowed.intersection(current.roles):
        raise Forbidden("This role cannot perform this action")


# ---------------------------------------------------------------------------
# Internal posting helper (shared by approve_dividend_run and repair)
# ---------------------------------------------------------------------------
async def _post_entry(
    session: AsyncSession,
    *,
    cid: uuid.UUID,
    run_id: uuid.UUID,
    pool_kobo: int,
    record_date: date,
    actor_id: uuid.UUID,
) -> uuid.UUID:
    """Post a balanced journal entry for the dividend run.

    Debits dividends_declared for pool_kobo; credits each member's member_savings
    for their allocation.  Returns the new journal_entries.id.

    Callers must hold the row lock on the dividend_runs row (SELECT … FOR UPDATE).
    The idempotency key 'dividend:<run_id>' prevents double-posting.
    """
    # Resolve account IDs
    accounts_result = await session.execute(
        text(
            """SELECT code, id FROM accounts
               WHERE cooperative_id = :cid
                 AND code IN ('dividends_declared', 'member_savings')"""
        ),
        {"cid": cid},
    )
    accounts = {row[0]: row[1] for row in accounts_result}
    if "dividends_declared" not in accounts or "member_savings" not in accounts:
        raise Conflict("Required accounts (dividends_declared, member_savings) not found")

    # Fetch allocated lines
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

    financial_year = record_date.year
    journal_lines: list[dict[str, Any]] = [
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
    entry_id = await session.scalar(
        text(
            "SELECT post_journal_entry(:cid, :date, 'dividend', :source,"
            " :description, :actor, :key, CAST(:lines AS jsonb), NULL)"
        ),
        {
            "cid": cid,
            "date": record_date,
            "source": run_id,
            "description": f"Dividend {financial_year}",
            "actor": actor_id,
            "key": f"dividend:{run_id}",
            "lines": json.dumps(journal_lines),
        },
    )
    if entry_id is None:
        raise RuntimeError(f"post_journal_entry returned NULL for run {run_id}")
    return uuid.UUID(str(entry_id))


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------
async def prepare_dividend_run(
    current: CurrentSession,
    *,
    record_date: date,
    meeting_or_resolution: str | None,
    pool_kobo: int,
    financial_year: int,
) -> dict[str, Any]:
    """Prepare a dividend run with share snapshot at record_date.

    Args:
        current: Caller session (treasurer or admin).
        record_date: Effective date for the share snapshot.
        meeting_or_resolution: Free-text reference (e.g. "AGM 2026"), optional.
        pool_kobo: Total dividend pool in integer kobo (must be positive).
        financial_year: Calendar year for this run (e.g. 2025).  Stored as
            period_key; only one active run per (cooperative, period_key) is
            allowed.

    Returns:
        Dividend run dict with status='unprocessed'.

    Raises:
        Forbidden: Caller lacks treasurer or admin role.
        Conflict: An unprocessed, approved, or posted run for the same year
            already exists.
    """
    _role(current, {"treasurer", "admin"})
    cid = current.cooperative_id
    uid = current.user_id
    period_key = str(financial_year)

    async with transaction(cid) as session:
        # Advisory lock scoped to (cooperative, period) prevents races between
        # two simultaneous prepares for the same period.
        lock_key = abs(hash((str(cid), period_key))) % (2**31)
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

        # Explicit check so the error message names the period.
        existing = await session.execute(
            text(
                """SELECT status FROM dividend_runs
                   WHERE cooperative_id = :cid
                     AND period_key = :period_key
                     AND status IN ('unprocessed','approved','posted')
                   LIMIT 1"""
            ),
            {"cid": cid, "period_key": period_key},
        )
        row = existing.first()
        if row:
            raise Conflict(f"A dividend run for {financial_year} already exists ({row[0]})")

        # Fetch the cooperative's share unit price so we can store share COUNT.
        unit_price_naira = await session.scalar(
            text("SELECT share_unit_price_naira FROM cooperatives WHERE id = :cid"),
            {"cid": cid},
        )
        if unit_price_naira is None:
            raise Conflict("Cooperative not found or missing share_unit_price_naira")
        unit_price_naira = int(unit_price_naira)
        # 1 share = unit_price_naira naira = unit_price_naira * 100 kobo
        kobo_per_share = unit_price_naira * 100

        run_id = uuid.uuid4()
        await session.execute(
            text(
                """INSERT INTO dividend_runs (
                    id, cooperative_id, record_date, meeting_or_resolution,
                    pool_kobo, prepared_by, status, period_key
                ) VALUES (
                    :run_id, :cid, :record_date, :meeting_or_resolution,
                    :pool_kobo, :uid, 'unprocessed', :period_key
                )"""
            ),
            {
                "run_id": run_id,
                "cid": cid,
                "record_date": record_date,
                "meeting_or_resolution": meeting_or_resolution,
                "pool_kobo": pool_kobo,
                "uid": uid,
                "period_key": period_key,
            },
        )

        # Share snapshot at record_date for active members.
        shares_result = await session.execute(
            text(
                """WITH member_snapshots AS (
                    SELECT
                        l.member_id,
                        SUM(l.credit_kobo) AS shares_kobo
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
        total_shares_kobo = sum(row[1] for row in member_shares)

        if total_shares_kobo > 0:
            # Largest-remainder allocation so lines sum to exactly pool_kobo.
            exact = [(mid, (skb * pool_kobo) / total_shares_kobo) for mid, skb in member_shares]
            floored = [(mid, int(v)) for mid, v in exact]
            remainders = sorted(
                range(len(exact)),
                key=lambda i: -(exact[i][1] - floored[i][1]),
            )
            remainder_total = pool_kobo - sum(a for _, a in floored)
            for idx in remainders[:remainder_total]:
                mid, amt = floored[idx]
                floored[idx] = (mid, amt + 1)
            for member_id, amount_kobo in floored:
                if amount_kobo > 0:
                    # shares_held = share COUNT (kobo_value / kobo_per_share)
                    shares_kobo_for_member: int = dict(member_shares)[member_id]
                    shares_count = max(1, shares_kobo_for_member // kobo_per_share)
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
                            "shares_held": shares_count,
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
    """List dividend runs for the cooperative (paginated).

    Allowed: treasurer, admin, officer, auditor, committee.
    """
    _role(current, {"treasurer", "admin", "officer", "auditor", "committee"})
    cid = current.cooperative_id
    offset = (page - 1) * page_size
    async with transaction(cid) as session:
        count_result = await session.execute(
            text("SELECT COUNT(*) FROM dividend_runs WHERE cooperative_id = :cid"),
            {"cid": cid},
        )
        total = count_result.scalar() or 0
        runs_result = await session.execute(
            text(
                """SELECT
                    id, record_date, meeting_or_resolution, pool_kobo,
                    prepared_by, prepared_at, approved_by, approved_at,
                    rejected_by, rejected_at, rejection_reason,
                    posted_by, posted_at, status, created_at, updated_at,
                    journal_entry_id, period_key
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

    Allowed: treasurer, admin, officer, auditor, committee.
    """
    _role(current, {"treasurer", "admin", "officer", "auditor", "committee"})
    cid = current.cooperative_id
    async with transaction(cid) as session:
        run_dict = await _get_dividend_run_dict(session, cid, run_id)
        if not run_dict:
            raise NotFound(f"Dividend run {run_id} not found")
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
    """Approve a prepared dividend run and post its ledger entry atomically.

    This merges what was previously a two-step approve-then-post flow into one
    atomic action.  After this call the run status is 'posted' and the member
    savings balances have already increased.

    Allowed: admin only.
    Transition: unprocessed → posted.

    Raises:
        Forbidden: Caller is not admin.
        NotFound: Run does not exist.
        Conflict: Run is not in 'unprocessed' status, or no lines to post.
    """
    _role(current, {"admin"})
    cid = current.cooperative_id
    uid = current.user_id
    async with transaction(cid) as session:
        # Row lock prevents concurrent double-approval.
        status_result = await session.execute(
            text(
                """SELECT status, pool_kobo, record_date
                   FROM dividend_runs
                   WHERE id = :run_id AND cooperative_id = :cid
                   FOR UPDATE"""
            ),
            {"run_id": run_id, "cid": cid},
        )
        row = status_result.first()
        if not row:
            raise NotFound(f"Dividend run {run_id} not found")
        status, pool_kobo, record_date = row
        if status != "unprocessed":
            raise Conflict(f"Cannot approve run with status={status}")

        entry_id = await _post_entry(
            session,
            cid=cid,
            run_id=run_id,
            pool_kobo=pool_kobo,
            record_date=record_date,
            actor_id=uid,
        )
        await session.execute(
            text(
                """UPDATE dividend_runs
                   SET status = 'posted',
                       approved_by = :uid,
                       approved_at = CURRENT_TIMESTAMP,
                       posted_by = :uid,
                       posted_at = CURRENT_TIMESTAMP,
                       journal_entry_id = :entry_id
                   WHERE id = :run_id AND cooperative_id = :cid"""
            ),
            {"uid": uid, "entry_id": entry_id, "run_id": run_id, "cid": cid},
        )
        run_dict = await _get_dividend_run_dict(session, cid, run_id)
        if not run_dict:
            raise RuntimeError(f"Failed to retrieve dividend run {run_id}")
        return run_dict


async def reject_dividend_run(
    current: CurrentSession, run_id: uuid.UUID, reason: str
) -> dict[str, Any]:
    """Reject a prepared dividend run.

    Allowed: admin only.  Transition: unprocessed → rejected.
    """
    _role(current, {"admin"})
    cid = current.cooperative_id
    uid = current.user_id
    async with transaction(cid) as session:
        status_result = await session.execute(
            text("SELECT status FROM dividend_runs WHERE id = :run_id AND cooperative_id = :cid"),
            {"run_id": run_id, "cid": cid},
        )
        row = status_result.first()
        if not row:
            raise NotFound(f"Dividend run {run_id} not found")
        if row[0] != "unprocessed":
            raise Conflict(f"Cannot reject run with status={row[0]}")
        await session.execute(
            text(
                """UPDATE dividend_runs
                   SET status = 'rejected',
                       rejected_by = :uid,
                       rejected_at = CURRENT_TIMESTAMP,
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

    Allowed: preparer or admin.  Transition: unprocessed → cancelled.
    """
    _role(current, {"treasurer", "admin"})
    cid = current.cooperative_id
    async with transaction(cid) as session:
        status_result = await session.execute(
            text(
                "SELECT status, prepared_by FROM dividend_runs"
                " WHERE id = :run_id AND cooperative_id = :cid"
            ),
            {"run_id": run_id, "cid": cid},
        )
        row = status_result.first()
        if not row:
            raise NotFound(f"Dividend run {run_id} not found")
        status, prepared_by = row
        if status != "unprocessed":
            raise Conflict(f"Cannot cancel run with status={status}")
        if current.user_id != prepared_by and "admin" not in current.roles:
            raise Forbidden("Only preparer or admin can cancel a dividend run")
        await session.execute(
            text(
                "UPDATE dividend_runs SET status = 'cancelled'"
                " WHERE id = :run_id AND cooperative_id = :cid"
            ),
            {"run_id": run_id, "cid": cid},
        )
        run_dict = await _get_dividend_run_dict(session, cid, run_id)
        if not run_dict:
            raise RuntimeError(f"Failed to retrieve dividend run {run_id}")
        return run_dict


async def post_dividend_run(current: CurrentSession, run_id: uuid.UUID) -> dict[str, Any]:
    """Post an already-approved run to the ledger.

    Kept for backward compatibility with existing API consumers.  With the new
    flow, approve_dividend_run posts in one step and this endpoint is a no-op
    for already-posted runs (idempotent).  It still handles the legacy case
    where a run reached status='approved' without a journal entry.

    Allowed: admin or treasurer.
    Acceptable starting statuses: 'approved' (legacy) or 'posted' (no-op).
    """
    _role(current, {"admin", "treasurer"})
    cid = current.cooperative_id
    uid = current.user_id
    async with transaction(cid) as session:
        status_result = await session.execute(
            text(
                """SELECT status, pool_kobo, record_date, journal_entry_id
                   FROM dividend_runs
                   WHERE id = :run_id AND cooperative_id = :cid
                   FOR UPDATE"""
            ),
            {"run_id": run_id, "cid": cid},
        )
        row = status_result.first()
        if not row:
            raise NotFound(f"Dividend run {run_id} not found")
        status, pool_kobo, record_date, existing_entry_id = row
        if status == "posted" and existing_entry_id is not None:
            # Already posted — return current state (idempotent).
            run_dict = await _get_dividend_run_dict(session, cid, run_id)
            if not run_dict:
                raise RuntimeError(f"Failed to retrieve dividend run {run_id}")
            return run_dict
        if status not in ("approved", "posted"):
            raise Conflict(f"Cannot post run with status={status}")
        entry_id = await _post_entry(
            session,
            cid=cid,
            run_id=run_id,
            pool_kobo=pool_kobo,
            record_date=record_date,
            actor_id=uid,
        )
        await session.execute(
            text(
                """UPDATE dividend_runs
                   SET status = 'posted',
                       posted_by = :uid,
                       posted_at = CURRENT_TIMESTAMP,
                       journal_entry_id = :entry_id
                   WHERE id = :run_id AND cooperative_id = :cid"""
            ),
            {"uid": uid, "entry_id": entry_id, "run_id": run_id, "cid": cid},
        )
        run_dict = await _get_dividend_run_dict(session, cid, run_id)
        if not run_dict:
            raise RuntimeError(f"Failed to retrieve dividend run {run_id}")
        return run_dict


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
async def _get_dividend_run_dict(
    session: AsyncSession, cid: uuid.UUID, run_id: uuid.UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """SELECT
                id, record_date, meeting_or_resolution, pool_kobo,
                prepared_by, prepared_at, approved_by, approved_at,
                rejected_by, rejected_at, rejection_reason,
                posted_by, posted_at, status, created_at, updated_at,
                journal_entry_id, period_key
            FROM dividend_runs
            WHERE id = :run_id AND cooperative_id = :cid"""
        ),
        {"run_id": run_id, "cid": cid},
    )
    row = result.first()
    return _row_to_dividend_run_dict(row) if row else None


def _row_to_dividend_run_dict(row: Any) -> dict[str, Any]:
    return {
        "run_id": str(row[0]),
        "record_date": row[1].isoformat(),
        "meeting_or_resolution": row[2],
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
        "journal_entry_id": str(row[16]) if row[16] else None,
        "period_key": row[17],
    }
