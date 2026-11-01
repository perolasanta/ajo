"""Regression tests for payroll access controls and response contracts."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.api import payroll as payroll_api
from app.core.errors import NotFound, install_error_handlers
from app.domains.auth.service import CurrentSession
from app.domains.payroll import service

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
RUN_ID = "11111111-1111-1111-1111-111111111111"
PROFILE_ID = "22222222-2222-2222-2222-222222222222"


def _session_for(role: str) -> CurrentSession:
    return CurrentSession(
        uuid.uuid4(),
        "payroll-contract-test",
        uuid.uuid4(),
        uuid.uuid4(),
        "staff@example.test",
        (role,),
        bytes(32),
    )


@asynccontextmanager
async def _unexpected_transaction(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("authorization must reject before opening a database transaction")
    yield None


JSON_RESPONSE_CONTRACTS: dict[tuple[str, str], tuple[set[str], dict[str, Any]]] = {
    (
        "GET",
        "/payroll/runs",
    ): (
        {"items", "page", "page_size", "total"},
        {
            "items": [
                {
                    "run_id": RUN_ID,
                    "period_year": 2026,
                    "period_month": 10,
                    "status": "imported",
                    "expected_total_kobo": 1200,
                    "file_total_kobo": 1190,
                    "counts": {
                        "matched": 1,
                        "short": 0,
                        "over": 0,
                        "missing": 0,
                        "unknown_member": 0,
                        "duplicate": 0,
                    },
                    "created_by": PROFILE_ID,
                    "posted_by": None,
                    "created_at": "2026-10-01T10:00:00Z",
                    "imported_at": "2026-10-02T10:00:00Z",
                    "posted_at": None,
                    "signed_off_at": None,
                }
            ],
            "page": 1,
            "page_size": 10,
            "total": 1,
        },
    ),
    (
        "GET",
        "/payroll/runs/{run_id}",
    ): (
        {
            "run_id",
            "period_year",
            "period_month",
            "status",
            "expected_total_kobo",
            "file_total_kobo",
            "bank_receipt_total_kobo",
            "variance_kobo",
            "counts",
            "component_totals_kobo",
            "created_by",
            "imported_by",
            "posted_by",
            "created_at",
            "imported_at",
            "posted_at",
            "signed_off_at",
            "exceptions",
        },
        {
            "run_id": RUN_ID,
            "period_year": 2026,
            "period_month": 10,
            "status": "posted",
            "expected_total_kobo": 1200,
            "file_total_kobo": 1190,
            "bank_receipt_total_kobo": 1190,
            "variance_kobo": 0,
            "counts": {
                "matched": 1,
                "short": 1,
                "over": 0,
                "missing": 0,
                "unknown_member": 0,
                "duplicate": 0,
            },
            "component_totals_kobo": {"loan": 1000, "savings": 100, "dues": 100},
            "created_by": PROFILE_ID,
            "imported_by": PROFILE_ID,
            "posted_by": PROFILE_ID,
            "created_at": "2026-10-01T10:00:00Z",
            "imported_at": "2026-10-02T10:00:00Z",
            "posted_at": "2026-10-03T10:00:00Z",
            "signed_off_at": None,
            "exceptions": [
                {
                    "staff_no_raw": "S-2",
                    "classification": "short",
                    "actual_kobo": 90,
                    "resolution_note": "Review deduction",
                }
            ],
        },
    ),
    ("POST", "/payroll/profiles"): ({"profile_id"}, {"profile_id": PROFILE_ID}),
    ("POST", "/payroll/runs"): ({"run_id"}, {"run_id": RUN_ID}),
    (
        "POST",
        "/payroll/runs/{run_id}/dry-run",
    ): (
        {"file_sha256", "file_total_kobo", "rows", "errors"},
        {
            "file_sha256": "a" * 64,
            "file_total_kobo": 12,
            "rows": [
                {
                    "row": 2,
                    "staff_no_raw": "S-1",
                    "member_id": None,
                    "actual_kobo": 12,
                    "classification": "pending",
                    "future_row_field": "must be hidden",
                }
            ],
            "errors": [{"row": 3, "error": "invalid amount", "future_error_field": True}],
        },
    ),
    (
        "POST",
        "/payroll/runs/{run_id}/import",
    ): (
        {"file_sha256", "file_total_kobo", "errors"},
        {
            "file_sha256": "b" * 64,
            "file_total_kobo": 12,
            "errors": [{"row": 3, "error": "invalid amount", "future_error_field": True}],
        },
    ),
    ("POST", "/payroll/runs/{run_id}/post"): ({"posted"}, {"posted": 1}),
    (
        "POST",
        "/payroll/runs/{run_id}/sign-off",
    ): ({"csv_base64", "json_base64"}, {"csv_base64": "YQ==", "json_base64": "e30="}),
}


def _route(method: str, path: str) -> Any:
    return next(
        route
        for route in payroll_api.router.routes
        if route.path == path and method in route.methods
    )


def test_payroll_json_endpoints_have_explicit_narrow_response_schemas() -> None:
    for (method, path), (expected_keys, sample) in JSON_RESPONSE_CONTRACTS.items():
        model = _route(method, path).response_model
        assert isinstance(model, type) and issubclass(model, BaseModel), (
            f"{method} {path} must declare a named response model"
        )
        assert set(model.model_fields) == expected_keys
        response = model.model_validate({**sample, "future_field": "must be hidden"})
        assert set(response.model_dump()) == expected_keys
        assert "future_field" not in response.model_dump()


def test_nested_dry_run_response_models_hide_new_row_and_error_fields() -> None:
    model = _route("POST", "/payroll/runs/{run_id}/dry-run").response_model
    assert isinstance(model, type) and issubclass(model, BaseModel)
    sample = JSON_RESPONSE_CONTRACTS[("POST", "/payroll/runs/{run_id}/dry-run")][1]
    response = model.model_validate(sample)
    assert set(response.rows[0].model_dump()) == {
        "row",
        "staff_no_raw",
        "member_id",
        "actual_kobo",
        "classification",
    }
    assert set(response.errors[0].model_dump()) == {"row", "error"}


def test_payroll_run_list_has_exact_safe_nested_keys() -> None:
    model = _route("GET", "/payroll/runs").response_model
    assert isinstance(model, type) and issubclass(model, BaseModel)
    sample = JSON_RESPONSE_CONTRACTS[("GET", "/payroll/runs")][1]
    response = model.model_validate({**sample, "future_field": "hidden"})
    item = response.model_dump()["items"][0]
    assert set(item) == {
        "run_id",
        "period_year",
        "period_month",
        "status",
        "expected_total_kobo",
        "file_total_kobo",
        "counts",
        "created_by",
        "posted_by",
        "created_at",
        "imported_at",
        "posted_at",
        "signed_off_at",
    }
    assert set(item["counts"]) == {
        "matched",
        "short",
        "over",
        "missing",
        "unknown_member",
        "duplicate",
    }
    assert not {"salary", "phone", "email", "bank", "member_id"} & set(item)


def test_payroll_run_detail_has_exact_safe_persisted_fields() -> None:
    model = _route("GET", "/payroll/runs/{run_id}").response_model
    assert isinstance(model, type) and issubclass(model, BaseModel)
    sample = JSON_RESPONSE_CONTRACTS[("GET", "/payroll/runs/{run_id}")][1]
    response = model.model_validate({**sample, "future_field": "hidden"})
    payload = response.model_dump()
    assert set(payload) == JSON_RESPONSE_CONTRACTS[("GET", "/payroll/runs/{run_id}")][0]
    assert set(payload["counts"]) == set(sample["counts"])
    assert set(payload["component_totals_kobo"]) == {"loan", "savings", "dues"}
    assert set(payload["exceptions"][0]) == {
        "staff_no_raw",
        "classification",
        "actual_kobo",
        "resolution_note",
    }
    assert "row" not in payload["exceptions"][0]
    assert not {"salary", "phone", "email", "bank", "member_id"} & set(payload)


@pytest.mark.parametrize("role", ["officer", "treasurer", "admin"])
async def test_payroll_run_list_response_contract_is_identical_for_read_roles(
    role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = JSON_RESPONSE_CONTRACTS[("GET", "/payroll/runs")][1]
    item = sample["items"][0]
    database_row = {**item, "id": item["run_id"], **item["counts"]}
    database_row.pop("run_id")
    database_row.pop("counts")
    current = _session_for(role)

    class _Rows:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self.rows = rows

        def mappings(self) -> _Rows:
            return self

        def all(self) -> list[dict[str, Any]]:
            return self.rows

    class _Session:
        async def scalar(self, statement: Any, params: dict[str, Any]) -> int:
            assert params["cid"] == current.cooperative_id
            return 1

        async def execute(self, statement: Any, params: dict[str, Any]) -> _Rows:
            assert params["cid"] == current.cooperative_id
            return _Rows([database_row])

    @asynccontextmanager
    async def _transaction(cooperative_id: uuid.UUID) -> Any:
        assert cooperative_id == current.cooperative_id
        yield _Session()

    monkeypatch.setattr(payroll_api.service, "transaction", _transaction)
    result = await payroll_api.list_runs(current=current, page=1, page_size=10)
    model = _route("GET", "/payroll/runs").response_model
    assert isinstance(model, type) and issubclass(model, BaseModel)
    response = model.model_validate(result)
    assert set(response.model_dump()) == {"items", "page", "page_size", "total"}
    assert set(response.model_dump()["items"][0]) == set(sample["items"][0])
    assert set(response.model_dump()["items"][0]["counts"]) == set(item["counts"])


@pytest.mark.parametrize("role", ["officer", "treasurer", "admin"])
async def test_payroll_run_detail_response_contract_is_identical_for_read_roles(
    role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = JSON_RESPONSE_CONTRACTS[("GET", "/payroll/runs/{run_id}")][1]
    run = {
        key: value
        for key, value in sample.items()
        if key not in {"counts", "component_totals_kobo", "exceptions"}
    }
    run["id"] = run.pop("run_id")
    totals = {
        **sample["counts"],
        "loan_kobo": sample["component_totals_kobo"]["loan"],
        "savings_kobo": sample["component_totals_kobo"]["savings"],
        "dues_kobo": sample["component_totals_kobo"]["dues"],
    }
    current = _session_for(role)

    class _Result:
        def __init__(
            self, row: dict[str, Any] | None = None, rows: list[dict[str, Any]] | None = None
        ):
            self.row = row
            self.rows = rows or []

        def mappings(self) -> _Result:
            return self

        def one_or_none(self) -> dict[str, Any] | None:
            return self.row

        def one(self) -> dict[str, Any]:
            assert self.row is not None
            return self.row

        def all(self) -> list[dict[str, Any]]:
            return self.rows

    class _Session:
        async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            assert params["cid"] == current.cooperative_id
            query = str(statement)
            if "FROM payroll_runs" in query:
                return _Result(row=run)
            if "count(*) FILTER" in query:
                return _Result(row=totals)
            return _Result(rows=sample["exceptions"])

    @asynccontextmanager
    async def _transaction(cooperative_id: uuid.UUID) -> Any:
        assert cooperative_id == current.cooperative_id
        yield _Session()

    monkeypatch.setattr(payroll_api.service, "transaction", _transaction)
    result = await payroll_api.get_run(run_id=uuid.UUID(RUN_ID), current=current)
    model = _route("GET", "/payroll/runs/{run_id}").response_model
    assert isinstance(model, type) and issubclass(model, BaseModel)
    response = model.model_validate(result).model_dump()
    assert set(response) == set(sample)
    assert set(response["counts"]) == set(sample["counts"])
    assert set(response["component_totals_kobo"]) == set(sample["component_totals_kobo"])
    assert set(response["exceptions"][0]) == set(sample["exceptions"][0])


def test_expected_csv_route_is_explicitly_a_csv_not_a_json_object() -> None:
    route = _route("GET", "/payroll/runs/{run_id}/expected.csv")
    assert route.response_model is None


@pytest.mark.parametrize("trigger", ["=", "+", "-", "@", "\t", "\r"])
def test_csv_sanitization_is_text_only_and_leaves_negative_numbers_unchanged(
    trigger: str,
) -> None:
    sanitize = getattr(
        service, "_sanitize_csv_row", lambda values, text_fields, max_lengths=None: values
    )
    text_fields = {"staff_no_raw", "name", "classification", "resolution_note"}
    row: dict[str, Any] = {field: f"{trigger}formula" for field in text_fields}
    row["actual_kobo"] = -123

    sanitized = sanitize(row, text_fields, {"resolution_note": 500})

    for field in text_fields:
        expected = f"'{trigger}formula"
        if field == "resolution_note":
            expected = expected[:500]
        assert sanitized[field] == expected
    assert sanitized["actual_kobo"] == -123
    assert isinstance(sanitized["actual_kobo"], int)


def test_csv_sanitization_caps_exported_resolution_note_at_500_characters() -> None:
    sanitize = getattr(
        service, "_sanitize_csv_row", lambda values, text_fields, max_lengths=None: values
    )
    sanitized = sanitize(
        {"resolution_note": "x" * 501},
        {"resolution_note"},
        {"resolution_note": 500},
    )
    assert len(sanitized["resolution_note"]) == 500


def test_payroll_decision_1b_records_final_gates_and_resolution_note_policy() -> None:
    decisions = (ROOT / "docs" / "decisions.md").read_text()
    assert "## 1b. Payroll access and reporting" in decisions
    for path in (
        "POST /payroll/runs",
        "GET /payroll/runs",
        "GET /payroll/runs/{run_id}",
        "GET /payroll/runs/{run_id}/expected.csv",
        "POST /payroll/runs/{run_id}/dry-run",
        "POST /payroll/runs/{run_id}/import",
        "POST /payroll/runs/{run_id}/post",
        "POST /payroll/profiles",
        "POST /payroll/runs/{run_id}/sign-off",
    ):
        assert path in decisions
    assert "read-only sign-off report for auditors" in decisions.lower()
    assert "no application write path" in decisions.lower()
    assert "500 characters" in decisions
    assert "personal and bank data" in decisions.lower()
    assert "Dry-run results are not persisted" in decisions
    assert "raw uploaded rows that were never imported" in decisions
    assert "does not include original row numbers" in decisions


@pytest.mark.parametrize(
    ("role", "method", "path", "payload"),
    [
        (
            "admin",
            "POST",
            "/payroll/runs",
            {"profile_id": PROFILE_ID, "period_year": 2099, "period_month": 1},
        ),
        ("admin", "GET", f"/payroll/runs/{RUN_ID}/expected.csv", None),
        (
            "admin",
            "POST",
            f"/payroll/runs/{RUN_ID}/dry-run",
            {"filename": "payroll.csv", "content_base64": "QQ=="},
        ),
        (
            "admin",
            "POST",
            f"/payroll/runs/{RUN_ID}/import",
            {"filename": "payroll.csv", "content_base64": "QQ=="},
        ),
        ("admin", "POST", f"/payroll/runs/{RUN_ID}/post", None),
        *[
            (role, method, path, payload)
            for role in ("committee", "auditor", "member")
            for method, path, payload in (
                (
                    "POST",
                    "/payroll/profiles",
                    {"staff_no_column": "Staff", "amount_column": "Amount"},
                ),
                (
                    "POST",
                    "/payroll/runs",
                    {"profile_id": PROFILE_ID, "period_year": 2099, "period_month": 1},
                ),
                ("GET", f"/payroll/runs/{RUN_ID}/expected.csv", None),
                ("GET", "/payroll/runs", None),
                ("GET", f"/payroll/runs/{RUN_ID}", None),
                (
                    "POST",
                    f"/payroll/runs/{RUN_ID}/dry-run",
                    {"filename": "payroll.csv", "content_base64": "QQ=="},
                ),
                (
                    "POST",
                    f"/payroll/runs/{RUN_ID}/import",
                    {"filename": "payroll.csv", "content_base64": "QQ=="},
                ),
                ("POST", f"/payroll/runs/{RUN_ID}/post", None),
                ("POST", f"/payroll/runs/{RUN_ID}/sign-off", {"bank_receipt_total_kobo": 0}),
            )
        ],
    ],
)
async def test_roles_without_payroll_access_receive_403(
    role: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(payroll_api.service, "transaction", _unexpected_transaction)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(payroll_api.router, prefix="/api/v1")
    app.dependency_overrides[payroll_api.current_session] = lambda: _session_for(role)
    app.dependency_overrides[payroll_api.require_csrf] = lambda: None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, f"/api/v1{path}", json=payload)
    assert response.status_code == 403


@pytest.mark.parametrize(("result_status",), [("csv",), ("json",)])
async def test_expected_csv_and_signoff_responses_set_cache_control_no_store(
    monkeypatch: pytest.MonkeyPatch, result_status: str
) -> None:
    response = Response()
    current = _session_for("officer" if result_status == "csv" else "admin")
    if result_status == "csv":
        monkeypatch.setattr(
            payroll_api.service, "export_expected", _async_return(b"Staff,Amount\r\n")
        )
        result = await payroll_api.export_expected(run_id=uuid.UUID(RUN_ID), current=current)
        assert isinstance(result, Response)
        assert result.headers.get("cache-control") == "no-store"
    else:
        monkeypatch.setattr(payroll_api.service, "sign_off", _async_return((b"csv", b"{}")))
        result = await payroll_api.sign_off(
            run_id=uuid.UUID(RUN_ID),
            body=payroll_api.SignOffBody(bank_receipt_total_kobo=0),
            current=current,
            response=response,
        )
        assert set(result) == {"csv_base64", "json_base64"}
        assert response.headers.get("cache-control") == "no-store"


async def test_payroll_run_list_is_scoped_to_the_current_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _session_for("officer")
    calls: list[tuple[str, dict[str, Any]]] = []

    class _Rows:
        def mappings(self) -> _Rows:
            return self

        def all(self) -> list[dict[str, Any]]:
            return []

    class _Session:
        async def scalar(self, statement: Any, params: dict[str, Any]) -> int:
            calls.append((str(statement), params))
            return 0

        async def execute(self, statement: Any, params: dict[str, Any]) -> _Rows:
            calls.append((str(statement), params))
            return _Rows()

    @asynccontextmanager
    async def _tenant_transaction(cooperative_id: uuid.UUID) -> Any:
        assert cooperative_id == current.cooperative_id
        yield _Session()

    monkeypatch.setattr(service, "transaction", _tenant_transaction)
    await service.list_runs(current, page=2, page_size=10)

    assert len(calls) == 2
    for statement, params in calls:
        assert "cooperative_id=:cid" in statement
        assert params["cid"] == current.cooperative_id
    assert calls[1][1]["limit"] == 10
    assert calls[1][1]["offset"] == 10


async def test_payroll_run_detail_is_scoped_to_the_current_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _session_for("officer")
    calls: list[tuple[str, dict[str, Any]]] = []

    class _Result:
        def mappings(self) -> _Result:
            return self

        def one_or_none(self) -> None:
            return None

    class _Session:
        async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            calls.append((str(statement), params))
            return _Result()

    @asynccontextmanager
    async def _tenant_transaction(cooperative_id: uuid.UUID) -> Any:
        assert cooperative_id == current.cooperative_id
        yield _Session()

    monkeypatch.setattr(service, "transaction", _tenant_transaction)
    with pytest.raises(NotFound, match="Payroll run not found"):
        await service.get_run(current, uuid.UUID(RUN_ID))

    assert len(calls) == 1
    statement, params = calls[0]
    assert "cooperative_id=:cid" in statement
    assert params["cid"] == current.cooperative_id


def _async_return(value: Any) -> Any:
    async def _return(*args: Any, **kwargs: Any) -> Any:
        return value

    return _return
