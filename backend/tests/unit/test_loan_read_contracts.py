"""Role-specific loan read contracts are conservative and tenant-scoped."""

from __future__ import annotations

import uuid

import pytest

from app.api.loans import (
    LoanApplicationDetailAdminOfficerResponse,
    LoanApplicationDetailAuditorResponse,
    LoanApplicationDetailCommitteeResponse,
    LoanApplicationDetailTreasurerResponse,
    LoanApplicationListAdminOfficerResponse,
    LoanApplicationListAuditorResponse,
    LoanApplicationListCommitteeResponse,
    LoanApplicationListTreasurerResponse,
    _loan_read_role,
)
from app.core.errors import Forbidden
from app.domains.auth.service import CurrentSession

pytestmark = pytest.mark.unit


APPLICATION_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
BASE = {
    "id": APPLICATION_ID,
    "member_no": "M-001",
    "full_name": "Ada Example",
    "product_name": "Test loan",
    "principal_kobo": 500_000,
    "tenor_months": 6,
    "annual_rate_bps": 1200,
    "status": "under_review",
    "submitted_at": "2026-10-31T10:00:00Z",
    "decision": None,
    "decision_reason": None,
    "decided_at": None,
    "consent_statuses": [{"kind": "applicant_ack", "status": "confirmed"}],
    "guarantor_count": 0,
}


@pytest.mark.parametrize(
    ("model", "extra"),
    [
        (LoanApplicationListAdminOfficerResponse, {}),
        (LoanApplicationListTreasurerResponse, {}),
        (LoanApplicationListCommitteeResponse, {"phone_e164": "+2348000000000"}),
        (LoanApplicationListAuditorResponse, {"account_number": "never"}),
    ],
)
def test_loan_list_contract_omits_forbidden_fields(model: type, extra: dict[str, object]) -> None:
    row = {**BASE, **extra, "future_field": "hidden by default"}
    response = model.model_validate(row)
    assert set(response.model_dump(exclude_none=False)) == set(BASE)
    assert "phone_e164" not in response.model_dump()
    assert "account_number" not in response.model_dump()
    assert "future_field" not in response.model_dump()


@pytest.mark.parametrize(
    "model",
    [
        LoanApplicationDetailAdminOfficerResponse,
        LoanApplicationDetailTreasurerResponse,
        LoanApplicationDetailCommitteeResponse,
        LoanApplicationDetailAuditorResponse,
    ],
)
def test_loan_detail_contract_keeps_sensitive_fields_out(model: type) -> None:
    row = {
        **BASE,
        "terms_snapshot": {"principal_kobo": 500_000},
        "status_timeline": [{"status": "submitted", "at": "2026-10-31T10:00:00Z"}],
        "guarantors": [{"member_no": "M-002", "full_name": "Bola Example"}],
        "schedule": [],
        "destination_bank_account": {"bank_code": "058", "last4": "4321"},
        "phone_e164": "+2348000000000",
        "email": "ada@example.test",
        "account_number": "never",
        "token_hash": "never",
        "otp_hash": "never",
    }
    response = model.model_validate(row)
    dumped = response.model_dump(exclude_none=False)
    assert "phone_e164" not in dumped
    assert "email" not in dumped
    assert "account_number" not in dumped
    assert "token_hash" not in dumped
    assert "otp_hash" not in dumped
    assert "guarantors" in dumped


@pytest.mark.parametrize("role", ["member", "", "platform_operator"])
def test_non_staff_roles_are_forbidden(role: str) -> None:
    current = CurrentSession(
        uuid.uuid4(),
        "loan-read-contract",
        uuid.uuid4(),
        uuid.uuid4(),
        "user@example.test",
        (role,),
        bytes(32),
    )
    with pytest.raises(Forbidden):
        _loan_read_role(current)


@pytest.mark.parametrize("role", ["admin", "officer", "treasurer", "committee", "auditor"])
def test_all_read_roles_are_allowed(role: str) -> None:
    current = CurrentSession(
        uuid.uuid4(),
        "loan-read-contract",
        uuid.uuid4(),
        uuid.uuid4(),
        "user@example.test",
        (role,),
        bytes(32),
    )
    assert _loan_read_role(current) == role
