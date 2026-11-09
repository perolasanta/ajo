from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest
from pydantic import ValidationError

from app.api.policies import PolicyBody
from app.domains.loans.rules import (
    format_rate,
    format_savings_cap_message,
    full_membership_months,
    validate_guarantor_ids,
)


def independent_reducing_schedule(
    principal_kobo: int, annual_rate_bps: int, months: int
) -> list[tuple[int, int]]:
    monthly = Decimal(annual_rate_bps) / Decimal(120000)
    if monthly:
        factor = (Decimal(1) + monthly) ** months
        payment = (Decimal(principal_kobo) * monthly * factor / (factor - 1)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    else:
        payment = Decimal(principal_kobo // months)
    remaining = principal_kobo
    rows = []
    for index in range(months):
        interest = int(
            (Decimal(remaining) * monthly).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        principal = (
            remaining if index == months - 1 else min(remaining, max(0, int(payment) - interest))
        )
        rows.append((principal, interest))
        remaining -= principal
    return rows


def test_independent_reducing_balance_examples_and_exact_principal() -> None:
    for rate, expected_payment, expected_interest in (
        (3600, 2_421_759, 2_374_068),
        (300, 2_148_976, 191_807),
    ):
        rows = independent_reducing_schedule(17_000_000, rate, 8)
        assert abs(rows[0][0] + rows[0][1] - expected_payment) <= 1
        assert sum(principal for principal, _ in rows) == 17_000_000
        assert abs(sum(interest for _, interest in rows) - expected_interest) <= 1


@pytest.mark.parametrize("value", [True, -1, 601, 1.5, "6", None])
def test_min_membership_months_requires_integer_zero_to_six_hundred(value) -> None:
    with pytest.raises(ValidationError, match="min_membership_months"):
        PolicyBody(
            effective_from=date(2026, 1, 1),
            salary_basis="net",
            deduction_cap_bps=7000,
            max_active_loans=1,
            guarantors_required=0,
            min_savings_kobo=0,
            loan_to_savings_multiple_x100=100,
            interest_method="reducing_balance",
            settings={"min_membership_months": value},
            reason="test",
        )


def test_membership_month_boundary_is_calendar_months() -> None:
    assert full_membership_months(date(2025, 5, 9), date(2025, 11, 9)) == 6
    assert full_membership_months(date(2025, 5, 9), date(2025, 11, 8)) == 5


def test_guarantor_validation_messages() -> None:
    with pytest.raises(
        ValueError, match="Guarantor must be an active member of the same cooperative"
    ):
        validate_guarantor_ids(
            "borrower", ["suspended"], {"suspended": ("same", "suspended")}, True
        )
    with pytest.raises(ValueError, match="Borrower cannot be a guarantor"):
        validate_guarantor_ids("borrower", ["borrower"], {"borrower": ("same", "active")}, True)
    with pytest.raises(ValueError, match="Guarantors must not be duplicated"):
        validate_guarantor_ids("borrower", ["g1", "g1"], {"g1": ("same", "active")}, True)


def test_rate_display_uses_annual_bps() -> None:
    assert format_rate(3600) == "36.00% a year (3.00% a month)"


def test_savings_cap_message_includes_amount_and_multiple() -> None:
    assert format_savings_cap_message(1_700_000, 1000) == (
        "Loan is above the maximum of ₦17,000.00 (10x of savings)"
    )
