"""Provider-neutral SMS delivery adapter for the outbox worker."""

from __future__ import annotations

from typing import Protocol
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings


class SmsSender(Protocol):
    async def send(self, destination: str, message: str) -> None: ...


class HttpSmsSender:
    """Send JSON to a configured SMS gateway; credentials are never stored in source.

    The gateway contract is POST {to, message, sender_id?} with Bearer authentication.
    A provider-specific adapter can replace this class after procurement.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        if not settings.sms_gateway_url:
            raise RuntimeError("SMS_GATEWAY_URL is required for the outbox worker")
        api_key = settings.sms_gateway_api_key.get_secret_value()
        if not api_key:
            raise RuntimeError("SMS_GATEWAY_API_KEY is required for the outbox worker")
        parsed = urlsplit(settings.sms_gateway_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("SMS_GATEWAY_URL must be an absolute HTTP URL")
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(
                "SMS_GATEWAY_URL must not embed credentials, query data, or a fragment"
            )
        if settings.app_env != "development" and parsed.scheme != "https":
            raise RuntimeError("SMS_GATEWAY_URL must use HTTPS outside development")
        self._url = settings.sms_gateway_url
        self._api_key = api_key
        self._sender_id = settings.sms_sender_id.strip()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=settings.sms_request_timeout_seconds)

    async def send(self, destination: str, message: str) -> None:
        payload = {"to": destination, "message": message}
        if self._sender_id:
            payload["sender_id"] = self._sender_id
        response = await self._client.post(
            self._url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=payload,
        )
        response.raise_for_status()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
