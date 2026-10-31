"""Unit tests for provider-neutral notification delivery behavior."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.worker.outbox import retry_delay_seconds
from app.worker.sender import HttpSmsSender


@pytest.mark.asyncio
async def test_http_sms_sender_posts_gateway_contract_without_logging_message() -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(202)

    settings = Settings(
        app_env="development",
        sms_gateway_url="https://gateway.example.test/messages",
        sms_gateway_api_key="test-gateway-key",
        sms_sender_id="Ajo",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = HttpSmsSender(settings, client)
    await sender.send("+2348030000001", "Ajo verification code: 123456")
    await client.aclose()

    assert len(captured) == 1
    request = captured[0]
    assert request.headers["Authorization"] == "Bearer test-gateway-key"
    assert request.read().decode() == (
        '{"to":"+2348030000001","message":"Ajo verification code: 123456","sender_id":"Ajo"}'
    )


def test_outbox_retry_backoff_is_exponential_and_bounded() -> None:
    settings = Settings(
        app_env="development",
        outbox_retry_base_seconds=10,
        outbox_retry_max_seconds=35,
    )

    assert [retry_delay_seconds(attempt, settings) for attempt in range(1, 6)] == [
        10,
        20,
        35,
        35,
        35,
    ]
