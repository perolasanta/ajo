"""Wait for the pooled application database before starting the API server."""

from __future__ import annotations

import asyncio
from time import monotonic

import structlog
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import connection
from app.core.logging import configure_logging


async def wait_for_database(
    *, timeout_seconds: float = 300, retry_interval_seconds: float = 2
) -> None:
    """Poll the configured pool with SELECT 1 without logging connection details."""
    log = structlog.get_logger("api_startup")
    deadline = monotonic() + timeout_seconds
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            log.error("database_readiness_timeout")
            raise RuntimeError("database readiness timed out")
        try:
            async with asyncio.timeout(min(5.0, remaining)):
                async with connection() as db_connection:
                    await db_connection.execute(text("SELECT 1"))
        except Exception as exc:
            remaining = deadline - monotonic()
            if remaining <= 0:
                log.error("database_readiness_timeout", error_code=type(exc).__name__)
                raise RuntimeError("database readiness timed out") from None
            await asyncio.sleep(min(retry_interval_seconds, remaining))
        else:
            log.info("database_ready")
            return


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    await wait_for_database()


if __name__ == "__main__":
    asyncio.run(main())
