"""Outbox worker (Phase 2). Skeleton: heartbeat only.

Real implementation claims notification rows with FOR UPDATE SKIP LOCKED,
sends, records attempts, backs off, and dead-letters after N failures.
"""

import asyncio

import structlog

from app.core.config import get_settings
from app.core.logging import configure_logging


async def main() -> None:
    configure_logging(get_settings().log_level)
    log = structlog.get_logger("worker")
    while True:
        log.info("worker_heartbeat")
        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
