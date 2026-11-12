"""Generic SMTP adapter for action-notification email delivery."""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Any

from app.core.config import Settings
from app.worker.email_sender import PermanentEmailDeliveryError

SMTP_TIMEOUT_SECONDS = 10.0


class RetryableEmailDeliveryError(Exception):
    """Safe SMTP error code for a delivery that should use outbox backoff."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SmtpEmailSender:
    """Send multipart action email using SMTP with STARTTLS or implicit TLS."""

    def __init__(
        self,
        settings: Settings,
        *,
        smtp_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not settings.email_enabled:
            raise RuntimeError("EMAIL_ENABLED must be true to create the SMTP sender")
        missing: list[str] = []
        if not settings.smtp_host.strip():
            missing.append("SMTP_HOST")
        if not settings.smtp_port.strip():
            missing.append("SMTP_PORT")
        if not settings.smtp_username.strip():
            missing.append("SMTP_USERNAME")
        if not settings.smtp_password.get_secret_value():
            missing.append("SMTP_PASSWORD")
        if not settings.smtp_security.strip():
            missing.append("SMTP_SECURITY")
        if missing:
            raise RuntimeError(
                "Required when EMAIL_ENABLED is true and EMAIL_PROVIDER=smtp: " + ", ".join(missing)
            )
        if not settings.email_from_address.strip():
            raise RuntimeError("EMAIL_FROM_ADDRESS is required when EMAIL_ENABLED is true")
        if not settings.email_from_name.strip():
            raise RuntimeError("EMAIL_FROM_NAME is required when EMAIL_ENABLED is true")

        try:
            port = int(settings.smtp_port.strip())
        except ValueError as exc:
            raise RuntimeError("SMTP_PORT must be an integer from 1 to 65535") from exc
        if not 1 <= port <= 65535:
            raise RuntimeError("SMTP_PORT must be an integer from 1 to 65535")

        security = settings.smtp_security.strip().lower()
        if security not in {"starttls", "ssl", "none"}:
            raise RuntimeError("SMTP_SECURITY must be starttls or ssl (none is development-only)")
        if security == "none" and settings.app_env != "development":
            raise RuntimeError("SMTP_SECURITY=none is allowed only in development")

        self._host = settings.smtp_host.strip()
        self._port = port
        self._username = settings.smtp_username.strip()
        self._password = settings.smtp_password.get_secret_value()
        self._security = security
        self._from_address = settings.email_from_address.strip()
        self._from_name = settings.email_from_name.strip()
        self._smtp_factory = smtp_factory

    async def send(
        self, recipient: str, subject: str, text_content: str, html_content: str
    ) -> None:
        message = EmailMessage()
        message["From"] = formataddr((self._from_name, self._from_address))
        message["To"] = recipient
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=False, usegmt=True)
        message["Message-ID"] = make_msgid()
        message.set_content(text_content)
        message.add_alternative(html_content, subtype="html")

        try:
            await asyncio.to_thread(self._send_sync, message)
        except TimeoutError:
            raise RetryableEmailDeliveryError("smtp_timeout") from None
        except smtplib.SMTPRecipientsRefused as exc:
            self._map_recipient_refusal(exc.recipients.values())
        except smtplib.SMTPResponseException as exc:
            self._map_response_code(exc.smtp_code)
        except (OSError, smtplib.SMTPServerDisconnected):
            raise RetryableEmailDeliveryError("smtp_connection_error") from None
        except smtplib.SMTPException:
            raise RetryableEmailDeliveryError("smtp_protocol_error") from None

    def _send_sync(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        factory = self._smtp_factory
        if factory is None:
            factory = smtplib.SMTP_SSL if self._security == "ssl" else smtplib.SMTP
        options: dict[str, Any] = {"timeout": SMTP_TIMEOUT_SECONDS}
        if self._security == "ssl":
            options["context"] = context
        with factory(self._host, self._port, **options) as client:
            if self._security == "starttls":
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
            client.login(self._username, self._password)
            refused = client.send_message(message)
            if refused:
                self._map_recipient_refusal(refused.values())

    @staticmethod
    def _map_recipient_refusal(responses: Any) -> None:
        codes = [int(response[0]) for response in responses]
        if codes and all(400 <= code < 500 for code in codes):
            raise RetryableEmailDeliveryError("smtp_temporary_failure") from None
        raise PermanentEmailDeliveryError("smtp_recipient_rejected") from None

    @staticmethod
    def _map_response_code(code: int) -> None:
        if 400 <= code < 500:
            raise RetryableEmailDeliveryError("smtp_temporary_failure") from None
        if 500 <= code < 600:
            raise PermanentEmailDeliveryError("smtp_permanent_failure") from None
        raise RetryableEmailDeliveryError("smtp_protocol_error") from None

    async def aclose(self) -> None:
        """SMTP connections are scoped to each send and need no async cleanup."""
