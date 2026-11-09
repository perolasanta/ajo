from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import notifications
from app.domains.auth.service import CurrentSession
from app.domains.notifications import eligibility

pytestmark = pytest.mark.unit


EVENTS = tuple(eligibility.ACTION_SPECS)


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


def _candidates() -> list[dict[str, Any]]:
    return [
        {"event_type": event_type, "entity_id": uuid.uuid4(), "excluded_user_ids": []}
        for event_type in EVENTS
        for _ in range(2)
    ]


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (
            "admin",
            {
                "payroll": {"awaiting_sign_off": 2},
                "disbursements": {"awaiting_authorization": 2, "awaiting_confirmation": 2},
                "role_changes": {"awaiting_approval": 2},
            },
        ),
        (
            "treasurer",
            {
                "payroll": {"awaiting_posting": 2},
                "disbursements": {"ready_to_record": 2},
                "loans": {"disputed_or_paused": 2},
                "withdrawal_requests": {"awaiting_approval": 2},
            },
        ),
        ("officer", {}),
        ("committee", {"loans": {"awaiting_decision": 2}}),
        ("auditor", {}),
        ("member", {}),
    ],
)
async def test_notification_summary_counts_only_shared_user_action_ids(
    role: str, expected: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    current = _session(role)
    candidates = _candidates()

    @asynccontextmanager
    async def transaction(cooperative_id: uuid.UUID) -> Any:
        assert cooperative_id == current.cooperative_id
        yield object()

    async def get_candidates(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return candidates

    monkeypatch.setattr(notifications.reads, "transaction", transaction)
    monkeypatch.setattr(eligibility, "get_all_action_candidates", get_candidates)
    app = FastAPI()
    app.include_router(notifications.router, prefix="/api/v1")
    app.dependency_overrides[notifications.current_session] = lambda: current
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/notifications/summary")
    assert response.status_code == 200
    assert response.json() == expected


async def test_notification_summary_unions_roles_but_never_duplicates_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _session("admin", "treasurer", "committee")
    candidates = _candidates()

    @asynccontextmanager
    async def transaction(cooperative_id: uuid.UUID) -> Any:
        assert cooperative_id == current.cooperative_id
        yield object()

    async def get_candidates(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return candidates

    monkeypatch.setattr(notifications.reads, "transaction", transaction)
    monkeypatch.setattr(eligibility, "get_all_action_candidates", get_candidates)
    app = FastAPI()
    app.include_router(notifications.router, prefix="/api/v1")
    app.dependency_overrides[notifications.current_session] = lambda: current
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/notifications/summary")
    assert response.status_code == 200
    assert response.json() == {
        "payroll": {"awaiting_posting": 2, "awaiting_sign_off": 2},
        "disbursements": {
            "awaiting_authorization": 2,
            "awaiting_confirmation": 2,
            "ready_to_record": 2,
        },
        "role_changes": {"awaiting_approval": 2},
        "loans": {"awaiting_decision": 2, "disputed_or_paused": 2},
        "withdrawal_requests": {"awaiting_approval": 2},
    }
