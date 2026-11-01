"""Read-only audit log endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from app.api.auth import current_session
from app.domains.audit import reads
from app.domains.audit.service import verify_audit_chain
from app.domains.auth.service import CurrentSession

router = APIRouter(tags=["audit"])


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    timestamp: datetime
    actor_id: str | None
    actor_role: str | None
    action: str
    entity_type: str
    entity_id: str | None
    safe_summary: dict[str, str | int | bool | None]


class AuditEventPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AuditEventResponse]
    page: int
    page_size: int
    total: int


class AuditVerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    entries_checked: int
    first_broken_position: int | None


def _page_projection(page: dict[str, object]) -> dict[str, object]:
    return AuditEventPageResponse.model_validate(
        {**page, "items": [AuditEventResponse.model_validate(item) for item in page["items"]]}
    ).model_dump(mode="json")


@router.get("/audit-events", response_model=AuditEventPageResponse)
async def list_audit_events(
    current: Annotated[CurrentSession, Depends(current_session)],
    from_timestamp: Annotated[datetime | None, Query(alias="from")] = None,
    to_timestamp: Annotated[datetime | None, Query(alias="to")] = None,
    actor_id: uuid.UUID | None = None,
    action: str | None = Query(default=None, max_length=100),
    entity_type: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict[str, object]:
    reads.read_role(current)
    page_data = await reads.list_audit_events(
        current,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        page=page,
        page_size=page_size,
    )
    return _page_projection(page_data)


@router.get("/audit-events/verify", response_model=AuditVerificationResponse)
async def verify_audit_events(
    current: Annotated[CurrentSession, Depends(current_session)],
) -> AuditVerificationResponse:
    reads.read_role(current)
    result = await verify_audit_chain(current.cooperative_id)
    return AuditVerificationResponse(
        ok=result.valid,
        entries_checked=result.entries_checked,
        first_broken_position=None if result.valid else result.entries_checked,
    )


def _audit_projection(row: dict[str, object]) -> dict[str, object]:
    """Compatibility wrapper used by the contract tests and API projection."""
    return reads.audit_projection(row)
