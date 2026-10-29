"""Auth, forced tenant RLS, and audit-chain tests on the configured dev branch."""

import hashlib
import json

import asyncpg
import pyotp
import pytest
from cryptography.fernet import Fernet

from app.core import security
from app.core.config import get_settings
from app.core.db import asyncpg_dsn, dispose_engine
from app.core.security import hash_password
from app.domains.audit.service import verify_audit_chain
from app.domains.auth import service as auth_service
from tests.conftest import create_tenant

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
async def isolated_app_engine(monkeypatch: pytest.MonkeyPatch):
    """Keep pooled async connections on their owning event loop and use a test key."""
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    get_settings.cache_clear()
    security._fernet.cache_clear()
    yield
    await dispose_engine()
    security._fernet.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
async def tenant(test_database) -> dict[str, object]:
    app = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    password = "Test-only-password!290"
    seed = await create_tenant(
        app,
        name="Auth integration test",
        slug_prefix="auth-test",
        roles=("admin",),
        password_hash=hash_password(password),
    )
    cooperative_id = seed.cooperative_id
    user_id = seed.user_ids[0]
    slug = f"auth-test-{cooperative_id.hex}"
    email = await app.fetchval(
        "SELECT email FROM users WHERE cooperative_id=$1 AND id=$2", cooperative_id, user_id
    )
    await app.execute(
        "INSERT INTO tenant_directory (slug, cooperative_id) VALUES ($1, $2)",
        slug,
        cooperative_id,
    )
    await app.close()
    yield {
        "cooperative_id": cooperative_id,
        "user_id": user_id,
        "slug": slug,
        "email": email,
        "password": password,
    }
    # Each test owns a fresh cooperative; the session fixture drops the whole database.


async def _start_enrollment(tenant: dict[str, object]) -> tuple[str, str]:
    result = await auth_service.login(
        slug=str(tenant["slug"]),
        email=str(tenant["email"]),
        password=str(tenant["password"]),
        totp_code=None,
        recovery_code=None,
        ip="127.0.0.1",
        user_agent="pytest",
    )
    assert result.data["mfa_enrollment_required"] is True
    provisioning_uri = str(result.data["provisioning_uri"])
    assert "secret=" in provisioning_uri
    assert result.session_cookie is not None
    secret = pyotp.parse_uri(provisioning_uri).secret
    return result.session_cookie, secret


async def test_mfa_is_mandatory_and_first_login_enrolls(tenant: dict[str, object]) -> None:
    cookie, secret = await _start_enrollment(tenant)
    codes, current = await auth_service.complete_mfa_enrollment(
        cookie, pyotp.TOTP(secret).now(), "127.0.0.1"
    )
    assert current is not None
    assert len(codes) == 10
    assert all(len(code) == 32 for code in codes)
    no_code = await auth_service.login(
        slug=str(tenant["slug"]),
        email=str(tenant["email"]),
        password=str(tenant["password"]),
        totp_code=None,
        recovery_code=None,
        ip="127.0.0.1",
        user_agent="pytest",
    )
    assert no_code.data == {"mfa_required": True}
    assert no_code.session_cookie is not None
    verified = await auth_service.verify_pending_mfa(
        no_code.session_cookie, pyotp.TOTP(secret).now(), "127.0.0.1"
    )
    assert verified is not None
    assert "admin" in verified.roles


async def test_lockout(tenant: dict[str, object], test_database) -> None:
    bad_results = [
        await auth_service.login(
            slug=str(tenant["slug"]),
            email=str(tenant["email"]),
            password="wrong",
            totp_code=None,
            recovery_code=None,
            ip="127.0.0.2",
            user_agent="pytest",
        )
        for _ in range(5)
    ]
    assert all(result.data.get("error") == "invalid_credentials" for result in bad_results)
    assert bad_results[-1].data["message"] == auth_service.GENERIC_LOGIN_ERROR
    app = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    try:
        async with app.transaction():
            await app.execute(
                "SELECT set_config('app.cooperative_id', $1, true)",
                str(tenant["cooperative_id"]),
            )
            assert await app.fetchval(
                "SELECT locked_until > now() FROM users WHERE id = $1", tenant["user_id"]
            )
    finally:
        await app.close()


async def test_wrong_tenant_login_denied(tenant: dict[str, object]) -> None:
    wrong_tenant = await auth_service.login(
        slug=f"wrong-{tenant['slug']}",
        email=str(tenant["email"]),
        password=str(tenant["password"]),
        totp_code=None,
        recovery_code=None,
        ip="127.0.0.3",
        user_agent="pytest",
    )
    assert wrong_tenant.data["message"] == auth_service.GENERIC_LOGIN_ERROR


async def test_session_expiry_is_enforced(tenant: dict[str, object], test_database) -> None:
    cookie, secret = await _start_enrollment(tenant)
    _, current = await auth_service.complete_mfa_enrollment(
        cookie, pyotp.TOTP(secret).now(), "127.0.0.1"
    )
    assert current is not None
    app = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    try:
        async with app.transaction():
            await app.execute(
                "SELECT set_config('app.cooperative_id', $1, true)",
                str(tenant["cooperative_id"]),
            )
            await app.execute(
                "UPDATE user_sessions SET expires_at = now() - interval '1 second' "
                "WHERE cooperative_id=$1 AND id=$2",
                tenant["cooperative_id"],
                current.session_id,
            )
    finally:
        await app.close()
    with pytest.raises(Exception, match="Authentication required"):
        await auth_service.load_session(cookie, require_authenticated=True)


async def test_http_login_cookie_reaches_protected_business_endpoint(
    tenant: dict[str, object],
) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="https://testserver",
    ) as client:
        csrf = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]
        login = await client.post(
            "/api/v1/auth/login",
            headers={"X-CSRF-Token": csrf},
            json={
                "cooperative_slug": tenant["slug"],
                "email": tenant["email"],
                "password": tenant["password"],
            },
        )
        assert login.status_code == 200
        secret = pyotp.parse_uri(login.json()["provisioning_uri"]).secret
        csrf = client.cookies.get("ajo_csrf")
        enrolled = await client.post(
            "/api/v1/auth/mfa/enroll",
            headers={"X-CSRF-Token": csrf},
            json={"code": pyotp.TOTP(secret).now()},
        )
        assert enrolled.status_code == 200

        members = await client.post(
            "/api/v1/members/import",
            headers={"X-CSRF-Token": client.cookies.get("ajo_csrf")},
            json={
                "csv_text": "member_no,full_name,phone_e164\nM-HTTP,Http Test,+2348000000000",
                "confirm": False,
            },
        )

    assert members.status_code == 200
    assert members.json() == {"imported": 0, "errors": [], "valid_rows": 1}


async def test_audit_chain_verifies_and_detects_owner_tampering(
    tenant: dict[str, object], test_database
) -> None:
    cookie, secret = await _start_enrollment(tenant)
    _, current = await auth_service.complete_mfa_enrollment(
        cookie, pyotp.TOTP(secret).now(), "127.0.0.1"
    )
    assert current is not None
    cooperative_id = tenant["cooperative_id"]
    valid = await verify_audit_chain(cooperative_id)  # type: ignore[arg-type]
    assert valid.valid and valid.entries_checked >= 2
    owner = await asyncpg.connect(asyncpg_dsn(test_database.owner_url))
    try:
        await owner.execute("ALTER TABLE audit_logs DISABLE TRIGGER USER")
        await owner.execute(
            "UPDATE audit_logs SET summary = $2::jsonb WHERE cooperative_id = $1 "
            "AND action = 'login_success'",
            cooperative_id,
            json.dumps({"tampered": True}),
        )
        await owner.execute("ALTER TABLE audit_logs ENABLE TRIGGER USER")
    finally:
        await owner.close()
    invalid = await verify_audit_chain(cooperative_id)  # type: ignore[arg-type]
    assert not invalid.valid
    assert invalid.first_invalid_id is not None


async def test_credentials_and_tokens_are_not_in_audit_or_log_outputs(
    tenant: dict[str, object], caplog: pytest.LogCaptureFixture, test_database
) -> None:
    secret_password = str(tenant["password"])
    result = await auth_service.login(
        slug=str(tenant["slug"]),
        email=str(tenant["email"]),
        password="wrong-password",
        totp_code="123456",
        recovery_code=None,
        ip="127.0.0.4",
        user_agent="pytest",
    )
    assert result.data.get("error") == "invalid_credentials"
    assert secret_password not in caplog.text
    assert "wrong-password" not in caplog.text
    app = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    try:
        async with app.transaction():
            await app.execute(
                "SELECT set_config('app.cooperative_id', $1, true)",
                str(tenant["cooperative_id"]),
            )
            summaries = await app.fetchval(
                "SELECT string_agg(summary::text, ' ') FROM audit_logs WHERE cooperative_id = $1",
                tenant["cooperative_id"],
            )
        stored = str(summaries or "")
        for forbidden in (
            secret_password,
            "wrong-password",
            "123456",
            "password",
            "token",
            "secret",
        ):
            assert forbidden.lower() not in stored.lower()
        assert hashlib.sha256(secret_password.encode()).hexdigest() not in stored
    finally:
        await app.close()
