"""Staff endpoints for manually authorized loan disbursements."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth import current_session, require_csrf
from app.domains.auth.service import CurrentSession
from app.domains.disbursements import service

router = APIRouter(tags=["disbursements"], dependencies=[Depends(require_csrf)])


class TransferBody(BaseModel):
    reference: str = Field(min_length=1, max_length=200)
    transfer_date: date


class FailureBody(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/loans/{loan_id}/disbursements")
async def prepare(
    loan_id: uuid.UUID, request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, str]:
    identifier = await service.prepare(current, loan_id, _ip(request))
    if identifier is None:
        raise HTTPException(status_code=409, detail="Destination account changed or is not verified")
    return {"disbursement_id": str(identifier)}


@router.post("/disbursements/{disbursement_id}/authorize")
async def authorize(
    disbursement_id: uuid.UUID, request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, bool]:
    ok = await service.authorize(current, disbursement_id, _ip(request))
    if not ok:
        raise HTTPException(status_code=409, detail="Destination account changed or is not verified")
    return {"authorized": True}


@router.post("/disbursements/{disbursement_id}/transfer")
async def record_transfer(
    disbursement_id: uuid.UUID, body: TransferBody, request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, bool]:
    ok = await service.record_transfer(current, disbursement_id, body.reference, body.transfer_date, _ip(request))
    if not ok:
        raise HTTPException(status_code=409, detail="Destination account changed or is not verified")
    return {"recorded": True}


@router.post("/disbursements/{disbursement_id}/confirm-paid")
async def confirm_paid(
    disbursement_id: uuid.UUID, request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, str]:
    entry_id = await service.confirm_paid(current, disbursement_id, _ip(request))
    if entry_id is None:
        raise HTTPException(status_code=409, detail="Destination account changed or is not verified")
    return {"journal_entry_id": str(entry_id)}


@router.post("/disbursements/{disbursement_id}/failed")
async def fail_transfer(
    disbursement_id: uuid.UUID, body: FailureBody, request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, bool]:
    await service.fail_transfer(current, disbursement_id, body.reason, _ip(request))
    return {"failed": True}


@router.post("/disbursements/{disbursement_id}/reverse")
async def reverse_paid(
    disbursement_id: uuid.UUID, body: FailureBody, request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, str]:
    entry_id = await service.reverse_paid(current, disbursement_id, body.reason, _ip(request))
    return {"journal_entry_id": str(entry_id)}
