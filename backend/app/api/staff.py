"""Admin-only staff directory and role-change proposal reads."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.auth import current_session
from app.domains.auth import reads
from app.domains.auth.reads import ProposalStatus
from app.domains.auth.service import CurrentSession

router = APIRouter(tags=["staff"])


class StaffResponse(BaseModel):
    user_id: str
    email: str
    status: Literal["active", "disabled"]
    roles: list[Literal["officer", "committee", "treasurer", "admin", "auditor"]]
    created_at: datetime


class StaffPageResponse(BaseModel):
    items: list[StaffResponse]
    page: int
    page_size: int
    total: int


class RoleChangeProposalResponse(BaseModel):
    proposal_id: str
    target_user_id: str
    target_email: str | None
    proposer_user_id: str
    proposer_email: str | None
    role: Literal["officer", "committee", "treasurer", "admin", "auditor"]
    action: Literal["add", "remove"]
    reason: str
    status: Literal["pending", "approved", "rejected", "cancelled", "expired"]
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    decided_by: str | None


class RoleChangeProposalPageResponse(BaseModel):
    items: list[RoleChangeProposalResponse]
    page: int
    page_size: int
    total: int


@router.get("/staff", response_model=StaffPageResponse)
async def list_staff(
    current: Annotated[CurrentSession, Depends(current_session)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict[str, object]:
    return await reads.list_staff(current, page=page, page_size=page_size)


@router.get("/role-change-proposals", response_model=RoleChangeProposalPageResponse)
async def list_role_change_proposals(
    current: Annotated[CurrentSession, Depends(current_session)],
    status: Annotated[ProposalStatus, Query()] = "pending",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict[str, object]:
    return await reads.list_role_change_proposals(
        current, status=status, page=page, page_size=page_size
    )
