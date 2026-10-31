"""Security contracts for bearer-token consent routes."""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from app.api import consent as consent_api
from app.core.errors import NotFound
from app.core.logging import configure_logging
from app.main import create_app

TOKEN = "0123456789abcdef" * 4


def test_all_consent_token_routes_set_no_store_and_no_referrer(monkeypatch) -> None:
    async def fake_view(slug: str, token: str) -> dict[str, object]:
        return {
            "cooperative_name": "Test Cooperative",
            "kind": "final_terms",
            "expires_at": "2030-01-01T00:00:00+00:00",
        }

    async def fake_send_otp(slug: str, token: str, ip: str) -> None:
        return None

    async def fake_verify_otp(slug: str, token: str, otp: str, ip: str) -> dict[str, object]:
        return {"applicant": "Test Member", "terms": {"principal_kobo": 100}}

    async def fake_decide(slug: str, token: str, decision: str, ip: str, user_agent: str) -> str:
        return "recorded"

    monkeypatch.setattr(consent_api.service, "view", fake_view)
    monkeypatch.setattr(consent_api.service, "send_otp", fake_send_otp)
    monkeypatch.setattr(consent_api.service, "verify_otp", fake_verify_otp, raising=False)
    monkeypatch.setattr(consent_api.service, "decide", fake_decide)
    client = TestClient(create_app())
    base = f"/api/v1/confirm/test-coop/{TOKEN}"
    responses = [
        client.get(base),
        client.post(f"{base}/otp"),
        client.post(f"{base}/otp/verify", json={"otp": "123456"}),
        client.post(f"{base}/decision", json={"decision": "confirm"}),
    ]
    assert all(response.status_code < 400 for response in responses)
    assert responses[1].json() == {"status": "queued"}
    for response in responses:
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"

    async def missing_consent(slug: str, token: str) -> dict[str, object]:
        raise NotFound("Confirmation unavailable")

    monkeypatch.setattr(consent_api.service, "view", missing_consent)
    error_response = client.get(base)
    assert error_response.status_code == 404
    assert error_response.headers["cache-control"] == "no-store"
    assert error_response.headers["referrer-policy"] == "no-referrer"


def test_uvicorn_access_log_redacts_consent_token_path_segment() -> None:
    configure_logging("INFO")
    token = "abcdef0123456789" * 4
    path = f"/api/v1/confirm/test-coop/{token}/otp"
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s"',
        ("127.0.0.1", "POST", path, "1.1"),
        None,
    )
    access_logger = logging.getLogger("uvicorn.access")
    for log_filter in access_logger.filters:
        log_filter.filter(record)
    message = record.getMessage()
    assert token not in message
    assert "/confirm/test-coop/[redacted]/otp" in message
