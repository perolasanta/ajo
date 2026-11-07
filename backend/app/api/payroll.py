"""Payroll reconciliation workspace endpoints."""

import base64
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.auth import current_session, require_csrf
from app.core.errors import PayloadTooLarge, UnsupportedMediaType
from app.domains.auth.service import CurrentSession
from app.domains.payroll import service

router = APIRouter(tags=["payroll"])


class ProfileBody(BaseModel):
    staff_no_column: str = Field(min_length=1, max_length=100)
    amount_column: str = Field(min_length=1, max_length=100)
    name_column: str | None = Field(default=None, max_length=100)
    header_row: int = Field(default=1, gt=0)


class RunBody(BaseModel):
    profile_id: uuid.UUID
    period_year: int = Field(ge=2000, le=9999)
    period_month: int = Field(ge=1, le=12)


class SignOffBody(BaseModel):
    bank_receipt_total_kobo: int = Field(ge=0)


class ResolutionNoteBody(BaseModel):
    note: str = Field(min_length=10, max_length=500)

    @field_validator("note", mode="before")
    @classmethod
    def trim_note(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class _PayrollResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ProfileResponse(_PayrollResponse):
    profile_id: str


class PayrollProfileSummaryResponse(_PayrollResponse):
    id: str
    name: str
    staff_no_column: str
    amount_column: str
    name_column: str | None
    created_at: datetime


class PayrollProfileListResponse(_PayrollResponse):
    items: list[PayrollProfileSummaryResponse]


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


class ResolutionNoteResponse(_PayrollResponse):
    resolved: bool


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
    created_by_name: str
    posted_by: str | None
    posted_by_name: str | None
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
    created_by_name: str
    imported_by: str | None
    imported_by_name: str | None
    posted_by: str | None
    posted_by_name: str | None
    created_at: datetime
    imported_at: datetime | None
    posted_at: datetime | None
    signed_off_at: datetime | None
    exceptions: list[PayrollRunExceptionResponse]


def _is_csv_filename(filename: str) -> bool:
    return filename.lower().endswith(".csv")


async def _read_limited_body(request: Request, max_bytes: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise PayloadTooLarge("Payroll upload exceeds the 5 MiB limit.")
        except ValueError:
            pass

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise PayloadTooLarge("Payroll upload exceeds the 5 MiB limit.")
        body.extend(chunk)
    return bytes(body)


async def _read_payroll_upload(request: Request) -> tuple[bytes, str]:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "text/csv":
        raise UnsupportedMediaType("Payroll uploads must use Content-Type text/csv.")
    filename = request.query_params.get("filename", "payroll.csv")
    if not _is_csv_filename(filename):
        raise UnsupportedMediaType(
            "Only .csv payroll files with Content-Type text/csv are supported."
        )
    return await _read_limited_body(request, service.MAX_PAYROLL_UPLOAD_BYTES), filename


@router.post(
    "/payroll/profiles", response_model=ProfileResponse, dependencies=[Depends(require_csrf)]
)
async def create_profile(
    body: ProfileBody, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, str]:
    profile_id = await service.create_profile(current, body.model_dump())
    return {"profile_id": str(profile_id)}


@router.get("/payroll/profiles", response_model=PayrollProfileListResponse)
async def list_profiles(
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, object]:
    return await service.list_profiles(current)


@router.post("/payroll/runs", response_model=RunResponse, dependencies=[Depends(require_csrf)])
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


@router.post(
    "/payroll/runs/{run_id}/dry-run",
    response_model=DryRunResponse,
    dependencies=[Depends(require_csrf)],
)
async def dry_run(
    run_id: uuid.UUID,
    request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, object]:
    content, filename = await _read_payroll_upload(request)
    report = await service.dry_run(current, run_id, content, filename)
    return {
        "file_sha256": report.file_sha256,
        "file_total_kobo": report.file_total_kobo,
        "rows": report.rows,
        "errors": report.errors,
    }


@router.post(
    "/payroll/runs/{run_id}/import",
    response_model=ImportResponse,
    dependencies=[Depends(require_csrf)],
)
async def import_file(
    run_id: uuid.UUID,
    request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, object]:
    content, filename = await _read_payroll_upload(request)
    report = await service.import_file(current, run_id, content, filename)
    return {
        "file_sha256": report.file_sha256,
        "file_total_kobo": report.file_total_kobo,
        "errors": report.errors,
    }


@router.post(
    "/payroll/runs/{run_id}/post", response_model=PostResponse, dependencies=[Depends(require_csrf)]
)
async def post_run(
    run_id: uuid.UUID, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, int]:
    return {"posted": await service.post_run(current, run_id)}


@router.post(
    "/payroll/runs/{run_id}/sign-off",
    response_model=SignOffResponse,
    dependencies=[Depends(require_csrf)],
)
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


@router.post(
    "/payroll/runs/{run_id}/items/{item_id}/resolution",
    response_model=ResolutionNoteResponse,
    dependencies=[Depends(require_csrf)],
)
async def add_resolution_note(
    run_id: uuid.UUID,
    item_id: uuid.UUID,
    body: ResolutionNoteBody,
    current: Annotated[CurrentSession, Depends(current_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> dict[str, bool]:
    await service.add_resolution_note(current, run_id, item_id, body.note, idempotency_key)
    return {"resolved": True}
