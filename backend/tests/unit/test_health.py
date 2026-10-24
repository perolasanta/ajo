import pytest
from fastapi.testclient import TestClient

from app.main import create_app

pytestmark = pytest.mark.unit


def test_liveness_without_database():
    client = TestClient(create_app())
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
