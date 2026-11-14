"""Bank statement CSV import parser, duplicate handling, and preview persistence."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import transaction
from app.core.errors import Forbidden, PayloadTooLarge, UnprocessableEntity
from app.domains.auth.service import CurrentSession

MAX_BANK_STATEMENT_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_BANK_STATEMENT_ROWS = 5_000
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class ParsedStatementRow:
    row_number: int
    line_date: date
    narration: str
    reference: str | None
    credit_kobo: int
    debit_kobo: int
    fingerprint: str


def _role(current: CurrentSession, allowed: set[str]) -> str:
    role = next((item for item in current.roles if item in allowed), None)
    if role is None:
        raise Forbidden("This role cannot upload bank statements")
    return role


def _sanitize_csv_text(value: Any) -> str:
    if value is None:
        return ""
    text_value = str(value)
    if text_value.startswith(_CSV_FORMULA_PREFIXES):
        text_value = "'" + text_value
    return text_value.strip()


def _normalize_narration(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def _normalize_reference(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def _parse_date(value: Any) -> date:
    text_value = str(value).strip()
    if not text_value:
        raise ValueError("date is required")
    for fmt in (
        "%d/%m/%Y",
        "%d/%m/%y",
        "%Y-%m-%d",
        "%d-%b-%Y",
        "%d-%B-%Y",
        "%d %b %Y",
        "%d %B %Y",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(text_value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unsupported date format: {text_value!r}")


def _parse_amount(value: Any) -> int:
    if value is None:
        return 0
    text_value = str(value).strip()
    if not text_value:
        return 0
    if text_value.startswith("(") and text_value.endswith(")"):
        negative = True
        text_value = text_value[1:-1].strip()
    else:
        negative = False
    text_value = text_value.replace(" ", "").replace(",", "").replace("₦", "").replace("NGN", "")
    if text_value in {"", "+", "-"}:
        return 0
    try:
        decimal_value = Decimal(text_value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid amount {value!r}") from exc
    if decimal_value.is_nan() or decimal_value.is_infinite():
        raise ValueError(f"Invalid amount {value!r}")
    result = int((decimal_value * 100).to_integral_value(ROUND_HALF_UP))
    if negative:
        result = -result
    return result


def _read_statement_csv(
    content: bytes, *, column_map: dict[str, str]
) -> tuple[list[ParsedStatementRow], list[dict[str, object]]]:
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UnprocessableEntity("Bank statement CSV must be UTF-8 encoded.") from exc

    reader = csv.DictReader(io.StringIO(decoded))
    if reader.fieldnames is None:
        raise UnprocessableEntity("Bank statement file is missing a header row.")

    rows: list[ParsedStatementRow] = []
    errors: list[dict[str, object]] = []
    required = {"date", "narration"}
    present = {key for key in required if column_map.get(key)}
    missing = required - present
    if missing:
        raise UnprocessableEntity("Missing required columns: " + ", ".join(sorted(missing)))

    for row_number, row in enumerate(reader, start=2):
        if row is None:
            continue
        if not any((value or "").strip() for value in row.values() if value is not None):
            continue
        if len(rows) >= MAX_BANK_STATEMENT_ROWS:
            raise UnprocessableEntity(
                f"Bank statement files may contain at most {MAX_BANK_STATEMENT_ROWS:,} rows."
            )
        try:
            date_value = row.get(column_map["date"])
            narration_value = row.get(column_map["narration"])
            if date_value is None or narration_value is None:
                raise ValueError("date and narration are required for every row")
            line_date = _parse_date(date_value)
            narration = _sanitize_csv_text(narration_value)
            reference_raw = (
                row.get(column_map.get("reference")) if column_map.get("reference") else None
            )
            reference = (
                _sanitize_csv_text(reference_raw) if reference_raw not in (None, "") else None
            )

            credit_kobo = 0
            debit_kobo = 0
            if column_map.get("amount"):
                amount_str = row.get(column_map["amount"])
                amount_kobo = _parse_amount(amount_str)
                if amount_kobo >= 0:
                    credit_kobo = amount_kobo
                else:
                    debit_kobo = abs(amount_kobo)
            else:
                credit_field = column_map.get("credit")
                debit_field = column_map.get("debit")
                if credit_field:
                    credit_kobo = _parse_amount(row.get(credit_field))
                if debit_field:
                    debit_kobo = _parse_amount(row.get(debit_field))
            if credit_kobo <= 0 and debit_kobo <= 0:
                raise ValueError("amount or credit/debit value must be positive")
            fingerprint = hashlib.sha256(
                (
                    f"{line_date.isoformat()}|{_normalize_narration(narration)}|"
                    f"{_normalize_reference(reference)}|{max(credit_kobo, debit_kobo)}"
                ).encode()
            ).hexdigest()
            rows.append(
                ParsedStatementRow(
                    row_number=row_number,
                    line_date=line_date,
                    narration=narration,
                    reference=reference,
                    credit_kobo=max(credit_kobo, 0),
                    debit_kobo=max(debit_kobo, 0),
                    fingerprint=fingerprint,
                )
            )
        except ValueError as exc:
            errors.append({"row": row_number, "error": str(exc)})
    return rows, errors


async def _existing_file_hash(
    session: AsyncSession, cooperative_id: uuid.UUID, file_hash: str
) -> bool:
    result = await session.execute(
        text(
            "SELECT EXISTS(SELECT 1 FROM bank_statement_imports WHERE cooperative_id=:cid AND file_hash=:file_hash)"
        ),
        {"cid": cooperative_id, "file_hash": file_hash},
    )
    return bool(result.scalar_one())


async def preview_statement_import(
    current: CurrentSession,
    *,
    content: bytes,
    filename: str,
    column_map: dict[str, str],
    preview_limit: int = 10,
) -> dict[str, Any]:
    _role(current, {"officer", "treasurer"})
    if len(content) > MAX_BANK_STATEMENT_UPLOAD_BYTES:
        raise PayloadTooLarge("Bank statement upload exceeds the 2 MiB limit.")
    rows, errors = _read_statement_csv(content, column_map=column_map)
    file_hash = hashlib.sha256(content).hexdigest()
    preview_rows = [
        {
            "row": row.row_number,
            "date": row.line_date.isoformat(),
            "narration": row.narration,
            "reference": row.reference,
            "credit_kobo": row.credit_kobo,
            "debit_kobo": row.debit_kobo,
            "fingerprint": row.fingerprint,
        }
        for row in rows[:preview_limit]
    ]
    async with transaction(current.cooperative_id) as session:
        duplicate = await _existing_file_hash(session, current.cooperative_id, file_hash)
    return {
        "file_sha256": file_hash,
        "filename": filename,
        "total_rows": len(rows),
        "preview": preview_rows,
        "errors": errors,
        "duplicate_file": duplicate,
    }


async def import_statement_file(
    current: CurrentSession,
    *,
    content: bytes,
    filename: str,
    column_map: dict[str, str],
) -> dict[str, Any]:
    _role(current, {"officer", "treasurer"})
    if len(content) > MAX_BANK_STATEMENT_UPLOAD_BYTES:
        raise PayloadTooLarge("Bank statement upload exceeds the 2 MiB limit.")
    rows, errors = _read_statement_csv(content, column_map=column_map)
    file_hash = hashlib.sha256(content).hexdigest()
    if not rows and errors:
        return {
            "file_sha256": file_hash,
            "filename": filename,
            "inserted_count": 0,
            "skipped_count": 0,
            "errors": errors,
        }

    async with transaction(current.cooperative_id) as session:
        duplicate = await _existing_file_hash(session, current.cooperative_id, file_hash)
        if duplicate:
            return {
                "file_sha256": file_hash,
                "filename": filename,
                "inserted_count": 0,
                "skipped_count": len(rows),
                "errors": errors,
                "duplicate_file": True,
            }

        fingerprint_rows = [row.fingerprint for row in rows]
        existing_fingerprints = await session.execute(
            text(
                "SELECT fingerprint FROM bank_statement_lines WHERE cooperative_id=:cid AND fingerprint = ANY(:fingerprints)"
            ),
            {"cid": current.cooperative_id, "fingerprints": fingerprint_rows},
        )
        seen = {item["fingerprint"] for item in existing_fingerprints.mappings().all()}
        new_rows = [row for row in rows if row.fingerprint not in seen]
        import_id = uuid.uuid4()
        await session.execute(
            text(
                """
                INSERT INTO bank_statement_imports(
                    id, cooperative_id, file_hash, filename, uploaded_by, row_count, inserted_count, skipped_count
                ) VALUES (:id, :cid, :file_hash, :filename, :uploaded_by, :row_count, :inserted_count, :skipped_count)
                """
            ),
            {
                "id": import_id,
                "cid": current.cooperative_id,
                "file_hash": file_hash,
                "filename": filename,
                "uploaded_by": current.user_id,
                "row_count": len(rows),
                "inserted_count": len(new_rows),
                "skipped_count": len(rows) - len(new_rows),
            },
        )
        if new_rows:
            await session.execute(
                text(
                    """
                    INSERT INTO bank_statement_lines (
                        id, cooperative_id, import_id, line_date, narration, reference,
                        credit_kobo, debit_kobo, fingerprint, status
                    ) VALUES (:id, :cid, :import_id, :line_date, :narration, :reference, :credit_kobo, :debit_kobo, :fingerprint, 'unmatched')
                    """
                ),
                [
                    {
                        "id": uuid.uuid4(),
                        "cid": current.cooperative_id,
                        "import_id": import_id,
                        "line_date": row.line_date.isoformat(),
                        "narration": row.narration,
                        "reference": row.reference,
                        "credit_kobo": row.credit_kobo,
                        "debit_kobo": row.debit_kobo,
                        "fingerprint": row.fingerprint,
                    }
                    for row in new_rows
                ],
            )
        return {
            "file_sha256": file_hash,
            "filename": filename,
            "inserted_count": len(new_rows),
            "skipped_count": len(rows) - len(new_rows),
            "errors": errors,
            "duplicate_file": False,
        }
