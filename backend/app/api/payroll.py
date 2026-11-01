"""Payroll reconciliation workspace endpoints."""

import base64
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import current_session, require_csrf
from app.domains.auth.service import CurrentSession
from app.domains.payroll import service

router = APIRouter(tags=["payroll"], dependencies=[Depends(require_csrf)])


class ProfileBody(BaseModel):
    staff_no_column: str = Field(min_length=1, max_length=100)
    amount_column: str = Field(min_length=1, max_length=100)
    name_column: str | None = Field(default=None, max_length=100)
    header_row: int = Field(default=1, gt=0)


class RunBody(BaseModel):
    profile_id: uuid.UUID
    period_year: int = Field(ge=2000, le=9999)
    period_month: int = Field(ge=1, le=12)


class FileBody(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1)


class SignOffBody(BaseModel):
    bank_receipt_total_kobo: int = Field(ge=0)


class _PayrollResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ProfileResponse(_PayrollResponse):
    profile_id: str


class RunResponse(_PayrollResponse):
    run_id: str


class DryRunRowResponse(_PayrollResponse):
    row: int
    staff_no_raw: str
    member_id: str | None
    actual_kobo: int
    classification: str


class PayrollImportErrorResponse(_PayrollResponse):
    row: int
    error: str


class DryRunResponse(_PayrollResponse):
    file_sha256: str
    file_total_kobo: int
    rows: list[DryRunRowResponse]
    errors: list[PayrollImportErrorResponse]


class ImportResponse(_PayrollResponse):
    file_sha256: str
    file_total_kobo: int
    errors: list[PayrollImportErrorResponse]


class PostResponse(_PayrollResponse):
    posted: int


class SignOffResponse(_PayrollResponse):
    csv_base64: str
    json_base64: str


class PayrollRunCountsResponse(_PayrollResponse):
    matched: int
    short: int
    over: int
    missing: int
    unknown_member: int
    duplicate: int


class PayrollRunSummaryResponse(_PayrollResponse):
    run_id: str
    period_year: int
    period_month: int
    status: str
    expected_total_kobo: int
    file_total_kobo: int | None
    counts: PayrollRunCountsResponse
    created_by: str
    posted_by: str | None
    created_at: datetime
    imported_at: datetime | None
    posted_at: datetime | None
    signed_off_at: datetime | None


class PayrollRunListResponse(_PayrollResponse):
    items: list[PayrollRunSummaryResponse]
    page: int
    page_size: int
    total: int


class PayrollRunExceptionResponse(_PayrollResponse):
    staff_no_raw: str
    classification: str
    actual_kobo: int | None
    resolution_note: str | None


class PayrollComponentTotalsResponse(_PayrollResponse):
    loan: int
    savings: int
    dues: int


class PayrollRunDetailResponse(_PayrollResponse):
    run_id: str
    period_year: int
    period_month: int
    status: str
    expected_total_kobo: int
    file_total_kobo: int | None
    bank_receipt_total_kobo: int | None
    variance_kobo: int | None
    counts: PayrollRunCountsResponse
    component_totals_kobo: PayrollComponentTotalsResponse
    created_by: str
    imported_by: str | None
    posted_by: str | None
    created_at: datetime
    imported_at: datetime | None
    posted_at: datetime | None
    signed_off_at: datetime | None
    exceptions: list[PayrollRunExceptionResponse]


def _content(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise ValueError("Invalid file encoding") from exc


@router.post("/payroll/profiles", response_model=ProfileResponse)
async def create_profile(
    body: ProfileBody, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, str]:
    profile_id = await service.create_profile(current, body.model_dump())
    return {"profile_id": str(profile_id)}


@router.post("/payroll/runs", response_model=RunResponse)
async def create_run(
    body: RunBody, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, str]:
    run_id = await service.create_run(current, body.profile_id, body.period_year, body.period_month)
    return {"run_id": str(run_id)}


@router.get("/payroll/runs", response_model=PayrollRunListResponse)
async def list_runs(
    current: Annotated[CurrentSession, Depends(current_session)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
) -> dict[str, object]:
    return await service.list_runs(current, page, page_size)


@router.get("/payroll/runs/{run_id}", response_model=PayrollRunDetailResponse)
async def get_run(
    run_id: uuid.UUID, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, object]:
    return await service.get_run(current, run_id)


@router.get("/payroll/runs/{run_id}/expected.csv", response_class=Response, response_model=None)
async def export_expected(
    run_id: uuid.UUID, current: Annotated[CurrentSession, Depends(current_session)]
) -> Response:
    return Response(
        await service.export_expected(current, run_id),
        media_type="text/csv",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/payroll/runs/{run_id}/dry-run", response_model=DryRunResponse)
async def dry_run(
    run_id: uuid.UUID, body: FileBody, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, object]:
    report = await service.dry_run(current, run_id, _content(body.content_base64), body.filename)
    return {
        "file_sha256": report.file_sha256,
        "file_total_kobo": report.file_total_kobo,
        "rows": report.rows,
        "errors": report.errors,
    }


@router.post("/payroll/runs/{run_id}/import", response_model=ImportResponse)
async def import_file(
    run_id: uuid.UUID, body: FileBody, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, object]:
    report = await service.import_file(
        current, run_id, _content(body.content_base64), body.filename
    )
    return {
        "file_sha256": report.file_sha256,
        "file_total_kobo": report.file_total_kobo,
        "errors": report.errors,
    }


@router.post("/payroll/runs/{run_id}/post", response_model=PostResponse)
async def post_run(
    run_id: uuid.UUID, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, int]:
    return {"posted": await service.post_run(current, run_id)}


@router.post("/payroll/runs/{run_id}/sign-off", response_model=SignOffResponse)
async def sign_off(
    run_id: uuid.UUID,
    body: SignOffBody,
    current: Annotated[CurrentSession, Depends(current_session)],
    response: Response,
) -> dict[str, str]:
    csv_report, json_report = await service.sign_off(current, run_id, body.bank_receipt_total_kobo)
    response.headers["Cache-Control"] = "no-store"
    return {
        "csv_base64": base64.b64encode(csv_report).decode(),
        "json_base64": base64.b64encode(json_report).decode(),
    }
