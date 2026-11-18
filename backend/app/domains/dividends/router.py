"""API endpoints for dividend management."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.auth import current_session
from app.domains.auth.service import CurrentSession
from app.domains.dividends import service as dividend_service

router = APIRouter(tags=["dividends"])


class PrepareDividendRunRequest(BaseModel):
    """Request to prepare a dividend run."""

    record_date: date = Field(
        description="Effective date for share snapshot (date cutoff for membership)"
    )
    declared_by_meeting_id: str = Field(
        description="UUID of the meeting that declared the dividend"
    )
    pool_kobo: int = Field(
        description="Total dividend pool in integer kobo", gt=0
    )


class DividendLineResponse(BaseModel):
    """Allocated dividend line item."""

    line_id: str
    member_id: str
    shares_held: int
    amount_kobo: int
    created_at: str


class DividendRunResponse(BaseModel):
    """Dividend run details."""

    run_id: str
    record_date: str
    declared_by_meeting_id: str
    pool_kobo: int
    prepared_by: str
    prepared_at: str
    approved_by: str | None = None
    approved_at: str | None = None
    rejected_by: str | None = None
    rejected_at: str | None = None
    rejection_reason: str | None = None
    posted_by: str | None = None
    posted_at: str | None = None
    status: str
    created_at: str
    updated_at: str
    items: list[DividendLineResponse] | None = None


class DividendRunListResponse(BaseModel):
    """Paginated list of dividend runs."""

    items: list[DividendRunResponse]
    total: int
    page: int
    page_size: int


class RejectDividendRunRequest(BaseModel):
    """Request to reject a dividend run."""

    reason: str = Field(description="Reason for rejection")


@router.post(
    "/dividends/runs",
    response_model=DividendRunResponse,
    summary="Prepare a dividend run",
    description="Prepare a new dividend run. Only treasurer and admin can prepare runs.",
)
async def prepare_dividend_run(
    request: PrepareDividendRunRequest,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> DividendRunResponse:
    """Prepare a new dividend run.

    Creates a dividend run with share snapshot at the specified record_date.
    Allocates dividend pool proportionally to member shares.

    Requires: treasurer or admin role
    """
    result = await dividend_service.prepare_dividend_run(
        current,
        record_date=request.record_date,
        declared_by_meeting_id=uuid.UUID(request.declared_by_meeting_id),
        pool_kobo=request.pool_kobo,
    )
    return DividendRunResponse(**result)


@router.get(
    "/dividends/runs",
    response_model=DividendRunListResponse,
    summary="List dividend runs",
    description="List all dividend runs for the cooperative.",
)
async def list_dividend_runs(
    current: Annotated[CurrentSession, Depends(current_session)],
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> DividendRunListResponse:
    """List dividend runs for the cooperative.

    Returns paginated list of dividend runs with basic details (no line items).

    Requires: treasurer, admin, or officer role
    """
    result = await dividend_service.list_dividend_runs(
        current, page=page, page_size=page_size
    )
    return DividendRunListResponse(**result)


@router.get(
    "/dividends/runs/{run_id}",
    response_model=DividendRunResponse,
    summary="Get dividend run details",
    description="Get a single dividend run with all allocated line items.",
)
async def get_dividend_run(
    run_id: str,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> DividendRunResponse:
    """Get a single dividend run with allocated lines.

    Requires: treasurer, admin, or officer role
    """
    result = await dividend_service.get_dividend_run(current, uuid.UUID(run_id))
    return DividendRunResponse(**result)


@router.post(
    "/dividends/runs/{run_id}/approve",
    response_model=DividendRunResponse,
    summary="Approve a dividend run",
    description="Approve a prepared dividend run for posting.",
)
async def approve_dividend_run(
    run_id: str,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> DividendRunResponse:
    """Approve a prepared dividend run.

    Changes status from 'unprocessed' to 'approved'.

    Requires: admin role
    """
    result = await dividend_service.approve_dividend_run(current, uuid.UUID(run_id))
    return DividendRunResponse(**result)


@router.post(
    "/dividends/runs/{run_id}/reject",
    response_model=DividendRunResponse,
    summary="Reject a dividend run",
    description="Reject a prepared dividend run.",
)
async def reject_dividend_run(
    run_id: str,
    request: RejectDividendRunRequest,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> DividendRunResponse:
    """Reject a prepared dividend run.

    Changes status from 'unprocessed' to 'rejected'.

    Requires: admin role
    """
    result = await dividend_service.reject_dividend_run(
        current, uuid.UUID(run_id), request.reason
    )
    return DividendRunResponse(**result)


@router.post(
    "/dividends/runs/{run_id}/cancel",
    response_model=DividendRunResponse,
    summary="Cancel a dividend run",
    description="Cancel a prepared (unprocessed) dividend run.",
)
async def cancel_dividend_run(
    run_id: str,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> DividendRunResponse:
    """Cancel a prepared dividend run.

    Changes status from 'unprocessed' to 'cancelled'.
    Can only be done by the preparer or admin.

    Requires: treasurer or admin role
    """
    result = await dividend_service.cancel_dividend_run(current, uuid.UUID(run_id))
    return DividendRunResponse(**result)


@router.post(
    "/dividends/runs/{run_id}/post",
    response_model=DividendRunResponse,
    summary="Post approved dividends to ledger",
    description="Post an approved dividend run to the ledger.",
)
async def post_dividend_run(
    run_id: str,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> DividendRunResponse:
    """Post an approved dividend run to the ledger.

    Creates balanced journal entries:
    - Debit: dividends_declared (total pool)
    - Credit: member_savings (per-member allocations)

    Changes status from 'approved' to 'posted'.

    Requires: admin or treasurer role
    """
    result = await dividend_service.post_dividend_run(current, uuid.UUID(run_id))
    return DividendRunResponse(**result)
