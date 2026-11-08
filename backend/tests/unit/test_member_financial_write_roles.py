"""Financial member-write role and maker-checker policies; no database is used."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.api import members as members_api
from app.core.errors import Conflict, Forbidden
from app.domains.auth.service import CurrentSession
from app.domains.members import service

COOPERATIVE_ID = uuid.uuid4()
ACTOR_ID = uuid.uuid4()
MEMBER_ID = uuid.uuid4()
REQUEST_ID = uuid.uuid4()


@asynccontextmanager
async def no_database_access(_cooperative_id: uuid.UUID):
    raise AssertionError("forbidden write reached a database transaction")
    yield None


def current(*roles: str, user_id: uuid.UUID = ACTOR_ID) -> CurrentSession:
    return CurrentSession(
        COOPERATIVE_ID,
        "test",
        uuid.uuid4(),
        user_id,
        "staff@example.test",
        roles,
        bytes(32),
    )


async def invoke_write(action: str, actor: CurrentSession) -> object:
    if action == "contribution":
        return await members_api.contribute(
            members_api.ContributionBody(
                member_id=MEMBER_ID,
                kind="savings",
                amount_kobo=100,
                idempotency_key="contribution-key",
            ),
            actor,
        )
    if action == "withdrawal_request":
        return await members_api.withdraw(
            members_api.WithdrawalBody(
                member_id=MEMBER_ID,
                amount_kobo=100,
                idempotency_key="withdrawal-key",
            ),
            actor,
        )
    if action in {"withdrawal_approval", "withdrawal_post"}:
        # Approval and ledger posting are deliberately one API operation today.
        return await members_api.approve_withdrawal(REQUEST_ID, actor)
    if action == "withdrawal_rejection":
        return await members_api.reject_withdrawal(
            REQUEST_ID,
            members_api.WithdrawalRejectBody(reason="Not enough supporting detail"),
            actor,
        )
    raise AssertionError(f"Unknown action: {action}")


@pytest.mark.parametrize(
    "action",
    ["contribution", "withdrawal_request", "withdrawal_approval", "withdrawal_post"],
)
@pytest.mark.asyncio
async def test_admin_gets_403_for_all_four_financial_write_actions(
    action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "transaction", no_database_access)
    with pytest.raises(Forbidden) as error:
        await invoke_write(action, current("admin"))
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_officer_gets_403_when_approving_withdrawal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "transaction", no_database_access)
    with pytest.raises(Forbidden) as error:
        await invoke_write("withdrawal_approval", current("officer"))
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_officer_gets_403_when_rejecting_withdrawal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "transaction", no_database_access)
    with pytest.raises(Forbidden) as error:
        await invoke_write("withdrawal_rejection", current("officer"))
    assert error.value.status_code == 403


@pytest.mark.parametrize("role", ["committee", "auditor"])
@pytest.mark.parametrize(
    "action", ["contribution", "withdrawal_request", "withdrawal_approval", "withdrawal_post"]
)
@pytest.mark.asyncio
async def test_committee_and_auditor_get_403_for_all_financial_writes(
    role: str, action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "transaction", no_database_access)
    with pytest.raises(Forbidden) as error:
        await invoke_write(action, current(role))
    assert error.value.status_code == 403


class RequestResult:
    def __init__(self, row: object) -> None:
        self.row = row

    def one_or_none(self) -> object:
        return self.row


class RequestSession:
    def __init__(self, row: object) -> None:
        self.row = row
        self.calls = 0

    async def execute(self, *_args: object, **_kwargs: object) -> RequestResult:
        self.calls += 1
        return RequestResult(self.row)


@asynccontextmanager
async def transaction_with(session: RequestSession, _cooperative_id: uuid.UUID):
    yield session


@pytest.mark.parametrize(
    "roles", [("treasurer",), ("admin", "treasurer"), ("treasurer", "officer")]
)
@pytest.mark.asyncio
async def test_treasurer_cannot_approve_own_request_even_with_multiple_roles(
    roles: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = current(*roles)
    row = SimpleNamespace(
        id=REQUEST_ID,
        member_id=MEMBER_ID,
        amount_kobo=100,
        requested_by=actor.user_id,
        status="pending",
    )
    session = RequestSession(row)
    monkeypatch.setattr(
        service,
        "transaction",
        lambda cooperative_id: transaction_with(session, cooperative_id),
    )

    with pytest.raises(Conflict, match="different user"):
        await service.approve_withdrawal(actor, REQUEST_ID)
    assert session.calls == 1


def test_admin_treasurer_combo_uses_treasurer_actor_role() -> None:
    actor = current("admin", "treasurer")
    assert service._financial_actor_role(actor, ("treasurer", "officer")) == "treasurer"
    assert service._financial_actor_role(actor, ("treasurer",)) == "treasurer"


def test_contribution_request_carries_effective_date_reference_and_payment_method() -> None:
    body = members_api.ContributionBody(
        member_id=MEMBER_ID,
        kind="savings",
        amount_kobo=1250,
        effective_date="2026-11-08",
        reference="cash receipt 14",
        payment_method="cash",
        idempotency_key="contribution-meta",
    )
    assert body.effective_date.isoformat() == "2026-11-08"
    assert body.reference == "cash receipt 14"
    assert body.payment_method == "cash"


def test_withdrawal_request_carries_reason() -> None:
    body = members_api.WithdrawalBody(
        member_id=MEMBER_ID,
        amount_kobo=100,
        reason="Member requested a savings payout",
        idempotency_key="withdrawal-reason",
    )
    assert body.reason == "Member requested a savings payout"
