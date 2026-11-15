"""Persistent notification outbox worker entry point."""

import asyncio
from typing import Any
from urllib.parse import urlsplit

import structlog

from app.core.config import get_settings
from app.core.db import check_application_role, dispose_engine
from app.core.logging import configure_logging
from app.worker.email_sender import BrevoEmailSender
from app.worker.outbox import run_forever
from app.worker.sender import HttpSmsSender
from app.worker.smtp_email_sender import SmtpEmailSender
from app.worker.telegram_sender import HttpTelegramSender


def _validate_public_url(public_base_url: str, app_env: str) -> None:
    public_url = urlsplit(public_base_url)
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
    if app_env != "development" and public_url.scheme != "https":
        raise RuntimeError("PUBLIC_BASE_URL must use HTTPS outside development")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = structlog.get_logger("worker")
    senders: dict[str, Any] = {}

    if settings.sms_enabled:
        _validate_public_url(settings.public_base_url, settings.app_env)
        senders["sms"] = HttpSmsSender(settings)
    else:
        log.info("sms_delivery_disabled")

    if settings.email_enabled:
        email_provider = settings.email_provider.strip().lower() or "brevo_api"
        if email_provider == "brevo_api":
            senders["email"] = BrevoEmailSender(settings)
        elif email_provider == "smtp":
            senders["email"] = SmtpEmailSender(settings)
        else:
            raise RuntimeError("EMAIL_PROVIDER must be brevo_api or smtp")
    else:
        log.info("email_delivery_disabled")

    if settings.telegram_enabled:
        senders["telegram"] = HttpTelegramSender(settings)
    else:
        log.info("telegram_delivery_disabled")

    log.info("outbox_worker_started", enabled_channels=sorted(senders))
    try:
        await check_application_role(settings.database_url)
        await run_forever(senders, settings)
    finally:
        for sender in senders.values():
            await sender.aclose()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
