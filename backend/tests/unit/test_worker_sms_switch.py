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


@pytest.mark.parametrize("email_enabled", [False, True])
@pytest.mark.asyncio
async def test_worker_entrypoint_maps_senders_by_enabled_channel(
    monkeypatch, email_enabled: bool
) -> None:
    settings = Settings(
        app_env="development",
        sms_enabled=True,
        email_enabled=email_enabled,
        public_base_url="https://ajo.example.test",
        sms_gateway_url="https://gateway.example.test/messages",
        sms_gateway_api_key="test-key",
        brevo_api_key="test-key",
        email_from_address="noreply@example.test",
        email_from_name="Ajo",
    )
    captured: dict[str, object] = {}

    class FakeSender:
        async def aclose(self) -> None:
            return None

    async def stop_after_mapping(senders, current_settings) -> None:
        captured["senders"] = senders
        raise RuntimeError("stop after mapping")

    async def no_database_check(database_url: str) -> None:
        return None

    async def no_dispose() -> None:
        return None

    monkeypatch.setattr(worker_main, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_main, "configure_logging", lambda level: None)
    monkeypatch.setattr(worker_main, "HttpSmsSender", lambda current_settings: FakeSender())
    monkeypatch.setattr(worker_main, "BrevoEmailSender", lambda current_settings: FakeSender())
    monkeypatch.setattr(worker_main, "check_application_role", no_database_check)
    monkeypatch.setattr(worker_main, "run_forever", stop_after_mapping)
    monkeypatch.setattr(worker_main, "dispose_engine", no_dispose)

    with pytest.raises(RuntimeError, match="stop after mapping"):
        await worker_main.main()

    senders = captured["senders"]
    assert isinstance(senders, dict)
    assert "sms" in senders
    assert ("email" in senders) is email_enabled
