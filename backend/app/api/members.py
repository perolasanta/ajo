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
from app.domains.members import reads, service
from app.domains.notifications import eligibility

router = APIRouter(tags=["members"])


class _MemberResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MemberListAdminOfficerResponse(_MemberResponse):
    id: uuid.UUID
    has_pending_request: bool = False
    member_no: str
    full_name: str
    phone_e164: str | None
    email: str | None
    staff_no: str | None
    status: str
    joined_on: date | None


class MemberListTreasurerResponse(_MemberResponse):
    id: uuid.UUID
    has_pending_request: bool = False
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
    active_loans_count: int | None = None


class MemberStatementItem(_MemberResponse):
    id: uuid.UUID
    date: date
    recorded_at: datetime | None = None
    type: str
    amount_kobo: int
    running_balance_kobo: int


class MemberStatementPage(_MemberResponse):
    items: list[MemberStatementItem]
    shares_balance_kobo: int = 0
    page: int
    page_size: int
    total: int


class MemberLoanItem(_MemberResponse):
    application_id: uuid.UUID
    loan_id: uuid.UUID | None = None
    application_status: str
    loan_status: str | None = None
    principal_kobo: int
    outstanding_balance_kobo: int | None = None
    next_installment_at: datetime | None = None
    next_installment_amount_kobo: int | None = None
    submitted_at: datetime


class MemberLoanPage(_MemberResponse):
    items: list[MemberLoanItem]
    page: int
    page_size: int
    total: int


class MemberActivityItem(_MemberResponse):
    event_id: str
    timestamp: datetime
    actor_id: str | None
    actor_display_name: str
    actor_role: str | None
    action: str
    entity_type: str
    entity_id: str | None
    safe_summary: dict[str, str | int | None]


class MemberActivityPage(_MemberResponse):
    items: list[MemberActivityItem]
    page: int
    page_size: int
    total: int


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


class MemberDetailAdminOfficerResponse(_MemberResponse):
    id: uuid.UUID
    member_no: str
    full_name: str
    phone_e164: str | None
    email: str | None
    staff_no: str | None
    employer: str | None
    status: str
    joined_on: date | None
    bank_accounts: list[MemberBankAccountAdminOfficerResponse]
    balances: MemberBalancesResponse


class MemberDetailTreasurerResponse(_MemberResponse):
    id: uuid.UUID
    member_no: str
    full_name: str
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

FULL_MEMBER_LIST_QUERY = text("""SELECT id,member_no,full_name,phone_e164,email,staff_no,status,joined_on,
         EXISTS(SELECT 1 FROM withdrawal_requests wr WHERE wr.cooperative_id=members.cooperative_id AND wr.member_id=members.id AND wr.status='pending') AS has_pending_request
  FROM members
  WHERE cooperative_id=:cid
    AND (
      CAST(:search AS text) IS NULL
      OR full_name ILIKE (CAST(:search AS text) || '%')
      OR member_no ILIKE (CAST(:search AS text) || '%')
      OR phone_e164 ILIKE (CAST(:search AS text) || '%')
      OR staff_no ILIKE (CAST(:search AS text) || '%')
    ) AND (NOT :has_pending_request OR EXISTS(SELECT 1 FROM withdrawal_requests wr WHERE wr.cooperative_id=members.cooperative_id AND wr.member_id=members.id AND wr.status='pending'))
  ORDER BY member_no""")
RESTRICTED_MEMBER_LIST_QUERY = text("""SELECT id,member_no,full_name,
         EXISTS(SELECT 1 FROM withdrawal_requests wr WHERE wr.cooperative_id=members.cooperative_id AND wr.member_id=members.id AND wr.status='pending') AS has_pending_request
  FROM members
  WHERE cooperative_id=:cid
    AND (
      CAST(:search AS text) IS NULL
      OR full_name ILIKE (CAST(:search AS text) || '%')
      OR member_no ILIKE (CAST(:search AS text) || '%')
    ) AND (NOT :has_pending_request OR EXISTS(SELECT 1 FROM withdrawal_requests wr WHERE wr.cooperative_id=members.cooperative_id AND wr.member_id=members.id AND wr.status='pending'))
  ORDER BY member_no""")
AUDITOR_MEMBER_LIST_QUERY = text("""SELECT id,member_no,full_name,status,joined_on,false AS has_pending_request
  FROM members
  WHERE cooperative_id=:cid
    AND (
      CAST(:search AS text) IS NULL
      OR full_name ILIKE (CAST(:search AS text) || '%')
      OR member_no ILIKE (CAST(:search AS text) || '%')
    ) AND (NOT :has_pending_request OR EXISTS(SELECT 1 FROM withdrawal_requests wr WHERE wr.cooperative_id=members.cooperative_id AND wr.member_id=members.id AND wr.status='pending'))
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
    effective_date: date = Field(default_factory=date.today)
    reference: str = Field(default="", max_length=200)
    payment_method: str = Field(default="cash", min_length=1, max_length=40)
    idempotency_key: str = Field(min_length=1, max_length=200)


class WithdrawalBody(BaseModel):
    member_id: uuid.UUID
    amount_kobo: int = Field(gt=0)
    reason: str = Field(default="Member withdrawal request", min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=200)


class WithdrawalRejectBody(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class MemberWithdrawalRequestItem(_MemberResponse):
    id: uuid.UUID
    amount_kobo: int
    status: str
    created_at: datetime
    requested_by: uuid.UUID
    requester_name: str
    reason: str
    rejection_reason: str | None


class MemberWithdrawalRequestPage(_MemberResponse):
    items: list[MemberWithdrawalRequestItem]
    current_user_id: str


class WithdrawalQueueItem(_MemberResponse):
    id: uuid.UUID
    member_id: uuid.UUID
    member_name: str
    member_no: str
    amount_kobo: int
    requester_name: str
    requested_by: uuid.UUID
    requested_at: datetime
    status: str
    reason: str
    rejection_reason: str | None


class WithdrawalQueuePage(_MemberResponse):
    items: list[WithdrawalQueueItem]
    page: int
    page_size: int
    total: int


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
    has_pending_request: bool = False,
) -> list[MemberListResponse]:
    role = _member_role(current)
    if role not in {"admin", "officer", "treasurer"}:
        has_pending_request = False
    async with transaction(current.cooperative_id) as session:
        result = await session.execute(
            MEMBER_LIST_QUERIES[role],
            {
                "cid": current.cooperative_id,
                "search": search.strip() if search else None,
                "has_pending_request": has_pending_request,
            },
        )
        model = _list_response_model(role)
        return cast(
            list[MemberListResponse],
            [model.model_validate(dict(row)) for row in result.mappings()],
        )


@router.get("/withdrawal-requests", response_model=WithdrawalQueuePage)
async def list_withdrawal_requests(
    current: Annotated[CurrentSession, Depends(current_session)],
    status: str | None = Query(default=None, max_length=20),
    mine: str = Query(default="all", pattern="^(all|needed)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    roles = set(current.roles)
    if not roles.intersection({"admin", "officer", "treasurer"}):
        raise Forbidden("This role cannot read withdrawal requests")
    officer_only = "officer" in roles and "treasurer" not in roles and "admin" not in roles
    requested_status = status.strip() if status else None
    if officer_only and requested_status not in (None, "pending"):
        return {"items": [], "page": page, "page_size": page_size, "total": 0}
    action_ids: tuple[uuid.UUID, ...] | None = None
    async with transaction(current.cooperative_id) as session:
        if mine == "needed" and "treasurer" in roles:
            items = await eligibility.get_action_items_for_user(
                session, current.cooperative_id, current.user_id, roles
            )
            action_ids = items.get("withdrawal_requests", ())
        elif mine == "needed":
            action_ids = ()
        query = text("""WITH filtered AS (
          SELECT wr.id,wr.member_id,m.full_name AS member_name,m.member_no,wr.amount_kobo,
                 wr.requested_by,coalesce(nullif(trim(u.full_name),''),'Unnamed user') AS requester_name,
                 wr.created_at AS requested_at,wr.status,wr.reason,wr.rejection_reason
          FROM withdrawal_requests wr
          JOIN members m ON m.cooperative_id=wr.cooperative_id AND m.id=wr.member_id
          JOIN users u ON u.cooperative_id=wr.cooperative_id AND u.id=wr.requested_by
          WHERE wr.cooperative_id=:cid
            AND (CAST(:status AS text) IS NULL OR wr.status=CAST(:status AS text))
            AND (NOT :officer_only OR wr.status='pending')
            AND (CAST(:action_ids AS uuid[]) IS NULL OR wr.id=ANY(CAST(:action_ids AS uuid[])))
        ) SELECT *,count(*) OVER() AS total FROM filtered
          ORDER BY requested_at DESC,id DESC LIMIT :limit OFFSET :offset""")
        rows = (
            (
                await session.execute(
                    query,
                    {
                        "cid": current.cooperative_id,
                        "status": "pending"
                        if officer_only and requested_status is None
                        else requested_status,
                        "officer_only": officer_only,
                        "action_ids": list(action_ids) if action_ids is not None else None,
                        "limit": page_size,
                        "offset": (page - 1) * page_size,
                    },
                )
            )
            .mappings()
            .all()
        )
    return {
        "items": [dict(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": int(rows[0]["total"]) if rows else 0,
    }


@router.get(
    "/members/{member_id}",
    response_model=MemberDetailResponse,
    response_model_exclude_unset=True,
)
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


@router.get("/members/{member_id}/statement", response_model=MemberStatementPage)
async def member_statement(
    member_id: uuid.UUID,
    current: Annotated[CurrentSession, Depends(current_session)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    return await reads.list_statement(current, member_id, page=page, page_size=page_size)


@router.get(
    "/members/{member_id}/loans",
    response_model=MemberLoanPage,
    response_model_exclude_unset=True,
)
async def member_loans(
    member_id: uuid.UUID,
    current: Annotated[CurrentSession, Depends(current_session)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    return await reads.list_loans(current, member_id, page=page, page_size=page_size)


@router.get("/members/{member_id}/activity", response_model=MemberActivityPage)
async def member_activity(
    member_id: uuid.UUID,
    current: Annotated[CurrentSession, Depends(current_session)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    return await reads.list_activity(current, member_id, page=page, page_size=page_size)


@router.post("/members/contributions", dependencies=[Depends(require_csrf)])
async def contribute(
    body: ContributionBody, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, str]:
    entry_id = await service.record_contribution(
        current,
        body.member_id,
        body.kind,
        body.amount_kobo,
        body.idempotency_key,
        effective_date=body.effective_date,
        reference=body.reference,
        payment_method=body.payment_method,
    )
    return {"journal_entry_id": str(entry_id)}


@router.post("/members/withdrawals", dependencies=[Depends(require_csrf)])
async def withdraw(
    body: WithdrawalBody, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, str]:
    request_id = await service.request_withdrawal(
        current, body.member_id, body.amount_kobo, body.idempotency_key, reason=body.reason
    )
    return {"withdrawal_request_id": str(request_id)}


@router.post("/members/withdrawals/{request_id}/approve", dependencies=[Depends(require_csrf)])
async def approve_withdrawal(
    request_id: uuid.UUID, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, str]:
    entry_id = await service.approve_withdrawal(current, request_id)
    return {"journal_entry_id": str(entry_id)}


@router.post("/members/withdrawals/{request_id}/reject", dependencies=[Depends(require_csrf)])
async def reject_withdrawal(
    request_id: uuid.UUID,
    body: WithdrawalRejectBody,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, str]:
    await service.reject_withdrawal(current, request_id, body.reason)
    return {"status": "rejected"}


@router.get("/members/{member_id}/withdrawals", response_model=MemberWithdrawalRequestPage)
async def member_withdrawals(
    member_id: uuid.UUID,
    current: Annotated[CurrentSession, Depends(current_session)],
) -> dict[str, Any]:
    roles = set(current.roles)
    if not roles.intersection({"admin", "officer", "treasurer"}):
        raise Forbidden("This role cannot read withdrawal requests")
    officer_only = "officer" in roles and "treasurer" not in roles and "admin" not in roles
    async with transaction(current.cooperative_id) as session:
        rows = await session.execute(
            text("""SELECT wr.id,wr.amount_kobo,wr.status,wr.created_at,
          wr.requested_by,coalesce(nullif(trim(u.full_name),''),'Unnamed user') AS requester_name,
          wr.reason,wr.rejection_reason
          FROM withdrawal_requests wr JOIN users u
            ON u.cooperative_id=wr.cooperative_id AND u.id=wr.requested_by
          WHERE wr.cooperative_id=:cid AND wr.member_id=:mid
            AND (NOT :officer_only OR wr.status='pending' OR wr.requested_by=:user_id)
          ORDER BY wr.created_at DESC,wr.id"""),
            {
                "cid": current.cooperative_id,
                "mid": member_id,
                "officer_only": officer_only,
                "user_id": current.user_id,
            },
        )
        items = [dict(row) for row in rows.mappings().all()]
    return {"items": items, "current_user_id": str(current.user_id)}


@router.post("/members/import", dependencies=[Depends(require_csrf)])
async def import_members(
    body: ImportBody, current: Annotated[CurrentSession, Depends(current_session)]
) -> dict[str, Any]:
    return await service.import_members(current, body.csv_text, body.confirm)
