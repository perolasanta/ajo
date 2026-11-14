"""Bank statement CSV import preview and import endpoints."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app.api.auth import current_session, require_csrf
from app.domains.auth.service import CurrentSession
from app.domains.bank_statements import service

router = APIRouter(tags=["bank-statements"])


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
