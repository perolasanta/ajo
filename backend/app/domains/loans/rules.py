"""Pure validation and display helpers for cooperative loan rules."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from app.core.money import format_naira


def full_membership_months(joined_on: date | None, application_date: date) -> int:
    if joined_on is None or joined_on > application_date:
        return 0
    months = (
        (application_date.year - joined_on.year) * 12 + application_date.month - joined_on.month
    )
    return max(0, months - int(application_date.day < joined_on.day))


def format_rate(annual_rate_bps: int) -> str:
    annual = Decimal(annual_rate_bps) / Decimal(100)
    monthly = Decimal(annual_rate_bps) / Decimal(1200)
    return f"{annual:.2f}% a year ({monthly:.2f}% a month)"


def format_savings_cap_message(max_amount_kobo: int, multiple_x100: int) -> str:
    multiple = Decimal(multiple_x100) / Decimal(100)
    return (
        f"Loan is above the maximum of {format_naira(max_amount_kobo)} ({multiple:g}x of savings)"
    )


def validate_guarantor_ids(
    borrower_id: str,
    guarantor_ids: list[str],
    members: Mapping[str, tuple[str, str]],
    must_be_active: bool = True,
) -> None:
    if len(guarantor_ids) != len(set(guarantor_ids)):
        raise ValueError("Guarantors must not be duplicated")
    for guarantor_id in guarantor_ids:
        if guarantor_id == borrower_id:
            raise ValueError("Borrower cannot be a guarantor")
        member = members.get(guarantor_id)
        if member is None or member[0] != "same" or (must_be_active and member[1] != "active"):
            raise ValueError("Guarantor must be an active member of the same cooperative")


def validate_min_membership(joined_on: date | None, application_date: date, minimum: int) -> None:
    months = full_membership_months(joined_on, application_date)
    if months < minimum:
        raise ValueError(
            f"Member has been a member for {months} months; at least {minimum} required."
        )
