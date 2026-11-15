from __future__ import annotations

import importlib
import json
import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.scheduler import __main__ as scheduler_main

pytestmark = pytest.mark.unit


def test_scheduler_interval_defaults_to_sixty_minutes() -> None:
    settings = Settings(_env_file=None, app_env="development")
    assert settings.scheduler_interval_minutes == 60


def test_run_once_cli_accepts_as_of_and_dry_run() -> None:
    args = scheduler_main.parse_args(
        ["--run-once", "--as-of", "2026-12-01", "--dry-run"]
    )
    assert args.run_once is True
    assert args.as_of == date(2026, 12, 1)
    assert args.dry_run is True


def test_overdue_cash_rule_is_shared_and_uses_strict_wat_day_comparison() -> None:
    rules = importlib.import_module("app.domains.payments.cash_rules")
    assert "cash_banking_days" in rules.CASH_BANKING_POLICY_LATERAL_SQL
    assert "COALESCE" in rules.CASH_BANKING_POLICY_LATERAL_SQL
    assert "<" in rules.CASH_OVERDUE_PREDICATE_SQL
    assert ":as_of_date" in rules.CASH_OVERDUE_PREDICATE_SQL
    assert "Africa/Lagos" in rules.CASH_OVERDUE_PREDICATE_SQL


def test_daily_entity_id_is_stable_per_cooperative_and_day() -> None:
    scheduler = importlib.import_module("app.scheduler.overdue_cash")
    cooperative_id = uuid.uuid4()
    day = date(2026, 12, 1)
    assert scheduler.daily_entity_id(cooperative_id, day) == scheduler.daily_entity_id(
        cooperative_id, day
    )
    assert scheduler.daily_entity_id(cooperative_id, day) != scheduler.daily_entity_id(
        cooperative_id, day + timedelta(days=1)
    )
    assert scheduler.daily_entity_id(cooperative_id, day) != scheduler.daily_entity_id(
        uuid.uuid4(), day
    )


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def execute(self, *_args: object, **_kwargs: object) -> None:
        return None


class _Factory:
    def __call__(self) -> _Session:
        return _Session()


@pytest.mark.asyncio
async def test_dry_run_prints_only_counts_and_never_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = importlib.import_module("app.scheduler.overdue_cash")
    cooperative_id = uuid.uuid4()
    as_of = date(2026, 12, 1)

    async def overdue_receipts(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return [
            {
                "id": uuid.uuid4(),
                "recorded_by": uuid.uuid4(),
                "member_name": "Sensitive Member Name",
                "amount_kobo": 125000,
            }
        ]

    async def recipients(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return (object(),)

    enqueue = AsyncMock()
    monkeypatch.setattr(scheduler.payment_service, "get_overdue_cash_receipts", overdue_receipts)
    monkeypatch.setattr(scheduler.outbox, "get_eligible_action_recipients", recipients)
    monkeypatch.setattr(scheduler.outbox, "enqueue_action_notifications_safely", enqueue)
    output: list[str] = []

    results = await scheduler.run_overdue_cash_alerts(
        _Factory(),
        cooperative_ids=[cooperative_id],
        as_of_date=as_of,
        dry_run=True,
        emit=output.append,
    )

    assert enqueue.await_count == 0
    assert results[0]["overdue_receipts"] == 1
    assert results[0]["eligible_recipients"] == 1
    assert results[0]["queued_notifications"] == 0
    assert len(output) == 1
    assert "Sensitive Member Name" not in output[0]
    assert "125000" not in output[0]


@pytest.mark.asyncio
async def test_scheduler_continues_after_one_cooperative_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = importlib.import_module("app.scheduler.overdue_cash")
    failed_id, good_id = uuid.uuid4(), uuid.uuid4()
    processed: list[uuid.UUID] = []

    async def process(_factory: object, cooperative_id: uuid.UUID, **_kwargs: object) -> dict[str, int]:
        processed.append(cooperative_id)
        if cooperative_id == failed_id:
            raise RuntimeError("Sensitive Member Name amount 125000")
        return {
            "overdue_receipts": 1,
            "eligible_recipients": 1,
            "queued_notifications": 1,
        }

    monkeypatch.setattr(scheduler, "process_cooperative", process)
    output: list[str] = []
    results = await scheduler.run_overdue_cash_alerts(
        _Factory(),
        cooperative_ids=[failed_id, good_id],
        as_of_date=date(2026, 12, 1),
        emit=output.append,
    )

    assert processed == [failed_id, good_id]
    assert len(results) == 2
    assert results[0]["exception_class"] == "RuntimeError"
    assert results[1]["queued_notifications"] == 1
    assert all("Sensitive Member Name" not in line for line in output)
    assert all("125000" not in line for line in output)
