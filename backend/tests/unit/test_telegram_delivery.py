from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.domains.notifications import eligibility, outbox
from app.worker.outbox import ClaimedNotification, PermanentDeliveryError, _telegram_message
from app.worker.telegram_sender import (
    HttpTelegramSender,
    TelegramRateLimited,
    TelegramRejected,
    set_telegram_webhook,
)

pytestmark = pytest.mark.unit


async def _resolved(
    recipients: list[eligibility.ActionRecipient],
) -> list[eligibility.ActionRecipient]:
    return recipients


@pytest.mark.parametrize("linked", [True, False])
async def test_enqueue_creates_channel_rows_only_for_linked_eligible_users(
    linked: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cooperative, entity, eligible, requester = (uuid.uuid4() for _ in range(4))
    recipients = [eligibility.ActionRecipient("payroll_sign_off", entity, eligible)]
    monkeypatch.setattr(
        outbox, "get_eligible_action_recipients", lambda *args: _resolved(recipients)
    )
    monkeypatch.setattr(
        outbox,
        "get_settings",
        lambda: Settings(
            app_env="development",
            email_enabled=True,
            telegram_enabled=True,
            public_base_url="https://ajo.example.test",
        ),
    )
    monkeypatch.setattr(outbox, "encrypt_payload", lambda payload: repr(payload))

    class Result:
        def __init__(self, value: bool):
            self.value = value

        def scalar_one_or_none(self) -> object | None:
            return uuid.uuid4() if self.value else None

    class Session:
        def __init__(self) -> None:
            self.rows: list[dict[str, Any]] = []

        async def execute(self, query: Any, params: dict[str, Any]) -> Result:
            statement = str(query).lower()
            if "telegram_links" in statement:
                return Result(linked)
            self.rows.append(params)
            return Result(False)

    session = Session()
    await outbox.enqueue_action_notifications(session, cooperative, "payroll_sign_off", entity)
    assert {row["channel"] for row in session.rows} == (
        {"email", "telegram"} if linked else {"email"}
    )
    assert len(session.rows) == (2 if linked else 1)
    assert all(
        row["recipient_user_id"] == eligible and row["recipient_user_id"] != requester
        for row in session.rows
    )
    assert all(
        row["payload"]
        == repr(
            {
                "section": "Payroll",
                "action_kind": "payroll sign-off",
                "message": "A payroll run needs your sign-off.",
                "url": f"https://ajo.example.test/payroll/runs/{entity}",
            }
        )
        for row in session.rows
    )
    assert "chat_id" not in " ".join(str(row) for row in session.rows)


async def test_telegram_sender_maps_forbidden_and_chat_not_found_to_permanent_rejection() -> None:
    for status, description in [
        (403, "Forbidden: bot was blocked"),
        (400, "Bad Request: chat not found"),
    ]:

        async def handler(
            request: httpx.Request, status: int = status, description: str = description
        ) -> httpx.Response:
            return httpx.Response(status, json={"ok": False, "description": description})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sender = HttpTelegramSender(
            Settings(app_env="development", telegram_bot_token="hidden"), client=client
        )
        with pytest.raises(TelegramRejected):
            await sender.send("chat-private", "Payroll https://ajo.example.test/payroll/runs")
        await client.aclose()


async def test_telegram_sender_honours_retry_after() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, headers={"Retry-After": "1"}, json={"ok": False, "parameters": {"retry_after": 37}}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = HttpTelegramSender(
        Settings(app_env="development", telegram_bot_token="hidden"), client=client
    )
    with pytest.raises(TelegramRateLimited) as caught:
        await sender.send("chat-private", "Ajo action alert")
    assert caught.value.retry_after == 37
    await client.aclose()


async def test_enqueue_conflict_target_includes_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    cooperative, entity, user_id = (uuid.uuid4() for _ in range(3))
    recipients = [eligibility.ActionRecipient("payroll_sign_off", entity, user_id)]
    monkeypatch.setattr(
        outbox, "get_eligible_action_recipients", lambda *args: _resolved(recipients)
    )
    monkeypatch.setattr(
        outbox,
        "get_settings",
        lambda: Settings(
            app_env="development",
            email_enabled=True,
            telegram_enabled=True,
            public_base_url="https://ajo.example.test",
        ),
    )
    monkeypatch.setattr(outbox, "encrypt_payload", lambda payload: "safe-payload")

    class Result:
        def scalar_one_or_none(self) -> object:
            return uuid.uuid4()

    class Session:
        def __init__(self) -> None:
            self.statements: list[str] = []
            self.channels: list[str] = []

        async def execute(self, query: Any, params: dict[str, Any]) -> Result:
            self.statements.append(str(query).lower())
            if "insert into notifications" in str(query).lower():
                self.channels.append(params["channel"])
            return Result()

    session = Session()
    await outbox.enqueue_action_notifications(session, cooperative, "payroll_sign_off", entity)
    assert session.channels == ["email", "telegram"]
    insert = next(query for query in session.statements if "insert into notifications" in query)
    assert "on conflict (cooperative_id,recipient_user_id,channel,kind,entity_id)" in insert
    assert all("telegram" not in query or "telegram_links" in query for query in session.statements)


def test_telegram_payload_is_action_sentence_plus_direct_link() -> None:
    claim = ClaimedNotification(
        uuid.uuid4(),
        uuid.uuid4(),
        None,
        uuid.uuid4(),
        "payroll_sign_off",
        "telegram",
        "v1",
        "encrypted",
        1,
        uuid.uuid4(),
    )
    payload = {
        "section": "Payroll",
        "action_kind": "payroll sign-off",
        "message": "A payroll run needs your sign-off.",
        "url": "https://ajo.example.test/payroll/runs/11111111-1111-1111-1111-111111111111",
    }
    message = _telegram_message(claim, payload)
    assert (
        message
        == "A payroll run needs your sign-off. https://ajo.example.test/payroll/runs/11111111-1111-1111-1111-111111111111"
    )
    with pytest.raises(PermanentDeliveryError):
        _telegram_message(claim, {**payload, "amount": "₦5,000"})


async def test_set_webhook_uses_public_url_and_secret_without_printing_them() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(
        app_env="production",
        public_base_url="https://ajo.example.test/base",
        telegram_bot_token="bot-token-private",
        telegram_webhook_secret="webhook-secret-private",
    )
    assert await set_telegram_webhook(settings, client)
    request = seen[0]
    assert request.url.path.endswith("/setWebhook")
    assert request.url.scheme == "https"
    assert request.content.find(b"https://ajo.example.test/base/webhooks/telegram") >= 0
    assert request.content.find(b"webhook-secret-private") >= 0
    assert b"bot-token-private" not in request.content
    await client.aclose()


@pytest.mark.parametrize(
    "failure", [TelegramRejected("telegram_forbidden"), TelegramRateLimited(37)]
)
async def test_worker_applies_telegram_permanent_and_retry_failures(
    failure: Exception, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from contextlib import asynccontextmanager

    from app.worker import outbox as worker_outbox

    cooperative, user_id, chat_id = uuid.uuid4(), uuid.uuid4(), "private-chat-id"
    claim = ClaimedNotification(
        uuid.uuid4(),
        cooperative,
        None,
        user_id,
        "payroll_sign_off",
        "telegram",
        "v1",
        "encrypted",
        1,
        uuid.uuid4(),
    )
    settings = Settings(app_env="development", outbox_max_attempts=5)
    monkeypatch.setattr(worker_outbox, "get_settings", lambda: settings)

    async def claimed(*args: Any, **kwargs: Any) -> ClaimedNotification:
        return claim

    monkeypatch.setattr(worker_outbox, "_claim_next", claimed)
    monkeypatch.setattr(
        worker_outbox,
        "decrypt_payload",
        lambda value: {
            "section": "Payroll",
            "action_kind": "payroll sign-off",
            "message": "A payroll run needs your sign-off.",
            "url": "https://ajo.example.test/payroll/runs/11111111-1111-1111-1111-111111111111",
        },
    )

    async def destination(*args: Any, **kwargs: Any) -> str:
        return chat_id

    monkeypatch.setattr(worker_outbox, "_telegram_destination", destination)

    class Sender:
        async def send(self, destination: str, message: str) -> None:
            raise failure

    captured: list[dict[str, Any]] = []

    async def finish(*args: Any, **kwargs: Any) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(worker_outbox, "_finish_failure", finish)
    deleted: list[dict[str, Any]] = []

    class Session:
        async def execute(self, query: Any, params: dict[str, Any]) -> None:
            deleted.append(params)

    @asynccontextmanager
    async def fake_transaction(*args: Any, **kwargs: Any):
        yield Session()

    monkeypatch.setattr(worker_outbox, "transaction", fake_transaction)
    assert await worker_outbox.process_next_notification(
        cooperative, {"telegram": Sender()}, settings
    )
    assert captured
    if isinstance(failure, TelegramRejected):
        assert captured[0]["permanent"] is True
        assert deleted == [{"cid": cooperative, "user_id": user_id, "chat_id": chat_id}]
    else:
        assert captured[0]["permanent"] is False
        assert captured[0]["retry_after"] == 37
        assert deleted == []
    assert chat_id not in caplog.text


async def test_webhook_cli_prints_status_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app import cli

    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(app_env="development", telegram_bot_token="do-not-print"),
    )

    async def succeeded(settings: Settings) -> bool:
        return True

    monkeypatch.setattr(cli, "set_telegram_webhook", succeeded)
    args = cli._parser().parse_args(["telegram-set-webhook"])
    assert await cli._run(args) == 0
    assert capsys.readouterr().out == "ok\n"

    async def failed(settings: Settings) -> bool:
        return False

    monkeypatch.setattr(cli, "set_telegram_webhook", failed)
    assert await cli._run(args) == 1
    assert capsys.readouterr().out == "failed\n"


def test_delivery_migration_uses_channel_aware_unique_index() -> None:
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[2]
        / "migrations/versions/0025_telegram_notification_delivery.py"
    )
    source = migration.read_text()
    assert "channel IN ('email','telegram')" in source
    assert "cooperative_id,recipient_user_id,channel,kind,entity_id" in source


async def test_disabled_telegram_row_is_dead_lettered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.worker import outbox as worker_outbox

    claim = ClaimedNotification(
        uuid.uuid4(),
        uuid.uuid4(),
        None,
        uuid.uuid4(),
        "payroll_sign_off",
        "telegram",
        "v1",
        "encrypted",
        1,
        uuid.uuid4(),
    )
    settings = Settings(app_env="development")
    monkeypatch.setattr(worker_outbox, "_claim_next", lambda *args: _claimed(claim))
    monkeypatch.setattr(
        worker_outbox,
        "decrypt_payload",
        lambda value: {
            "section": "Payroll",
            "action_kind": "payroll sign-off",
            "message": "A payroll run needs your sign-off.",
            "url": "https://ajo.example.test/payroll/runs/11111111-1111-1111-1111-111111111111",
        },
    )
    failures: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def finish(*args: Any, **kwargs: Any) -> None:
        failures.append((args, kwargs))

    monkeypatch.setattr(worker_outbox, "_finish_failure", finish)
    assert await worker_outbox.process_next_notification(claim.cooperative_id, {}, settings)
    assert failures[0][0][2] == "telegram_delivery_disabled"
    assert failures[0][1]["permanent"] is True


async def _claimed(claim: ClaimedNotification) -> ClaimedNotification:
    return claim
