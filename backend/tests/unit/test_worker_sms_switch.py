"""No-database tests for the SMS outbox worker switch."""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import Settings
from app.worker import __main__ as worker_main

pytestmark = pytest.mark.unit


def test_sms_enabled_defaults_to_false() -> None:
    assert Settings(app_env="development").sms_enabled is False


def test_email_enabled_defaults_to_false() -> None:
    assert Settings(app_env="development").email_enabled is False


@pytest.mark.asyncio
async def test_disabled_worker_logs_and_waits_without_database_or_gateway(monkeypatch) -> None:
    settings = Settings(app_env="development", sms_enabled=False, email_enabled=False)
    messages: list[str] = []

    class Logger:
        def info(self, event: str, **kwargs: object) -> None:
            messages.append(event)

    monkeypatch.setattr(worker_main, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_main, "configure_logging", lambda level: None)
    monkeypatch.setattr(worker_main.structlog, "get_logger", lambda name: Logger())
    monkeypatch.setattr(
        worker_main,
        "check_application_role",
        lambda database_url: pytest.fail("disabled worker must not connect to the database"),
    )
    monkeypatch.setattr(
        worker_main,
        "HttpSmsSender",
        lambda current_settings: pytest.fail("disabled worker must not build an SMS sender"),
    )

    task = asyncio.create_task(worker_main.main())
    try:
        await asyncio.sleep(0)
        assert "sms_delivery_disabled" in messages
        assert "email_delivery_disabled" in messages
        assert not task.done()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_enabled_worker_requires_gateway_settings_before_database(monkeypatch) -> None:
    settings = Settings(
        app_env="development",
        sms_enabled=True,
        public_base_url="https://ajo.example.test",
    )
    monkeypatch.setattr(worker_main, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_main, "configure_logging", lambda level: None)
    monkeypatch.setattr(
        worker_main,
        "check_application_role",
        lambda database_url: pytest.fail("gateway settings are checked before database access"),
    )

    with pytest.raises(RuntimeError, match="SMS_GATEWAY_URL"):
        await worker_main.main()


@pytest.mark.asyncio
async def test_enabled_email_requires_brevo_settings_before_database(monkeypatch) -> None:
    settings = Settings(app_env="development", email_enabled=True, sms_enabled=False)
    monkeypatch.setattr(worker_main, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_main, "configure_logging", lambda level: None)
    monkeypatch.setattr(
        worker_main,
        "check_application_role",
        lambda database_url: pytest.fail("Brevo settings are checked before database access"),
    )

    with pytest.raises(RuntimeError, match="BREVO_API_KEY"):
        await worker_main.main()
