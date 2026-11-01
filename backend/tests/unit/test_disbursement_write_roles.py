"""Server-side disbursement write gates run before database access."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import date

import pytest

from app.core.errors import Forbidden
from app.domains.auth.service import CurrentSession
from app.domains.disbursements import service

COOPERATIVE_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
DISBURSEMENT_ID = uuid.uuid4()


@asynccontextmanager
async def no_database_access(_cooperative_id: uuid.UUID):
    raise AssertionError("forbidden write reached the database transaction")
    yield None


def current(*roles: str) -> CurrentSession:
    return CurrentSession(
        COOPERATIVE_ID, "test", uuid.uuid4(), USER_ID, "staff@example.test", roles, bytes(32)
    )


async def invoke(action: str, actor: CurrentSession) -> object:
    if action == "prepare":
        return await service.prepare(actor, uuid.uuid4(), None)
    if action == "authorize":
        return await service.authorize(actor, DISBURSEMENT_ID, None)
    if action == "record_transfer":
        return await service.record_transfer(actor, DISBURSEMENT_ID, "REF", date.today(), None)
    if action == "confirm_paid":
        return await service.confirm_paid(actor, DISBURSEMENT_ID, None)
    if action == "fail_transfer":
        return await service.fail_transfer(actor, DISBURSEMENT_ID, "reason", None)
    if action == "reverse_paid":
        return await service.reverse_paid(actor, DISBURSEMENT_ID, "reason", None)
    raise AssertionError(f"Unknown action: {action}")


@pytest.mark.parametrize(
    ("action", "allowed_roles"),
    [
        ("prepare", {"treasurer"}),
        ("authorize", {"admin"}),
        ("record_transfer", {"treasurer"}),
        ("confirm_paid", {"admin"}),
        ("fail_transfer", {"treasurer", "admin"}),
        ("reverse_paid", {"admin"}),
    ],
)
@pytest.mark.parametrize("role", ["committee", "officer", "auditor"])
async def test_committee_officer_and_auditor_are_forbidden_from_every_write(
    action: str, allowed_roles: set[str], role: str, monkeypatch
) -> None:
    assert role not in allowed_roles
    monkeypatch.setattr(service, "transaction", no_database_access)
    with pytest.raises(Forbidden):
        await invoke(action, current(role))


@pytest.mark.parametrize(
    ("action", "allowed_roles", "role"),
    [
        ("prepare", {"treasurer"}, "admin"),
        ("authorize", {"admin"}, "treasurer"),
        ("record_transfer", {"treasurer"}, "admin"),
        ("confirm_paid", {"admin"}, "treasurer"),
        ("fail_transfer", {"treasurer", "admin"}, "committee"),
        ("reverse_paid", {"admin"}, "treasurer"),
    ],
)
async def test_write_matrix_denies_other_staff_roles(
    action: str, allowed_roles: set[str], role: str, monkeypatch
) -> None:
    assert role not in allowed_roles
    monkeypatch.setattr(service, "transaction", no_database_access)
    with pytest.raises(Forbidden):
        await invoke(action, current(role))
