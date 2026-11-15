"""Claim, render, and deliver encrypted notification outbox rows."""

from __future__ import annotations

import asyncio
import hmac
import math
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import structlog
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.db import connection, transaction
from app.domains.notifications.eligibility import ACTION_SPECS
from app.domains.notifications.outbox import decrypt_payload
from app.worker.email_sender import PermanentEmailDeliveryError, build_action_email
from app.worker.smtp_email_sender import RetryableEmailDeliveryError
from app.worker.telegram_sender import TelegramRateLimited, TelegramRejected

_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class ClaimedNotification:
    id: uuid.UUID
    cooperative_id: uuid.UUID
    member_id: uuid.UUID | None
    recipient_user_id: uuid.UUID | None
    kind: str
    channel: str
    template_version: str
    payload_encrypted: str | None
    attempts: int
    claim_token: uuid.UUID


class PermanentDeliveryError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def retry_delay_seconds(attempt: int, settings: Settings) -> int:
    exponent = max(0, attempt - 1)
    base_seconds: int = settings.outbox_retry_base_seconds
    max_seconds: int = settings.outbox_retry_max_seconds
    delay_seconds: int = base_seconds * (2**exponent)
    return min(delay_seconds, max_seconds)


async def _claim_next(
    cooperative_id: uuid.UUID, settings: Settings, channels: list[str]
) -> ClaimedNotification | None:
    claim_token = uuid.uuid4()
    async with transaction(cooperative_id) as session:
        await session.execute(
            text("""UPDATE notifications SET status='failed',claimed_at=NULL,claim_token=NULL,
              last_error_code='worker_crash_limit'
              WHERE cooperative_id=:cid AND status='processing' AND channel = ANY(:channels)
                AND claimed_at <= now()-make_interval(secs => :lease_seconds)
                AND attempts >= :max_attempts"""),
            {
                "cid": cooperative_id,
                "lease_seconds": settings.outbox_claim_lease_seconds,
                "max_attempts": settings.outbox_max_attempts,
                "channels": channels,
            },
        )
        row = (
            (
                await session.execute(
                    text("""SELECT id,cooperative_id,member_id,recipient_user_id,kind,channel,template_version,
                  payload_encrypted,attempts FROM notifications
                  WHERE cooperative_id=:cid AND attempts < :max_attempts
                    AND channel = ANY(:channels)
                    AND ((status='pending' AND next_attempt_at<=now()) OR
                      (status='processing' AND claimed_at<=now()-make_interval(secs => :lease_seconds)))
                  ORDER BY next_attempt_at,created_at
                  LIMIT 1 FOR UPDATE SKIP LOCKED"""),
                    {
                        "cid": cooperative_id,
                        "lease_seconds": settings.outbox_claim_lease_seconds,
                        "max_attempts": settings.outbox_max_attempts,
                        "channels": channels,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        attempts = int(row["attempts"]) + 1
        await session.execute(
            text("""UPDATE notifications SET status='processing',claimed_at=now(),claim_token=:claim,
              attempts=:attempts,last_error_code=NULL
              WHERE cooperative_id=:cid AND id=:id"""),
            {
                "cid": cooperative_id,
                "id": row["id"],
                "claim": claim_token,
                "attempts": attempts,
            },
        )
        return ClaimedNotification(
            id=row["id"],
            cooperative_id=row["cooperative_id"],
            member_id=row["member_id"],
            recipient_user_id=row["recipient_user_id"],
            kind=row["kind"],
            channel=row["channel"],
            template_version=row["template_version"],
            payload_encrypted=row["payload_encrypted"],
            attempts=attempts,
            claim_token=claim_token,
        )


async def _consent_is_current(
    claim: ClaimedNotification, payload: dict[str, Any], settings: Settings
) -> bool:
    try:
        consent_id = uuid.UUID(str(payload["consent_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise PermanentDeliveryError("invalid_consent_reference") from exc
    async with transaction(claim.cooperative_id) as session:
        row = (
            (
                await session.execute(
                    text("""SELECT status,expires_at>clock_timestamp() AS consent_unexpired,
                  otp_hash,otp_expires_at IS NOT NULL AND otp_expires_at>clock_timestamp() AS otp_unexpired
                  FROM member_consents WHERE cooperative_id=:cid AND id=:consent_id"""),
                    {"cid": claim.cooperative_id, "consent_id": consent_id},
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None or row["status"] != "pending" or not row["consent_unexpired"]:
        return False
    if claim.kind == "confirmation_request":
        return True
    if claim.kind != "confirmation_otp":
        raise PermanentDeliveryError("unsupported_notification_kind")
    code = payload.get("code")
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
        raise PermanentDeliveryError("invalid_otp_payload")
    if not row["otp_unexpired"]:
        return False
    expected_hash = hmac.digest(
        settings.secret_key.encode(), f"{consent_id}:{code}".encode(), "sha256"
    )
    return row["otp_hash"] is not None and hmac.compare_digest(row["otp_hash"], expected_hash)


async def _destination(claim: ClaimedNotification, payload: dict[str, Any]) -> str:
    destination = payload.get("destination")
    if destination is None and claim.kind == "confirmation_request":
        if claim.member_id is None:
            raise PermanentDeliveryError("invalid_sms_recipient_reference")
        async with transaction(claim.cooperative_id) as session:
            destination = (
                await session.execute(
                    text(
                        "SELECT phone_e164 FROM members WHERE cooperative_id=:cid AND id=:member_id"
                    ),
                    {"cid": claim.cooperative_id, "member_id": claim.member_id},
                )
            ).scalar_one_or_none()
    if not isinstance(destination, str) or not _E164.fullmatch(destination):
        raise PermanentDeliveryError("invalid_sms_destination")
    return destination


async def _email_destination(claim: ClaimedNotification) -> str:
    if claim.recipient_user_id is None:
        raise PermanentDeliveryError("invalid_email_recipient_reference")
    async with transaction(claim.cooperative_id) as session:
        destination = (
            await session.execute(
                text("SELECT email FROM users WHERE cooperative_id=:cid AND id=:user_id"),
                {"cid": claim.cooperative_id, "user_id": claim.recipient_user_id},
            )
        ).scalar_one_or_none()
    if not isinstance(destination, str) or not _EMAIL.fullmatch(destination):
        raise PermanentDeliveryError("invalid_email_destination")
    return destination


async def _telegram_destination(claim: ClaimedNotification) -> str:
    if claim.recipient_user_id is None:
        raise PermanentDeliveryError("invalid_telegram_recipient_reference")
    async with transaction(claim.cooperative_id) as session:
        destination = (
            await session.execute(
                text(
                    "SELECT chat_id FROM telegram_links WHERE cooperative_id=:cid AND user_id=:user_id"
                ),
                {"cid": claim.cooperative_id, "user_id": claim.recipient_user_id},
            )
        ).scalar_one_or_none()
    if not isinstance(destination, str) or not destination:
        raise PermanentDeliveryError("telegram_link_missing")
    return destination


def _telegram_message(claim: ClaimedNotification, payload: dict[str, Any]) -> str:
    section, action_kind, message, link = (
        payload.get(key) for key in ("section", "action_kind", "message", "url")
    )
    parsed = urlsplit(link) if isinstance(link, str) else None
    spec = ACTION_SPECS.get(claim.kind)
    if (
        claim.recipient_user_id is None
        or set(payload) != {"section", "action_kind", "message", "url"}
        or spec is None
        or section != spec.section_label
        or action_kind != spec.action_kind
        or message != spec.sentence
        or parsed is None
        or parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise PermanentDeliveryError("invalid_telegram_action_payload")
    return f"{message} {link}"


def _masked_recipient_fields(channel: str, recipient: str | None) -> dict[str, str]:
    if channel != "email" or recipient is None:
        return {}
    if not _EMAIL.fullmatch(recipient):
        return {"recipient": "***"}
    local_part, domain = recipient.rsplit("@", 1)
    return {"recipient": f"{local_part[0]}***@{domain}"}


async def _message(claim: ClaimedNotification, payload: dict[str, Any], settings: Settings) -> str:
    if claim.channel != "sms":
        raise PermanentDeliveryError("unsupported_notification_channel")
    async with transaction(claim.cooperative_id) as session:
        template = (
            await session.execute(
                text("""SELECT body FROM notification_templates
                  WHERE kind=:kind AND channel=:channel AND version=:version"""),
                {
                    "kind": claim.kind,
                    "channel": claim.channel,
                    "version": claim.template_version,
                },
            )
        ).scalar_one_or_none()
    if not isinstance(template, str):
        raise PermanentDeliveryError("notification_template_missing")
    if claim.kind == "confirmation_request":
        link_path = payload.get("url")
        if not isinstance(link_path, str) or not link_path.startswith("/api/v1/confirm/"):
            raise PermanentDeliveryError("invalid_confirmation_link")
        base = settings.public_base_url.rstrip("/")
        parsed = urlsplit(base)
        if not parsed.netloc or parsed.scheme not in {"http", "https"}:
            raise PermanentDeliveryError("public_base_url_missing")
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise PermanentDeliveryError("public_base_url_invalid")
        if settings.app_env != "development" and parsed.scheme != "https":
            raise PermanentDeliveryError("public_base_url_requires_https")
        values = {"link": f"{base}{link_path}"}
    elif claim.kind == "confirmation_otp":
        seconds = payload.get("otp_validity_seconds")
        code = payload.get("code")
        if not isinstance(seconds, int) or not isinstance(code, str):
            raise PermanentDeliveryError("invalid_otp_payload")
        values = {"code": code, "minutes": str(max(1, math.ceil(seconds / 60)))}
    else:
        raise PermanentDeliveryError("unsupported_notification_kind")
    try:
        return template.format_map(values)
    except (KeyError, ValueError) as exc:
        raise PermanentDeliveryError("invalid_notification_template") from exc


async def _finish_sent(claim: ClaimedNotification) -> None:
    async with transaction(claim.cooperative_id) as session:
        await session.execute(
            text("""UPDATE notifications SET status='sent',sent_at=now(),claimed_at=NULL,
              claim_token=NULL,last_error_code=NULL
              WHERE cooperative_id=:cid AND id=:id AND claim_token=:claim"""),
            {"cid": claim.cooperative_id, "id": claim.id, "claim": claim.claim_token},
        )


async def _finish_expired(claim: ClaimedNotification) -> None:
    async with transaction(claim.cooperative_id) as session:
        await session.execute(
            text("""UPDATE notifications SET status='expired',claimed_at=NULL,claim_token=NULL,
              last_error_code=NULL
              WHERE cooperative_id=:cid AND id=:id AND claim_token=:claim"""),
            {"cid": claim.cooperative_id, "id": claim.id, "claim": claim.claim_token},
        )


async def _finish_failure(
    claim: ClaimedNotification,
    settings: Settings,
    error_code: str,
    *,
    permanent: bool,
    retry_after: int | None = None,
) -> None:
    dead_letter = permanent or claim.attempts >= settings.outbox_max_attempts
    delay = (
        0
        if dead_letter
        else retry_after
        if retry_after is not None
        else retry_delay_seconds(claim.attempts, settings)
    )
    async with transaction(claim.cooperative_id) as session:
        await session.execute(
            text("""UPDATE notifications SET status=:status,claimed_at=NULL,claim_token=NULL,
              last_error_code=:error_code,
              next_attempt_at=CASE WHEN :delay=0 THEN next_attempt_at
                ELSE now()+make_interval(secs => :delay) END
              WHERE cooperative_id=:cid AND id=:id AND claim_token=:claim"""),
            {
                "cid": claim.cooperative_id,
                "id": claim.id,
                "claim": claim.claim_token,
                "status": "failed" if dead_letter else "pending",
                "error_code": error_code[:80],
                "delay": delay,
            },
        )


async def process_next_notification(
    cooperative_id: uuid.UUID,
    senders: Mapping[str, Any],
    settings: Settings | None = None,
) -> bool:
    current_settings = settings or get_settings()
    # Claim disabled channels too so they can be dead-lettered with an explicit reason.
    channels = sorted(set(senders) | {"email", "telegram"})
    claim = await _claim_next(cooperative_id, current_settings, channels)
    if claim is None:
        return False
    log = structlog.get_logger("outbox")
    recipient: str | None = None
    try:
        if claim.payload_encrypted is None:
            raise PermanentDeliveryError("notification_payload_missing")
        try:
            payload = decrypt_payload(claim.payload_encrypted)
        except ValueError as exc:
            raise PermanentDeliveryError("notification_payload_invalid") from exc
        if claim.channel == "sms":
            if not await _consent_is_current(claim, payload, current_settings):
                await _finish_expired(claim)
                return True
            recipient = await _destination(claim, payload)
            sms_message = await _message(claim, payload, current_settings)
            await senders["sms"].send(recipient, sms_message)
        elif claim.channel == "email":
            if "email" not in senders:
                raise PermanentEmailDeliveryError("email_delivery_disabled")
            recipient = await _email_destination(claim)
            email_message = build_action_email(claim.kind, payload, current_settings)
            await senders["email"].send(
                recipient,
                email_message.subject,
                email_message.text,
                email_message.html,
            )
        elif claim.channel == "telegram":
            sender = senders.get("telegram")
            if sender is None:
                raise PermanentDeliveryError("telegram_delivery_disabled")
            recipient = await _telegram_destination(claim)
            await sender.send(recipient, _telegram_message(claim, payload))
        else:
            raise PermanentDeliveryError("unsupported_notification_channel")
    except PermanentEmailDeliveryError as exc:
        await _finish_failure(claim, current_settings, exc.code, permanent=True)
        log.warning(
            "notification_dead_lettered",
            cooperative_id=str(cooperative_id),
            notification_id=str(claim.id),
            kind=claim.kind,
            error_code=exc.code,
            **_masked_recipient_fields(claim.channel, recipient),
        )
    except RetryableEmailDeliveryError as exc:
        await _finish_failure(claim, current_settings, exc.code, permanent=False)
        log.warning(
            "notification_delivery_failed",
            cooperative_id=str(cooperative_id),
            notification_id=str(claim.id),
            kind=claim.kind,
            attempt=claim.attempts,
            error_code=exc.code,
            **_masked_recipient_fields(claim.channel, recipient),
        )
    except PermanentDeliveryError as exc:
        await _finish_failure(claim, current_settings, exc.code, permanent=True)
        log.warning(
            "notification_dead_lettered",
            cooperative_id=str(cooperative_id),
            notification_id=str(claim.id),
            kind=claim.kind,
            error_code=exc.code,
            **_masked_recipient_fields(claim.channel, recipient),
        )
    except TelegramRejected as exc:
        if claim.recipient_user_id is not None:
            async with transaction(claim.cooperative_id) as session:
                await session.execute(
                    text(
                        "DELETE FROM telegram_links WHERE cooperative_id=:cid AND user_id=:user_id AND chat_id=:chat_id"
                    ),
                    {
                        "cid": claim.cooperative_id,
                        "user_id": claim.recipient_user_id,
                        "chat_id": recipient,
                    },
                )
        await _finish_failure(claim, current_settings, str(exc), permanent=True)
        log.warning(
            "notification_dead_lettered",
            cooperative_id=str(cooperative_id),
            notification_id=str(claim.id),
            kind=claim.kind,
            error_code=str(exc),
        )
    except TelegramRateLimited as exc:
        await _finish_failure(
            claim,
            current_settings,
            "telegram_rate_limited",
            permanent=False,
            retry_after=exc.retry_after,
        )
        log.warning(
            "notification_delivery_failed",
            cooperative_id=str(cooperative_id),
            notification_id=str(claim.id),
            kind=claim.kind,
            attempt=claim.attempts,
            error_code="telegram_rate_limited",
        )
    except Exception as exc:
        error_code = type(exc).__name__
        await _finish_failure(claim, current_settings, error_code, permanent=False)
        log.warning(
            "notification_delivery_failed",
            cooperative_id=str(cooperative_id),
            notification_id=str(claim.id),
            kind=claim.kind,
            attempt=claim.attempts,
            error_code=error_code,
            **_masked_recipient_fields(claim.channel, recipient),
        )
    else:
        await _finish_sent(claim)
        log.info(
            "notification_sent",
            cooperative_id=str(cooperative_id),
            notification_id=str(claim.id),
            kind=claim.kind,
            **_masked_recipient_fields(claim.channel, recipient),
        )
    return True


async def active_cooperative_ids() -> list[uuid.UUID]:
    async with connection() as db_connection:
        result = await db_connection.execute(
            text(
                "SELECT cooperative_id FROM tenant_directory WHERE status='active' ORDER BY cooperative_id"
            )
        )
        return list(result.scalars().all())


async def run_forever(senders: Mapping[str, Any], settings: Settings | None = None) -> None:
    current_settings = settings or get_settings()
    log = structlog.get_logger("outbox")
    while True:
        processed = 0
        try:
            cooperative_ids = await active_cooperative_ids()
        except Exception as exc:
            log.warning("outbox_tenant_scan_failed", error_code=type(exc).__name__)
            await asyncio.sleep(current_settings.outbox_poll_interval_seconds)
            continue
        for cooperative_id in cooperative_ids:
            for _ in range(current_settings.outbox_batch_size_per_tenant):
                try:
                    did_process = await process_next_notification(
                        cooperative_id, senders, current_settings
                    )
                except Exception as exc:
                    log.warning(
                        "outbox_tenant_poll_failed",
                        cooperative_id=str(cooperative_id),
                        error_code=type(exc).__name__,
                    )
                    break
                if not did_process:
                    break
                processed += 1
        if processed == 0:
            await asyncio.sleep(current_settings.outbox_poll_interval_seconds)
