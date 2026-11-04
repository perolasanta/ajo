from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import dashboard
from app.core.errors import install_error_handlers
from app.domains.auth.service import CurrentSession

pytestmark = pytest.mark.unit

LOAN_STATUSES = {
    "under_review",
    "awaiting_applicant_confirmation",
    "approved_awaiting_final",
    "ready_for_disbursement",
    "repaying",
    "closed",
    "disputed",
}
DISBURSEMENT_STATUSES = {
    "pending_authorization",
    "transfer_recorded",
    "authorized",
    "paid",
    "failed",
    "cancelled",
}
ALL_ROWS = [
    {"source": "loans", "status": status, "count": 1} for status in sorted(LOAN_STATUSES)
] + [
    {"source": "disbursements", "status": status, "count": 2}
    for status in sorted(DISBURSEMENT_STATUSES)
]


def _session(*roles: str) -> CurrentSession:
    return CurrentSession(
        uuid.uuid4(),
        "dashboard-summary-test",
        uuid.uuid4(),
        uuid.uuid4(),
        "unused@example.test",
        roles,
        bytes(32),
    )


def _allowed_statuses(role: str) -> tuple[set[str], set[str]]:
    if role == "committee":
        return {"under_review"}, set()
    loans = LOAN_STATUSES if role in {"admin", "officer", "treasurer", "auditor"} else set()
    if role == "admin":
        disbursements = {"pending_authorization", "transfer_recorded"}
    elif role == "treasurer":
        disbursements = {"authorized"}
    elif role == "auditor":
        disbursements = DISBURSEMENT_STATUSES
    else:
        disbursements = set()
    return loans, disbursements


@pytest.mark.parametrize("role", ["admin", "officer", "treasurer", "committee", "auditor"])
async def test_dashboard_summary_returns_only_role_allowlisted_counts(
    role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = _session(role)
    loan_statuses, disbursement_statuses = _allowed_statuses(role)
    calls: list[tuple[str, dict[str, object]]] = []

    class _Result:
        def mappings(self) -> _Result:
            return self

        def all(self) -> list[dict[str, str | int]]:
            return [
                row
                for row in ALL_ROWS
                if (row["source"] == "loans" and row["status"] in loan_statuses)
                or (row["source"] == "disbursements" and row["status"] in disbursement_statuses)
            ]

    class _Session:
        async def execute(self, statement: Any, params: dict[str, object]) -> _Result:
            calls.append((str(statement), params))
            return _Result()

    @asynccontextmanager
    async def _transaction(cooperative_id: uuid.UUID) -> Any:
        assert cooperative_id == current.cooperative_id
        yield _Session()

    monkeypatch.setattr(dashboard.reads, "transaction", _transaction)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(dashboard.router, prefix="/api/v1")
    app.dependency_overrides[dashboard.current_session] = lambda: current

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/summary")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"loans", "disbursements"}
    assert set(body["loans"]) == loan_statuses
    assert set(body["disbursements"]) == disbursement_statuses
    assert all(value == 1 for value in body["loans"].values())
    assert all(value == 2 for value in body["disbursements"].values())
    assert len(calls) == 1
    assert calls[0][1]["cooperative_id"] == current.cooperative_id
    assert set(calls[0][1]["loan_statuses"]) == loan_statuses
    assert set(calls[0][1]["disbursement_statuses"]) == disbursement_statuses


async def test_dashboard_summary_rejects_roles_without_dashboard_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(dashboard.router, prefix="/api/v1")
    app.dependency_overrides[dashboard.current_session] = lambda: _session("member")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/summary")

    assert response.status_code == 403
