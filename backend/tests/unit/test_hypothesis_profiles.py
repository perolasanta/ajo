"""Hypothesis profiles for database-backed properties must not time out on I/O."""

from __future__ import annotations

import os

import pytest
from hypothesis import settings


@pytest.mark.unit
def test_default_and_ci_profiles_disable_deadlines_and_preserve_example_counts() -> None:
    configured_profile = os.environ.get("HYPOTHESIS_PROFILE", "default")
    try:
        settings.load_profile("default")
        assert settings.default.deadline is None
        assert settings.default.max_examples == 25

        settings.load_profile("ci")
        assert settings.default.deadline is None
        assert settings.default.max_examples == 200
    finally:
        settings.load_profile(configured_profile)
