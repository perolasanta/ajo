"""Loan application submission and committee decision endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import current_session, require_csrf
from app.domains.auth.service import CurrentSession
from app.domains.loans import reads, service

router = APIRouter(tags=["loans"])


class ConsentStatus(BaseModel):
    kind: str
    status: str


class GuarantorSummary(BaseModel):
    member_no: str
    full_name: str


class ScheduleItem(BaseModel):
    installment_no: int
    due_at: datetime
    principal_kobo: int
    interest_kobo: int
    payment_kobo: int
    balance_kobo: int
    dues_kobo: int
    savings_kobo: int


class DestinationBankAccount(BaseModel):
    bank_code: str
    last4: str


class LoanApplicationListBase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    member_no: str
    full_name: str
    product_name: str
    principal_kobo: int
    tenor_months: int
    annual_rate_bps: int
    status: str
    submitted_at: datetime
    decision: str | None
    decision_reason: str | None
    decided_at: datetime | None
    consent_statuses: list[ConsentStatus]
    guarantor_count: int


class LoanApplicationListAdminOfficerResponse(LoanApplicationListBase):
    pass


class LoanApplicationListTreasurerResponse(LoanApplicationListBase):
    pass


class LoanApplicationListCommitteeResponse(LoanApplicationListBase):
    pass


class LoanApplicationListAuditorResponse(LoanApplicationListBase):
    pass


class LoanApplicationDetailBase(LoanApplicationListBase):
    terms_snapshot: dict[str, object] | None
    status_timeline: list[dict[str, object]]
    guarantors: list[GuarantorSummary]
    schedule: list[ScheduleItem]


class LoanApplicationDetailAdminOfficerResponse(LoanApplicationDetailBase):
    destination_bank_account: DestinationBankAccount | None = None


class LoanApplicationDetailTreasurerResponse(LoanApplicationDetailBase):
    destination_bank_account: DestinationBankAccount | None = None


class LoanApplicationDetailCommitteeResponse(LoanApplicationDetailBase):
    pass


class LoanApplicationDetailAuditorResponse(LoanApplicationDetailBase):
    pass


class LoanApplicationListResponse(BaseModel):
    items: list[LoanApplicationListBase]
    page: int
    page_size: int
    total: int


class LoanProductResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    interest_method: str
    annual_rate_bps: int
    min_amount_kobo: int
    max_amount_kobo: int
    min_tenor_months: int
    max_tenor_months: int
    fee_bps: int
    guarantors_required: int


def _loan_read_role(current: CurrentSession) -> str:
    return reads.read_role(current)


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


@router.get("/loan-applications", response_model=LoanApplicationListResponse)
async def list_applications(
    current: Annotated[CurrentSession, Depends(current_session)],
    status: Annotated[str | None, Query(max_length=50)] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> LoanApplicationListResponse:
    _loan_read_role(current)
    result = await reads.list_applications(
        current, status=status, search=search, page=page, page_size=page_size
    )
    return LoanApplicationListResponse.model_validate(result)


@router.get(
    "/loan-applications/{application_id}",
    response_model=(
        LoanApplicationDetailAdminOfficerResponse
        | LoanApplicationDetailTreasurerResponse
        | LoanApplicationDetailCommitteeResponse
        | LoanApplicationDetailAuditorResponse
    ),
)
async def application_detail(
    application_id: uuid.UUID,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> (
    LoanApplicationDetailAdminOfficerResponse
    | LoanApplicationDetailTreasurerResponse
    | LoanApplicationDetailCommitteeResponse
    | LoanApplicationDetailAuditorResponse
):
    role = _loan_read_role(current)
    result = await reads.get_application(current, application_id)
    model = {
        "admin": LoanApplicationDetailAdminOfficerResponse,
        "officer": LoanApplicationDetailAdminOfficerResponse,
        "treasurer": LoanApplicationDetailTreasurerResponse,
        "committee": LoanApplicationDetailCommitteeResponse,
        "auditor": LoanApplicationDetailAuditorResponse,
    }[role]
    return cast(
        LoanApplicationDetailAdminOfficerResponse
        | LoanApplicationDetailTreasurerResponse
        | LoanApplicationDetailCommitteeResponse
        | LoanApplicationDetailAuditorResponse,
        model.model_validate(result),
    )


@router.get("/loan-products", response_model=list[LoanProductResponse])
async def loan_products(
    current: Annotated[CurrentSession, Depends(current_session)],
) -> list[LoanProductResponse]:
    _loan_read_role(current)
    return [LoanProductResponse.model_validate(row) for row in await reads.list_products(current)]


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
