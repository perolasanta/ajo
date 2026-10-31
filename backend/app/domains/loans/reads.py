"""Tenant-scoped, read-only projections for staff loan screens."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from app.core.db import transaction
from app.core.errors import Forbidden, NotFound
from app.domains.auth.service import CurrentSession

READ_ROLES = {"admin", "officer", "treasurer", "committee", "auditor"}
BANK_ROLES = {"admin", "officer", "treasurer"}


def read_role(current: CurrentSession) -> str:
    for role in ("admin", "officer", "treasurer", "committee", "auditor"):
        if role in current.roles:
            return role
    raise Forbidden("Staff role required")


APPLICATION_SELECT = """
  SELECT a.id,a.member_id,m.member_no,m.full_name,p.name AS product_name,
         a.principal_kobo,a.tenor_months,a.annual_rate_bps,a.status,
         a.created_at AS submitted_at,ap.decision,ap.reason AS decision_reason,
         ap.created_at AS decided_at,
         COALESCE(consents.statuses, '[]'::jsonb) AS consent_statuses,
         COALESCE(guarantees.guarantor_count, 0) AS guarantor_count
    FROM loan_applications a
    JOIN members m ON m.cooperative_id=a.cooperative_id AND m.id=a.member_id
    JOIN loan_products p ON p.cooperative_id=a.cooperative_id AND p.id=a.product_id
    LEFT JOIN LATERAL (
      SELECT decision,reason,created_at
        FROM approvals
       WHERE cooperative_id=a.cooperative_id AND application_id=a.id
       ORDER BY created_at DESC LIMIT 1
    ) ap ON true
    LEFT JOIN LATERAL (
      SELECT jsonb_agg(jsonb_build_object('kind',kind,'status',status)
                       ORDER BY kind) AS statuses
        FROM member_consents
       WHERE cooperative_id=a.cooperative_id AND application_id=a.id
    ) consents ON true
    LEFT JOIN LATERAL (
      SELECT count(*)::int AS guarantor_count
        FROM guarantees
       WHERE cooperative_id=a.cooperative_id AND application_id=a.id
         AND status <> 'released'
    ) guarantees ON true
   WHERE a.cooperative_id=:cid
     AND (CAST(:status AS text) IS NULL OR a.status=CAST(:status AS text))
     AND (CAST(:search AS text) IS NULL OR m.full_name ILIKE ('%' || CAST(:search AS text) || '%')
          OR m.member_no ILIKE ('%' || CAST(:search AS text) || '%'))
"""

APPLICATION_COUNT_QUERY = text("""
  SELECT count(*)
    FROM loan_applications a
    JOIN members m ON m.cooperative_id=a.cooperative_id AND m.id=a.member_id
   WHERE a.cooperative_id=:cid
     AND (CAST(:status AS text) IS NULL OR a.status=CAST(:status AS text))
     AND (CAST(:search AS text) IS NULL OR m.full_name ILIKE ('%' || CAST(:search AS text) || '%')
          OR m.member_no ILIKE ('%' || CAST(:search AS text) || '%'))
""")

APPLICATION_LIST_QUERY = text(
    APPLICATION_SELECT + " ORDER BY a.created_at DESC, a.id DESC LIMIT :limit OFFSET :offset"
)


def _application_row(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["id"] = uuid.UUID(str(result["id"]))
    return result


async def list_applications(
    current: CurrentSession,
    *,
    status: str | None,
    search: str | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    read_role(current)
    offset = (page - 1) * page_size
    params = {
        "cid": current.cooperative_id,
        "status": status.strip() if status else None,
        "search": search.strip() if search else None,
        "limit": page_size,
        "offset": offset,
    }
    async with transaction(current.cooperative_id) as session:
        total = int(
            (
                await session.execute(
                    APPLICATION_COUNT_QUERY,
                    {key: params[key] for key in ("cid", "status", "search")},
                )
            ).scalar_one()
        )
        result = await session.execute(APPLICATION_LIST_QUERY, params)
        items = [_application_row(row) for row in result.mappings()]
    return {"items": items, "page": page, "page_size": page_size, "total": total}


async def get_application(current: CurrentSession, application_id: uuid.UUID) -> dict[str, Any]:
    role = read_role(current)
    async with transaction(current.cooperative_id) as session:
        result = await session.execute(
            text(
                f"""{APPLICATION_SELECT}
                AND a.id=:id"""
            ),
            {"cid": current.cooperative_id, "status": None, "search": None, "id": application_id},
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise NotFound("Loan application not found")
        application = _application_row(row)

        snapshot = await session.execute(
            text("""SELECT terms FROM loan_terms_snapshots
                 WHERE cooperative_id=:cid AND application_id=:id
                 ORDER BY created_at DESC LIMIT 1"""),
            {"cid": current.cooperative_id, "id": application_id},
        )
        application["terms_snapshot"] = snapshot.scalar_one_or_none()

        timeline_result = await session.execute(
            text("""SELECT status,created_at AS at FROM loan_applications
                 WHERE cooperative_id=:cid AND id=:id
                 UNION ALL
                 SELECT decision AS status,created_at AS at FROM approvals
                 WHERE cooperative_id=:cid AND application_id=:id
                 ORDER BY at"""),
            {"cid": current.cooperative_id, "id": application_id},
        )
        application["status_timeline"] = [dict(item) for item in timeline_result.mappings()]

        consent_result = await session.execute(
            text("""SELECT kind,status FROM member_consents
                 WHERE cooperative_id=:cid AND application_id=:id ORDER BY kind"""),
            {"cid": current.cooperative_id, "id": application_id},
        )
        application["consent_statuses"] = [dict(item) for item in consent_result.mappings()]

        guarantor_result = await session.execute(
            text("""SELECT m.member_no,m.full_name FROM guarantees g
                 JOIN members m ON m.cooperative_id=g.cooperative_id AND m.id=g.guarantor_member_id
                 WHERE g.cooperative_id=:cid AND g.application_id=:id AND g.status <> 'released'
                 ORDER BY m.member_no"""),
            {"cid": current.cooperative_id, "id": application_id},
        )
        application["guarantors"] = [dict(item) for item in guarantor_result.mappings()]

        if role in BANK_ROLES:
            bank_result = await session.execute(
                text("""SELECT bank_code,last4 FROM bank_accounts b
                     JOIN loan_applications a ON a.cooperative_id=b.cooperative_id
                       AND a.destination_bank_account_id=b.id
                     WHERE a.cooperative_id=:cid AND a.id=:id"""),
                {"cid": current.cooperative_id, "id": application_id},
            )
            application["destination_bank_account"] = bank_result.mappings().one_or_none()

        schedule_result = await session.execute(
            text("""SELECT s.installment_no,s.due_at,s.principal_kobo,s.interest_kobo,
                        s.payment_kobo,s.balance_kobo,s.dues_kobo,s.savings_kobo
                 FROM loan_schedule s JOIN loans l ON l.cooperative_id=s.cooperative_id
                   AND l.id=s.loan_id
                 WHERE l.cooperative_id=:cid AND l.application_id=:id
                 ORDER BY s.installment_no"""),
            {"cid": current.cooperative_id, "id": application_id},
        )
        application["schedule"] = [dict(item) for item in schedule_result.mappings()]
    return application


async def list_products(current: CurrentSession) -> list[dict[str, Any]]:
    read_role(current)
    async with transaction(current.cooperative_id) as session:
        result = await session.execute(
            text("""SELECT id,code,name,interest_method,annual_rate_bps,min_amount_kobo,
                        max_amount_kobo,min_tenor_months,max_tenor_months,fee_bps,
                        guarantors_required FROM loan_products
                 WHERE cooperative_id=:cid AND status='approved'
                 ORDER BY name,code"""),
            {"cid": current.cooperative_id},
        )
        return [dict(row) for row in result.mappings()]
