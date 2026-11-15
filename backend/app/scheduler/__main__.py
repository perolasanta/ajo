"""Scheduler entry point with a single-instance advisory lock."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import date

import asyncpg
import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import asyncpg_dsn
from app.core.logging import configure_logging
from app.scheduler.overdue_cash import run_overdue_cash_alerts

SCHEDULER_LOCK_KEY = 727_001  # stable across deployments


def _parse_as_of(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--as-of must be YYYY-MM-DD") from exc


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Ajo scheduled jobs")
    parser.add_argument("--run-once", action="store_true", help="run the job once and exit")
    parser.add_argument("--as-of", type=_parse_as_of, help="use this WAT business date")
    parser.add_argument(
        "--dry-run", action="store_true", help="count eligible alerts without queuing"
    )
    args = parser.parse_args(argv)
    if args.dry_run and not args.run_once:
        parser.error("--dry-run requires --run-once")
    if args.as_of and not args.run_once:
        parser.error("--as-of requires --run-once")
    return args


async def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
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
        await conn.close()
        return
    engine = None
    try:
        engine = create_async_engine(
            settings.database_url_scheduler,
            pool_size=1,
            max_overflow=0,
            pool_pre_ping=True,
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        if args.run_once:
            await run_overdue_cash_alerts(
                session_factory,
                as_of_date=args.as_of,
                dry_run=args.dry_run,
            )
            return
        log.info("scheduler_started", interval_minutes=settings.scheduler_interval_minutes)
        while True:
            try:
                await run_overdue_cash_alerts(session_factory)
            except Exception as exc:
                log.error("scheduler_iteration_failed", exception_class=type(exc).__name__)
            await conn.execute("SELECT 1")
            await asyncio.sleep(settings.scheduler_interval_minutes * 60)
    finally:
        if engine is not None:
            await engine.dispose()
        try:
            await conn.execute("SELECT pg_advisory_unlock($1)", SCHEDULER_LOCK_KEY)
        finally:
            await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
