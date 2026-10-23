from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.money import format_naira, naira_to_kobo


def test_naira_to_kobo_exact():
    assert naira_to_kobo("1250000.00") == 125_000_000
    assert naira_to_kobo(Decimal("0.01")) == 1
    assert naira_to_kobo(5) == 500


def test_half_up_rounding():
    assert naira_to_kobo("0.005") == 1
    assert naira_to_kobo("0.004") == 0


def test_float_rejected():
    with pytest.raises(TypeError):
        naira_to_kobo(1.1)


def test_format():
    assert format_naira(125_000_000) == "₦1,250,000.00"
    assert format_naira(-50) == "-₦0.50"


@given(st.integers(min_value=-(10**15), max_value=10**15))
def test_roundtrip(kobo):
    text = format_naira(kobo).replace("₦", "").replace(",", "")
    assert naira_to_kobo(text) == kobo
