"""Share purchase request and maker-checker endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.auth import current_session, require_csrf
from app.core.db import transaction
from app.core.errors import Forbidden, NotFound
from app.domains.auth.service import CurrentSession
from app.domains.notifications import eligibility
from app.domains.share_purchases import service

router = APIRouter(tags=["share purchases"])


class SharePurchaseRequestBody(BaseModel):
    member_id: uuid.UUID
    shares_count: int = Field(gt=0)


class SharePurchaseDecisionBody(BaseModel):
    note: str = Field(min_length=1, max_length=500)


class SharePurchaseItem(BaseModel):
    id: uuid.UUID
    member_id: uuid.UUID
    member_name: str
    shares_count: int
    unit_price_naira: int
    amount_kobo: int
    status: str
    requested_by: uuid.UUID
    requester_name: str
    decided_by: uuid.UUID | None = None
    decision_note: str | None = None
    created_at: datetime
    decided_at: datetime | None = None
    cancelled_at: datetime | None = None
    journal_entry_id: uuid.UUID | None = None


class SharePurchasePage(BaseModel):
    items: list[SharePurchaseItem]
    page: int
    page_size: int
    total: int


def _can_read(current: CurrentSession) -> bool:
    return bool(set(current.roles).intersection({"admin", "officer", "treasurer", "auditor"}))


@router.get("/share-purchases", response_model=SharePurchasePage)
async def list_share_purchases(
    current: Annotated[CurrentSession, Depends(current_session)],
    status: str | None = Query(default=None, pattern="^(pending|approved|rejected|cancelled)$"),
    mine: str = Query(default="all", pattern="^(all|needed)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    if not _can_read(current):
        raise Forbidden("This role cannot read share purchases")
    action_ids: tuple[uuid.UUID, ...] | None = None
    async with transaction(current.cooperative_id) as session:
        if mine == "needed":
            if "treasurer" not in current.roles:
                action_ids = ()
            else:
                items = await eligibility.get_action_items_for_user(
                    session, current.cooperative_id, current.user_id, set(current.roles)
                )
                action_ids = items.get("share_purchases", ())
        rows = await session.execute(
            text("""WITH filtered AS (
              SELECT r.id,r.member_id,coalesce(nullif(trim(m.full_name),''),'Unnamed user') AS member_name,
                     r.shares_count,r.unit_price_naira,r.amount_kobo,r.status,r.requested_by,
                     coalesce(nullif(trim(u.full_name),''),'Unnamed user') AS requester_name,
                     r.decided_by,r.decision_note,r.created_at,r.decided_at,r.cancelled_at,r.journal_entry_id
                FROM share_purchase_requests r
                JOIN members m ON m.cooperative_id=r.cooperative_id AND m.id=r.member_id
                JOIN users u ON u.cooperative_id=r.cooperative_id AND u.id=r.requested_by
               WHERE r.cooperative_id=:cid
                 AND (CAST(:status AS text) IS NULL OR r.status=CAST(:status AS text))
                 AND (CAST(:action_ids AS uuid[]) IS NULL OR r.id=ANY(CAST(:action_ids AS uuid[])))
            ) SELECT *,count(*) OVER() AS total FROM filtered
              ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset"""),
            {
                "cid": current.cooperative_id,
                "status": status,
                "action_ids": list(action_ids) if action_ids is not None else None,
                "limit": page_size,
                "offset": (page - 1) * page_size,
            },
        )
        records = [dict(row) for row in rows.mappings().all()]
    return {
        "items": records,
        "page": page,
        "page_size": page_size,
        "total": int(records[0]["total"]) if records else 0,
    }


@router.get("/share-purchases/{request_id}", response_model=SharePurchaseItem)
async def get_share_purchase(
    request_id: uuid.UUID, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, Any]:
    if not _can_read(current):
        raise Forbidden("This role cannot read share purchases")
    async with transaction(current.cooperative_id) as session:
        row = await session.execute(
            text("""SELECT r.id,r.member_id,coalesce(nullif(trim(m.full_name),''),'Unnamed user') AS member_name,
                     r.shares_count,r.unit_price_naira,r.amount_kobo,r.status,r.requested_by,
                     coalesce(nullif(trim(u.full_name),''),'Unnamed user') AS requester_name,
                     r.decided_by,r.decision_note,r.created_at,r.decided_at,r.cancelled_at,r.journal_entry_id
                FROM share_purchase_requests r
                JOIN members m ON m.cooperative_id=r.cooperative_id AND m.id=r.member_id
                JOIN users u ON u.cooperative_id=r.cooperative_id AND u.id=r.requested_by
               WHERE r.cooperative_id=:cid AND r.id=:id"""),
            {"cid": current.cooperative_id, "id": request_id},
        )
        record = row.mappings().one_or_none()
    if record is None:
        raise NotFound("Share purchase request not found")
    return dict(record)


@router.post("/share-purchases", dependencies=[Depends(require_csrf)])
async def request_share_purchase(
    body: SharePurchaseRequestBody, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, str]:
    request_id = await service.request_purchase(current, body.member_id, body.shares_count)
    return {"share_purchase_request_id": str(request_id)}


@router.post("/share-purchases/{request_id}/approve", dependencies=[Depends(require_csrf)])
async def approve_share_purchase(
    request_id: uuid.UUID, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, str]:
    entry_id = await service.approve_purchase(current, request_id)
    return {"journal_entry_id": str(entry_id)}


@router.post("/share-purchases/{request_id}/reject", dependencies=[Depends(require_csrf)])
async def reject_share_purchase(
    request_id: uuid.UUID,
    body: SharePurchaseDecisionBody,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, str]:
    await service.reject_purchase(current, request_id, body.note)
    return {"status": "rejected"}


@router.post("/share-purchases/{request_id}/cancel", dependencies=[Depends(require_csrf)])
async def cancel_share_purchase(
    request_id: uuid.UUID, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, str]:
    await service.cancel_purchase(current, request_id)
    return {"status": "cancelled"}
