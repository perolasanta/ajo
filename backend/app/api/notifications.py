from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.api.auth import current_session
from app.domains.auth.service import CurrentSession
from app.domains.notifications import reads

router = APIRouter(tags=["notifications"])


class PayrollActionCounts(BaseModel):
    awaiting_posting: int | None = None
    awaiting_sign_off: int | None = None


class DisbursementActionCounts(BaseModel):
    awaiting_authorization: int | None = None
    awaiting_confirmation: int | None = None
    ready_to_record: int | None = None


class RoleChangeActionCounts(BaseModel):
    awaiting_approval: int | None = None


class LoanActionCounts(BaseModel):
    awaiting_decision: int | None = None
    disputed_or_paused: int | None = None


class NotificationSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payroll: PayrollActionCounts | None = None
    disbursements: DisbursementActionCounts | None = None
    role_changes: RoleChangeActionCounts | None = None
    loans: LoanActionCounts | None = None


@router.get(
    "/notifications/summary",
    response_model=NotificationSummaryResponse,
    response_model_exclude_none=True,
)
async def get_notification_summary(
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, dict[str, int]]:
    return await reads.get_action_counts(current)
