"""No-network tests for SMTP action-email delivery."""

from __future__ import annotations

import smtplib
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from typing import Any

import pytest

from app.core.config import Settings
from app.worker import outbox as notification_worker
from app.worker.outbox import ClaimedNotification
from app.worker.smtp_email_sender import SmtpEmailSender

pytestmark = pytest.mark.unit


@pytest.fixture
def smtp_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="development",
        app_name="Ajo Co-op",
        email_enabled=True,
        email_provider="smtp",
        email_from_address="noreply@example.test",
        email_from_name="Ajo Notifications",
        smtp_host="smtp.example.test",
        smtp_port="587",
        smtp_username="test-user",
        smtp_password="smtp-test-password",
        smtp_security="starttls",
    )


class FakeSMTP:
    def __init__(
        self,
        messages: list[EmailMessage],
        *,
        failure: Exception | dict[str, tuple[int, bytes]] | None = None,
        timeout_values: list[float] | None = None,
        timeout: float,
        context: Any = None,
    ) -> None:
        del context
        self._messages = messages
        self._failure = failure
        if timeout_values is not None:
            timeout_values.append(timeout)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def ehlo(self) -> None:
        return None

    def starttls(self, *, context: Any) -> None:
        del context

    def login(self, username: str, password: str) -> None:
        assert username == "test-user"
        assert password == "smtp-test-password"

    def send_message(self, message: EmailMessage) -> dict[str, tuple[int, bytes]]:
        self._messages.append(message)
        if isinstance(self._failure, Exception):
            raise self._failure
        return self._failure or {}


def _smtp_factory(
    messages: list[EmailMessage],
    *,
    failure: Exception | dict[str, tuple[int, bytes]] | None = None,
    timeout_values: list[float] | None = None,
) -> Callable[..., FakeSMTP]:
    def factory(host: str, port: int, *, timeout: float, context: Any = None) -> FakeSMTP:
        assert host == "smtp.example.test"
        assert port == 587
        return FakeSMTP(
            messages,
            failure=failure,
            timeout_values=timeout_values,
            timeout=timeout,
            context=context,
        )

    return factory


async def test_smtp_sender_sends_multipart_message_with_mime_headers(
    smtp_settings: Settings,
) -> None:
    messages: list[EmailMessage] = []
    timeouts: list[float] = []
    sender = SmtpEmailSender(
        smtp_settings, smtp_factory=_smtp_factory(messages, timeout_values=timeouts)
    )

    await sender.send(
        "private.person@example.test",
        "Action needed in Ajo Co-op: payroll sign-off",
        "A payroll run needs your sign-off. https://ajo.example.test/payroll/runs/abc",
        '<p>A payroll run needs your sign-off. <a href="https://ajo.example.test/payroll/runs/abc">Open item</a>.</p>',
    )

    assert timeouts == [10.0]
    assert len(messages) == 1
    message = messages[0]
    assert message["From"] == "Ajo Notifications <noreply@example.test>"
    assert message["To"] == "private.person@example.test"
    assert message["Subject"] == "Action needed in Ajo Co-op: payroll sign-off"
    assert message["Message-ID"]
    assert parsedate_to_datetime(str(message["Date"])).tzinfo == UTC
    assert message.is_multipart()
    assert message.get_content_type() == "multipart/alternative"
    parts = list(message.iter_parts())
    assert [part.get_content_type() for part in parts] == ["text/plain", "text/html"]
    assert "A payroll run needs your sign-off." in parts[0].get_content()
    assert "<a href=" in parts[1].get_content()


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_code", "expected_delay"),
    [
        (smtplib.SMTPDataError(450, b"temporary failure"), "pending", "smtp_temporary_failure", 10),
        (smtplib.SMTPDataError(550, b"permanent failure"), "failed", "smtp_permanent_failure", 0),
        (
            {"private.person@example.test": (550, b"recipient rejected")},
            "failed",
            "smtp_recipient_rejected",
            0,
        ),
    ],
)
async def test_smtp_outcomes_use_outbox_retry_or_dead_letter_mapping(
    smtp_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception | dict[str, tuple[int, bytes]],
    expected_status: str,
    expected_code: str,
    expected_delay: int,
) -> None:
    messages: list[EmailMessage] = []
    sender = SmtpEmailSender(smtp_settings, smtp_factory=_smtp_factory(messages, failure=failure))
    result = await _run_worker_delivery(monkeypatch, smtp_settings, sender)

    status_write = next(
        (
            params
            for statement, params in result["writes"]
            if "UPDATE notifications SET status" in statement
        ),
        None,
    )
    assert status_write is not None
    assert status_write["status"] == expected_status
    assert status_write["error_code"] == expected_code
    assert status_write["delay"] == expected_delay


async def test_smtp_timeout_retries_and_logs_only_masked_recipient(
    smtp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages: list[EmailMessage] = []
    sender = SmtpEmailSender(
        smtp_settings,
        smtp_factory=_smtp_factory(messages, failure=TimeoutError("smtp-test-password")),
    )

    result = await _run_worker_delivery(monkeypatch, smtp_settings, sender)

    status_write = next(
        params
        for statement, params in result["writes"]
        if "UPDATE notifications SET status" in statement
    )
    assert status_write["status"] == "pending"
    assert status_write["error_code"] == "smtp_timeout"
    assert status_write["delay"] == 10
    logs = repr(result["logged"])
    assert "smtp-test-password" not in logs
    assert "private.person@example.test" not in logs
    assert "p***@example.test" in logs


def test_smtp_sender_fails_at_startup_with_all_missing_settings() -> None:
    settings = Settings(
        _env_file=None,
        app_env="development",
        email_enabled=True,
        email_provider="smtp",
        email_from_address="noreply@example.test",
        email_from_name="Ajo Notifications",
    )

    with pytest.raises(RuntimeError) as error:
        SmtpEmailSender(settings)

    for setting_name in (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_SECURITY",
    ):
        assert setting_name in str(error.value)


async def _run_worker_delivery(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, sender: SmtpEmailSender
) -> dict[str, Any]:
    cooperative_id = uuid.uuid4()
    claim = ClaimedNotification(
        id=uuid.uuid4(),
        cooperative_id=cooperative_id,
        member_id=None,
        recipient_user_id=uuid.uuid4(),
        kind="payroll_sign_off",
        channel="email",
        template_version="v1",
        payload_encrypted="encrypted-payload",
        attempts=1,
        claim_token=uuid.uuid4(),
    )
    writes: list[tuple[str, dict[str, Any]]] = []
    logged: list[tuple[str, dict[str, Any]]] = []

    async def claim_next(*args: Any, **kwargs: Any) -> ClaimedNotification:
        del args, kwargs
        return claim

    async def email_destination(*args: Any, **kwargs: Any) -> str:
        del args, kwargs
        return "private.person@example.test"

    class FakeSession:
        async def execute(self, statement: Any, params: dict[str, Any]) -> None:
            writes.append((str(statement), params))

    @asynccontextmanager
    async def fake_transaction(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        yield FakeSession()

    class Logger:
        def info(self, event: str, **kwargs: Any) -> None:
            logged.append((event, kwargs))

        def warning(self, event: str, **kwargs: Any) -> None:
            logged.append((event, kwargs))

    monkeypatch.setattr(notification_worker, "_claim_next", claim_next)
    monkeypatch.setattr(notification_worker, "_email_destination", email_destination)
    monkeypatch.setattr(
        notification_worker,
        "decrypt_payload",
        lambda value: {
            "section": "Payroll",
            "action_kind": "payroll sign-off",
            "message": "A payroll run needs your sign-off.",
            "url": "https://ajo.example.test/payroll/runs/11111111-1111-1111-1111-111111111111",
        },
    )
    monkeypatch.setattr(notification_worker, "transaction", fake_transaction)
    monkeypatch.setattr(
        notification_worker.structlog, "get_logger", lambda *args, **kwargs: Logger()
    )

    assert await notification_worker.process_next_notification(
        cooperative_id, {"email": sender}, settings
    )
    await sender.aclose()
    return {"writes": writes, "logged": logged}
