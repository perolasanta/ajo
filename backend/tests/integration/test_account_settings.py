from __future__ import annotations

import secrets
import uuid
from typing import Any

import asyncpg
import httpx
import pyotp
import pytest

from app.core.db import asyncpg_dsn
from app.core.security import hash_password
from app.domains.auth import service as auth_service
from app.domains.auth.service import LoginResult
from tests.conftest import create_tenant

pytestmark = pytest.mark.db
ROLES = ("admin", "officer", "treasurer", "committee", "auditor")
CURRENT_PASSWORD = "Test-only-password!290"


@pytest.fixture(params=ROLES)
async def account(request: pytest.FixtureRequest, test_database: Any) -> dict[str, Any]:
    role = str(request.param)
    app = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    seed = await create_tenant(
        app,
        name="Account settings test",
        slug_prefix="account-test",
        roles=(role,),
        password_hash=hash_password(CURRENT_PASSWORD),
    )
    cooperative_id = seed.cooperative_id
    user_id = seed.user_ids[0]
    slug = f"account-test-{cooperative_id.hex}"
    email = await app.fetchval(
        "SELECT email FROM users WHERE cooperative_id=$1 AND id=$2", cooperative_id, user_id
    )
    await app.execute(
        "INSERT INTO tenant_directory (slug, cooperative_id) VALUES ($1, $2)",
        slug,
        cooperative_id,
    )
    await app.close()

    login = await auth_service.login(
        slug=slug,
        email=email,
        password=CURRENT_PASSWORD,
        totp_code=None,
        recovery_code=None,
        ip="127.0.0.1",
        user_agent="pytest Chrome on Linux",
    )
    assert login.session_cookie is not None
    secret = pyotp.parse_uri(str(login.data["provisioning_uri"])).secret
    recovery_codes, current = await auth_service.complete_mfa_enrollment(
        login.session_cookie, pyotp.TOTP(secret).now(), "127.0.0.1"
    )
    assert current is not None
    assert len(recovery_codes) == 10
    yield {
        "cooperative_id": cooperative_id,
        "email": email,
        "role": role,
        "secret": secret,
        "session_cookie": login.session_cookie,
        "session_id": current.session_id,
        "slug": slug,
        "user_id": user_id,
    }


async def _client_with_session(cookie: str) -> httpx.AsyncClient:
    from app.main import create_app

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()),
        base_url="https://testserver",
    )
    client.cookies.set("ajo_session", cookie, path="/api/v1")
    return client


async def _post_as(cookie: str, path: str, body: dict[str, str]) -> httpx.Response:
    async with await _client_with_session(cookie) as client:
        csrf = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]
        return await client.post(
            f"/api/v1{path}",
            headers={"X-CSRF-Token": csrf},
            json=body,
        )


async def test_wrong_current_password_rejected_for_each_role(account: dict[str, Any]) -> None:
    response = await _post_as(
        account["session_cookie"],
        "/me/password",
        {"current_password": "not-the-current-password", "new_password": "Better-password!290"},
    )
    assert response.status_code == 403
    assert "not-the-current-password" not in response.text


async def test_weak_password_rejected_for_each_role(account: dict[str, Any]) -> None:
    response = await _post_as(
        account["session_cookie"],
        "/me/password",
        {"current_password": CURRENT_PASSWORD, "new_password": "password1234"},
    )
    assert response.status_code == 422


async def test_new_password_must_differ_from_current_for_each_role(
    account: dict[str, Any],
) -> None:
    response = await _post_as(
        account["session_cookie"],
        "/me/password",
        {"current_password": CURRENT_PASSWORD, "new_password": CURRENT_PASSWORD},
    )
    assert response.status_code == 422


async def test_password_change_revokes_other_sessions_but_keeps_current(
    account: dict[str, Any], test_database: Any
) -> None:
    second_login: LoginResult = await auth_service.login(
        slug=account["slug"],
        email=account["email"],
        password=CURRENT_PASSWORD,
        totp_code=pyotp.TOTP(account["secret"]).now(),
        recovery_code=None,
        ip="127.0.0.2",
        user_agent="pytest Firefox on Android",
    )
    assert second_login.session_cookie is not None
    second_session = await auth_service.load_session(
        second_login.session_cookie, require_authenticated=True
    )

    changed = await _post_as(
        account["session_cookie"],
        "/me/password",
        {"current_password": CURRENT_PASSWORD, "new_password": "A-better-password!291"},
    )
    assert changed.status_code == 200

    async with await _client_with_session(account["session_cookie"]) as client:
        sessions = await client.get("/api/v1/me/sessions")
    assert sessions.status_code == 200
    items = sessions.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == str(account["session_id"])
    assert items[0]["this_device"] is True
    assert items[0]["device"] == "Chrome on Linux"
    assert "token" not in items[0]
    assert "ip" not in items[0]
    assert "user_agent" not in items[0]

    app = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    try:
        async with app.transaction():
            await app.execute(
                "SELECT set_config('app.cooperative_id',$1,true)", str(account["cooperative_id"])
            )
            assert await app.fetchval(
                "SELECT revoked_at IS NULL FROM user_sessions WHERE cooperative_id=$1 AND id=$2",
                account["cooperative_id"],
                account["session_id"],
            )
            assert await app.fetchval(
                "SELECT revoked_at IS NOT NULL FROM user_sessions "
                "WHERE cooperative_id=$1 AND id=$2",
                account["cooperative_id"],
                second_session.session_id,
            )
    finally:
        await app.close()


async def test_user_cannot_revoke_another_users_session(
    account: dict[str, Any], test_database: Any
) -> None:
    other_user_id = uuid.uuid4()
    token_hash = secrets.token_bytes(32)
    csrf_hash = secrets.token_bytes(32)
    app = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    try:
        async with app.transaction():
            await app.execute(
                "SELECT set_config('app.cooperative_id',$1,true)", str(account["cooperative_id"])
            )
            await app.execute(
                "INSERT INTO users(id,cooperative_id,email,password_hash,mfa_enabled_at) "
                "VALUES($1,$2,$3,$4,now())",
                other_user_id,
                account["cooperative_id"],
                f"other-{other_user_id.hex}@example.test",
                hash_password(CURRENT_PASSWORD),
            )
            await app.execute(
                "INSERT INTO user_roles(cooperative_id,user_id,role) VALUES($1,$2,'officer')",
                account["cooperative_id"],
                other_user_id,
            )
            other_session_id = await app.fetchval(
                "INSERT INTO user_sessions(cooperative_id,user_id,token_hash,csrf_token_hash, "
                "expires_at,ip,user_agent,mfa_pending,authenticated_at) "
                "VALUES($1,$2,$3,$4,now()+interval '1 hour','192.0.2.3','Secret full UA',false,now()) "
                "RETURNING id",
                account["cooperative_id"],
                other_user_id,
                token_hash,
                csrf_hash,
            )
    finally:
        await app.close()

    async with await _client_with_session(account["session_cookie"]) as client:
        csrf = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]
        response = await client.post(
            f"/api/v1/me/sessions/{other_session_id}/revoke",
            headers={"X-CSRF-Token": csrf},
        )
    assert response.status_code == 404

    async with await _client_with_session(account["session_cookie"]) as client:
        visible_sessions = await client.get("/api/v1/me/sessions")
    assert visible_sessions.status_code == 200
    assert str(other_session_id) not in visible_sessions.text
    assert "Secret full UA" not in visible_sessions.text

    app = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    try:
        async with app.transaction():
            await app.execute(
                "SELECT set_config('app.cooperative_id',$1,true)", str(account["cooperative_id"])
            )
            assert await app.fetchval(
                "SELECT revoked_at IS NULL FROM user_sessions WHERE cooperative_id=$1 AND id=$2",
                account["cooperative_id"],
                other_session_id,
            )
    finally:
        await app.close()


async def test_password_change_is_rate_limited(
    account: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "login_rate_limit_attempts", 1)
    monkeypatch.setattr(settings, "login_max_attempts", 100)
    first = await _post_as(
        account["session_cookie"],
        "/me/password",
        {"current_password": "wrong-one", "new_password": "A-better-password!291"},
    )
    second = await _post_as(
        account["session_cookie"],
        "/me/password",
        {"current_password": "wrong-one", "new_password": "A-better-password!291"},
    )
    assert first.status_code == 403
    assert second.status_code == 429


async def test_passwords_never_appear_in_audit_or_application_logs(
    account: dict[str, Any], test_database: Any, caplog: pytest.LogCaptureFixture
) -> None:
    new_password = "Private-new-password!293"
    response = await _post_as(
        account["session_cookie"],
        "/me/password",
        {"current_password": CURRENT_PASSWORD, "new_password": new_password},
    )
    assert response.status_code == 200
    assert CURRENT_PASSWORD not in caplog.text
    assert new_password not in caplog.text

    app = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    try:
        async with app.transaction():
            await app.execute(
                "SELECT set_config('app.cooperative_id',$1,true)", str(account["cooperative_id"])
            )
            summary = await app.fetchval(
                "SELECT summary::text FROM audit_logs WHERE cooperative_id=$1 "
                "AND actor_user_id=$2 AND action='password_changed' ORDER BY created_at DESC LIMIT 1",
                account["cooperative_id"],
                account["user_id"],
            )
    finally:
        await app.close()
    assert summary == "{}"
    assert CURRENT_PASSWORD not in str(summary)
    assert new_password not in str(summary)


async def test_password_change_requires_csrf(account: dict[str, Any]) -> None:
    async with await _client_with_session(account["session_cookie"]) as client:
        response = await client.post(
            "/api/v1/me/password",
            json={"current_password": CURRENT_PASSWORD, "new_password": "A-better-password!291"},
        )
    assert response.status_code == 403
