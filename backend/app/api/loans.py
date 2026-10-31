"""Loan application submission and committee decision endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.auth import current_session, require_csrf
from app.domains.auth.service import CurrentSession
from app.domains.loans import service

router = APIRouter(tags=["loans"])


class GuaranteeBody(BaseModel):
    member_id: uuid.UUID
    amount_kobo: int = Field(gt=0)


class ApplicationBody(BaseModel):
    member_id: uuid.UUID
    product_id: uuid.UUID
    principal_kobo: int = Field(gt=0)
    tenor_months: int = Field(gt=0)
    beneficiary_member_id: uuid.UUID | None = None
    destination_bank_account_id: uuid.UUID | None = None
    guarantors: list[GuaranteeBody] = Field(default_factory=list)


class DecisionBody(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    reason: str = Field(min_length=1, max_length=1000)


class TermChangeBody(BaseModel):
    principal_kobo: int | None = Field(default=None, gt=0)
    tenor_months: int | None = Field(default=None, gt=0)
    annual_rate_bps: int | None = Field(default=None, ge=0)
    beneficiary_member_id: uuid.UUID | None = None
    destination_bank_account_id: uuid.UUID | None = None
    reason: str = Field(min_length=1, max_length=1000)


@router.post("/loan-applications", dependencies=[Depends(require_csrf)])
async def submit(
    body: ApplicationBody,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, str]:
    application_id = await service.submit_application(
        current,
        member_id=body.member_id,
        product_id=body.product_id,
        principal_kobo=body.principal_kobo,
        tenor_months=body.tenor_months,
        beneficiary_member_id=body.beneficiary_member_id,
        destination_bank_account_id=body.destination_bank_account_id,
        guarantors=[(item.member_id, item.amount_kobo) for item in body.guarantors],
    )
    return {"application_id": str(application_id)}


@router.post("/loan-applications/{application_id}/decision", dependencies=[Depends(require_csrf)])
async def decide(
    application_id: uuid.UUID,
    body: DecisionBody,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, str | None]:
    loan_id = await service.decide_application(current, application_id, body.decision, body.reason)
    return {"loan_id": str(loan_id) if loan_id else None, "decision": body.decision}


@router.patch("/loan-applications/{application_id}/terms", dependencies=[Depends(require_csrf)])
async def change_terms(
    application_id: uuid.UUID,
    body: TermChangeBody,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, bool]:
    changes = body.model_dump(exclude={"reason"}, exclude_unset=True)
    await service.change_application_terms(current, application_id, changes, body.reason)
    return {"updated": True}


@router.post(
    "/loan-applications/{application_id}/consents/{consent_id}/reissue",
    dependencies=[Depends(require_csrf)],
    status_code=201,
)
async def reissue_consent(
    application_id: uuid.UUID,
    consent_id: uuid.UUID,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, str]:
    new_consent_id = await service.reissue_consent(current, application_id, consent_id)
    return {"consent_id": str(new_consent_id)}
