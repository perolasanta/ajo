from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.api import members
from app.core.errors import Forbidden
from app.domains.auth.service import CurrentSession

pytestmark = pytest.mark.unit
REQUEST_ID = uuid.uuid4()
COOPERATIVE_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def _session(*roles: str) -> CurrentSession:
    return CurrentSession(
        cooperative_id=COOPERATIVE_ID,
        slug="withdrawal-queue-test",
        session_id=uuid.uuid4(),
        user_id=USER_ID,
        email="user@example.test",
        roles=tuple(roles),
        csrf_hash=bytes(32),
    )


class _Result:
    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return [
            {
                "id": REQUEST_ID,
                "member_id": uuid.uuid4(),
                "member_name": "Private Member",
                "member_no": "M-001",
                "amount_kobo": 10000,
                "requested_by": uuid.uuid4(),
                "requester_name": "Requester",
                "requested_at": "2026-10-01T10:00:00Z",
                "status": "pending",
                "reason": "Need funds",
                "rejection_reason": None,
                "total": 1,
            }
        ]


@pytest.mark.parametrize(
    ("roles", "mine", "expected_ids", "officer_only"),
    [
        (("admin",), "all", None, False),
        (("officer",), "all", None, True),
        (("treasurer",), "needed", [REQUEST_ID], False),
        (("officer", "treasurer"), "needed", [REQUEST_ID], False),
    ],
)
async def test_withdrawal_queue_uses_role_policy_and_shared_action_ids(
    roles: tuple[str, ...],
    mine: str,
    expected_ids: list[uuid.UUID] | None,
    officer_only: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class _DB:
        async def execute(self, query: Any, params: dict[str, Any]) -> _Result:
            calls.append((str(query), params))
            return _Result()

    @asynccontextmanager
    async def transaction(cooperative_id: uuid.UUID) -> Any:
        assert cooperative_id == COOPERATIVE_ID
        yield _DB()

    async def action_items(*args: Any) -> dict[str, tuple[uuid.UUID, ...]]:
        assert args[1] == COOPERATIVE_ID
        assert args[2] == USER_ID
        assert args[3] == set(roles)
        return {"withdrawal_requests": (REQUEST_ID,)}

    monkeypatch.setattr(members, "transaction", transaction)
    monkeypatch.setattr(members.eligibility, "get_action_items_for_user", action_items)
    result = await members.list_withdrawal_requests(
        _session(*roles),
        status="pending" if officer_only or mine == "needed" else None,
        mine=mine,
        page=1,
        page_size=25,
    )
    assert result["total"] == 1
    assert len(calls) == 1
    params = calls[0][1]
    assert params["cid"] == COOPERATIVE_ID
    assert params["officer_only"] is officer_only
    assert params["action_ids"] == expected_ids


@pytest.mark.parametrize("role", ["committee", "auditor", "member"])
async def test_withdrawal_queue_denies_roles_without_read_access(role: str) -> None:
    with pytest.raises(Forbidden):
        await members.list_withdrawal_requests(
            _session(role), status=None, mine="all", page=1, page_size=25
        )
