"""Role-checked cooperative policy endpoints."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.auth import current_session, require_csrf
from app.domains.auth.service import CurrentSession
from app.domains.policies import service

router = APIRouter(tags=["policies"])


class PolicyBody(BaseModel):
    effective_from: date
    salary_basis: str = Field(pattern="^(net|gross)$")
    deduction_cap_bps: int = Field(ge=0, le=10000)
    max_active_loans: int = Field(ge=1)
    guarantors_required: int = Field(ge=0)
    min_savings_kobo: int = Field(ge=0)
    loan_to_savings_multiple_x100: int = Field(ge=0)
    interest_method: str = Field(pattern="^(reducing_balance|flat)$")
    confirmation_expiry_minutes: int = Field(default=1440, gt=0)
    otp_validity_seconds: int = Field(default=300, gt=0)
    settings: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=1000)


class ReasonBody(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class SupersedePolicyBody(ReasonBody):
    effective_to: date


class ModuleBody(BaseModel):
    enabled: bool


class LoanProductBody(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=160)
    interest_method: str = Field(pattern="^(reducing_balance|flat)$")
    annual_rate_bps: int = Field(ge=0, le=100000)
    min_amount_kobo: int = Field(ge=0)
    max_amount_kobo: int = Field(ge=0)
    min_tenor_months: int = Field(ge=1)
    max_tenor_months: int = Field(ge=1)
    fee_bps: int = Field(default=0, ge=0, le=100000)
    guarantors_required: int = Field(ge=0)
    supersedes_id: uuid.UUID | None = None
    effective_on: date
    reason: str = Field(min_length=1, max_length=1000)


@router.post("/policies", dependencies=[Depends(require_csrf)])
async def propose_policy(
    body: PolicyBody,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, str]:
    values = body.model_dump(exclude={"reason"})
    policy_id = await service.propose_policy(current, values, body.reason)
    return {"policy_version_id": str(policy_id)}


@router.post("/policies/{policy_id}/approve", dependencies=[Depends(require_csrf)])
async def approve_policy(
    policy_id: uuid.UUID,
    body: ReasonBody,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, bool]:
    await service.approve_policy(current, policy_id, body.reason)
    return {"approved": True}


@router.post("/policies/{policy_id}/supersede", dependencies=[Depends(require_csrf)])
async def supersede_policy(
    policy_id: uuid.UUID,
    body: SupersedePolicyBody,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, bool]:
    await service.supersede_policy(current, policy_id, body.effective_to, body.reason)
    return {"superseded": True}


@router.get("/policies/effective")
async def effective_policy(
    current: Annotated[CurrentSession, Depends(current_session)],
    on_date: Annotated[date, Query()],
) -> dict[str, Any]:
    if not set(current.roles).intersection({"officer", "admin"}):
        from app.core.errors import Forbidden

        raise Forbidden("This role cannot view cooperative policy")
    return await service.get_effective_policy(current.cooperative_id, on_date)


@router.put("/modules/{module}", dependencies=[Depends(require_csrf)])
async def update_module(
    module: str,
    body: ModuleBody,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, bool]:
    await service.set_module_enabled(current, module, body.enabled)
    return {"updated": True}


@router.post("/loan-products", dependencies=[Depends(require_csrf)])
async def propose_loan_product(
    body: LoanProductBody,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, str]:
    values = body.model_dump(exclude={"effective_on", "reason"})
    product_id = await service.create_loan_product(current, values, body.effective_on, body.reason)
    return {"loan_product_id": str(product_id)}


@router.post("/loan-products/{product_id}/approve", dependencies=[Depends(require_csrf)])
async def approve_loan_product(
    product_id: uuid.UUID,
    body: ReasonBody,
    current: Annotated[CurrentSession, Depends(current_session)],
    on_date: Annotated[date, Query()],
) -> dict[str, bool]:
    await service.approve_loan_product(current, product_id, on_date, body.reason)
    return {"approved": True}
