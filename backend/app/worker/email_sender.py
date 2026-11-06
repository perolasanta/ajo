"""Brevo transactional email adapter for action notifications."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings
from app.domains.notifications.eligibility import ACTION_SPECS

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


@dataclass(frozen=True)
class ActionEmail:
    subject: str
    text: str
    html: str


class PermanentEmailDeliveryError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_action_email(event_type: str, payload: dict[str, Any], settings: Settings) -> ActionEmail:
    spec = ACTION_SPECS.get(event_type)
    if spec is None or set(payload) != {"section", "url"}:
        raise PermanentEmailDeliveryError("invalid_action_email_payload")
    section = payload.get("section")
    url = payload.get("url")
    if section != spec.section or not isinstance(url, str):
        raise PermanentEmailDeliveryError("invalid_action_email_payload")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PermanentEmailDeliveryError("invalid_action_email_link")
    if settings.app_env != "development" and parsed.scheme != "https":
        raise PermanentEmailDeliveryError("action_email_link_requires_https")

    subject = f"Action needed in {settings.app_name}: {section}"
    text_content = f"Action needed in {section}; review it here: {url}."
    html_content = (
        f"<p>Action needed in {escape(section)}; review it "
        f'<a href="{escape(url, quote=True)}">here</a>.</p>'
    )
    return ActionEmail(subject=subject, text=text_content, html=html_content)


class BrevoEmailSender:
    """Send one transactional email using Brevo's v3 SMTP endpoint."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        if not settings.email_enabled:
            raise RuntimeError("EMAIL_ENABLED must be true to create the Brevo sender")
        api_key = settings.brevo_api_key.get_secret_value().strip()
        if not api_key:
            raise RuntimeError("BREVO_API_KEY is required when EMAIL_ENABLED is true")
        if not settings.email_from_address.strip():
            raise RuntimeError("EMAIL_FROM_ADDRESS is required when EMAIL_ENABLED is true")
        if not settings.email_from_name.strip():
            raise RuntimeError("EMAIL_FROM_NAME is required when EMAIL_ENABLED is true")
        if urlsplit(BREVO_API_URL).scheme != "https":
            raise RuntimeError("Brevo API must use HTTPS")

        self._api_key = api_key
        self._from_address = settings.email_from_address.strip()
        self._from_name = settings.email_from_name.strip()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=10.0)

    async def send(
        self,
        recipient: str,
        subject: str,
        text_content: str,
        html_content: str,
    ) -> None:
        response = await self._client.post(
            BREVO_API_URL,
            headers={
                "api-key": self._api_key,
                "accept": "application/json",
                "content-type": "application/json",
            },
            json={
                "sender": {"name": self._from_name, "email": self._from_address},
                "to": [{"email": recipient}],
                "subject": subject,
                "textContent": text_content,
                "htmlContent": html_content,
            },
        )
        if 400 <= response.status_code < 500:
            raise PermanentEmailDeliveryError("brevo_http_4xx")
        response.raise_for_status()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
