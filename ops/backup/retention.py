"""Small, deterministic helpers for backup manifests and retention selection."""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterable
from datetime import date, datetime

_BACKUP_NAME = re.compile(r"^ajo-(\d{8}T\d{6}Z)\.age$")


def backup_date(name: str) -> date | None:
    match = _BACKUP_NAME.fullmatch(name)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").date()


def files_to_delete(
    names: Iterable[str], *, today: date, daily: int, weekly: int, monthly: int
) -> list[str]:
    """Return dated backup names outside daily/weekly/monthly retention windows."""
    dated = [(name, backup_date(name)) for name in names]
    dated = [(name, stamp) for name, stamp in dated if stamp is not None]
    daily_dates = {today.fromordinal(today.toordinal() - offset) for offset in range(daily)}
    weeks = {(stamp.isocalendar().year, stamp.isocalendar().week) for _, stamp in dated}
    keep_weeks = sorted(
        weeks,
        key=lambda value: date.fromisocalendar(value[0], value[1], 1),
        reverse=True,
    )[:weekly]
    months = {(stamp.year, stamp.month) for _, stamp in dated}
    keep_months = sorted(months, reverse=True)[:monthly]
    keep: set[str] = set()
    for name, stamp in dated:
        if (
            stamp in daily_dates
            or (stamp.isocalendar().year, stamp.isocalendar().week) in keep_weeks
            or (stamp.year, stamp.month) in keep_months
        ):
            keep.add(name)
    return sorted(name for name, stamp in dated if name not in keep)


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--today", required=True)
    parser.add_argument("--daily", type=int, required=True)
    parser.add_argument("--weekly", type=int, required=True)
    parser.add_argument("--monthly", type=int, required=True)
    args = parser.parse_args()
    if min(args.daily, args.weekly, args.monthly) < 0:
        raise SystemExit("retention values must be non-negative")
    names = [line.strip() for line in __import__("sys").stdin if line.strip()]
    for name in files_to_delete(
        names,
        today=date.fromisoformat(args.today),
        daily=args.daily,
        weekly=args.weekly,
        monthly=args.monthly,
    ):
        print(name)


if __name__ == "__main__":
    _main()
