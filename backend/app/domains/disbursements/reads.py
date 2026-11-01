"""Tenant-scoped conservative read projections for manual disbursements."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from app.core.db import transaction
from app.core.errors import Forbidden, NotFound
from app.domains.auth.service import CurrentSession

READ_ROLES = {"admin", "treasurer", "auditor"}
BANK_ROLES = {"admin", "treasurer"}

_DISBURSEMENT_COMMON_FIELDS = (
    "id",
    "loan_id",
    "application_id",
    "member_no",
    "full_name",
    "amount_kobo",
    "status",
    "prepared_by",
    "authorized_by",
    "transfer_reference",
    "transfer_date",
    "confirmed_by",
    "created_at",
    "authorized_at",
    "transfer_recorded_at",
    "paid_at",
    "failed_at",
    "reversed_at",
    "reversal_reason",
    "failure_reason",
)
_DISBURSEMENT_BANK_FIELDS = ("bank_code", "last4")
_DISBURSEMENT_FIELDS_BY_ROLE: dict[str, tuple[str, ...]] = {
    "admin": _DISBURSEMENT_COMMON_FIELDS + _DISBURSEMENT_BANK_FIELDS,
    "treasurer": _DISBURSEMENT_COMMON_FIELDS + _DISBURSEMENT_BANK_FIELDS,
    "auditor": _DISBURSEMENT_COMMON_FIELDS,
}
_READY_LOAN_FIELDS = (
    "loan_id",
    "application_id",
    "member_no",
    "full_name",
    "amount_kobo",
    "status",
)
_UUID_FIELDS = ("id", "loan_id", "application_id", "prepared_by", "authorized_by", "confirmed_by")


def read_role(current: CurrentSession) -> str:
    for role in ("admin", "treasurer", "auditor"):
        if role in current.roles:
            return role
    raise Forbidden("This role cannot read disbursements")


SELECT = """
SELECT d.id,d.loan_id,a.id AS application_id,m.member_no,m.full_name,d.amount_kobo,
       d.status,d.bank_code,d.last4,d.prepared_by,d.authorized_by,d.transfer_reference,
       d.transfer_date,d.paid_confirmed_by AS confirmed_by,d.created_at,d.authorized_at,
       d.transfer_recorded_at,d.paid_at,d.failed_at,d.reversed_at,d.reversal_reason,
       (SELECT al.reason FROM audit_logs al
          WHERE al.cooperative_id=d.cooperative_id AND al.entity_type='disbursement'
            AND al.entity_id=d.id::text AND al.action='disbursement_failed'
          ORDER BY al.created_at DESC,al.id DESC LIMIT 1) AS failure_reason
  FROM disbursements d
  JOIN loans l ON l.cooperative_id=d.cooperative_id AND l.id=d.loan_id
  JOIN loan_applications a ON a.cooperative_id=l.cooperative_id AND a.id=l.application_id
  JOIN members m ON m.cooperative_id=l.cooperative_id AND m.id=l.member_id
 WHERE d.cooperative_id=:cid
   AND (CAST(:status AS text) IS NULL OR d.status=CAST(:status AS text))
   AND (CAST(:search AS text) IS NULL OR m.full_name ILIKE ('%'||CAST(:search AS text)||'%')
        OR m.member_no ILIKE ('%'||CAST(:search AS text)||'%'))
"""


def _row(item: Any, role: str) -> dict[str, Any]:
    source = dict(item)
    result = {key: source[key] for key in _DISBURSEMENT_FIELDS_BY_ROLE[role] if key in source}
    for key in _UUID_FIELDS:
        if result.get(key) is not None:
            result[key] = str(result[key])
    return result


def _ready_loan_row(item: Any) -> dict[str, Any]:
    source = dict(item)
    result = {key: source[key] for key in _READY_LOAN_FIELDS if key in source}
    for key in ("loan_id", "application_id"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    return result


async def list_disbursements(
    current: CurrentSession, *, status: str | None, search: str | None, page: int, page_size: int
) -> dict[str, Any]:
    role = read_role(current)
    params = {
        "cid": current.cooperative_id,
        "status": status.strip() if status else None,
        "search": search.strip() if search else None,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    async with transaction(current.cooperative_id) as session:
        total = int(
            (
                await session.execute(
                    text("""SELECT count(*) FROM disbursements d
          JOIN loans l ON l.cooperative_id=d.cooperative_id AND l.id=d.loan_id
          JOIN members m ON m.cooperative_id=l.cooperative_id AND m.id=l.member_id
          WHERE d.cooperative_id=:cid
            AND (CAST(:status AS text) IS NULL OR d.status=CAST(:status AS text))
            AND (CAST(:search AS text) IS NULL OR m.full_name ILIKE ('%'||CAST(:search AS text)||'%')
              OR m.member_no ILIKE ('%'||CAST(:search AS text)||'%'))"""),
                    {key: params[key] for key in ("cid", "status", "search")},
                )
            ).scalar_one()
        )
        result = await session.execute(
            text(SELECT + " ORDER BY d.created_at DESC,d.id DESC LIMIT :limit OFFSET :offset"),
            params,
        )
        items = [_row(item, role) for item in result.mappings()]
    return {"items": items, "page": page, "page_size": page_size, "total": total}


async def get_disbursement(current: CurrentSession, disbursement_id: uuid.UUID) -> dict[str, Any]:
    role = read_role(current)
    async with transaction(current.cooperative_id) as session:
        result = await session.execute(
            text(SELECT + " AND d.id=:id"),
            {"cid": current.cooperative_id, "status": None, "search": None, "id": disbursement_id},
        )
        row = result.mappings().one_or_none()
    if row is None:
        raise NotFound("Disbursement not found")
    return _row(row, role)


async def list_ready_loans(
    current: CurrentSession, *, search: str | None, page: int, page_size: int
) -> dict[str, Any]:
    read_role(current)
    params = {
        "cid": current.cooperative_id,
        "search": search.strip() if search else None,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    common = """FROM loans l
      JOIN loan_applications a ON a.cooperative_id=l.cooperative_id AND a.id=l.application_id
      JOIN members m ON m.cooperative_id=l.cooperative_id AND m.id=l.member_id
     WHERE a.cooperative_id=:cid AND a.status='ready_for_disbursement'
       AND NOT EXISTS (SELECT 1 FROM disbursements d WHERE d.cooperative_id=l.cooperative_id
         AND d.loan_id=l.id AND d.status NOT IN ('failed','cancelled'))
       AND (CAST(:search AS text) IS NULL OR m.full_name ILIKE ('%'||CAST(:search AS text)||'%')
         OR m.member_no ILIKE ('%'||CAST(:search AS text)||'%'))"""
    async with transaction(current.cooperative_id) as session:
        total = int(
            (
                await session.execute(
                    text("SELECT count(*) " + common),
                    {"cid": params["cid"], "search": params["search"]},
                )
            ).scalar_one()
        )
        result = await session.execute(
            text(
                """SELECT l.id AS loan_id,a.id AS application_id,m.member_no,m.full_name,
              a.principal_kobo AS amount_kobo,a.status """
                + common
                + " ORDER BY a.updated_at DESC,a.id DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        )
        items = [_ready_loan_row(item) for item in result.mappings()]
    return {"items": items, "page": page, "page_size": page_size, "total": total}
