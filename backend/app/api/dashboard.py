"""Read-only dashboard summary endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.api.auth import current_session
from app.domains.auth.service import CurrentSession
from app.domains.dashboard import reads

router = APIRouter(tags=["dashboard"])


class DashboardSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loans: dict[str, int]
    disbursements: dict[str, int]


@router.get("/dashboard/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    current: Annotated[CurrentSession, Depends(current_session)],
) -> DashboardSummaryResponse:
    summary = await reads.get_dashboard_summary(current)
    return DashboardSummaryResponse.model_validate(summary)
