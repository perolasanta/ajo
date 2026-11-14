"""Tenant-scoped read-only share-register projection from the financial ledger."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import transaction
from app.core.errors import Forbidden, NotFound
from app.domains.auth.service import CurrentSession

REGISTER_ROLES = frozenset({"admin", "treasurer", "officer", "auditor", "committee"})

# Each query repeats the CTE deliberately so Ruff can verify that every SQL statement
# is a complete static literal rather than a string assembled at runtime.
_SUMMARY_QUERY = """
WITH share_movements AS (
    SELECT line.member_id,
           (line.credit_kobo - line.debit_kobo)::bigint AS balance_delta_kobo,
           ((line.credit_kobo - line.debit_kobo)
             / (purchase.unit_price_naira * 100))::bigint AS shares_delta,
           entry.source_type AS entry_source_type,
           entry.effective_date
      FROM journal_lines AS line
      JOIN accounts AS account
        ON account.cooperative_id=line.cooperative_id AND account.id=line.account_id
      JOIN journal_entries AS entry
        ON entry.cooperative_id=line.cooperative_id AND entry.id=line.entry_id
      JOIN journal_entries AS original_entry
        ON original_entry.cooperative_id=entry.cooperative_id
       AND original_entry.id=coalesce(entry.reverses_entry_id,entry.id)
       AND original_entry.source_type='share_purchase'
      JOIN share_purchase_requests AS purchase
        ON purchase.cooperative_id=original_entry.cooperative_id
       AND purchase.id=original_entry.source_id
       AND purchase.member_id=line.member_id
     WHERE line.cooperative_id=:cid
       AND entry.cooperative_id=:cid
       AND original_entry.cooperative_id=:cid
       AND purchase.cooperative_id=:cid
       AND account.code='member_shares'
), holdings AS (
    SELECT member_id,
           sum(shares_delta)::bigint AS shares_held,
           max(effective_date) FILTER (
               WHERE entry_source_type='share_purchase' AND balance_delta_kobo > 0
           ) AS last_purchase_date
      FROM share_movements
     GROUP BY member_id
    HAVING sum(shares_delta) > 0
)
SELECT coalesce(sum(shares_held),0)::bigint AS total_shares,
       count(*)::bigint AS members_holding_shares
  FROM holdings
"""

_TOTAL_QUERY = """
WITH share_movements AS (
    SELECT line.member_id,
           (line.credit_kobo - line.debit_kobo)::bigint AS balance_delta_kobo,
           ((line.credit_kobo - line.debit_kobo)
             / (purchase.unit_price_naira * 100))::bigint AS shares_delta,
           entry.source_type AS entry_source_type,
           entry.effective_date
      FROM journal_lines AS line
      JOIN accounts AS account
        ON account.cooperative_id=line.cooperative_id AND account.id=line.account_id
      JOIN journal_entries AS entry
        ON entry.cooperative_id=line.cooperative_id AND entry.id=line.entry_id
      JOIN journal_entries AS original_entry
        ON original_entry.cooperative_id=entry.cooperative_id
       AND original_entry.id=coalesce(entry.reverses_entry_id,entry.id)
       AND original_entry.source_type='share_purchase'
      JOIN share_purchase_requests AS purchase
        ON purchase.cooperative_id=original_entry.cooperative_id
       AND purchase.id=original_entry.source_id
       AND purchase.member_id=line.member_id
     WHERE line.cooperative_id=:cid
       AND entry.cooperative_id=:cid
       AND original_entry.cooperative_id=:cid
       AND purchase.cooperative_id=:cid
       AND account.code='member_shares'
), holdings AS (
    SELECT member_id,
           sum(shares_delta)::bigint AS shares_held,
           max(effective_date) FILTER (
               WHERE entry_source_type='share_purchase' AND balance_delta_kobo > 0
           ) AS last_purchase_date
      FROM share_movements
     GROUP BY member_id
    HAVING sum(shares_delta) > 0
)
SELECT count(*)
  FROM holdings AS holding
  LEFT JOIN members AS member
    ON member.cooperative_id=:cid AND member.id=holding.member_id
 WHERE (CAST(:search AS text) IS NULL
        OR member.full_name ILIKE ('%'||CAST(:search AS text)||'%'))
"""

_REGISTER_QUERY = """
WITH share_movements AS (
    SELECT line.member_id,
           (line.credit_kobo - line.debit_kobo)::bigint AS balance_delta_kobo,
           ((line.credit_kobo - line.debit_kobo)
             / (purchase.unit_price_naira * 100))::bigint AS shares_delta,
           entry.source_type AS entry_source_type,
           entry.effective_date
      FROM journal_lines AS line
      JOIN accounts AS account
        ON account.cooperative_id=line.cooperative_id AND account.id=line.account_id
      JOIN journal_entries AS entry
        ON entry.cooperative_id=line.cooperative_id AND entry.id=line.entry_id
      JOIN journal_entries AS original_entry
        ON original_entry.cooperative_id=entry.cooperative_id
       AND original_entry.id=coalesce(entry.reverses_entry_id,entry.id)
       AND original_entry.source_type='share_purchase'
      JOIN share_purchase_requests AS purchase
        ON purchase.cooperative_id=original_entry.cooperative_id
       AND purchase.id=original_entry.source_id
       AND purchase.member_id=line.member_id
     WHERE line.cooperative_id=:cid
       AND entry.cooperative_id=:cid
       AND original_entry.cooperative_id=:cid
       AND purchase.cooperative_id=:cid
       AND account.code='member_shares'
), holdings AS (
    SELECT member_id,
           sum(shares_delta)::bigint AS shares_held,
           max(effective_date) FILTER (
               WHERE entry_source_type='share_purchase' AND balance_delta_kobo > 0
           ) AS last_purchase_date
      FROM share_movements
     GROUP BY member_id
    HAVING sum(shares_delta) > 0
)
SELECT holding.member_id,
       CASE WHEN member.id IS NULL THEN 'Unknown user'
            WHEN nullif(btrim(member.full_name),'') IS NULL THEN 'Unnamed user'
            ELSE btrim(member.full_name) END AS member_name,
       holding.shares_held,holding.last_purchase_date
  FROM holdings AS holding
  LEFT JOIN members AS member
    ON member.cooperative_id=:cid AND member.id=holding.member_id
 WHERE (CAST(:search AS text) IS NULL
        OR member.full_name ILIKE ('%'||CAST(:search AS text)||'%'))
 ORDER BY lower(coalesce(nullif(btrim(member.full_name),''),'Unnamed user')),
          holding.member_id
 LIMIT :limit OFFSET :offset
"""


def require_register_role(current: CurrentSession) -> None:
    if not set(current.roles).intersection(REGISTER_ROLES):
        raise Forbidden("This role cannot read the share register")


async def _summary(
    session: AsyncSession, cooperative_id: uuid.UUID
) -> dict[str, int]:
    result = await session.execute(text(_SUMMARY_QUERY), {"cid": cooperative_id})
    row = result.mappings().one()
    return {
        "total_shares": int(row["total_shares"] or 0),
        "members_holding_shares": int(row["members_holding_shares"] or 0),
    }


async def list_share_register(
    current: CurrentSession,
    *,
    search: str | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    require_register_role(current)
    search_term = search.strip() if search and search.strip() else None
    async with transaction(current.cooperative_id) as session:
        current_unit_price = await session.scalar(
            text("SELECT share_unit_price_naira FROM cooperatives WHERE id=:cid"),
            {"cid": current.cooperative_id},
        )
        if current_unit_price is None:
            raise NotFound("Cooperative not found")
        current_unit_price = int(current_unit_price)
        totals = await _summary(session, current.cooperative_id)
        total = int(
            (
                await session.execute(
                    text(_TOTAL_QUERY),
                    {"cid": current.cooperative_id, "search": search_term},
                )
            ).scalar_one()
        )
        result = await session.execute(
            text(_REGISTER_QUERY),
            {
                "cid": current.cooperative_id,
                "search": search_term,
                "limit": page_size,
                "offset": (page - 1) * page_size,
            },
        )
        items: list[dict[str, Any]] = []
        for row in result.mappings():
            shares_held = int(row["shares_held"])
            items.append(
                {
                    "member_id": str(row["member_id"]),
                    "member_name": row["member_name"],
                    "shares_held": shares_held,
                    "value_kobo": shares_held * current_unit_price * 100,
                    "last_purchase_date": row["last_purchase_date"],
                }
            )
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        **totals,
        "total_value_kobo": totals["total_shares"] * current_unit_price * 100,
        "current_unit_price_naira": current_unit_price,
    }
