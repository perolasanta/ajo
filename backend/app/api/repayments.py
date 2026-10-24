"""Staff endpoints for recording and reversing repayment receipts."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.auth import current_session, require_csrf
from app.domains.auth.service import CurrentSession
from app.domains.repayments import service

router = APIRouter(tags=["repayments"], dependencies=[Depends(require_csrf)])


class RepaymentBody(BaseModel):
    member_id: uuid.UUID
    amount_kobo: int = Field(gt=0)
    source: str = Field(pattern="^(payroll|bank_transfer|cash)$")
    received_on: date
    reference: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ReversalBody(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/loans/{loan_id}/repayments")
async def post(
    loan_id: uuid.UUID,
    body: RepaymentBody,
    request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, str]:
    repayment_id = await service.post_repayment(
        current, loan_id=loan_id, ip=_ip(request), **body.model_dump()
    )
    return {"repayment_id": str(repayment_id)}


@router.post("/repayments/{repayment_id}/reverse")
async def reverse(
    repayment_id: uuid.UUID,
    body: ReversalBody,
    request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, str]:
    entry_id = await service.reverse_repayment(current, repayment_id, body.reason, _ip(request))
    return {"journal_entry_id": str(entry_id)}
