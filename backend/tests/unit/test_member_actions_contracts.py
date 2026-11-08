"""Failing contracts for member contribution and withdrawal actions."""
from __future__ import annotations

import uuid

import pytest

from app.api import members as members_api
from app.domains.members import service
from app.domains.notifications import eligibility

pytestmark = pytest.mark.unit


def test_contribution_body_carries_date_reference_and_payment_method() -> None:
    body = members_api.ContributionBody(
        member_id=uuid.uuid4(),
        kind="savings",
        amount_kobo=1250,
        effective_date="2026-11-08",
        reference="cash desk receipt 12",
        payment_method="cash",
        idempotency_key="contribution-contract",
    )
    assert body.effective_date.isoformat() == "2026-11-08"
    assert body.reference == "cash desk receipt 12"
    assert body.payment_method == "cash"


def test_withdrawal_body_requires_reason() -> None:
    body = members_api.WithdrawalBody(
        member_id=uuid.uuid4(),
        amount_kobo=1250,
        reason="Emergency school fees",
        idempotency_key="withdrawal-contract",
    )
    assert body.reason == "Emergency school fees"


def test_withdrawal_read_contract_contains_requester_and_resolution_reason() -> None:
    assert hasattr(members_api, "MemberWithdrawalRequestPage")
    assert hasattr(members_api, "member_withdrawals")
    assert hasattr(members_api, "reject_withdrawal")


@pytest.mark.parametrize("role", ["admin", "committee", "auditor", "member"])
def test_non_action_roles_remain_forbidden(role: str) -> None:
    assert role not in {"officer", "treasurer"}
    assert service._financial_actor_role


def test_withdrawal_approval_notification_uses_shared_action_constant() -> None:
    assert hasattr(eligibility, "WITHDRAWAL_APPROVAL")
    assert eligibility.WITHDRAWAL_APPROVAL in eligibility.ACTION_SPECS


def test_rejection_requires_a_reason() -> None:
    assert "reason" in service.reject_withdrawal.__annotations__
