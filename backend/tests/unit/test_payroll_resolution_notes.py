"""Unit contracts for payroll exception-resolution notes."""

from __future__ import annotations

import hashlib
import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import payroll as payroll_api
from app.core.errors import Conflict, Forbidden, NotFound, install_error_handlers
from app.domains.auth.service import CurrentSession
from app.domains.notifications import eligibility
from app.domains.payroll import service as payroll_service

pytestmark = pytest.mark.unit

RUN_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
ITEM_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
IDEMPOTENCY_KEY = "resolution-test-key-01"
NOTE = "Reviewed with the payroll register."


def _session(role: str, *, user_id: uuid.UUID | None = None) -> CurrentSession:
    return CurrentSession(
        uuid.uuid4(),
        "resolution-test",
        uuid.uuid4(),
        user_id or uuid.uuid4(),
        "staff@example.test",
        (role,),
        bytes(32),
    )


class _Result:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self.row

    def one(self) -> dict[str, Any]:
        assert self.row is not None
        return self.row


class _ResolutionSession:
    def __init__(
        self,
        cooperative_id: uuid.UUID,
        *,
        status: str = "posted",
        classification: str = "short",
        existing_note: str | None = None,
        unresolved_after: bool = False,
        existing_request: dict[str, Any] | None = None,
        run_exists: bool = True,
    ) -> None:
        self.cooperative_id = cooperative_id
        self.status = status
        self.classification = classification
        self.notes: dict[uuid.UUID, str] = {}
        if existing_note is not None:
            self.notes[ITEM_ID] = existing_note
        self.unresolved_after = unresolved_after
        self.existing_requests: dict[str, dict[str, Any]] = {}
        self.initial_request = existing_request
        self.run_exists = run_exists
        self.resolution_at = "2026-11-07T00:00:00Z"
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        sql = " ".join(str(statement).lower().split())
        self.calls.append((sql, params))
        if "payroll_runs" in sql and sql.startswith("select"):
            return _Result(
                {
                    "id": RUN_ID,
                    "status": self.status,
                    "posted_by": uuid.uuid4(),
                    "imported_by": None,
                }
                if params.get("cid") == self.cooperative_id and self.run_exists
                else None
            )
        if "payroll_item_resolution_idempotency" in sql and sql.startswith("select"):
            return _Result(self.existing_requests.get(str(params.get("key")), self.initial_request))
        if "payroll_items" in sql and sql.startswith("select"):
            item_id = params.get("item", ITEM_ID)
            return _Result(
                {
                    "id": item_id,
                    "classification": self.classification,
                    "resolution_note": self.notes.get(item_id),
                }
                if params.get("cid") == self.cooperative_id
                else None
            )
        if sql.startswith("update payroll_items"):
            self.notes[params["item"]] = params["note"]
            self.resolution_at = "2026-11-07T00:00:00Z"
            return _Result({"resolved_at": self.resolution_at})
        if sql.startswith("insert into payroll_item_resolution_idempotency"):
            self.existing_requests[str(params["key"])] = {
                "request_hash": params["request_hash"],
                "item_id": params["item"],
            }
        return _Result()

    async def scalar(self, statement: Any, params: dict[str, Any]) -> Any:
        sql = " ".join(str(statement).lower().split())
        self.calls.append((sql, params))
        if "canonical_request_hash" in sql:
            return hashlib.sha256(params["payload"].encode()).digest()
        if "select exists" in sql:
            return self.unresolved_after
        return None


@asynccontextmanager
async def _transaction_for(session: _ResolutionSession, cooperative_id: uuid.UUID) -> Any:
    assert cooperative_id == session.cooperative_id
    yield session


def test_resolution_route_requires_csrf_and_idempotency_header() -> None:
    route = next(
        (
            route
            for route in payroll_api.router.routes
            if route.path == "/payroll/runs/{run_id}/items/{item_id}/resolution"
            and "POST" in route.methods
        ),
        None,
    )
    assert route is not None
    assert any(
        dependency.call is payroll_api.require_csrf for dependency in route.dependant.dependencies
    )
    assert route.dependant.header_params[0].alias == "Idempotency-Key"


@pytest.mark.parametrize("role", ["admin", "officer", "committee", "auditor"])
async def test_only_treasurer_can_add_resolution_note(
    role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = _session(role)

    @asynccontextmanager
    async def unexpected_transaction(cooperative_id: uuid.UUID) -> Any:
        raise AssertionError("authorization must run before database access")
        yield None

    monkeypatch.setattr(payroll_service, "transaction", unexpected_transaction)
    with pytest.raises(Forbidden):
        await payroll_service.add_resolution_note(current, RUN_ID, ITEM_ID, NOTE, IDEMPOTENCY_KEY)


async def test_resolution_route_trims_note_and_returns_safe_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _session("treasurer")
    captured: dict[str, Any] = {}

    async def add_note(
        session: CurrentSession, run_id: uuid.UUID, item_id: uuid.UUID, note: str, key: str
    ) -> bool:
        captured.update(session=session, run_id=run_id, item_id=item_id, note=note, key=key)
        return True

    monkeypatch.setattr(payroll_api.service, "add_resolution_note", add_note)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(payroll_api.router, prefix="/api/v1")
    app.dependency_overrides[payroll_api.current_session] = lambda: current
    app.dependency_overrides[payroll_api.require_csrf] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/payroll/runs/{RUN_ID}/items/{ITEM_ID}/resolution",
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json={"note": f"  {NOTE}  "},
        )
    assert response.status_code == 200
    assert response.json() == {"resolved": True}
    assert captured == {
        "session": current,
        "run_id": RUN_ID,
        "item_id": ITEM_ID,
        "note": NOTE,
        "key": IDEMPOTENCY_KEY,
    }


@pytest.mark.parametrize("note", ["short", "   ", "x" * 501])
async def test_resolution_note_length_is_validated_after_trimming(note: str) -> None:
    current = _session("treasurer")
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(payroll_api.router, prefix="/api/v1")
    app.dependency_overrides[payroll_api.current_session] = lambda: current
    app.dependency_overrides[payroll_api.require_csrf] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/payroll/runs/{RUN_ID}/items/{ITEM_ID}/resolution",
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json={"note": note},
        )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("status", "classification", "existing_note", "error"),
    [
        ("imported", "short", None, Conflict),
        ("signed_off", "short", None, Conflict),
        ("posted", "matched", None, Conflict),
        ("posted", "short", "Already reviewed", Conflict),
    ],
)
async def test_resolution_rejects_wrong_run_state_matched_or_existing_note(
    status: str,
    classification: str,
    existing_note: str | None,
    error: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _session("treasurer")
    fake = _ResolutionSession(
        current.cooperative_id,
        status=status,
        classification=classification,
        existing_note=existing_note,
    )
    monkeypatch.setattr(payroll_service, "transaction", lambda cid: _transaction_for(fake, cid))
    with pytest.raises(error):
        await payroll_service.add_resolution_note(current, RUN_ID, ITEM_ID, NOTE, IDEMPOTENCY_KEY)


async def test_resolution_is_tenant_scoped_and_missing_foreign_run_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _session("treasurer")
    fake = _ResolutionSession(current.cooperative_id, run_exists=False)
    monkeypatch.setattr(payroll_service, "transaction", lambda cid: _transaction_for(fake, cid))
    with pytest.raises(NotFound):
        await payroll_service.add_resolution_note(current, RUN_ID, ITEM_ID, NOTE, IDEMPOTENCY_KEY)
    run_queries = [sql for sql, _ in fake.calls if "payroll_runs" in sql]
    assert run_queries and all("cooperative_id=:cid" in query for query in run_queries)
    assert all(
        params.get("cid") == current.cooperative_id for _, params in fake.calls if "cid" in params
    )


async def test_same_idempotency_key_replays_but_different_payload_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _session("treasurer")
    fake = _ResolutionSession(current.cooperative_id, unresolved_after=True)

    async def audit(session: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(payroll_service, "transaction", lambda cid: _transaction_for(fake, cid))
    monkeypatch.setattr(payroll_service, "append_audit", audit)
    assert await payroll_service.add_resolution_note(
        current, RUN_ID, ITEM_ID, NOTE, IDEMPOTENCY_KEY
    )
    inserts_after_first = sum(
        "insert into payroll_item_resolution_idempotency" in sql for sql, _ in fake.calls
    )
    assert await payroll_service.add_resolution_note(
        current, RUN_ID, ITEM_ID, NOTE, IDEMPOTENCY_KEY
    )
    inserts_after_replay = sum(
        "insert into payroll_item_resolution_idempotency" in sql for sql, _ in fake.calls
    )
    assert inserts_after_replay == inserts_after_first == 1
    with pytest.raises(Conflict, match="Idempotency key"):
        await payroll_service.add_resolution_note(
            current, RUN_ID, ITEM_ID, "A different resolution note.", IDEMPOTENCY_KEY
        )


async def test_last_exception_note_enqueues_sign_off_once_and_audit_omits_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _session("treasurer")
    fake = _ResolutionSession(current.cooperative_id, unresolved_after=True)
    notified: list[tuple[uuid.UUID, str, uuid.UUID]] = []
    audit_summaries: list[dict[str, Any]] = []

    async def enqueue(
        session: Any, cooperative_id: uuid.UUID, kind: str, entity_id: uuid.UUID
    ) -> None:
        notified.append((cooperative_id, kind, entity_id))

    async def audit(session: Any, **kwargs: Any) -> None:
        audit_summaries.append(kwargs["summary"])

    monkeypatch.setattr(payroll_service, "transaction", lambda cid: _transaction_for(fake, cid))
    monkeypatch.setattr(payroll_service, "enqueue_action_notifications_safely", enqueue)
    monkeypatch.setattr(payroll_service, "append_audit", audit)
    await payroll_service.add_resolution_note(current, RUN_ID, ITEM_ID, NOTE, IDEMPOTENCY_KEY)
    assert notified == []  # one exception remains unresolved
    assert audit_summaries and NOTE not in str(audit_summaries)

    second_item = uuid.UUID("33333333-3333-3333-3333-333333333333")
    fake.unresolved_after = False
    await payroll_service.add_resolution_note(
        current, RUN_ID, second_item, "Second exception reviewed.", "resolution-test-key-02"
    )
    assert notified == [(current.cooperative_id, eligibility.PAYROLL_SIGN_OFF, RUN_ID)]
    await payroll_service.add_resolution_note(
        current, RUN_ID, second_item, "Second exception reviewed.", "resolution-test-key-02"
    )
    assert notified == [(current.cooperative_id, eligibility.PAYROLL_SIGN_OFF, RUN_ID)]
    assert NOTE not in str(audit_summaries)


async def test_resolution_queries_and_audit_are_tenant_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _session("treasurer")
    fake = _ResolutionSession(current.cooperative_id)
    audit_rows: list[dict[str, Any]] = []

    async def audit(session: Any, **kwargs: Any) -> None:
        audit_rows.append(kwargs)

    monkeypatch.setattr(payroll_service, "transaction", lambda cid: _transaction_for(fake, cid))
    monkeypatch.setattr(payroll_service, "append_audit", audit)
    await payroll_service.add_resolution_note(current, RUN_ID, ITEM_ID, NOTE, IDEMPOTENCY_KEY)
    assert all(
        params.get("cid") == current.cooperative_id for _, params in fake.calls if "cid" in params
    )
    assert audit_rows[0]["cooperative_id"] == current.cooperative_id
    assert audit_rows[0]["actor_user_id"] == current.user_id
    assert audit_rows[0]["entity_id"] == str(ITEM_ID)
    update_sql, update_params = next(
        (sql, params) for sql, params in fake.calls if sql.startswith("update payroll_items")
    )
    assert "resolved_by=:actor" in update_sql and "resolved_at=now()" in update_sql
    assert update_params["actor"] == current.user_id


async def test_sign_off_still_rejects_the_poster(monkeypatch: pytest.MonkeyPatch) -> None:
    current = _session("admin")
    fake = _ResolutionSession(current.cooperative_id)
    fake.status = "posted"

    async def execute(statement: Any, params: dict[str, Any]) -> _Result:
        fake.calls.append((str(statement), params))
        return _Result(
            {
                "status": "posted",
                "posted_by": current.user_id,
                "imported_by": uuid.uuid4(),
                "file_total_kobo": 100,
            }
        )

    fake.execute = execute  # type: ignore[method-assign]
    monkeypatch.setattr(payroll_service, "transaction", lambda cid: _transaction_for(fake, cid))
    with pytest.raises(Conflict, match="different administrator"):
        await payroll_service.sign_off(current, RUN_ID, 100)
    assert len(fake.calls) == 1


def test_payroll_sign_off_eligibility_requires_posted_run_and_resolved_exceptions() -> None:
    query = " ".join(eligibility._CANDIDATE_SQL[eligibility.PAYROLL_SIGN_OFF].lower().split())
    assert "r.status='posted'" in query
    assert "pi.classification<>'matched' and pi.resolution_note is null" in query
    assert "not exists" in query
    assert "r.posted_by" in query and "r.imported_by" in query
