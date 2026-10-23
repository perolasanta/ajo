"""Time rules: store UTC, compute payroll periods in WAT (Africa/Lagos)."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

WAT = ZoneInfo("Africa/Lagos")


def utcnow() -> datetime:
    return datetime.now(UTC)


def payroll_period(moment: datetime) -> tuple[int, int]:
    """(year, month) of the payroll period containing ``moment`` in WAT.

    A timestamp at 23:30 UTC on the last day of a month is already the next
    month in Lagos (UTC+1), which is the classic month-end reconciliation bug.
    """
    if moment.tzinfo is None:
        raise ValueError("naive datetimes are not allowed")
    local = moment.astimezone(WAT)
    return local.year, local.month
