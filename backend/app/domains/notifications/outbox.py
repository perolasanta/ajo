"""Encrypted notification outbox primitives."""

from __future__ import annotations

import json
import uuid
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings


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
