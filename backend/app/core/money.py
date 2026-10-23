"""Money is an integer number of kobo. Never use float."""

from decimal import ROUND_HALF_UP, Decimal

KOBO_PER_NAIRA = 100


def naira_to_kobo(value: Decimal | str | int) -> int:
    """Convert naira to kobo. Floats are rejected on purpose.

    Rounding rule (documented, CONFIRM with EXCO): half-up to the nearest kobo.
    """
    if isinstance(value, float):
        raise TypeError("float is not allowed for money; pass str, int or Decimal")
    amount = Decimal(value) * KOBO_PER_NAIRA
    return int(amount.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def format_naira(kobo: int) -> str:
    """Format kobo as ₦1,250,000.00 (negative as -₦...)."""
    sign = "-" if kobo < 0 else ""
    whole, frac = divmod(abs(kobo), KOBO_PER_NAIRA)
    return f"{sign}₦{whole:,}.{frac:02d}"
