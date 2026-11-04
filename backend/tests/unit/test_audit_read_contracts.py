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
from app.domains.audit.service import AuditVerification
from app.domains.auth.service import CurrentSession

pytestmark = pytest.mark.unit


SAFE_KEYS = {
    "event_id",
    "timestamp",
    "actor_id",
    "actor_display_name",
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
    actor_id = uuid.uuid4()
    return {
        "id": uuid.uuid4(),
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "actor_user_id": actor_id,
        "joined_actor_id": actor_id,
        "actor_full_name": "Ada Admin",
        "actor_email": "ada@example.test",
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
    assert projected["actor_display_name"] == "Ada Admin (ada@example.test)"
    assert projected["actor_id"] is not None
    assert projected["safe_summary"] == {"status": "active"}
    assert not any(
        any(
            secret in key.lower()
            for secret in ("phone", "salary", "account", "token", "otp", "password", "mfa")
        )
        for key in projected
    )


def test_audit_projection_drops_unbounded_values_and_boolean_counts() -> None:
    row = _row()
    row["summary"] = {
        "status": "person@example.test",
        "decision": "free-form reason with account 1234567890",
        "old_role": "0123456789012345",
        "new_role": "admin",
        "count": True,
    }
    projected = audit_api._audit_projection(row)
    assert projected["safe_summary"] == {"new_role": "admin"}


def test_audit_actor_fallbacks_do_not_change_stored_actor_id() -> None:
    row = _row()
    row["actor_full_name"] = None
    assert audit_api._audit_projection(row)["actor_display_name"] == "ada@example.test"
    row["actor_email"] = None
    row["joined_actor_id"] = None
    missing_user = audit_api._audit_projection(row)
    assert missing_user["actor_display_name"] == "Unknown user"
    assert missing_user["actor_id"] == str(row["actor_user_id"])
    row["actor_user_id"] = None
    assert audit_api._audit_projection(row)["actor_display_name"] == "System"


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
async def test_allowed_roles_get_exact_contract(role: str, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert body["items"][0]["actor_display_name"] == "Ada Admin (ada@example.test)"


def test_tenant_id_is_required_by_read_service_contract() -> None:
    assert _session("admin").cooperative_id is not None


@pytest.mark.parametrize("role", ["admin", "auditor"])
async def test_verification_reports_first_broken_position(
    role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(audit_api.router)
    app.dependency_overrides[audit_api.current_session] = lambda: _session(role)

    async def stub(_: uuid.UUID) -> AuditVerification:
        return AuditVerification(False, 4, uuid.uuid4())

    monkeypatch.setattr(audit_api, "verify_audit_chain", stub)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/audit-events/verify")
    assert response.status_code == 200
    assert response.json() == {"ok": False, "entries_checked": 4, "first_broken_position": 4}
