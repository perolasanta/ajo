from datetime import date

from ops.backup.retention import backup_date, files_to_delete


def test_backup_date_rejects_unrelated_names() -> None:
    assert backup_date("ajo-20261102T021700Z.age") == date(2026, 11, 2)
    assert backup_date("manifest.txt") is None


def test_retention_keeps_daily_weekly_and_monthly_windows() -> None:
    names = [
        f"ajo-202611{day:02d}T021700Z.age" for day in range(1, 3)
    ] + [
        "ajo-20261025T021700Z.age",
        "ajo-20260901T021700Z.age",
        "ajo-20250101T021700Z.age",
        "not-a-backup.txt",
    ]
    deleted = files_to_delete(
        names, today=date(2026, 11, 2), daily=2, weekly=1, monthly=2
    )
    assert "ajo-20250101T021700Z.age" in deleted
    assert "ajo-20261101T021700Z.age" not in deleted
    assert "ajo-20261025T021700Z.age" not in deleted
    assert "not-a-backup.txt" not in deleted


def test_zero_retention_deletes_dated_files() -> None:
    names = ["ajo-20261102T021700Z.age"]
    assert files_to_delete(names, today=date(2026, 11, 2), daily=0, weekly=0, monthly=0) == names
