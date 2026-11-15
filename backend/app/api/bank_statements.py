"""Bank statement CSV import preview and import endpoints."""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from app.api.auth import current_session, require_csrf
from app.domains.auth.service import CurrentSession
from app.domains.bank_statements import service

router = APIRouter(tags=["bank-statements"])


class StatementAcceptBody(BaseModel):
    payment_id: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=255)


class StatementIgnoreBody(BaseModel):
    note: str = Field(min_length=1, max_length=500)


def _read_mapping(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {
            str(key).strip(): str(val).strip()
            for key, val in value.items()
            if val not in (None, "")
        }
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return {}
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return {
                str(key).strip(): str(val).strip()
                for key, val in parsed.items()
                if val not in (None, "")
            }
    return {}


async def _read_upload(request: Request) -> tuple[bytes, str, dict[str, str]]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    filename = request.query_params.get("filename", "statement.csv")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        uploaded = form.get("file")
        if uploaded is None:
            raise ValueError("Missing uploaded file")
        payload = uploaded.file.read() if hasattr(uploaded, "file") else b""
        filename = getattr(uploaded, "filename", filename) or filename
        mapping = _read_mapping(form.get("mapping") or form.get("column_map"))
        return payload, filename, mapping
    if content_type == "application/json":
        body = await request.json()
        payload = body.get("content", b"")
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        return (
            payload,
            body.get("filename", filename),
            _read_mapping(body.get("column_map") or body.get("mapping")),
        )
    payload = await request.body()
    return payload, filename, {}


@router.post("/bank-statements/preview", dependencies=[Depends(require_csrf)])
async def preview_bank_statement(
    current: Annotated[CurrentSession, Depends(current_session)],
    request: Request,
) -> dict[str, Any]:
    payload, filename, mapping = await _read_upload(request)
    return await service.preview_statement_import(
        current,
        content=payload,
        filename=filename,
        column_map=mapping,
    )


@router.post("/bank-statements/import", dependencies=[Depends(require_csrf)])
async def import_bank_statement(
    current: Annotated[CurrentSession, Depends(current_session)],
    request: Request,
) -> dict[str, Any]:
    payload, filename, mapping = await _read_upload(request)
    return await service.import_statement_file(
        current,
        content=payload,
        filename=filename,
        column_map=mapping,
    )


@router.post("/bank-statements/match", dependencies=[Depends(require_csrf)])
async def match_bank_statements(
    current: Annotated[CurrentSession, Depends(current_session)],
    window_days: int = Query(default=5, ge=0, le=30),
) -> dict[str, Any]:
    return await service.match_statement_lines(current, window_days=window_days)


@router.get("/bank-statements/lines")
async def list_bank_statement_lines(
    current: Annotated[CurrentSession, Depends(current_session)],
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    return await service.list_statement_lines(
        current, status=status, page=page, page_size=page_size
    )


@router.get("/bank-statements/lines/{line_id}")
async def get_bank_statement_line(
    current: Annotated[CurrentSession, Depends(current_session)],
    line_id: str,
) -> dict[str, Any]:
    import uuid

    return await service.get_statement_line(current, uuid.UUID(line_id))


@router.post("/payments/bank-statements/preview", dependencies=[Depends(require_csrf)])
async def preview_bank_statement_alias(
    current: Annotated[CurrentSession, Depends(current_session)],
    request: Request,
) -> dict[str, Any]:
    return await preview_bank_statement(current, request)


@router.post("/payments/bank-statements/import", dependencies=[Depends(require_csrf)])
async def import_bank_statement_alias(
    current: Annotated[CurrentSession, Depends(current_session)],
    request: Request,
) -> dict[str, Any]:
    return await import_bank_statement(current, request)


@router.post("/payments/bank-statements/match", dependencies=[Depends(require_csrf)])
async def match_bank_statements_alias(
    current: Annotated[CurrentSession, Depends(current_session)],
    window_days: int = Query(default=5, ge=0, le=30),
) -> dict[str, Any]:
    return await service.match_statement_lines(current, window_days=window_days)


@router.get("/payments/bank-statements/lines")
async def list_bank_statement_lines_alias(
    current: Annotated[CurrentSession, Depends(current_session)],
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    return await service.list_statement_lines(
        current, status=status, page=page, page_size=page_size
    )


@router.get("/bank-statements")
async def list_bank_statements(
    current: Annotated[CurrentSession, Depends(current_session)],
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    return await service.list_statement_lines(
        current, status=status, page=page, page_size=page_size
    )


@router.get("/payments/bank-statements/lines/{line_id}")
async def get_bank_statement_line_alias(
    current: Annotated[CurrentSession, Depends(current_session)],
    line_id: str,
) -> dict[str, Any]:
    return await service.get_statement_line(current, uuid.UUID(line_id))


@router.get("/bank-statements/{line_id}")
async def get_bank_statement_by_id(
    line_id: str,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, Any]:
    return await service.get_statement_line(current, uuid.UUID(line_id))


@router.post("/bank-statements/match", dependencies=[Depends(require_csrf)])
async def match_bank_statement_lines(
    current: Annotated[CurrentSession, Depends(current_session)],
    window_days: int = Query(default=5, ge=0, le=30),
) -> dict[str, Any]:
    return await service.auto_match_statement_lines(current, window_days=window_days)


@router.post("/bank-statements/{line_id}/accept", dependencies=[Depends(require_csrf)])
async def accept_bank_statement_line(
    line_id: str,
    body: StatementAcceptBody,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, Any]:
    return await service.accept_statement_line(
        current,
        uuid.UUID(line_id),
        idempotency_key=body.idempotency_key,
        payment_id=uuid.UUID(body.payment_id) if body.payment_id else None,
    )


@router.post("/bank-statements/{line_id}/ignore", dependencies=[Depends(require_csrf)])
async def ignore_bank_statement_line(
    line_id: str,
    body: StatementIgnoreBody,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, Any]:
    return await service.ignore_statement_line(current, uuid.UUID(line_id), note=body.note)


@router.post("/bank-statements/{line_id}/undo", dependencies=[Depends(require_csrf)])
async def undo_bank_statement_decision(
    line_id: str,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, Any]:
    return await service.undo_statement_decision(current, uuid.UUID(line_id))
