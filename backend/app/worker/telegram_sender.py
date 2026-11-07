"""Telegram Bot API sender for private staff action alerts."""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from app.core.config import Settings


class TelegramRejected(Exception):
    """Telegram rejected a destination permanently (blocked bot or missing chat)."""


class TelegramRateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("telegram_rate_limited")
        self.retry_after = max(1, retry_after)


class HttpTelegramSender:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        token = settings.telegram_bot_token
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required for the Telegram sender")
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        if urlsplit(self._url).scheme != "https":
            raise RuntimeError("Telegram Bot API must use HTTPS")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=settings.sms_request_timeout_seconds)

    async def send(self, chat_id: str, message: str) -> None:
        response = await self._client.post(
            self._url,
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
        )
        if response.status_code == 429:
            retry_after = 1
            try:
                retry_after = int(response.json().get("parameters", {}).get("retry_after", 1))
            except (ValueError, TypeError, AttributeError):
                try:
                    retry_after = int(response.headers.get("Retry-After", "1"))
                except ValueError:
                    pass
            raise TelegramRateLimited(retry_after)
        if response.status_code == 403:
            raise TelegramRejected("telegram_forbidden")
        if response.status_code == 400:
            try:
                description = str(response.json().get("description", "")).lower()
            except (ValueError, AttributeError):
                description = ""
            if "chat not found" in description:
                raise TelegramRejected("telegram_chat_not_found")
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("telegram_invalid_response") from exc
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise RuntimeError("telegram_send_failed")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


async def set_telegram_webhook(settings: Settings, client: httpx.AsyncClient | None = None) -> bool:
    """Register webhook; return provider success without exposing credentials or response text."""
    token = settings.telegram_bot_token
    secret = settings.telegram_webhook_secret
    base = settings.public_base_url.rstrip("/")
    parsed = urlsplit(base)
    if not token or not secret or not parsed.netloc or parsed.scheme != "https":
        return False
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return False
    owns_client = client is None
    request_client = client or httpx.AsyncClient(timeout=10)
    try:
        response = await request_client.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            json={"url": f"{base}/webhooks/telegram", "secret_token": secret},
        )
        return response.is_success and response.json().get("ok") is True
    except (httpx.HTTPError, ValueError, AttributeError):
        return False
    finally:
        if owns_client:
            await request_client.aclose()
