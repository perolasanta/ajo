"""Persistent notification outbox worker entry point."""

import asyncio
from urllib.parse import urlsplit

import structlog

from app.core.config import get_settings
from app.core.db import check_application_role, dispose_engine
from app.core.logging import configure_logging
from app.worker.outbox import run_forever
from app.worker.sender import HttpSmsSender


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    public_url = urlsplit(settings.public_base_url)
    if public_url.scheme not in {"http", "https"} or not public_url.netloc:
        raise RuntimeError("PUBLIC_BASE_URL must be an absolute HTTP URL")
    if (
        public_url.username is not None
        or public_url.password is not None
        or public_url.query
        or public_url.fragment
    ):
        raise RuntimeError(
            "PUBLIC_BASE_URL must not contain credentials, query data, or a fragment"
        )
    if settings.app_env != "development" and public_url.scheme != "https":
        raise RuntimeError("PUBLIC_BASE_URL must use HTTPS outside development")
    sender = HttpSmsSender(settings)
    log = structlog.get_logger("worker")
    log.info("outbox_worker_started")
    try:
        await check_application_role(settings.database_url)
        await run_forever(sender, settings)
    finally:
        await sender.aclose()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
