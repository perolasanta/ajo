"""Account settings, password, session, and privacy contracts."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.api import account as account_api
from app.api import auth as auth_api
from app.core.errors import Forbidden, NotFound, TooManyRequests, UnprocessableEntity
from app.domains.auth import service as auth_service
from app.domains.auth.service import CurrentSession
from app.main import create_app

pytestmark = pytest.mark.unit

COOPERATIVE_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
SESSION_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
OTHER_SESSION_ID = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
ROLES = ["admin", "officer", "treasurer", "committee", "auditor", "member"]
CURRENT_PASSWORD = "Current-Password-91!"
NEW_PASSWORD = "Replacement-Password-72!"


def _current(role: str = "admin") -> CurrentSession:
    return CurrentSession(
        COOPERATIVE_ID,
        "account-settings-test",
        SESSION_ID,
        USER_ID,
        "aisha@example.test",
        (role,),
        bytes(32),
    )


class _Result:
    def __init__(
        self,
        row: Any = None,
        rows: list[dict[str, Any]] | None = None,
        scalar: Any = None,
    ) -> None:
        self.row = row
        self.rows = rows or []
        self.scalar = scalar

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> Any:
        return self.row

    def one(self) -> Any:
        assert self.row is not None
        return self.row

    def all(self) -> list[dict[str, Any]]:
        return self.rows

    def scalar_one_or_none(self) -> Any:
        return self.scalar


class _AccountSession:
    def __init__(
        self,
        *,
        rate_limited: bool = False,
        failed_count: int = 1,
        revoke_row: Any = None,
        sessions: list[dict[str, Any]] | None = None,
    ) -> None:
        self.rate_limited = rate_limited
        self.failed_count = failed_count
        self.revoke_row = revoke_row
        self.user = {
            "id": USER_ID,
            "email": "aisha@example.test",
            "password_hash": "existing-argon-hash",
            "failed_login_count": 0,
            "locked_until": None,
            "status": "active",
        }
        self.sessions = sessions or []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.updated_password_hash: str | None = None

    async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        sql = " ".join(str(statement).lower().split())
        self.calls.append((sql, params))
        if sql.startswith("select locked_until from auth_rate_limits"):
            locked_until = datetime.now(UTC) + timedelta(minutes=10) if self.rate_limited else None
            return _Result(scalar=locked_until)
        if sql.startswith("select") and "from users" in sql and "for update" in sql:
            return _Result(row=self.user)
        if sql.startswith("select") and "from user_sessions" in sql:
            return _Result(rows=self.sessions)
        if sql.startswith("insert into auth_rate_limits"):
            return _Result(row=(self.failed_count, None))
        if sql.startswith("update users") and "password_hash" in sql:
            self.updated_password_hash = params.get("password_hash")
        if sql.startswith("update user_sessions") and "returning" in sql:
            return _Result(scalar=self.revoke_row)
        return _Result()


@asynccontextmanager
async def _transaction_for(session: _AccountSession, cooperative_id: uuid.UUID) -> Any:
    assert cooperative_id == COOPERATIVE_ID
    yield session


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        secret_key="unit-test-only-secret",
        login_max_attempts=5,
        login_rate_limit_attempts=3,
        login_rate_limit_window_minutes=5,
        login_lockout_minutes=15,
        session_idle_minutes=30,
    )


def _wire_service(
    monkeypatch: pytest.MonkeyPatch,
    session: _AccountSession,
    audits: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    captured = audits if audits is not None else []

    async def append_audit(_session: Any, **kwargs: Any) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(auth_service, "transaction", lambda cid: _transaction_for(session, cid))
    monkeypatch.setattr(auth_service, "get_settings", _settings)
    monkeypatch.setattr(auth_service, "append_audit", append_audit)
    monkeypatch.setattr(
        auth_service,
        "verify_password",
        lambda _password_hash, password: password == CURRENT_PASSWORD,
    )
    monkeypatch.setattr(auth_service, "hash_password", lambda password: f"hashed:{password}")
    return captured


def test_me_routes_require_authenticated_session_and_csrf() -> None:
    expected = {
        ("/api/v1/me/password", "POST"),
        ("/api/v1/me/sessions", "GET"),
        ("/api/v1/me/sessions/{session_id}/revoke", "POST"),
        ("/api/v1/me/sessions/revoke-others", "POST"),
    }
    app_paths = set(create_app().openapi()["paths"])
    assert {path for path, _method in expected} <= app_paths
    routes = {
        (route.path, method): route
        for route in account_api.router.routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    }
    for path, method in expected:
        route = routes[(path.removeprefix("/api/v1"), method)]
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        assert auth_api.current_session in dependencies
        if method == "POST":
            assert auth_api.require_csrf in dependencies


@pytest.mark.parametrize("role", ROLES)
async def test_account_sessions_are_available_to_every_role(
    role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def list_sessions(_current: CurrentSession) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(auth_service, "list_sessions", list_sessions, raising=False)
    app = create_app()
    app.dependency_overrides[auth_api.current_session] = lambda: _current(role)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/me/sessions")
    assert response.status_code == 200
    assert response.json() == {"items": []}


@pytest.mark.parametrize("role", ROLES)
async def test_wrong_current_password_is_rejected_without_logging_secrets(
    role: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake = _AccountSession()
    audits = _wire_service(monkeypatch, fake)
    with pytest.raises(Forbidden):
        await auth_service.change_password(
            _current(role),
            "Wrong-current-password-44!",
            NEW_PASSWORD,
            "192.0.2.10",
        )
    assert fake.updated_password_hash is None
    assert audits
    assert "Wrong-current-password-44!" not in str(audits)
    assert NEW_PASSWORD not in str(audits)
    assert "Wrong-current-password-44!" not in caplog.text
    assert NEW_PASSWORD not in caplog.text


@pytest.mark.parametrize(
    "new_password",
    [
        "Short-1!",
        CURRENT_PASSWORD,
        "aisha@example.test",
        "password123456",
        "x" * 129,
    ],
)
def test_weak_or_reused_password_is_rejected(new_password: str) -> None:
    with pytest.raises(UnprocessableEntity):
        auth_service.validate_new_password(
            new_password,
            current_password=CURRENT_PASSWORD,
            email="aisha@example.test",
        )


@pytest.mark.parametrize("role", ROLES)
async def test_password_change_hashes_new_password_and_preserves_current_session(
    role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _AccountSession()
    audits = _wire_service(monkeypatch, fake)
    await auth_service.change_password(_current(role), CURRENT_PASSWORD, NEW_PASSWORD, "192.0.2.11")
    assert fake.updated_password_hash == f"hashed:{NEW_PASSWORD}"
    revocations = [
        (sql, params) for sql, params in fake.calls if sql.startswith("update user_sessions")
    ]
    assert revocations
    sql, params = revocations[-1]
    assert "user_id" in sql and "cooperative_id" in sql and "<>" in sql
    assert params.get("current_session_id") == SESSION_ID
    assert audits and all(NEW_PASSWORD not in str(event) for event in audits)


async def test_login_rate_limit_blocks_password_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _AccountSession(rate_limited=True)
    _wire_service(monkeypatch, fake)
    with pytest.raises(TooManyRequests):
        await auth_service.change_password(_current(), CURRENT_PASSWORD, NEW_PASSWORD, "192.0.2.12")
    assert not any("update users set password_hash" in sql for sql, _ in fake.calls)


@pytest.mark.parametrize("role", ROLES)
async def test_cannot_revoke_another_users_session(
    role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _AccountSession(revoke_row=None)
    _wire_service(monkeypatch, fake)
    with pytest.raises(NotFound):
        await auth_service.revoke_session(_current(role), OTHER_SESSION_ID, "192.0.2.13")
    updates = [
        (sql, params) for sql, params in fake.calls if sql.startswith("update user_sessions")
    ]
    assert updates
    sql, params = updates[-1]
    assert "cooperative_id" in sql and "user_id" in sql
    assert params["cooperative_id"] == COOPERATIVE_ID
    assert params["user_id"] == USER_ID


async def test_session_list_is_a_safe_user_agent_summary_and_marks_current_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    fake = _AccountSession(
        sessions=[
            {
                "id": SESSION_ID,
                "created_at": now,
                "last_seen_at": now,
                "user_agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/131.0.0.0",
                "ip": "203.0.113.77",
                "token_hash": b"must-not-return",
                "csrf_token_hash": b"also-secret",
                "is_current": True,
            }
        ]
    )
    _wire_service(monkeypatch, fake)
    items = await auth_service.list_sessions(_current())
    assert len(items) == 1
    assert set(items[0]) == {"id", "device", "created_at", "last_seen_at", "this_device"}
    assert items[0]["this_device"] is True
    assert "Chrome" in items[0]["device"]
    assert "203.0.113.77" not in str(items)
    assert "must-not-return" not in str(items)
    assert "also-secret" not in str(items)
    select = next(
        sql for sql, _ in fake.calls if sql.startswith("select") and "user_sessions" in sql
    )
    assert "ip" not in select and "token_hash" not in select and "csrf_token_hash" not in select
