"""Evaluate backup freshness from timestamped encrypted-object names."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime, timedelta

_BACKUP_NAME = re.compile(r"^ajo-(\d{8}T\d{6}Z)\.age$")


def newest_backup(names: list[str]) -> datetime | None:
    available = set(names)
    timestamps: list[datetime] = []
    for name in names:
        match = _BACKUP_NAME.fullmatch(name)
        if match is None or f"{name}.sha256" not in available:
            continue
        try:
            timestamps.append(
                datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            )
        except ValueError:
            continue
    return max(timestamps, default=None)


def is_fresh(names: list[str], *, now: datetime, max_age_hours: int) -> bool:
    newest = newest_backup(names)
    return newest is not None and now - newest <= timedelta(hours=max_age_hours)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", required=True, type=int, help="current UTC Unix timestamp")
    parser.add_argument("--max-age-hours", type=int, default=26)
    args = parser.parse_args()
    if args.max_age_hours <= 0:
        parser.error("max-age-hours must be positive")
    names = [line.strip() for line in sys.stdin if line.strip()]
    now = datetime.fromtimestamp(args.now, tz=UTC)
    return 0 if is_fresh(names, now=now, max_age_hours=args.max_age_hours) else 1


if __name__ == "__main__":
    raise SystemExit(main())
