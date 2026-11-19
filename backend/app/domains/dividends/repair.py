"""Repair command for approved-but-unposted dividend runs.

Staging had runs that reached status='approved' without a journal entry because
the approval flow previously did not post.  This command finds every such run and
posts the missing entry exactly once.

Usage (on the VPS, run from within the api or scheduler container):
    docker compose exec api python -m app.domains.dividends.repair --dry-run
    docker compose exec api python -m app.domains.dividends.repair

Requires DATABASE_URL_DIRECT (the owner connection) so the cross-tenant
discovery query can bypass RLS.  Each run is then posted through the normal
app-role transaction() so RLS applies per row.

Returns dict with 'posted' (or 'would_post') and 'skipped'.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import text

from app.core.db import make_engine, transaction


def _direct_url() -> str:
    import os
    from urllib.parse import urlparse

    load_dotenv(Path(__file__).resolve().parents[4] / ".env")
    url = os.environ.get("DATABASE_URL_DIRECT", "")
    parsed = urlparse(url)
    if not url or parsed.username == "ajo_app" or "-pooler" in (parsed.hostname or ""):
        raise RuntimeError("DATABASE_URL_DIRECT must be set to the direct owner connection")
    return url


async def repair_approved_runs(*, dry_run: bool = False) -> dict[str, int]:
    """Post missing ledger entries for approved-but-unposted dividend runs.

    For each run with status='approved' and journal_entry_id IS NULL:
    1. Acquires a row lock (re-checks state inside the lock).
    2. Calls the same _post_entry helper used by approve_dividend_run.
    3. Updates the run to status='posted'.

    Running twice is safe: the second call finds no eligible rows.

    Args:
        dry_run: If True, count eligible runs but do not post anything.

    Returns:
        dict: {'posted': N, 'skipped': M} or {'would_post': N, 'skipped': M}.
    """
    # Import lazily to avoid circular import at module level.
    from app.domains.dividends.service import _post_entry

    owner_engine = make_engine(_direct_url(), pooled=False, application_name="ajo-dividend-repair")

    try:
        # Cross-tenant discovery uses the owner role (bypasses RLS).
        async with owner_engine.begin() as conn:
            rows = await conn.execute(
                text(
                    """SELECT id, cooperative_id, pool_kobo, record_date, approved_by
                       FROM dividend_runs
                       WHERE status = 'approved'
                         AND journal_entry_id IS NULL
                       ORDER BY cooperative_id, created_at"""
                )
            )
            broken: list[tuple[uuid.UUID, uuid.UUID, int, Any, uuid.UUID | None]] = [
                (
                    uuid.UUID(str(r[0])),
                    uuid.UUID(str(r[1])),
                    int(r[2]),
                    r[3],
                    uuid.UUID(str(r[4])) if r[4] else None,
                )
                for r in rows
            ]
    finally:
        await owner_engine.dispose()

    posted = 0
    skipped = 0

    for run_id, cid, pool_kobo, record_date, approved_by_id in broken:
        # Fall back to a sentinel actor if approved_by is somehow NULL.
        actor_id: uuid.UUID = approved_by_id if approved_by_id else uuid.UUID(int=0)

        if dry_run:
            posted += 1
            continue

        try:
            async with transaction(cid) as session:
                # Re-check inside the row lock so concurrent calls are safe.
                check = await session.execute(
                    text(
                        """SELECT status, journal_entry_id
                           FROM dividend_runs
                           WHERE id = :run_id AND cooperative_id = :cid
                           FOR UPDATE"""
                    ),
                    {"run_id": run_id, "cid": cid},
                )
                row = check.first()
                if not row or row[0] != "approved" or row[1] is not None:
                    skipped += 1
                    continue

                entry_id = await _post_entry(
                    session,
                    cid=cid,
                    run_id=run_id,
                    pool_kobo=pool_kobo,
                    record_date=record_date,
                    actor_id=actor_id,
                )

                await session.execute(
                    text(
                        """UPDATE dividend_runs
                           SET status = 'posted',
                               posted_by = :actor,
                               posted_at = CURRENT_TIMESTAMP,
                               journal_entry_id = :entry_id
                           WHERE id = :run_id AND cooperative_id = :cid"""
                    ),
                    {
                        "actor": actor_id,
                        "entry_id": entry_id,
                        "run_id": run_id,
                        "cid": cid,
                    },
                )
            posted += 1
        except Exception as exc:  # noqa: BLE001
            print(
                f"ERROR: failed to repair run {run_id} (cooperative {cid}): {exc}",
                file=sys.stderr,
            )
            skipped += 1

    if dry_run:
        return {"would_post": posted, "skipped": skipped}
    return {"posted": posted, "skipped": skipped}


def _main() -> None:
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Repair approved-but-unposted dividend runs.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts without changing anything.",
    )
    args = parser.parse_args()

    counts = asyncio.run(repair_approved_runs(dry_run=args.dry_run))
    if args.dry_run:
        print(f"DRY RUN: would_post={counts['would_post']} skipped={counts['skipped']}")
    else:
        print(f"posted={counts['posted']} skipped={counts['skipped']}")


if __name__ == "__main__":
    _main()
