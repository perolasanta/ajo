"""Fixtures for deterministic application settings in tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.config import Settings, get_settings


@pytest.fixture
def notification_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Provide predictable email, Telegram, and public URL settings per test."""
    monkeypatch.setenv("EMAIL_ENABLED", "false")
    monkeypatch.setenv("TELEGRAM_ENABLED", "false")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example.test")
    get_settings.cache_clear()
    try:
        yield get_settings()
    finally:
        get_settings.cache_clear()
