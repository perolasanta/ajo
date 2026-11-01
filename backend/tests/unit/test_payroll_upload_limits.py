"""Payroll upload size, content-type, and row-count limits."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.api import payroll as payroll_api
from app.core.errors import PayloadTooLarge, UnprocessableEntity, install_error_handlers
from app.domains.auth.service import CurrentSession
from app.domains.payroll import service

pytestmark = pytest.mark.unit
RUN_ID = "11111111-1111-1111-1111-111111111111"


def _session() -> CurrentSession:
    return CurrentSession(
        uuid.uuid4(),
        "payroll-upload-test",
        uuid.uuid4(),
        uuid.uuid4(),
        "staff@example.test",
        ("officer",),
        bytes(32),
    )


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(payroll_api.router, prefix="/api/v1")
    app.dependency_overrides[payroll_api.current_session] = _session
    app.dependency_overrides[payroll_api.require_csrf] = lambda: None
    return app


def _report() -> service.ImportReport:
    return service.ImportReport("a" * 64, 0, [], [])


def _make_request(chunks: list[bytes], content_length: str | None) -> tuple[Request, list[int]]:
    headers = [(b"content-type", b"text/csv")]
    if content_length is not None:
        headers.append((b"content-length", content_length.encode()))
    remaining = list(chunks)
    receive_calls = [0]

    async def receive() -> dict[str, Any]:
        receive_calls[0] += 1
        if not remaining:
            return {"type": "http.request", "body": b"", "more_body": False}
        body = remaining.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(remaining)}

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/payroll/runs/run/dry-run",
        "raw_path": b"/api/v1/payroll/runs/run/dry-run",
        "query_string": b"filename=payroll.csv",
        "headers": headers,
        "server": ("test", 80),
        "client": ("test", 123),
    }
    return Request(scope, receive), receive_calls


@pytest.mark.parametrize("action", ["dry-run", "import"])
async def test_upload_routes_accept_exactly_the_5_mib_limit(
    action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[int, str]] = []

    async def accept(
        _current: Any, _run_id: uuid.UUID, content: bytes, filename: str
    ) -> service.ImportReport:
        captured.append((len(content), filename))
        return _report()

    monkeypatch.setattr(
        service,
        "dry_run" if action == "dry-run" else "import_file",
        accept,
    )
    payload = b"x" * service.MAX_PAYROLL_UPLOAD_BYTES
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/payroll/runs/{RUN_ID}/{action}?filename=payroll.csv",
            content=payload,
            headers={"Content-Type": "text/csv"},
        )
    assert response.status_code == 200
    assert captured == [(service.MAX_PAYROLL_UPLOAD_BYTES, "payroll.csv")]


@pytest.mark.parametrize("action", ["dry-run", "import"])
async def test_upload_routes_reject_files_over_5_mib(
    action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unexpected(*_args: Any, **_kwargs: Any) -> service.ImportReport:
        raise AssertionError("oversized upload must be rejected before service work")

    monkeypatch.setattr(
        service,
        "dry_run" if action == "dry-run" else "import_file",
        unexpected,
    )
    payload = b"x" * (service.MAX_PAYROLL_UPLOAD_BYTES + 1)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/payroll/runs/{RUN_ID}/{action}?filename=payroll.csv",
            content=payload,
            headers={"Content-Type": "text/csv"},
        )
    assert response.status_code == 413
    assert "5 MiB" in response.json()["message"]


@pytest.mark.parametrize("action", ["dry-run", "import"])
@pytest.mark.parametrize(
    ("content_type", "filename"),
    [
        ("application/pdf", "payroll.csv"),
        ("application/json", "payroll.csv"),
        ("text/csv", "payroll.xlsx"),
    ],
)
async def test_upload_routes_reject_non_csv_content_types(
    action: str, content_type: str, filename: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unexpected(*_args: Any, **_kwargs: Any) -> service.ImportReport:
        raise AssertionError("wrong media type must be rejected before service work")

    monkeypatch.setattr(
        service,
        "dry_run" if action == "dry-run" else "import_file",
        unexpected,
    )
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/payroll/runs/{RUN_ID}/{action}?filename={filename}",
            content=b"not a csv request",
            headers={"Content-Type": content_type},
        )
    assert response.status_code == 415
    assert "text/csv" in response.json()["message"]


@pytest.mark.parametrize("action", ["dry-run", "import"])
async def test_upload_routes_return_422_for_more_than_50_000_rows(
    action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = {"staff_no_column": "Staff", "amount_column": "Amount", "header_row": 1}

    async def parse_and_reject(
        _current: Any, _run_id: uuid.UUID, content: bytes, filename: str
    ) -> service.ImportReport:
        service._read_rows(content, filename, profile)
        return _report()

    monkeypatch.setattr(
        service,
        "dry_run" if action == "dry-run" else "import_file",
        parse_and_reject,
    )
    payload = b"Staff,Amount\n" + b"S-1,1\n" * (service.MAX_PAYROLL_ROWS + 1)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/payroll/runs/{RUN_ID}/{action}?filename=payroll.csv",
            content=payload,
            headers={"Content-Type": "text/csv"},
        )
    assert response.status_code == 422
    assert "50,000 data rows" in response.json()["message"]


@pytest.mark.parametrize("content_length", [None, "1"])
async def test_missing_or_false_low_content_length_cannot_bypass_stream_limit(
    content_length: str | None,
) -> None:
    chunks = [b"a" * service.MAX_PAYROLL_UPLOAD_BYTES, b"b"]
    request, receive_calls = _make_request(chunks, content_length)

    with pytest.raises(PayloadTooLarge, match="5 MiB"):
        await payroll_api._read_limited_body(request, service.MAX_PAYROLL_UPLOAD_BYTES)
    assert receive_calls[0] == 2


async def test_oversized_content_length_is_rejected_without_reading_body() -> None:
    request, receive_calls = _make_request([b"small"], str(service.MAX_PAYROLL_UPLOAD_BYTES + 1))

    with pytest.raises(PayloadTooLarge, match="5 MiB"):
        await payroll_api._read_limited_body(request, service.MAX_PAYROLL_UPLOAD_BYTES)
    assert receive_calls[0] == 0


def test_csv_parser_accepts_50_000_rows_and_rejects_the_50_001st() -> None:
    profile = {"staff_no_column": "Staff", "amount_column": "Amount", "header_row": 1}
    under = b"Staff,Amount\n" + b"S-1,1\n" * service.MAX_PAYROLL_ROWS
    records, errors = service._read_rows(under, "payroll.csv", profile)
    assert len(records) == service.MAX_PAYROLL_ROWS
    assert errors == []

    over = under + b"S-2,2\n"
    with pytest.raises(UnprocessableEntity, match="50,000 data rows"):
        service._read_rows(over, "payroll.csv", profile)
