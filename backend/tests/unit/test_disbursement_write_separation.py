"""Disbursement separation checks are enforced before any ledger side effects."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest

from app.core.errors import Conflict
from app.domains.auth.service import CurrentSession
from app.domains.disbursements import service

COOPERATIVE_ID = uuid.uuid4()
ACTOR_ID = uuid.uuid4()
OTHER_ID = uuid.uuid4()


class Result:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self.row = row

    def mappings(self) -> Result:
        return self

    def one_or_none(self) -> dict[str, object] | None:
        return self.row

    def scalars(self) -> Result:
        return self

    def all(self) -> list[object]:
        return []


class Session:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row
        self.calls = 0

    async def execute(self, *_args: object, **_kwargs: object) -> Result:
        self.calls += 1
        return Result(self.row if self.calls == 1 else None)


@asynccontextmanager
async def transaction_with(session: Session, _cooperative_id: uuid.UUID):
    yield session


def actor(*roles: str) -> CurrentSession:
    return CurrentSession(
        COOPERATIVE_ID, "test", uuid.uuid4(), ACTOR_ID, "staff@example.test", roles, bytes(32)
    )


@pytest.mark.asyncio
async def test_preparer_with_admin_and_treasurer_roles_cannot_authorize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "member_id": uuid.uuid4(),
        "loan_id": uuid.uuid4(),
        "application_id": uuid.uuid4(),
        "prepared_by": ACTOR_ID,
        "submitted_by": OTHER_ID,
        "status": "pending_authorization",
    }
    session = Session(row)
    monkeypatch.setattr(service, "transaction", lambda cid: transaction_with(session, cid))
    monkeypatch.setattr(service, "_load", _load)
    monkeypatch.setattr(service, "_validate", _valid)
    with pytest.raises(Conflict, match="independent"):
        await service.authorize(actor("admin", "treasurer"), uuid.uuid4(), None)


@pytest.mark.asyncio
async def test_authorizer_with_admin_and_committee_roles_cannot_confirm_paid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "member_id": uuid.uuid4(),
        "loan_id": uuid.uuid4(),
        "application_id": uuid.uuid4(),
        "prepared_by": OTHER_ID,
        "submitted_by": OTHER_ID,
        "authorized_by": ACTOR_ID,
        "status": "transfer_recorded",
        "fee_kobo": 0,
    }
    session = Session(row)
    monkeypatch.setattr(service, "transaction", lambda cid: transaction_with(session, cid))
    monkeypatch.setattr(service, "_load", _load)
    monkeypatch.setattr(service, "_validate", _valid)
    with pytest.raises(Conflict, match="different admin"):
        await service.confirm_paid(actor("admin", "committee"), uuid.uuid4(), None)


async def _load(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {}


async def _valid(*_args: object, **_kwargs: object) -> bool:
    return True
