"""Scheduler: exactly ONE instance must run jobs (dues, schedules, reminders).

Uses a SESSION-level advisory lock on a dedicated direct connection as the
non-owner ``ajo_scheduler`` role. If another scheduler already holds the lock,
this exits.
"""

import asyncio
import sys

import asyncpg
import structlog

from app.core.config import get_settings
from app.core.db import asyncpg_dsn
from app.core.logging import configure_logging

SCHEDULER_LOCK_KEY = 727_001  # arbitrary, stable across deployments


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = structlog.get_logger("scheduler")
    if not settings.database_url_scheduler:
        log.error("DATABASE_URL_SCHEDULER is required for the scheduler")
        sys.exit(1)
    conn = await asyncpg.connect(asyncpg_dsn(settings.database_url_scheduler))
    got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", SCHEDULER_LOCK_KEY)
    if not got:
        log.warning("another scheduler holds the lock; exiting")
        sys.exit(0)
    log.info("scheduler_started")
    while True:
        # Phase 1+: run due jobs here, each guarded by an idempotency key.
        await conn.execute("SELECT 1")  # keeps the direct connection (and lock) alive
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
