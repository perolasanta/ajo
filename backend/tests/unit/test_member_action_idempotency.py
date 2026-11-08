from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from app.domains.auth.service import CurrentSession
from app.domains.members import service

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, rows: list[Any] | None = None, scalar: Any = None) -> None:
        self.rows = rows or []
        self.scalar = scalar

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> Any:
        return self.rows[0] if self.rows else None

    def scalar_one(self) -> Any:
        return self.scalar

    def __iter__(self):
        return iter(self.rows)


class _Session:
    def __init__(self) -> None:
        self.entry_id = uuid.uuid4()
        self.posts = 0
        self.existing = False

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        if "SELECT id,request_hash FROM journal_entries" in sql:
            if self.existing:
                return _Result([{"id": self.entry_id, "request_hash": bytes(32)}])
            return _Result()
        if "SELECT code,id FROM accounts" in sql:
            return _Result(
                [
                    SimpleNamespace(code="bank", id=uuid.uuid4()),
                    SimpleNamespace(code="member_savings", id=uuid.uuid4()),
                ]
            )
        if "post_journal_entry_with_payload" in sql:
            self.posts += 1
            self.existing = True
            return _Result(scalar=self.entry_id)
        return _Result()

    async def scalar(self, *_args: Any, **_kwargs: Any) -> bytes:
        return bytes(32)


@asynccontextmanager
async def _transaction(session: _Session, _cooperative_id: uuid.UUID):
    yield session


async def test_contribution_idempotency_replay_posts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    cooperative_id = uuid.uuid4()
    actor = CurrentSession(
        cooperative_id,
        "unit-test",
        uuid.uuid4(),
        uuid.uuid4(),
        "staff@example.test",
        ("treasurer",),
        bytes(32),
    )
    member_id = uuid.uuid4()
    session = _Session()
    monkeypatch.setattr(service, "transaction", lambda cid: _transaction(session, cid))

    async def no_audit(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(service, "append_audit", no_audit)
    first = await service.record_contribution(actor, member_id, "savings", 1250, "same-key")
    replay = await service.record_contribution(actor, member_id, "savings", 1250, "same-key")

    assert first == replay == session.entry_id
    assert session.posts == 1
