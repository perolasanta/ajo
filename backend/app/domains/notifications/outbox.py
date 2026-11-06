"""Encrypted notification outbox primitives and provider-neutral action enqueueing."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.domains.notifications import eligibility

logger = logging.getLogger(__name__)


def encrypt_payload(payload: dict[str, Any]) -> str:
    key = get_settings().encryption_key
    if not key:
        raise RuntimeError("ENCRYPTION_KEY is required for notification outbox")
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return Fernet(key.encode()).encrypt(raw).decode("ascii")


def decrypt_payload(payload_encrypted: str) -> dict[str, Any]:
    key = get_settings().encryption_key
    if not key:
        raise RuntimeError("ENCRYPTION_KEY is required for notification outbox")
    try:
        decoded = json.loads(Fernet(key.encode()).decrypt(payload_encrypted.encode("ascii")))
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid encrypted notification payload") from exc
    if not isinstance(decoded, dict) or not all(isinstance(field, str) for field in decoded):
        raise ValueError("Invalid notification payload shape")
    return decoded


def build_action_payload(event_type: str, settings: Settings) -> dict[str, str]:
    spec = eligibility.ACTION_SPECS[event_type]
    base = settings.public_base_url.rstrip("/")
    parsed = urlsplit(base)
    if (
        not parsed.netloc
        or parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("PUBLIC_BASE_URL must be an absolute public URL")
    if settings.app_env != "development" and parsed.scheme != "https":
        raise ValueError("PUBLIC_BASE_URL must use HTTPS outside development")
    return {"section": spec.section, "url": f"{base}{spec.path}"}


async def enqueue_notification(
    session: AsyncSession,
    *,
    cooperative_id: uuid.UUID,
    member_id: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
    channel: str = "sms",
    template_version: str = "v1",
) -> uuid.UUID:
    notification_id = uuid.uuid4()
    await session.execute(
        text("""INSERT INTO notifications
          (id,cooperative_id,member_id,kind,channel,template_version,payload_encrypted)
          VALUES(:id,:cid,:member,:kind,:channel,:template_version,:payload)"""),
        {
            "id": notification_id,
            "cid": cooperative_id,
            "member": member_id,
            "kind": kind,
            "channel": channel,
            "template_version": template_version,
            "payload": encrypt_payload(payload),
        },
    )
    return notification_id


async def get_eligible_action_recipients(
    session: AsyncSession,
    cooperative_id: uuid.UUID,
    event_type: str,
    entity_id: uuid.UUID,
) -> tuple[eligibility.ActionRecipient, ...]:
    return await eligibility.get_action_recipients(session, cooperative_id, event_type, entity_id)


async def enqueue_action_notifications(
    session: AsyncSession,
    cooperative_id: uuid.UUID,
    event_type: str,
    entity_id: uuid.UUID,
) -> None:
    recipients = await get_eligible_action_recipients(
        session, cooperative_id, event_type, entity_id
    )
    if not recipients:
        return
    payload = encrypt_payload(build_action_payload(event_type, get_settings()))
    for recipient in recipients:
        await session.execute(
            text("""INSERT INTO notifications
              (id,cooperative_id,member_id,recipient_user_id,kind,entity_id,
               channel,template_version,payload_encrypted)
              VALUES(:id,:cid,:member_id,:recipient_user_id,:kind,:entity_id,
                     :channel,:template_version,:payload)
              ON CONFLICT (cooperative_id,recipient_user_id,kind,entity_id)
                WHERE recipient_user_id IS NOT NULL DO NOTHING"""),
            {
                "id": uuid.uuid4(),
                "cid": cooperative_id,
                "member_id": None,
                "recipient_user_id": recipient.user_id,
                "kind": event_type,
                "entity_id": entity_id,
                "channel": "email",
                "template_version": "v1",
                "payload": payload,
            },
        )


async def enqueue_action_notifications_safely(
    session: AsyncSession,
    cooperative_id: uuid.UUID,
    event_type: str,
    entity_id: uuid.UUID,
) -> None:
    """Attempt enqueue in a savepoint so notification errors cannot abort the action."""
    try:
        async with session.begin_nested():
            await enqueue_action_notifications(session, cooperative_id, event_type, entity_id)
    except Exception as exc:
        logger.warning(
            "action_notification_enqueue_failed",
            extra={"event_type": event_type, "error_code": type(exc).__name__},
        )
