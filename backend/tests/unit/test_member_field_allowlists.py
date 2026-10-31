"""Role-specific member response contracts and committee loan-decision privacy tests."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import loans as loans_api
from app.api import members as members_api
from app.domains.auth.service import CurrentSession

pytestmark = pytest.mark.unit

MEMBER_ID = "33333333-3333-3333-3333-333333333333"
APPLICATION_ID = "44444444-4444-4444-4444-444444444444"
BALANCE_FIELDS = {"savings_kobo", "shares_kobo", "dues_paid_kobo", "locked_kobo", "available_kobo"}
ROLE_FIELDS = {
    "admin": {
        "list": {
            "id",
            "member_no",
            "full_name",
            "phone_e164",
            "email",
            "staff_no",
            "status",
            "joined_on",
        },
        "detail": {
            "id",
            "member_no",
            "full_name",
            "phone_e164",
            "email",
            "staff_no",
            "employer",
            "status",
            "joined_on",
            "bank_accounts",
            "balances",
        },
        "bank": {"id", "bank_code", "last4", "account_name", "verified_at", "status"},
    },
    "officer": {
        "list": {
            "id",
            "member_no",
            "full_name",
            "phone_e164",
            "email",
            "staff_no",
            "status",
            "joined_on",
        },
        "detail": {
            "id",
            "member_no",
            "full_name",
            "phone_e164",
            "email",
            "staff_no",
            "employer",
            "status",
            "joined_on",
            "bank_accounts",
            "balances",
        },
        "bank": {"id", "bank_code", "last4", "account_name", "verified_at", "status"},
    },
    "treasurer": {
        "list": {"id", "member_no", "full_name"},
        "detail": {"id", "member_no", "full_name", "bank_accounts", "balances"},
        "bank": {"id", "bank_code", "last4"},
    },
    "committee": {
        "list": {"id", "member_no", "full_name"},
        "detail": {"id", "member_no", "full_name"},
        "bank": set(),
    },
    "auditor": {
        "list": {"id", "member_no", "full_name", "status", "joined_on"},
        "detail": {"id", "member_no", "full_name", "status", "joined_on", "balances"},
        "bank": set(),
    },
}

MEMBER_LIST_ROW: dict[str, Any] = {
    "id": uuid.UUID(MEMBER_ID),
    "member_no": "M-001",
    "full_name": "Ada Example",
    "phone_e164": "+2348031234567",
    "email": "ada@example.test",
    "staff_no": "STAFF-1",
    "status": "active",
    "joined_on": date(2025, 1, 2),
    "future_member_field": "must not leak",
}
MEMBER_DETAIL_ROW: dict[str, Any] = {
    **MEMBER_LIST_ROW,
    "employer": "Example Employer",
}
BANK_ROW: dict[str, Any] = {
    "id": uuid.UUID("55555555-5555-5555-5555-555555555555"),
    "bank_code": "058",
    "last4": "4321",
    "account_name": "Ada Example",
    "verified_at": datetime(2025, 1, 2, tzinfo=UTC),
    "status": "verified",
    "future_bank_field": "must not leak",
}
BALANCES = {
    "savings_kobo": 10_000,
    "shares_kobo": 2_000,
    "dues_paid_kobo": 500,
    "locked_kobo": 1_000,
    "available_kobo": 9_000,
}


def _session(role: str) -> CurrentSession:
    return CurrentSession(
        uuid.uuid4(),
        "members-contract-test",
        uuid.uuid4(),
        uuid.uuid4(),
        "staff@example.test",
        (role,),
        bytes(32),
    )


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows

    def one_or_none(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class _FakeSession:
    async def execute(self, statement: Any, params: dict[str, Any]) -> _FakeResult:
        query = str(statement)
        if "FROM bank_accounts" in query:
            return _FakeResult([dict(BANK_ROW)])
        if "FROM members" in query and "employer" in query:
            return _FakeResult([dict(MEMBER_DETAIL_ROW)])
        if "FROM members" in query:
            return _FakeResult([dict(MEMBER_LIST_ROW)])
        raise AssertionError(f"unexpected query in member API test: {query}")


@asynccontextmanager
async def _fake_transaction(*args: Any, **kwargs: Any):
    yield _FakeSession()


async def _member_request(
    role: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any] | list[dict[str, Any]]:
    monkeypatch.setattr(members_api, "transaction", _fake_transaction)

    async def fake_balances(cooperative_id: uuid.UUID, member_id: uuid.UUID) -> dict[str, int]:
        return dict(BALANCES)

    monkeypatch.setattr(members_api.service, "member_balances", fake_balances)
    app = FastAPI()
    app.include_router(members_api.router, prefix="/api/v1")
    app.dependency_overrides[members_api.current_session] = lambda: _session(role)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1{path}")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("role", ["admin", "officer", "treasurer", "committee", "auditor"])
async def test_member_list_and_detail_have_exact_role_key_sets(
    role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = ROLE_FIELDS[role]
    list_rows = await _member_request(role, "/members", monkeypatch)
    detail = await _member_request(role, f"/members/{MEMBER_ID}", monkeypatch)

    assert isinstance(list_rows, list) and len(list_rows) == 1
    assert set(list_rows[0]) == expected["list"]
    assert isinstance(detail, dict)
    assert set(detail) == expected["detail"]
    if expected["bank"]:
        assert set(detail["bank_accounts"][0]) == expected["bank"]
    if "balances" in expected["detail"]:
        assert set(detail["balances"]) == BALANCE_FIELDS


@pytest.mark.parametrize("role", ["admin", "officer", "treasurer", "committee", "auditor"])
async def test_new_member_fields_are_hidden_by_default(
    role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    list_rows = await _member_request(role, "/members", monkeypatch)
    detail = await _member_request(role, f"/members/{MEMBER_ID}", monkeypatch)

    assert "future_member_field" not in list_rows[0]
    assert "future_member_field" not in detail
    assert "future_bank_field" not in detail.get("bank_accounts", [{}])[0]


def test_decision_1_records_final_member_role_matrix_and_committee_todo() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    decisions = (root / "docs" / "decisions.md").read_text()
    assert "**committee**" in decisions
    assert "id`, `member_no`, and `full_name` only on both member endpoints" in decisions
    assert "committee loan-summary todo" in decisions.lower()
    assert "**auditor**" in decisions
    assert "savings_kobo" in decisions and "available_kobo" in decisions
    assert "`bank_code` and `last4` only" in decisions


async def test_committee_loan_decision_response_contains_no_member_contact_or_bank_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application_id = uuid.UUID(APPLICATION_ID)
    loan_id = uuid.uuid4()

    async def fake_decide(*args: Any, **kwargs: Any) -> uuid.UUID:
        return loan_id

    monkeypatch.setattr(loans_api.service, "decide_application", fake_decide)
    response = await loans_api.decide(
        application_id=application_id,
        body=loans_api.DecisionBody(decision="approved", reason="Committee approval"),
        current=_session("committee"),
    )

    assert response == {"loan_id": str(loan_id), "decision": "approved"}
    assert not ({"phone_e164", "email", "bank_accounts", "account_name", "last4"} & set(response))
