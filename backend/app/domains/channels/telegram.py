from __future__ import annotations

import base64
import hashlib
import secrets
import uuid


def make_link_token(cooperative_id: uuid.UUID) -> str:
    tenant = base64.urlsafe_b64encode(cooperative_id.bytes).decode("ascii").rstrip("=")
    secret = base64.urlsafe_b64encode(secrets.token_bytes(30)).decode("ascii").rstrip("=")
    return f"{tenant}-{secret}"


def decode_token_tenant(token: str) -> uuid.UUID | None:
    if (
        not token
        or len(token) > 64
        or any(
            c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
            for c in token
        )
    ):
        return None
    tenant = token[:22]
    if len(token) < 23 or token[22] != "-":
        return None
    try:
        raw = base64.urlsafe_b64decode(tenant + "==")
        if len(raw) != 16:
            return None
        return uuid.UUID(bytes=raw)
    except (ValueError, TypeError):
        return None


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()
