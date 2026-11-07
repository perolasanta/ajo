from __future__ import annotations

import hmac
import re
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.auth import current_session, require_csrf
from app.core.config import get_settings
from app.core.db import transaction
from app.core.errors import Conflict, Forbidden
from app.domains.audit.service import append_audit
from app.domains.auth.service import CurrentSession
from app.domains.channels.telegram import decode_token_tenant, make_link_token, token_hash

router = APIRouter(tags=["telegram"])
webhook_router = APIRouter(tags=["telegram-webhook"])
_TOKEN_RE = re.compile(r"\A[A-Za-z0-9_-]{23,64}\Z")


@router.get("/me/telegram")
async def get_telegram_link(
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, bool]:
    async with transaction(current.cooperative_id) as session:
        row = await session.execute(
            text("SELECT 1 FROM telegram_links WHERE cooperative_id=:c AND user_id=:u"),
            {"c": current.cooperative_id, "u": current.user_id},
        )
        return {"linked": row.scalar_one_or_none() is not None}


@router.post("/me/telegram/link-token", dependencies=[Depends(require_csrf)])
async def create_link_token(
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, str | int]:
    settings = get_settings()
    if not settings.telegram_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    if not settings.telegram_bot_username:
        raise Conflict("Telegram linking is not configured")
    token = make_link_token(current.cooperative_id)
    async with transaction(current.cooperative_id) as session:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"telegram-link:{current.cooperative_id}:{current.user_id}"},
        )
        counts = await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE used_at IS NULL AND expires_at > now()), count(*) FILTER (WHERE created_at > now()-interval '1 hour') FROM telegram_link_tokens WHERE cooperative_id=:c AND user_id=:u"
            ),
            {"c": current.cooperative_id, "u": current.user_id},
        )
        live, recent = counts.one()
        if live >= 3 or recent >= 10:
            raise Forbidden("Telegram linking rate limit reached")
        await session.execute(
            text(
                "INSERT INTO telegram_link_tokens (cooperative_id,user_id,token_hash,expires_at) VALUES (:c,:u,:h,now()+interval '10 minutes')"
            ),
            {"c": current.cooperative_id, "u": current.user_id, "h": token_hash(token)},
        )
        await append_audit(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            role=current.roles[0] if current.roles else None,
            action="telegram_link_created",
            entity_type="telegram_link",
            entity_id=None,
            summary={},
            channel="web",
            ip=None,
        )
    username = settings.telegram_bot_username.lstrip("@")
    return {"deep_link": f"https://t.me/{quote(username)}?start={token}", "expires_in_seconds": 600}


@router.delete("/me/telegram", dependencies=[Depends(require_csrf)])
async def unlink_telegram(
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, bool]:
    async with transaction(current.cooperative_id) as session:
        await session.execute(
            text("DELETE FROM telegram_links WHERE cooperative_id=:c AND user_id=:u"),
            {"c": current.cooperative_id, "u": current.user_id},
        )
        await append_audit(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            role=current.roles[0] if current.roles else None,
            action="telegram_unlinked",
            entity_type="telegram_link",
            entity_id=None,
            summary={},
            channel="web",
            ip=None,
        )
    return {"linked": False}


@webhook_router.post("/webhooks/telegram")
async def telegram_webhook(request: Request) -> JSONResponse:
    settings = get_settings()
    if not settings.telegram_enabled:
        return JSONResponse({"ok": True})
    secret = settings.telegram_webhook_secret
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secret or not hmac.compare_digest(supplied, secret):
        return JSONResponse({"ok": True}, status_code=200)
    try:
        update: dict[str, Any] = await request.json()
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        text_value = message.get("text")
        chat_id = chat.get("id")
        if (
            chat.get("type") != "private"
            or not isinstance(text_value, str)
            or not isinstance(chat_id, (str, int))
        ):
            return JSONResponse({"ok": True})
        pieces = text_value.split()
        token = pieces[1] if len(pieces) == 2 and pieces[0] == "/start" else ""
        cooperative_id = decode_token_tenant(token) if _TOKEN_RE.fullmatch(token) else None
        linked = False
        if cooperative_id is not None:
            async with transaction(cooperative_id) as session:
                result = await session.execute(
                    text(
                        "SELECT user_id FROM telegram_link_tokens WHERE cooperative_id=:c AND token_hash=:h AND used_at IS NULL AND expires_at>now() FOR UPDATE"
                    ),
                    {"c": cooperative_id, "h": token_hash(token)},
                )
                row = result.first()
                if row:
                    user_id = row[0]
                    inserted = await session.execute(
                        text(
                            "INSERT INTO telegram_links (cooperative_id,user_id,chat_id) "
                            "VALUES (:c,:u,:chat) ON CONFLICT DO NOTHING RETURNING user_id"
                        ),
                        {"c": cooperative_id, "u": user_id, "chat": str(chat_id)},
                    )
                    if inserted.scalar_one_or_none() is not None:
                        await session.execute(
                            text(
                                "UPDATE telegram_link_tokens SET used_at=now() "
                                "WHERE cooperative_id=:c AND token_hash=:h"
                            ),
                            {"c": cooperative_id, "h": token_hash(token)},
                        )
                        await append_audit(
                            session,
                            cooperative_id=cooperative_id,
                            actor_user_id=user_id,
                            role=None,
                            action="telegram_linked",
                            entity_type="telegram_link",
                            entity_id=None,
                            summary={},
                            channel="telegram",
                            ip=None,
                        )
                        linked = True
        await _reply(
            settings.telegram_bot_token,
            chat_id,
            "Connected. You will get action alerts here."
            if linked
            else "Unable to connect this account.",
        )
    except Exception:
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": True})


async def _reply(bot_token: str, chat_id: str | int, message: str) -> None:
    if not bot_token:
        return
    import httpx

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": message},
            )
    except httpx.HTTPError:
        return
