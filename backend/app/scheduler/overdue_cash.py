"""RLS-scoped overdue cash alert job."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.notifications import eligibility, outbox
from app.domains.notifications.outbox import enqueue_action_notifications_safely
from app.domains.payments import service as payment_service

logger = logging.getLogger("scheduler.overdue_cash")


def daily_entity_id(cooperative_id: uuid.UUID, as_of_date: date) -> uuid.UUID:
    """One stable entity key per cooperative/day lets the channel-aware index dedupe alerts."""
    key = f"cash-deposit-overdue:{cooperative_id}:{as_of_date.isoformat()}".encode()
    return uuid.UUID(hex=hashlib.md5(key).hexdigest())


async def list_active_cooperative_ids(
    session_factory: Callable[[], AsyncSession],
) -> list[uuid.UUID]:
    """Read the tenant directory using the scheduler role; it contains IDs, not member data."""
    async with session_factory() as session, session.begin():
        result = await session.execute(
            text(
                "SELECT cooperative_id FROM tenant_directory "
                "WHERE status='active' ORDER BY cooperative_id"
            )
        )
        return [row["cooperative_id"] for row in result.mappings().all()]


async def process_cooperative(
    session_factory: Callable[[], AsyncSession],
    cooperative_id: uuid.UUID,
    *,
    as_of_date: date | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    async with session_factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.cooperative_id', :cid, true)"),
            {"cid": str(cooperative_id)},
        )
        if as_of_date is None:
            database_day = await session.scalar(
                text("SELECT (now() AT TIME ZONE 'Africa/Lagos')::date")
            )
            if not isinstance(database_day, date):
                raise RuntimeError("Unable to resolve the WAT business date")
            effective_date = database_day
        else:
            effective_date = as_of_date
        overdue = await payment_service.get_overdue_cash_receipts(
            session, cooperative_id, effective_date
        )
        if not overdue:
            return {
                "overdue_receipts": 0,
                "eligible_recipients": 0,
                "queued_notifications": 0,
            }
        event_id = daily_entity_id(cooperative_id, effective_date)
        recipients = await outbox.get_eligible_action_recipients(
            session,
            cooperative_id,
            eligibility.CASH_DEPOSIT_OVERDUE,
            event_id,
            as_of_date=effective_date,
        )
        if dry_run:
            return {
                "overdue_receipts": len(overdue),
                "eligible_recipients": len(recipients),
                "queued_notifications": 0,
            }
        await enqueue_action_notifications_safely(
            session,
            cooperative_id,
            eligibility.CASH_DEPOSIT_OVERDUE,
            event_id,
            as_of_date=effective_date,
        )
        queued = await session.scalar(
            text(
                "SELECT count(*) FROM notifications "
                "WHERE cooperative_id=:cid AND kind=:kind AND entity_id=:entity_id"
            ),
            {
                "cid": cooperative_id,
                "kind": eligibility.CASH_DEPOSIT_OVERDUE,
                "entity_id": event_id,
            },
        )
        return {
            "overdue_receipts": len(overdue),
            "eligible_recipients": len(recipients),
            "queued_notifications": int(queued or 0),
        }


async def run_overdue_cash_alerts(
    session_factory: Callable[[], AsyncSession],
    *,
    cooperative_ids: Iterable[uuid.UUID] | None = None,
    as_of_date: date | None = None,
    dry_run: bool = False,
    emit: Callable[[str], None] | None = print,
) -> list[dict[str, Any]]:
    """Process every active cooperative independently; one tenant error never stops the rest."""
    ids = (
        list(cooperative_ids)
        if cooperative_ids is not None
        else await list_active_cooperative_ids(session_factory)
    )
    results: list[dict[str, Any]] = []
    for cooperative_id in ids:
        try:
            counts = await process_cooperative(
                session_factory,
                cooperative_id,
                as_of_date=as_of_date,
                dry_run=dry_run,
            )
            result: dict[str, Any] = {
                "cooperative_id": str(cooperative_id),
                **counts,
                "dry_run": dry_run,
            }
            results.append(result)
            if emit is not None:
                emit(json.dumps(result, sort_keys=True))
        except Exception as exc:
            exception_class = type(exc).__name__
            logger.error(
                "overdue_cash_cooperative_failed cooperative_id=%s exception_class=%s",
                cooperative_id,
                exception_class,
            )
            results.append(
                {
                    "cooperative_id": str(cooperative_id),
                    "overdue_receipts": 0,
                    "eligible_recipients": 0,
                    "queued_notifications": 0,
                    "exception_class": exception_class,
                    "dry_run": dry_run,
                }
            )
    return results
