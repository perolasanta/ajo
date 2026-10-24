from datetime import UTC, datetime

import pytest

from app.core.time import payroll_period

pytestmark = pytest.mark.unit


def test_month_end_utc_is_next_month_in_lagos():
    # 23:30 UTC on 31 Jan is 00:30 on 1 Feb in Lagos (UTC+1)
    assert payroll_period(datetime(2026, 1, 31, 23, 30, tzinfo=UTC)) == (2026, 2)


def test_mid_month():
    assert payroll_period(datetime(2026, 3, 15, 12, 0, tzinfo=UTC)) == (2026, 3)


def test_naive_rejected():
    with pytest.raises(ValueError):
        payroll_period(datetime(2026, 3, 15, 12, 0))
