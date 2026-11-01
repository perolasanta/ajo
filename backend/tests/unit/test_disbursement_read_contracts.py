"""Role allowlists for disbursement reads exclude private and sensitive fields."""

import uuid

import pytest

from app.api.disbursements import _disbursement_projection
from app.core.errors import Forbidden
from app.domains.auth.service import CurrentSession
from app.domains.disbursements.reads import read_role


def _session(*roles: str) -> CurrentSession:
    return CurrentSession(
        uuid.uuid4(), "test", uuid.uuid4(), uuid.uuid4(), "unused", roles, bytes(32)
    )


@pytest.mark.parametrize("role", ["admin", "treasurer", "auditor"])
def test_only_decided_roles_can_read_disbursements(role: str) -> None:
    assert read_role(_session(role)) == role


@pytest.mark.parametrize("role", ["officer", "committee", "member"])
def test_other_roles_are_denied(role: str) -> None:
    with pytest.raises(Forbidden):
        read_role(_session(role))


def test_role_projections_are_exact_and_never_include_sensitive_columns() -> None:
    values = {
        "id": "d",
        "loan_id": "l",
        "application_id": "a",
        "member_no": "M-1",
        "full_name": "Member",
        "amount_kobo": 100,
        "status": "paid",
        "bank_code": "001",
        "last4": "1234",
        "prepared_by": "p",
        "authorized_by": "u",
        "transfer_reference": "R",
        "transfer_date": None,
        "confirmed_by": "c",
        "failure_reason": None,
        "reversal_reason": None,
        "created_at": None,
        "authorized_at": None,
        "transfer_recorded_at": None,
        "paid_at": None,
        "failed_at": None,
        "reversed_at": None,
        "email": "private",
        "phone": "private",
        "account_number": "never",
        "salary": 0,
        "staff_no": "never",
        "token": "never",
    }
    admin = _disbursement_projection(values, "admin")
    treasurer = _disbursement_projection(values, "treasurer")
    auditor = _disbursement_projection(values, "auditor")
    assert set(admin) == set(treasurer)
    assert "bank_code" in admin and "last4" in admin
    assert "bank_code" not in auditor and "last4" not in auditor
    assert all(
        not any(
            word in key
            for word in ("email", "phone", "account_number", "salary", "staff_no", "token")
        )
        for data in (admin, treasurer, auditor)
        for key in data
    )
