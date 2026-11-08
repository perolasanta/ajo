from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import client_ip, current_session, require_csrf
from app.domains.auth import service
from app.domains.auth.service import CurrentSession

router = APIRouter(tags=["account"])


class PasswordChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=128)


class PasswordChangeResponse(BaseModel):
    changed: bool


class SessionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    device: str
    created_at: datetime
    last_seen_at: datetime
    this_device: bool


class SessionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SessionSummary]


class SessionRevokedResponse(BaseModel):
    revoked: bool


class OtherSessionsRevokedResponse(BaseModel):
    revoked_count: int


@router.post(
    "/me/password",
    dependencies=[Depends(require_csrf)],
    response_model=PasswordChangeResponse,
)
async def post_change_password(
    body: PasswordChangeBody,
    request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, bool]:
    await service.change_password(
        current, body.current_password, body.new_password, client_ip(request)
    )
    return {"changed": True}


@router.get("/me/sessions", response_model=SessionListResponse)
async def get_sessions(
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, object]:
    return {"items": await service.list_sessions(current)}


@router.post(
    "/me/sessions/{session_id}/revoke",
    dependencies=[Depends(require_csrf)],
    response_model=SessionRevokedResponse,
)
async def post_revoke_session(
    session_id: uuid.UUID,
    request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, bool]:
    await service.revoke_session(current, session_id, client_ip(request))
    return {"revoked": True}


@router.post(
    "/me/sessions/revoke-others",
    dependencies=[Depends(require_csrf)],
    response_model=OtherSessionsRevokedResponse,
)
async def post_revoke_other_sessions(
    request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, int]:
    revoked_count = await service.revoke_other_sessions(current, client_ip(request))
    return {"revoked_count": revoked_count}
