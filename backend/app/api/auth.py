"""Staff authentication and session endpoints."""

from __future__ import annotations

import hmac
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.db import transaction
from app.core.errors import Forbidden
from app.core.security import csrf_matches, hash_csrf_token, hash_session_token, new_csrf_token
from app.domains.auth import service
from app.domains.auth.service import CurrentSession, LoginResult

router = APIRouter(prefix="/auth", tags=["auth"])
SESSION_COOKIE = "ajo_session"
CSRF_COOKIE = "ajo_csrf"


class LoginBody(BaseModel):
    cooperative_slug: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    totp_code: str | None = Field(default=None, min_length=6, max_length=8)
    recovery_code: str | None = Field(default=None, min_length=32, max_length=32)


class CodeBody(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class RoleChangeBody(BaseModel):
    role: str
    enabled: bool


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


async def require_csrf(
    request: Request,
    header_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    cookie_token = request.cookies.get(CSRF_COOKIE)
    if cookie_token is None or header_token is None or not csrf_matches(cookie_token, header_token):
        raise Forbidden("CSRF validation failed")
    parts = service.split_session_cookie(request.cookies.get(SESSION_COOKIE))
    if parts is not None:
        slug, token = parts
        tenant = await service.resolve_tenant_slug(slug)
        if tenant is not None:
            async with transaction(tenant[0]) as session:
                result = await session.execute(
                    text(
                        "SELECT csrf_token_hash FROM user_sessions "
                        "WHERE cooperative_id = :cooperative_id AND token_hash = :token_hash "
                        "AND revoked_at IS NULL"
                    ),
                    {
                        "cooperative_id": tenant[0],
                        "token_hash": hash_session_token(token),
                    },
                )
                stored_hash = result.scalar_one_or_none()
            if stored_hash is not None and not hmac.compare_digest(
                bytes(stored_hash), hash_csrf_token(cookie_token)
            ):
                raise Forbidden("CSRF validation failed")


async def current_session(request: Request) -> CurrentSession:
    return await service.load_session(
        request.cookies.get(SESSION_COOKIE), require_authenticated=True
    )


def set_session_cookies(response: Response, result: LoginResult) -> None:
    if result.session_cookie is not None:
        response.set_cookie(
            SESSION_COOKIE,
            result.session_cookie,
            max_age=result.session_max_age,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/api/v1/auth",
        )
    if result.csrf_token is not None:
        response.set_cookie(
            CSRF_COOKIE,
            result.csrf_token,
            max_age=result.session_max_age,
            httponly=False,
            secure=True,
            samesite="lax",
            path="/",
        )


@router.get("/csrf")
async def csrf_token(response: Response) -> dict[str, str]:
    token = new_csrf_token()
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=False,
        secure=True,
        samesite="lax",
        path="/",
    )
    return {"csrf_token": token}


@router.post("/login", dependencies=[Depends(require_csrf)])
async def login(body: LoginBody, request: Request) -> Response:
    result = await service.login(
        slug=body.cooperative_slug,
        email=body.email,
        password=body.password,
        totp_code=body.totp_code,
        recovery_code=body.recovery_code,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    response: JSONResponse = JSONResponse(
        result.data,
        status_code=401 if result.data.get("error") == "invalid_credentials" else 200,
    )
    set_session_cookies(response, result)
    return response


@router.post("/mfa/enroll", dependencies=[Depends(require_csrf)])
async def enroll_mfa(body: CodeBody, request: Request) -> dict[str, object]:
    codes, current = await service.complete_mfa_enrollment(
        request.cookies.get(SESSION_COOKIE), body.code, client_ip(request)
    )
    if current is None:
        raise Forbidden("Invalid verification code")
    return {"authenticated": True, "roles": list(current.roles), "recovery_codes": codes}


@router.post("/mfa/verify", dependencies=[Depends(require_csrf)])
async def verify_mfa(body: CodeBody, request: Request) -> dict[str, object]:
    current = await service.verify_pending_mfa(
        request.cookies.get(SESSION_COOKIE), body.code, client_ip(request)
    )
    if current is None:
        raise Forbidden("Invalid verification code")
    return {"authenticated": True, "roles": list(current.roles)}


@router.get("/me")
async def me(current: Annotated[CurrentSession, Depends(current_session)]) -> dict[str, object]:
    return {"user_id": str(current.user_id), "email": current.email, "roles": list(current.roles)}


@router.post("/logout", dependencies=[Depends(require_csrf)])
async def logout(
    request: Request, current: Annotated[CurrentSession, Depends(current_session)]
) -> Response:
    await service.logout(current, client_ip(request))
    response = JSONResponse({"logged_out": True})
    response.delete_cookie(
        SESSION_COOKIE, path="/api/v1/auth", secure=True, httponly=True, samesite="lax"
    )
    response.delete_cookie(CSRF_COOKIE, path="/", secure=True, samesite="lax")
    return response


@router.post("/revoke-all", dependencies=[Depends(require_csrf)])
async def revoke_all(
    request: Request, current: Annotated[CurrentSession, Depends(current_session)]
) -> Response:
    await service.logout(current, client_ip(request), revoke_all=True)
    response = JSONResponse({"sessions_revoked": True})
    response.delete_cookie(
        SESSION_COOKIE, path="/api/v1/auth", secure=True, httponly=True, samesite="lax"
    )
    return response


@router.post("/users/{user_id}/roles", dependencies=[Depends(require_csrf)])
async def change_role(
    user_id: uuid.UUID,
    body: RoleChangeBody,
    request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, bool]:
    await service.change_role(current, user_id, body.role, body.enabled, client_ip(request))
    return {"updated": True}
