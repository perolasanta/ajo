"""Admin-only staff management read contracts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import staff as staff_api
from app.core.errors import install_error_handlers
from app.domains.auth.service import CurrentSession

pytestmark = pytest.mark.unit


def _session(role: str) -> CurrentSession:
    return CurrentSession(
        uuid.uuid4(),
        "staff-read-contract-test",
        uuid.uuid4(),
        uuid.uuid4(),
        "unused",
        (role,),
        bytes(32),
    )


def _app(role: str) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(staff_api.router)
    app.dependency_overrides[staff_api.current_session] = lambda: _session(role)
    return app


@pytest.mark.parametrize("role", ["officer", "auditor", "treasurer", "committee"])
async def test_staff_and_proposal_reads_reject_non_admin(role: str) -> None:
    async with AsyncClient(transport=ASGITransport(app=_app(role)), base_url="http://test") as client:
        staff = await client.get("/staff")
        proposals = await client.get("/role-change-proposals")
        edit = await client.patch(f"/staff/{uuid.uuid4()}", json={"full_name": "Not allowed"})
    assert staff.status_code == 403
    assert proposals.status_code == 403
    assert edit.status_code == 403


async def test_admin_read_responses_have_only_documented_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    staff_id = str(uuid.uuid4())
    proposal_id = str(uuid.uuid4())
    proposer_id = str(uuid.uuid4())

    async def list_staff(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "items": [{
                "user_id": staff_id,
                "email": "officer@example.test",
                "full_name": "Officer One",
                "status": "active",
                "roles": ["officer"],
                "created_at": created_at,
                "password_hash": "must never be returned",
            }],
            "page": 1,
            "page_size": 25,
            "total": 1,
        }

    async def list_proposals(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "items": [{
                "proposal_id": proposal_id,
                "target_user_id": staff_id,
                "target_email": "officer@example.test",
                "proposer_user_id": proposer_id,
                "proposer_email": "admin@example.test",
                "proposer_display_name": "Admin One (admin@example.test)",
                "decided_by_display_name": "Admin Two (second@example.test)",
                "role": "treasurer",
                "action": "add",
                "reason": "Role rotation",
                "status": "approved",
                "created_at": created_at,
                "expires_at": created_at,
                "decided_at": created_at,
                "decided_by": str(uuid.uuid4()),
                "session_token": "must never be returned",
            }],
            "page": 1,
            "page_size": 25,
            "total": 1,
        }

    monkeypatch.setattr(staff_api.reads, "list_staff", list_staff)
    monkeypatch.setattr(staff_api.reads, "list_role_change_proposals", list_proposals)
    async with AsyncClient(transport=ASGITransport(app=_app("admin")), base_url="http://test") as client:
        staff = await client.get("/staff")
        proposals = await client.get("/role-change-proposals?status=pending")

    assert staff.status_code == proposals.status_code == 200
    assert set(staff.json()["items"][0]) == {
        "user_id", "email", "full_name", "status", "roles", "created_at"
    }
    assert set(proposals.json()["items"][0]) == {
        "proposal_id", "target_user_id", "target_email", "proposer_user_id",
        "proposer_email", "proposer_display_name", "decided_by_display_name",
        "role", "action", "reason", "status", "created_at",
        "expires_at", "decided_at", "decided_by",
    }
    assert "password_hash" not in staff.json()["items"][0]
    assert "session_token" not in proposals.json()["items"][0]
    assert staff.json()["items"][0]["full_name"] == "Officer One"
    assert proposals.json()["items"][0]["proposer_display_name"] == "Admin One (admin@example.test)"
    assert proposals.json()["items"][0]["decided_by_display_name"] == "Admin Two (second@example.test)"


async def test_admin_can_edit_existing_staff_full_name(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = uuid.uuid4()
    calls: list[tuple[uuid.UUID, str | None]] = []

    async def update_full_name(
        current: CurrentSession, target_user_id: uuid.UUID, full_name: str | None
    ) -> dict[str, object]:
        assert "admin" in current.roles
        calls.append((target_user_id, full_name))
        return {"user_id": str(target_user_id), "full_name": full_name}

    monkeypatch.setattr(
        staff_api.reads, "update_staff_full_name", update_full_name, raising=False
    )
    async with AsyncClient(transport=ASGITransport(app=_app("admin")), base_url="http://test") as client:
        response = await client.patch(f"/staff/{user_id}", json={"full_name": "Officer One"})
    assert response.status_code == 200
    assert response.json() == {"user_id": str(user_id), "full_name": "Officer One"}
    assert calls == [(user_id, "Officer One")]


async def test_proposal_status_filter_is_validated() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app("admin")), base_url="http://test") as client:
        response = await client.get("/role-change-proposals?status=secret")
    assert response.status_code == 422
