"""Member, contribution and withdrawal endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.api.auth import current_session, require_csrf
from app.core.db import transaction
from app.core.errors import Forbidden, NotFound
from app.domains.auth.service import CurrentSession
from app.domains.members import service

router = APIRouter(tags=["members"])


class _MemberResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MemberListAdminOfficerResponse(_MemberResponse):
    id: uuid.UUID
    member_no: str
    full_name: str
    phone_e164: str | None
    email: str | None
    staff_no: str | None
    status: str
    joined_on: date | None


class MemberListTreasurerResponse(_MemberResponse):
    id: uuid.UUID
    member_no: str
    full_name: str


class MemberListCommitteeResponse(_MemberResponse):
    id: uuid.UUID
    member_no: str
    full_name: str


class MemberListAuditorResponse(_MemberResponse):
    id: uuid.UUID
    member_no: str
    full_name: str
    status: str
    joined_on: date | None


MemberListResponse = (
    MemberListAdminOfficerResponse
    | MemberListTreasurerResponse
    | MemberListCommitteeResponse
    | MemberListAuditorResponse
)


class MemberBalancesResponse(_MemberResponse):
    savings_kobo: int
    shares_kobo: int
    dues_paid_kobo: int
    locked_kobo: int
    available_kobo: int


class MemberBankAccountAdminOfficerResponse(_MemberResponse):
    id: uuid.UUID
    bank_code: str
    last4: str
    account_name: str
    verified_at: datetime | None
    status: str


class MemberBankAccountTreasurerResponse(_MemberResponse):
    id: uuid.UUID
    bank_code: str
    last4: str


class MemberDetailAdminOfficerResponse(MemberListAdminOfficerResponse):
    employer: str | None
    bank_accounts: list[MemberBankAccountAdminOfficerResponse]
    balances: MemberBalancesResponse


class MemberDetailTreasurerResponse(MemberListTreasurerResponse):
    bank_accounts: list[MemberBankAccountTreasurerResponse]
    balances: MemberBalancesResponse


class MemberDetailCommitteeResponse(MemberListCommitteeResponse):
    pass


class MemberDetailAuditorResponse(MemberListAuditorResponse):
    balances: MemberBalancesResponse


MemberDetailResponse = (
    MemberDetailAdminOfficerResponse
    | MemberDetailTreasurerResponse
    | MemberDetailCommitteeResponse
    | MemberDetailAuditorResponse
)

FULL_MEMBER_LIST_QUERY = text("""SELECT id,member_no,full_name,phone_e164,email,staff_no,status,joined_on
  FROM members
  WHERE cooperative_id=:cid
    AND (
      CAST(:search AS text) IS NULL
      OR full_name ILIKE (CAST(:search AS text) || '%')
      OR member_no ILIKE (CAST(:search AS text) || '%')
      OR phone_e164 ILIKE (CAST(:search AS text) || '%')
      OR staff_no ILIKE (CAST(:search AS text) || '%')
    )
  ORDER BY member_no""")
RESTRICTED_MEMBER_LIST_QUERY = text("""SELECT id,member_no,full_name
  FROM members
  WHERE cooperative_id=:cid
    AND (
      CAST(:search AS text) IS NULL
      OR full_name ILIKE (CAST(:search AS text) || '%')
      OR member_no ILIKE (CAST(:search AS text) || '%')
    )
  ORDER BY member_no""")
AUDITOR_MEMBER_LIST_QUERY = text("""SELECT id,member_no,full_name,status,joined_on
  FROM members
  WHERE cooperative_id=:cid
    AND (
      CAST(:search AS text) IS NULL
      OR full_name ILIKE (CAST(:search AS text) || '%')
      OR member_no ILIKE (CAST(:search AS text) || '%')
    )
  ORDER BY member_no""")
MEMBER_LIST_QUERIES = {
    "admin": FULL_MEMBER_LIST_QUERY,
    "officer": FULL_MEMBER_LIST_QUERY,
    "treasurer": RESTRICTED_MEMBER_LIST_QUERY,
    "committee": RESTRICTED_MEMBER_LIST_QUERY,
    "auditor": AUDITOR_MEMBER_LIST_QUERY,
}
FULL_MEMBER_DETAIL_QUERY = text("""SELECT id,member_no,full_name,phone_e164,email,staff_no,employer,status,joined_on
  FROM members WHERE cooperative_id=:cid AND id=:id""")
RESTRICTED_MEMBER_DETAIL_QUERY = text(
    "SELECT id,member_no,full_name FROM members WHERE cooperative_id=:cid AND id=:id"
)
AUDITOR_MEMBER_DETAIL_QUERY = text(
    "SELECT id,member_no,full_name,status,joined_on FROM members WHERE cooperative_id=:cid AND id=:id"
)
MEMBER_DETAIL_QUERIES = {
    "admin": FULL_MEMBER_DETAIL_QUERY,
    "officer": FULL_MEMBER_DETAIL_QUERY,
    "treasurer": RESTRICTED_MEMBER_DETAIL_QUERY,
    "committee": RESTRICTED_MEMBER_DETAIL_QUERY,
    "auditor": AUDITOR_MEMBER_DETAIL_QUERY,
}
FULL_BANK_ACCOUNT_QUERY = text("""SELECT id,bank_code,last4,account_name,verified_at,status
  FROM bank_accounts WHERE cooperative_id=:cid AND member_id=:id""")
TREASURER_BANK_ACCOUNT_QUERY = text(
    "SELECT id,bank_code,last4 FROM bank_accounts WHERE cooperative_id=:cid AND member_id=:id"
)
BANK_ACCOUNT_QUERIES = {
    "admin": FULL_BANK_ACCOUNT_QUERY,
    "officer": FULL_BANK_ACCOUNT_QUERY,
    "treasurer": TREASURER_BANK_ACCOUNT_QUERY,
}


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


def _member_role(current: CurrentSession) -> str:
    """Resolve to the strongest explicit member-read role in the session."""
    for role in ("admin", "officer", "treasurer", "committee", "auditor"):
        if role in current.roles:
            return role
    raise Forbidden("Staff role required")


def _list_response_model(role: str) -> Any:
    return {
        "admin": MemberListAdminOfficerResponse,
        "officer": MemberListAdminOfficerResponse,
        "treasurer": MemberListTreasurerResponse,
        "committee": MemberListCommitteeResponse,
        "auditor": MemberListAuditorResponse,
    }[role]


def _detail_response_model(role: str) -> Any:
    return {
        "admin": MemberDetailAdminOfficerResponse,
        "officer": MemberDetailAdminOfficerResponse,
        "treasurer": MemberDetailTreasurerResponse,
        "committee": MemberDetailCommitteeResponse,
        "auditor": MemberDetailAuditorResponse,
    }[role]


@router.get("/members", response_model=list[MemberListResponse])
async def list_members(
    current: Annotated[CurrentSession, Depends(current_session)],
    search: Annotated[str | None, Query(max_length=100)] = None,
) -> list[MemberListResponse]:
    role = _member_role(current)
    async with transaction(current.cooperative_id) as session:
        result = await session.execute(
            MEMBER_LIST_QUERIES[role],
            {"cid": current.cooperative_id, "search": search.strip() if search else None},
        )
        model = _list_response_model(role)
        return cast(
            list[MemberListResponse],
            [model.model_validate(dict(row)) for row in result.mappings()],
        )


@router.get("/members/{member_id}", response_model=MemberDetailResponse)
async def member_detail(
    member_id: uuid.UUID, current: Annotated[CurrentSession, Depends(current_session)]
) -> MemberDetailResponse:
    role = _member_role(current)
    async with transaction(current.cooperative_id) as session:
        result = await session.execute(
            MEMBER_DETAIL_QUERIES[role],
            {"cid": current.cooperative_id, "id": member_id},
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise NotFound("Member not found")
        member = dict(row)
        if role in {"admin", "officer", "treasurer"}:
            banks = await session.execute(
                BANK_ACCOUNT_QUERIES[role],
                {"cid": current.cooperative_id, "id": member_id},
            )
            member["bank_accounts"] = [dict(bank) for bank in banks.mappings()]
    if role in {"admin", "officer", "treasurer", "auditor"}:
        member["balances"] = await service.member_balances(current.cooperative_id, member_id)
    model = _detail_response_model(role)
    return cast(MemberDetailResponse, model.model_validate(member))


@router.post("/members/contributions", dependencies=[Depends(require_csrf)])
async def contribute(
    body: ContributionBody, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, str]:
    entry_id = await service.record_contribution(
        current, body.member_id, body.kind, body.amount_kobo, body.idempotency_key
    )
    return {"journal_entry_id": str(entry_id)}


@router.post("/members/withdrawals", dependencies=[Depends(require_csrf)])
async def withdraw(
    body: WithdrawalBody, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, str]:
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
    return await service.import_members(current, body.csv_text, body.confirm)
