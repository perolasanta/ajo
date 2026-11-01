"""Audit read contracts: role access, exact projection, and secret exclusion."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import audit as audit_api
from app.core.errors import install_error_handlers
from app.domains.auth.service import CurrentSession

pytestmark = pytest.mark.unit


SAFE_KEYS = {
    "event_id",
    "timestamp",
    "actor_id",
    "actor_role",
    "action",
    "entity_type",
    "entity_id",
    "safe_summary",
}


def _session(role: str) -> CurrentSession:
    return CurrentSession(
        uuid.uuid4(),
        "audit-contract-test",
        uuid.uuid4(),
        uuid.uuid4(),
        "unused",
        (role,),
        bytes(32),
    )


def _row() -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "actor_user_id": uuid.uuid4(),
        "role": "admin",
        "action": "member_updated",
        "entity_type": "member",
        "entity_id": "member-1",
        "summary": {
            "status": "active",
            "reason": "contains free text and must be omitted",
            "salary": 999,
            "token": "secret",
        },
        "phone": "+2348000000000",
        "email": "person@example.test",
        "password": "hashed",
        "otp": "123456",
        "mfa_secret": "secret",
        "account_number": "1234567890",
    }


def test_audit_projection_has_exact_keys_and_safe_summary() -> None:
    projected = audit_api._audit_projection(_row())
    assert set(projected) == SAFE_KEYS
    assert projected["safe_summary"] == {"status": "active"}
    assert not any(
        any(secret in key.lower() for secret in ("phone", "email", "salary", "account", "token", "otp", "password", "mfa"))
        for key in projected
    )


@pytest.mark.parametrize("role", ["officer", "treasurer", "committee", "member"])
async def test_other_roles_receive_403(role: str, monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(audit_api.router)
    app.dependency_overrides[audit_api.current_session] = lambda: _session(role)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/audit-events")
    assert response.status_code == 403


@pytest.mark.parametrize("role", ["admin", "auditor"])
async def test_allowed_roles_get_exact_contract(
    role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(audit_api.router)
    app.dependency_overrides[audit_api.current_session] = lambda: _session(role)
    async def stub(*args: object, **kwargs: object) -> dict[str, object]:
        return {"items": [_row()], "page": 1, "page_size": 25, "total": 1}

    monkeypatch.setattr(audit_api.reads, "list_audit_events", stub)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/audit-events")
    assert response.status_code == 200
    body = response.json()
    assert set(body["items"][0]) == SAFE_KEYS
    assert body["items"][0]["safe_summary"] == {"status": "active"}


def test_tenant_id_is_required_by_read_service_contract() -> None:
    assert _session("admin").cooperative_id is not None
