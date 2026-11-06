"""No-network tests for Brevo transactional email delivery."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.worker import outbox as notification_worker
from app.worker.email_sender import BrevoEmailSender, build_action_email
from app.worker.outbox import ClaimedNotification

pytestmark = pytest.mark.unit


@pytest.fixture
def email_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="development",
        app_name="Ajo Co-op",
        email_enabled=True,
        brevo_api_key="fake-brevo-key-for-tests",
        email_from_address="noreply@example.test",
        email_from_name="Ajo Notifications",
    )


def test_action_email_has_one_generic_sentence_and_html_link(email_settings: Settings) -> None:
    message = build_action_email(
        "payroll_sign_off",
        {"section": "Payroll", "url": "https://ajo.example.test/payroll/runs"},
        email_settings,
    )

    assert message.subject == "Action needed in Ajo Co-op: Payroll"
    assert message.text == (
        "Action needed in Payroll; review it here: https://ajo.example.test/payroll/runs."
    )
    assert message.html == (
        "<p>Action needed in Payroll; review it "
        '<a href="https://ajo.example.test/payroll/runs">here</a>.</p>'
    )


@pytest.mark.parametrize(
    ("http_status", "expected_status", "expected_permanent"),
    [(202, "sent", None), (503, "pending", False), (422, "failed", True)],
)
async def test_worker_sends_through_brevo_and_preserves_outbox_outcomes(
    http_status: int,
    expected_status: str,
    expected_permanent: bool | None,
    email_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recipient_id = uuid.uuid4()
    cooperative_id = uuid.uuid4()
    claim = ClaimedNotification(
        id=uuid.uuid4(),
        cooperative_id=cooperative_id,
        member_id=None,
        recipient_user_id=recipient_id,
        kind="payroll_sign_off",
        channel="email",
        template_version="v1",
        payload_encrypted="encrypted-payload",
        attempts=1,
        claim_token=uuid.uuid4(),
    )
    captured_requests: list[httpx.Request] = []
    logged: list[tuple[str, dict[str, Any]]] = []
    writes: list[tuple[str, dict[str, Any]]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(http_status, json={"messageId": "message-id"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    sender = BrevoEmailSender(email_settings, client)

    async def claim_next(*args: Any, **kwargs: Any) -> ClaimedNotification:
        return claim

    async def email_destination(*args: Any, **kwargs: Any) -> str:
        return "private.person@example.test"

    class FakeSession:
        async def execute(self, statement: Any, params: dict[str, Any]) -> None:
            writes.append((str(statement), params))

    @asynccontextmanager
    async def fake_transaction(*args: Any, **kwargs: Any) -> Any:
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
            "url": "https://ajo.example.test/payroll/runs",
        },
    )
    monkeypatch.setattr(notification_worker, "transaction", fake_transaction)
    monkeypatch.setattr(
        notification_worker.structlog, "get_logger", lambda *args, **kwargs: Logger()
    )

    try:
        did_process = await notification_worker.process_next_notification(
            cooperative_id, {"email": sender}, email_settings
        )
    finally:
        await client.aclose()

    assert did_process is True
    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert request.url == "https://api.brevo.com/v3/smtp/email"
    assert request.headers["api-key"] == "fake-brevo-key-for-tests"
    body = json.loads(request.content)
    assert body == {
        "sender": {"name": "Ajo Notifications", "email": "noreply@example.test"},
        "to": [{"email": "private.person@example.test"}],
        "subject": "Action needed in Ajo Co-op: Payroll",
        "textContent": (
            "Action needed in Payroll; review it here: https://ajo.example.test/payroll/runs."
        ),
        "htmlContent": (
            "<p>Action needed in Payroll; review it "
            '<a href="https://ajo.example.test/payroll/runs">here</a>.</p>'
        ),
    }
    status_writes = [
        params for statement, params in writes if "UPDATE notifications SET status" in statement
    ]
    assert len(status_writes) == 1
    if expected_status == "sent":
        status_statement = next(
            statement for statement, _ in writes if "UPDATE notifications SET status" in statement
        )
        assert "SET status='sent'" in status_statement
    else:
        assert status_writes[0]["status"] == expected_status
        assert status_writes[0]["delay"] == (0 if expected_permanent else 10)
    logs = repr(logged)
    assert "fake-brevo-key-for-tests" not in logs
    assert "private.person@example.test" not in logs
    assert "p***@example.test" in logs


@pytest.mark.parametrize(
    ("missing", "error_message"),
    [
        ({}, "BREVO_API_KEY"),
        ({"brevo_api_key": "test-key"}, "EMAIL_FROM_ADDRESS"),
        (
            {"brevo_api_key": "test-key", "email_from_address": "sender@example.test"},
            "EMAIL_FROM_NAME",
        ),
    ],
)
def test_enabled_email_sender_requires_all_configuration(
    missing: dict[str, str], error_message: str
) -> None:
    settings = Settings(_env_file=None, app_env="development", email_enabled=True, **missing)

    with pytest.raises(RuntimeError, match=error_message):
        BrevoEmailSender(settings)


@pytest.mark.asyncio
async def test_notifications_dead_lists_staff_user_id_without_email_address(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app import cli as app_cli

    cooperative_id = uuid.uuid4()
    recipient_id = uuid.uuid4()
    notification_id = uuid.uuid4()

    class FakeResult:
        def all(self) -> list[Any]:
            return [
                type(
                    "DeadNotification",
                    (),
                    {
                        "id": notification_id,
                        "member_id": None,
                        "recipient_user_id": recipient_id,
                        "kind": "payroll_sign_off",
                        "attempts": 4,
                        "created_at": "created",
                        "next_attempt_at": "next",
                        "sent_at": None,
                    },
                )()
            ]

    class FakeSession:
        async def execute(self, statement: Any, params: dict[str, Any]) -> FakeResult:
            assert "recipient_user_id" in str(statement)
            return FakeResult()

    @asynccontextmanager
    async def fake_transaction(*args: Any, **kwargs: Any) -> Any:
        yield FakeSession()

    async def fake_resolve_slug(slug: str) -> tuple[uuid.UUID, str]:
        assert slug == "example"
        return cooperative_id, slug

    monkeypatch.setattr(app_cli, "transaction", fake_transaction)
    monkeypatch.setattr(app_cli, "resolve_tenant_slug", fake_resolve_slug)

    assert await app_cli.list_dead_notifications("example") == 0
    output = capsys.readouterr().out
    assert str(recipient_id) in output
    assert str(notification_id) in output
    assert "private.person@example.test" not in output
