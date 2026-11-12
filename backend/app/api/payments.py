"""Staff payment-inbox API endpoints."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field

from app.api.auth import current_session, require_csrf
from app.domains.auth.service import CurrentSession
from app.domains.payments import service

router = APIRouter(tags=["payments"])


class PaymentRecordBody(BaseModel):
    member_id: uuid.UUID
    channel: Literal["cash", "bank_transfer"]
    purpose: Literal["savings", "shares", "dues", "loan_repayment"]
    loan_id: uuid.UUID | None = None
    amount_kobo: int = Field(gt=0)
    provider_reference: str | None = Field(default=None, max_length=160)
    received_on: date
    fee_kobo: int = Field(default=0, ge=0)
    fee_bearer: Literal["member", "cooperative"] = "member"
    reason: str | None = Field(default=None, max_length=500)


class DecisionBody(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class RejectionBody(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class CashDepositBody(BaseModel):
    slip_reference: str = Field(min_length=1, max_length=160)
    deposited_on: date
    payment_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _action_key(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Idempotency-Key header is required")
    return normalized


@router.post("/payments", dependencies=[Depends(require_csrf)])
async def record_payment(
    body: PaymentRecordBody,
    current: Annotated[CurrentSession, Depends(current_session)],
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> dict[str, object]:
    return await service.record_payment(
        current,
        member_id=body.member_id,
        channel=body.channel,
        purpose=body.purpose,
        loan_id=body.loan_id,
        amount_kobo=body.amount_kobo,
        provider="manual",
        provider_reference=body.provider_reference,
        received_on=body.received_on,
        fee_kobo=body.fee_kobo,
        fee_bearer=body.fee_bearer,
        reason=body.reason,
        idempotency_key=_action_key(idempotency_key),
        ip=_ip(request),
    )


@router.get("/payments")
async def list_payments(
    current: Annotated[CurrentSession, Depends(current_session)],
    status: Literal["pending", "received", "confirmed", "rejected", "banked"] | None = None,
    channel: Literal["cash", "bank_transfer", "paystack", "virtual_account"] | None = None,
    purpose: Literal["savings", "shares", "dues", "loan_repayment"] | None = None,
    member: uuid.UUID | None = None,
    mine: bool = False,
    action: Literal["needed"] | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict[str, object]:
    return await service.list_payments(
        current,
        status=status,
        channel=channel,
        purpose=purpose,
        member_id=member,
        mine=mine,
        action=action,
        page=page,
        page_size=page_size,
    )


@router.post("/payments/{payment_id}/confirm", dependencies=[Depends(require_csrf)])
async def confirm_payment(
    payment_id: uuid.UUID,
    body: DecisionBody,
    current: Annotated[CurrentSession, Depends(current_session)],
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> dict[str, object]:
    return await service.confirm_payment(
        current,
        payment_id,
        _action_key(idempotency_key),
        reason=body.reason,
        ip=_ip(request),
    )


@router.post("/payments/{payment_id}/reject", dependencies=[Depends(require_csrf)])
async def reject_payment(
    payment_id: uuid.UUID,
    body: RejectionBody,
    current: Annotated[CurrentSession, Depends(current_session)],
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> dict[str, object]:
    return await service.reject_payment(
        current,
        payment_id,
        body.reason,
        _action_key(idempotency_key),
        ip=_ip(request),
    )


@router.get("/payments/cash-awaiting-deposit")
async def list_cash_awaiting_deposit(
    current: Annotated[CurrentSession, Depends(current_session)],
) -> list[dict[str, object]]:
    return await service.list_cash_awaiting_deposit(current)


@router.get("/payments/cash-on-hand")
async def get_cash_on_hand_summary(
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, object]:
    return await service.cash_on_hand_summary(current)


@router.get("/payments/{payment_id}")
async def get_payment(
    payment_id: uuid.UUID,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, object]:
    return await service.get_payment(current, payment_id)


@router.post("/payments/cash-deposits", dependencies=[Depends(require_csrf)])
async def record_cash_deposit(
    body: CashDepositBody,
    current: Annotated[CurrentSession, Depends(current_session)],
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> dict[str, object]:
    return await service.record_cash_deposit(
        current,
        slip_reference=body.slip_reference,
        deposited_on=body.deposited_on,
        payment_ids=body.payment_ids,
        idempotency_key=_action_key(idempotency_key),
        ip=_ip(request),
    )
