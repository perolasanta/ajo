"""Staff endpoints for manually authorized loan disbursements."""

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import current_session, require_csrf
from app.domains.auth.service import CurrentSession
from app.domains.disbursements import reads, service

router = APIRouter(tags=["disbursements"], dependencies=[Depends(require_csrf)])


class TransferBody(BaseModel):
    reference: str = Field(min_length=1, max_length=200)
    transfer_date: date


class FailureBody(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class DisbursementReadBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    loan_id: str
    application_id: str
    member_no: str
    full_name: str
    amount_kobo: int
    status: str
    prepared_by: str
    authorized_by: str | None
    transfer_reference: str | None
    transfer_date: date | None
    confirmed_by: str | None
    failure_reason: str | None
    reversal_reason: str | None
    created_at: datetime
    authorized_at: datetime | None
    transfer_recorded_at: datetime | None
    paid_at: datetime | None
    failed_at: datetime | None
    reversed_at: datetime | None


class DisbursementAdminTreasurerResponse(DisbursementReadBase):
    bank_code: str
    last4: str


class DisbursementAuditorResponse(DisbursementReadBase):
    pass


class DisbursementAdminTreasurerPageResponse(BaseModel):
    items: list[DisbursementAdminTreasurerResponse]
    page: int
    page_size: int
    total: int


class DisbursementAuditorPageResponse(BaseModel):
    items: list[DisbursementAuditorResponse]
    page: int
    page_size: int
    total: int


class ReadyLoanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loan_id: str
    application_id: str
    member_no: str
    full_name: str
    amount_kobo: int
    status: str


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


_BASE_FIELDS = {
    "id",
    "loan_id",
    "application_id",
    "member_no",
    "full_name",
    "amount_kobo",
    "status",
    "prepared_by",
    "authorized_by",
    "transfer_reference",
    "transfer_date",
    "confirmed_by",
    "failure_reason",
    "reversal_reason",
    "created_at",
    "authorized_at",
    "transfer_recorded_at",
    "paid_at",
    "failed_at",
    "reversed_at",
}


def _disbursement_projection(row: dict[str, object], role: str) -> dict[str, object]:
    fields = _BASE_FIELDS | ({"bank_code", "last4"} if role in reads.BANK_ROLES else set())
    payload = {key: row.get(key) for key in fields}
    schema = (
        DisbursementAuditorResponse if role == "auditor" else DisbursementAdminTreasurerResponse
    )
    return schema.model_validate(payload).model_dump(mode="json")


def _list_projection(page: dict[str, object], role: str) -> dict[str, object]:
    items = page["items"]
    assert isinstance(items, list)
    schema = (
        DisbursementAuditorPageResponse
        if role == "auditor"
        else DisbursementAdminTreasurerPageResponse
    )
    return schema.model_validate(
        {**page, "items": [_disbursement_projection(item, role) for item in items]}
    ).model_dump(mode="json")


@router.get("/disbursements")
async def list_disbursements(
    current: Annotated[CurrentSession, Depends(current_session)],
    status: str | None = Query(default=None, max_length=40),
    search: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
) -> dict[str, object]:
    role = reads.read_role(current)
    result = await reads.list_disbursements(
        current, status=status, search=search, page=page, page_size=page_size
    )
    return _list_projection(result, role)


@router.get("/disbursements/ready-loans")
async def ready_loans(
    current: Annotated[CurrentSession, Depends(current_session)],
    search: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
) -> dict[str, object]:
    reads.read_role(current)
    result = await reads.list_ready_loans(current, search=search, page=page, page_size=page_size)
    return {
        **result,
        "items": [
            ReadyLoanResponse.model_validate(item).model_dump(mode="json")
            for item in result["items"]
        ],
    }


@router.get("/disbursements/{disbursement_id}")
async def disbursement_detail(
    disbursement_id: uuid.UUID,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, object]:
    role = reads.read_role(current)
    row = await reads.get_disbursement(current, disbursement_id)
    return _disbursement_projection(row, role)


@router.post("/loans/{loan_id}/disbursements")
async def prepare(
    loan_id: uuid.UUID,
    request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, str]:
    identifier = await service.prepare(current, loan_id, _ip(request))
    if identifier is None:
        raise HTTPException(
            status_code=409, detail="Destination account changed or is not verified"
        )
    return {"disbursement_id": str(identifier)}


@router.post("/disbursements/{disbursement_id}/authorize")
async def authorize(
    disbursement_id: uuid.UUID,
    request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, bool]:
    ok = await service.authorize(current, disbursement_id, _ip(request))
    if not ok:
        raise HTTPException(
            status_code=409, detail="Destination account changed or is not verified"
        )
    return {"authorized": True}


@router.post("/disbursements/{disbursement_id}/transfer")
async def record_transfer(
    disbursement_id: uuid.UUID,
    body: TransferBody,
    request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, bool]:
    ok = await service.record_transfer(
        current, disbursement_id, body.reference, body.transfer_date, _ip(request)
    )
    if not ok:
        raise HTTPException(
            status_code=409, detail="Destination account changed or is not verified"
        )
    return {"recorded": True}


@router.post("/disbursements/{disbursement_id}/confirm-paid")
async def confirm_paid(
    disbursement_id: uuid.UUID,
    request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, str]:
    entry_id = await service.confirm_paid(current, disbursement_id, _ip(request))
    if entry_id is None:
        raise HTTPException(
            status_code=409, detail="Destination account changed or is not verified"
        )
    return {"journal_entry_id": str(entry_id)}


@router.post("/disbursements/{disbursement_id}/failed")
async def fail_transfer(
    disbursement_id: uuid.UUID,
    body: FailureBody,
    request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, bool]:
    await service.fail_transfer(current, disbursement_id, body.reason, _ip(request))
    return {"failed": True}


@router.post("/disbursements/{disbursement_id}/reverse")
async def reverse_paid(
    disbursement_id: uuid.UUID,
    body: FailureBody,
    request: Request,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, str]:
    entry_id = await service.reverse_paid(current, disbursement_id, body.reason, _ip(request))
    return {"journal_entry_id": str(entry_id)}
