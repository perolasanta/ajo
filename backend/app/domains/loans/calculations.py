"""Pure loan capacity and repayment schedule calculations; all amounts are integer kobo."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

WAT = ZoneInfo("Africa/Lagos")


@dataclass(frozen=True)
class CapacityResult:
    capacity_kobo: int
    partial_check: bool


@dataclass(frozen=True)
class Installment:
    due_at: datetime
    principal_kobo: int
    interest_kobo: int
    payment_kobo: int
    balance_kobo: int


def _round_ratio_half_up(numerator: int, denominator: int) -> int:
    if numerator < 0 or denominator <= 0:
        raise ValueError("ratio requires a non-negative numerator and positive denominator")
    return (2 * numerator + denominator) // (2 * denominator)


def deduction_capacity(
    salary_basis_kobo: int,
    cap_bps: int,
    committed_kobo: int,
    external_deductions_kobo: int | None,
) -> CapacityResult:
    if salary_basis_kobo < 0 or committed_kobo < 0 or not 0 <= cap_bps <= 10000:
        raise ValueError("salary, commitments and deduction cap are out of range")
    cap = _round_ratio_half_up(salary_basis_kobo * cap_bps, 10000)
    known_external = external_deductions_kobo or 0
    if known_external < 0:
        raise ValueError("external deductions cannot be negative")
    return CapacityResult(
        max(0, cap - committed_kobo - known_external), external_deductions_kobo is None
    )


def max_loan_from_savings(savings_kobo: int, multiple_x100: int) -> int:
    if savings_kobo < 0 or multiple_x100 < 0:
        raise ValueError("savings and multiple cannot be negative")
    return _round_ratio_half_up(savings_kobo * multiple_x100, 100)


def _first_wat_due(first_due_date: date | datetime) -> tuple[datetime, int]:
    if isinstance(first_due_date, datetime):
        local = (
            first_due_date.replace(tzinfo=WAT)
            if first_due_date.tzinfo is None
            else first_due_date.astimezone(WAT)
        )
        return local, local.day
    return datetime.combine(first_due_date, time.min, tzinfo=WAT), first_due_date.day


def _due_month(first: datetime, anchor_day: int, offset: int) -> datetime:
    month_index = first.year * 12 + first.month - 1 + offset
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(anchor_day, calendar.monthrange(year, month)[1])
    return first.replace(year=year, month=month, day=day)


def build_schedule(
    principal_kobo: int,
    annual_rate_bps: int,
    tenor_months: int,
    method: str,
    first_due_date: date | datetime,
) -> list[Installment]:
    if principal_kobo <= 0 or annual_rate_bps < 0 or tenor_months < 1:
        raise ValueError("principal, rate or tenor is out of range")
    if method not in {"reducing_balance", "flat"}:
        raise ValueError("unsupported interest method")
    first, anchor_day = _first_wat_due(first_due_date)
    rate = Decimal(annual_rate_bps) / Decimal(120000)
    if method == "reducing_balance" and rate:
        factor = (Decimal(1) + rate) ** tenor_months
        raw_payment = Decimal(principal_kobo) * rate * factor / (factor - Decimal(1))
        payment = int(raw_payment.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    else:
        payment = _round_ratio_half_up(principal_kobo, tenor_months)

    flat_interest_total = (
        _round_ratio_half_up(principal_kobo * annual_rate_bps * tenor_months, 120_000)
        if method == "flat"
        else 0
    )
    flat_base, flat_remainder = divmod(flat_interest_total, tenor_months)
    principal_base, principal_remainder = divmod(principal_kobo, tenor_months)
    remaining = principal_kobo
    schedule: list[Installment] = []
    for index in range(tenor_months):
        if method == "flat":
            principal_part = principal_base + (1 if index < principal_remainder else 0)
            interest_part = flat_base + (1 if index < flat_remainder else 0)
        else:
            interest_part = _round_ratio_half_up(remaining * annual_rate_bps, 120000)
            principal_part = min(remaining, max(0, payment - interest_part))
        if index == tenor_months - 1:
            principal_part = remaining
        remaining -= principal_part
        schedule.append(
            Installment(
                due_at=_due_month(first, anchor_day, index),
                principal_kobo=principal_part,
                interest_kobo=interest_part,
                payment_kobo=principal_part + interest_part,
                balance_kobo=remaining,
            )
        )
    return schedule
