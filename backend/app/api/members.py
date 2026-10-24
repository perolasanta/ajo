"""Officer member, contribution and withdrawal endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.auth import current_session, require_csrf
from app.core.db import transaction
from app.core.errors import Forbidden, NotFound
from app.domains.auth.service import CurrentSession
from app.domains.members import service

router = APIRouter(tags=["members"])


def _staff(current: CurrentSession) -> None:
    if not set(current.roles).intersection(
        {"officer", "committee", "treasurer", "admin", "auditor"}
    ):
        raise Forbidden("Staff role required")


class ContributionBody(BaseModel):
    member_id: uuid.UUID
    kind: str = Field(pattern="^(savings|shares|dues)$")
    amount_kobo: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=200)


class WithdrawalBody(BaseModel):
    member_id: uuid.UUID
    amount_kobo: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ImportBody(BaseModel):
    csv_text: str
    confirm: bool = False


@router.get("/members")
async def list_members(
    current: Annotated[CurrentSession, Depends(current_session)],
) -> list[dict[str, Any]]:
    _staff(current)
    async with transaction(current.cooperative_id) as session:
        result = await session.execute(
            text("""SELECT id,member_no,full_name,phone_e164,email,status,joined_on
          FROM members WHERE cooperative_id=:cid ORDER BY member_no"""),
            {"cid": current.cooperative_id},
        )
        return [dict(row) for row in result.mappings()]


@router.get("/members/{member_id}")
async def member_detail(
    member_id: uuid.UUID, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, Any]:
    _staff(current)
    async with transaction(current.cooperative_id) as session:
        result = await session.execute(
            text("""SELECT id,member_no,full_name,phone_e164,email,staff_no,employer,status,joined_on
          FROM members WHERE cooperative_id=:cid AND id=:id"""),
            {"cid": current.cooperative_id, "id": member_id},
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise NotFound("Member not found")
        member = dict(row)
        banks = await session.execute(
            text("""SELECT bank_code,last4,account_name,verified_at,status
          FROM bank_accounts WHERE cooperative_id=:cid AND member_id=:id"""),
            {"cid": current.cooperative_id, "id": member_id},
        )
        member["bank_accounts"] = [dict(bank) for bank in banks.mappings()]
    member["balances"] = await service.member_balances(current.cooperative_id, member_id)
    return member


@router.post("/members/contributions", dependencies=[Depends(require_csrf)])
async def contribute(
    body: ContributionBody, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, str]:
    _staff(current)
    entry_id = await service.record_contribution(
        current, body.member_id, body.kind, body.amount_kobo, body.idempotency_key
    )
    return {"journal_entry_id": str(entry_id)}


@router.post("/members/withdrawals", dependencies=[Depends(require_csrf)])
async def withdraw(
    body: WithdrawalBody, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, str]:
    _staff(current)
    request_id = await service.request_withdrawal(
        current, body.member_id, body.amount_kobo, body.idempotency_key
    )
    return {"withdrawal_request_id": str(request_id)}


@router.post("/members/withdrawals/{request_id}/approve", dependencies=[Depends(require_csrf)])
async def approve_withdrawal(
    request_id: uuid.UUID, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, str]:
    entry_id = await service.approve_withdrawal(current, request_id)
    return {"journal_entry_id": str(entry_id)}


@router.post("/members/import", dependencies=[Depends(require_csrf)])
async def import_members(
    body: ImportBody, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, Any]:
    _staff(current)
    return await service.import_members(current, body.csv_text, body.confirm)
