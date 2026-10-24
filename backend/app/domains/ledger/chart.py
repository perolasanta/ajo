"""Default accounts shared by cooperative bootstrap and later repair tooling."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_CHART = (
    ("bank", "Cooperative bank", "asset"),
    ("loans_receivable", "Loans receivable", "asset"),
    ("interest_receivable", "Interest receivable", "asset"),
    ("member_savings", "Withdrawable member savings", "liability"),
    ("unallocated_receipts", "Unallocated receipts", "liability"),
    ("member_shares", "Compulsory member shares", "equity"),
    ("dues_income", "Dues income", "income"),
    ("interest_income", "Interest income", "income"),
    ("fee_income", "Fee income", "income"),
    ("reserves", "Reserves", "equity"),
)


async def create_default_chart(session: AsyncSession, cooperative_id: uuid.UUID) -> None:
    for code, name, account_type in DEFAULT_CHART:
        await session.execute(
            text("""INSERT INTO accounts (cooperative_id,code,name,account_type)
            VALUES (:cid,:code,:name,:type) ON CONFLICT (cooperative_id,code) DO NOTHING"""),
            {"cid": cooperative_id, "code": code, "name": name, "type": account_type},
        )
