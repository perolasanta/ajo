"""No-database unit tests for API startup readiness polling."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app import wait_for_db

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_wait_for_database_retries_until_select_one_succeeds(monkeypatch) -> None:
    attempts = 0
    queries: list[str] = []

    class Connection:
        async def execute(self, query) -> None:
            queries.append(str(query))

    @asynccontextmanager
    async def fake_connection():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("database not ready")
        yield Connection()

    monkeypatch.setattr(wait_for_db, "connection", fake_connection)
    await wait_for_db.wait_for_database(timeout_seconds=1, retry_interval_seconds=0.001)

    assert attempts == 2
    assert queries == ["SELECT 1"]
