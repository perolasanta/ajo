from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import notifications
from app.domains.auth.service import CurrentSession

pytestmark = pytest.mark.unit

COUNTS = {
    "payroll_awaiting_posting": 11,
    "payroll_awaiting_sign_off": 12,
    "disbursement_awaiting_authorization": 13,
    "disbursement_awaiting_confirmation": 14,
    "disbursement_ready_to_record": 15,
    "role_changes_awaiting_approval": 16,
    "loans_awaiting_decision": 17,
    "disputed_or_paused_loans": 18,
}

ROLE_RESPONSES = [
    (
        "admin",
        {
            "payroll": {"awaiting_sign_off": 12},
            "disbursements": {"awaiting_authorization": 13, "awaiting_confirmation": 14},
            "role_changes": {"awaiting_approval": 16},
        },
    ),
    (
        "treasurer",
        {
            "payroll": {"awaiting_posting": 11},
            "disbursements": {"ready_to_record": 15},
            "loans": {"disputed_or_paused": 18},
        },
    ),
    ("officer", {}),
    ("committee", {"loans": {"awaiting_decision": 17}}),
    ("auditor", {}),
    ("member", {}),
]


def _session(*roles: str) -> CurrentSession:
    return CurrentSession(
        uuid.uuid4(),
        "notification-summary-test",
        uuid.uuid4(),
        uuid.uuid4(),
        "unused@example.test",
        roles,
        bytes(32),
    )


@pytest.mark.parametrize(("role", "expected"), ROLE_RESPONSES)
async def test_notification_summary_has_only_actionable_role_keys_and_counts(
    role: str, expected: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    current = _session(role)
    calls: list[tuple[str, dict[str, object]]] = []

    class _Result:
        def mappings(self) -> _Result:
            return self

        def one(self) -> dict[str, int]:
            return COUNTS

    class _Session:
        async def execute(self, statement: Any, params: dict[str, object]) -> _Result:
            calls.append((str(statement), params))
            return _Result()

    @asynccontextmanager
    async def _transaction(cooperative_id: uuid.UUID) -> Any:
        assert cooperative_id == current.cooperative_id
        yield _Session()

    monkeypatch.setattr(notifications.reads, "transaction", _transaction)
    app = FastAPI()
    app.include_router(notifications.router, prefix="/api/v1")
    app.dependency_overrides[notifications.current_session] = lambda: current

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/notifications/summary")

    assert response.status_code == 200
    assert response.json() == expected
    assert len(calls) == 1
    assert calls[0][1]["cooperative_id"] == current.cooperative_id
    assert calls[0][1]["user_id"] == current.user_id
    assert calls[0][1]["can_payroll_post"] is (role == "treasurer")
    assert calls[0][1]["can_payroll_sign_off"] is (role == "admin")
    assert calls[0][1]["can_disbursement_authorize"] is (role == "admin")
    assert calls[0][1]["can_disbursement_confirm"] is (role == "admin")
    assert calls[0][1]["can_disbursement_record"] is (role == "treasurer")
    assert calls[0][1]["can_approve_role_changes"] is (role == "admin")
    assert calls[0][1]["can_loan_decide"] is (role == "committee")
    assert calls[0][1]["can_repay_disputed_loans"] is (role == "treasurer")
    query = " ".join(calls[0][0].lower().split())
    assert "a.status = 'under_review'" in query
    assert "coalesce(r.posted_by, r.imported_by) is distinct from :user_id" in query
    assert "d.prepared_by is distinct from :user_id" in query
    assert "d.authorized_by is distinct from :user_id" in query
    assert "p.proposer_user_id is distinct from :user_id" in query
    assert "p.target_user_id is distinct from :user_id" in query


async def test_notification_summary_unions_actions_for_multiple_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _session("admin", "treasurer", "committee")
    calls: list[dict[str, object]] = []

    class _Result:
        def mappings(self) -> _Result:
            return self

        def one(self) -> dict[str, int]:
            return COUNTS

    class _Session:
        async def execute(self, statement: Any, params: dict[str, object]) -> _Result:
            calls.append(params)
            return _Result()

    @asynccontextmanager
    async def _transaction(cooperative_id: uuid.UUID) -> Any:
        assert cooperative_id == current.cooperative_id
        yield _Session()

    monkeypatch.setattr(notifications.reads, "transaction", _transaction)
    app = FastAPI()
    app.include_router(notifications.router, prefix="/api/v1")
    app.dependency_overrides[notifications.current_session] = lambda: current

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/notifications/summary")

    assert response.status_code == 200
    assert response.json() == {
        "payroll": {"awaiting_posting": 11, "awaiting_sign_off": 12},
        "disbursements": {
            "awaiting_authorization": 13,
            "awaiting_confirmation": 14,
            "ready_to_record": 15,
        },
        "role_changes": {"awaiting_approval": 16},
        "loans": {"awaiting_decision": 17, "disputed_or_paused": 18},
    }
    assert len(calls) == 1
    assert all(
        calls[0][key] is True
        for key in (
            "can_payroll_post",
            "can_payroll_sign_off",
            "can_disbursement_authorize",
            "can_disbursement_confirm",
            "can_disbursement_record",
            "can_approve_role_changes",
            "can_loan_decide",
            "can_repay_disputed_loans",
        )
    )
