from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.api import health as health_api
from app.main import create_app

pytestmark = pytest.mark.unit


def test_liveness_without_database():
    client = TestClient(create_app())
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readiness_only_discloses_status(monkeypatch):
    class Result:
        def one(self):
            return ("private_db_role", "private_server_version")

    class Connection:
        async def execute(self, query):
            return Result()

    @asynccontextmanager
    async def fake_connection():
        yield Connection()

    monkeypatch.setattr(health_api, "connection", fake_connection)
    response = TestClient(create_app()).get("/api/v1/health/db")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "private_db_role" not in response.text
    assert "private_server_version" not in response.text


def test_readiness_failure_only_discloses_not_ok(monkeypatch):
    @asynccontextmanager
    async def failing_connection():
        raise RuntimeError("private connection detail")
        yield

    monkeypatch.setattr(health_api, "connection", failing_connection)
    response = TestClient(create_app()).get("/api/v1/health/db")
    assert response.status_code == 503
    assert response.json() == {"status": "not ok"}
    assert "private connection detail" not in response.text
