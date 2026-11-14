"""Bank statement CSV import parser, duplicate handling, and preview persistence."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import transaction
from app.core.errors import Conflict, Forbidden, NotFound, PayloadTooLarge, UnprocessableEntity
from app.domains.auth.service import CurrentSession
from app.domains.payments.service import confirm_payment

MAX_BANK_STATEMENT_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_BANK_STATEMENT_ROWS = 5_000
DEFAULT_BANK_STATEMENT_MATCH_WINDOW_DAYS = 5
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
                        "line_date": row.line_date,
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


async def list_statement_lines(
    current: CurrentSession,
    *,
    status: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    _role(current, {"officer", "treasurer", "admin", "auditor"})
    if page < 1 or page_size < 1:
        raise ValueError("Page and page_size must be positive")
    if status is not None and status not in {"unmatched", "suggested", "matched", "ignored"}:
        raise ValueError("Unsupported statement line status")
    async with transaction(current.cooperative_id) as session:
        conditions = "WHERE cooperative_id=:cid"
        params: dict[str, Any] = {
            "cid": current.cooperative_id,
            "offset": (page - 1) * page_size,
            "limit": page_size,
        }
        if status is not None:
            conditions += " AND status=:status"
            params["status"] = status
        rows = await session.execute(
            text(
                """SELECT id, import_id, line_date, narration, reference, credit_kobo, debit_kobo,
                    fingerprint, status, matched_payment_id, ignored_note, decided_by, created_at, updated_at
                    FROM bank_statement_lines WHERE cooperative_id=:cid AND (:status::text IS NULL OR status=:status)
                    ORDER BY line_date DESC, id DESC LIMIT :limit OFFSET :offset"""
            ),
            params,
        )
        total = await session.scalar(
            text(
                "SELECT count(*) FROM bank_statement_lines WHERE cooperative_id=:cid AND (:status::text IS NULL OR status=:status)"
            ),
            {"cid": current.cooperative_id, "status": status},
        )
    items = [
        {
            "id": str(row["id"]),
            "import_id": str(row["import_id"]),
            "line_date": row["line_date"].isoformat() if row["line_date"] is not None else None,
            "narration": row["narration"],
            "reference": row["reference"],
            "credit_kobo": int(row["credit_kobo"] or 0),
            "debit_kobo": int(row["debit_kobo"] or 0),
            "status": row["status"],
            "matched_payment_id": str(row["matched_payment_id"])
            if row["matched_payment_id"]
            else None,
            "ignored_note": row["ignored_note"],
            "decided_by": str(row["decided_by"]) if row["decided_by"] else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows.mappings().all()
    ]
    return {"items": items, "page": page, "page_size": page_size, "total": int(total or 0)}


async def get_statement_line(current: CurrentSession, line_id: uuid.UUID) -> dict[str, Any]:
    _role(current, {"officer", "treasurer", "admin", "auditor"})
    async with transaction(current.cooperative_id) as session:
        row = await session.execute(
            text(
                """SELECT id, import_id, line_date, narration, reference, credit_kobo, debit_kobo,
                    fingerprint, status, matched_payment_id, ignored_note, decided_by, created_at, updated_at
                    FROM bank_statement_lines WHERE cooperative_id=:cid AND id=:id"""
            ),
            {"cid": current.cooperative_id, "id": line_id},
        )
        record = row.mappings().one_or_none()
    if record is None:
        raise NotFound("Bank statement line not found")
    return {
        "id": str(record["id"]),
        "import_id": str(record["import_id"]),
        "line_date": record["line_date"].isoformat(),
        "narration": record["narration"],
        "reference": record["reference"],
        "credit_kobo": int(record["credit_kobo"]),
        "debit_kobo": int(record["debit_kobo"]),
        "status": record["status"],
        "matched_payment_id": str(record["matched_payment_id"])
        if record["matched_payment_id"]
        else None,
        "ignored_note": record["ignored_note"],
        "decided_by": str(record["decided_by"]) if record["decided_by"] else None,
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    }


async def _line_candidates_for_match(
    session: AsyncSession,
    cooperative_id: uuid.UUID,
    line_date: date,
    amount_kobo: int,
    window_days: int,
) -> list[dict[str, Any]]:
    start_date = line_date - timedelta(days=window_days)
    end_date = line_date + timedelta(days=window_days)
    rows = await session.execute(
        text(
            """
            SELECT p.id, p.amount_kobo, p.provider_reference, p.recorded_by, p.received_on,
                   m.full_name AS member_name
            FROM payments p
            JOIN members m ON m.cooperative_id=p.cooperative_id AND m.id=p.member_id
            WHERE p.cooperative_id=:cid
              AND p.channel='bank_transfer'
              AND p.status='pending'
              AND p.amount_kobo=:amount_kobo
              AND p.received_on BETWEEN :start_date AND :end_date
            ORDER BY p.received_on, p.id
            """
        ),
        {
            "cid": cooperative_id,
            "amount_kobo": amount_kobo,
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    return [dict(row) for row in rows.mappings().all()]


async def auto_match_statement_lines(
    current: CurrentSession,
    *,
    window_days: int = DEFAULT_BANK_STATEMENT_MATCH_WINDOW_DAYS,
) -> dict[str, Any]:
    _role(current, {"officer", "treasurer"})
    if window_days < 0:
        raise ValueError("Match window must be non-negative")
    async with transaction(current.cooperative_id) as session:
        rows = await session.execute(
            text(
                """
                SELECT id, line_date, narration, reference, credit_kobo, status, matched_payment_id
                FROM bank_statement_lines
                WHERE cooperative_id=:cid AND status IN ('unmatched','suggested') AND credit_kobo > 0
                ORDER BY line_date, id
                """
            ),
            {"cid": current.cooperative_id},
        )
        all_rows = rows.mappings().all()
        used_payments: set[uuid.UUID] = set()
        matched_count = 0
        ambiguous_count = 0
        for row in all_rows:
            line_date = row["line_date"]
            amount_kobo = int(row["credit_kobo"])
            candidates = []
            for payment in await _line_candidates_for_match(
                session, current.cooperative_id, line_date, amount_kobo, window_days
            ):
                if payment["id"] in used_payments:
                    continue
                reference_match = False
                if row["reference"] and payment["provider_reference"]:
                    reference_match = _normalize_reference(
                        str(row["reference"])
                    ) == _normalize_reference(str(payment["provider_reference"]))
                member_match = False
                if row["narration"] and payment["member_name"]:
                    member_match = _normalize_reference(
                        str(payment["member_name"])
                    ) in _normalize_reference(str(row["narration"])) or _normalize_reference(
                        str(row["narration"])
                    ) in _normalize_reference(str(payment["member_name"]))
                if reference_match or member_match:
                    candidates.append((payment, True))
                else:
                    candidates.append((payment, False))
            if len(candidates) == 1:
                payment = candidates[0][0]
                used_payments.add(payment["id"])
                await session.execute(
                    text(
                        "UPDATE bank_statement_lines SET status=:status, matched_payment_id=:payment_id, decided_by=NULL, ignored_note=NULL, updated_at=now() WHERE cooperative_id=:cid AND id=:id"
                    ),
                    {
                        "cid": current.cooperative_id,
                        "id": row["id"],
                        "status": "suggested",
                        "payment_id": payment["id"],
                    },
                )
                matched_count += 1
            elif len(candidates) > 1:
                ambiguous_count += 1
                await session.execute(
                    text(
                        "UPDATE bank_statement_lines SET status='unmatched', matched_payment_id=NULL, decided_by=NULL, ignored_note=NULL, updated_at=now() WHERE cooperative_id=:cid AND id=:id"
                    ),
                    {"cid": current.cooperative_id, "id": row["id"]},
                )
    return {
        "matched_count": matched_count,
        "ambiguous_count": ambiguous_count,
        "window_days": window_days,
    }


async def accept_statement_line(
    current: CurrentSession,
    line_id: uuid.UUID,
    *,
    idempotency_key: str,
    payment_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    _role(current, {"officer", "treasurer"})
    if not idempotency_key.strip():
        raise ValueError("Idempotency key is required")
    async with transaction(current.cooperative_id) as session:
        row = await session.execute(
            text(
                "SELECT * FROM bank_statement_lines WHERE cooperative_id=:cid AND id=:id FOR UPDATE"
            ),
            {"cid": current.cooperative_id, "id": line_id},
        )
        record = row.mappings().one_or_none()
        if record is None:
            raise NotFound("Bank statement line not found")
        if record["status"] == "matched":
            raise Conflict("This statement line has already been matched")
        if record["status"] == "ignored":
            raise Conflict("This statement line was ignored and cannot be accepted")
        resolved_payment_id = payment_id or record["matched_payment_id"]
        if resolved_payment_id is None:
            raise Conflict("No suggested payment is available to accept")
        payment_row = await session.execute(
            text(
                "SELECT id, status, recorded_by FROM payments WHERE cooperative_id=:cid AND id=:id FOR UPDATE"
            ),
            {"cid": current.cooperative_id, "id": resolved_payment_id},
        )
        payment = payment_row.mappings().one_or_none()
        if payment is None:
            raise NotFound("Payment not found")
        if payment["status"] != "pending":
            raise Conflict(
                "This payment is no longer pending and cannot be accepted from the statement"
            )
        if payment["recorded_by"] == current.user_id:
            raise Conflict("A different user must confirm a payment")
    result = await confirm_payment(current, resolved_payment_id, idempotency_key)
    async with transaction(current.cooperative_id) as session:
        row = await session.execute(
            text(
                "SELECT * FROM bank_statement_lines WHERE cooperative_id=:cid AND id=:id FOR UPDATE"
            ),
            {"cid": current.cooperative_id, "id": line_id},
        )
        record = row.mappings().one_or_none()
        if record is None:
            raise NotFound("Bank statement line not found")
        if record["status"] == "matched":
            raise Conflict("This statement line has already been matched")
        if record["status"] == "ignored":
            raise Conflict("This statement line was ignored and cannot be accepted")
        await session.execute(
            text(
                "UPDATE bank_statement_lines SET status='matched', matched_payment_id=:payment_id, decided_by=:actor, ignored_note=NULL, updated_at=now() WHERE cooperative_id=:cid AND id=:id"
            ),
            {
                "cid": current.cooperative_id,
                "id": line_id,
                "payment_id": resolved_payment_id,
                "actor": current.user_id,
            },
        )
    return {
        "line_id": str(line_id),
        "payment_id": str(resolved_payment_id),
        "status": "matched",
        "confirmation": result,
    }


async def ignore_statement_line(
    current: CurrentSession,
    line_id: uuid.UUID,
    *,
    note: str,
) -> dict[str, Any]:
    _role(current, {"officer", "treasurer"})
    if not note.strip():
        raise ValueError("Ignore note is required")
    async with transaction(current.cooperative_id) as session:
        row = await session.execute(
            text(
                "SELECT * FROM bank_statement_lines WHERE cooperative_id=:cid AND id=:id FOR UPDATE"
            ),
            {"cid": current.cooperative_id, "id": line_id},
        )
        record = row.mappings().one_or_none()
        if record is None:
            raise NotFound("Bank statement line not found")
        if record["status"] == "matched":
            raise Conflict("This statement line has already been matched and cannot be ignored")
        if record["status"] == "ignored":
            raise Conflict("This statement line is already ignored")
        await session.execute(
            text(
                "UPDATE bank_statement_lines SET status='ignored', matched_payment_id=NULL, ignored_note=:note, decided_by=:actor, updated_at=now() WHERE cooperative_id=:cid AND id=:id"
            ),
            {
                "cid": current.cooperative_id,
                "id": line_id,
                "note": note.strip()[:500],
                "actor": current.user_id,
            },
        )
        return {"line_id": str(line_id), "status": "ignored", "note": note.strip()[:500]}


async def undo_statement_decision(current: CurrentSession, line_id: uuid.UUID) -> dict[str, Any]:
    _role(current, {"officer", "treasurer"})
    async with transaction(current.cooperative_id) as session:
        row = await session.execute(
            text(
                "SELECT * FROM bank_statement_lines WHERE cooperative_id=:cid AND id=:id FOR UPDATE"
            ),
            {"cid": current.cooperative_id, "id": line_id},
        )
        record = row.mappings().one_or_none()
        if record is None:
            raise NotFound("Bank statement line not found")
        if record["status"] not in {"matched", "ignored"}:
            raise Conflict("Only matched or ignored statement lines can be undone")
        if record["matched_payment_id"] is not None:
            payment = await session.execute(
                text("SELECT status FROM payments WHERE cooperative_id=:cid AND id=:id"),
                {"cid": current.cooperative_id, "id": record["matched_payment_id"]},
            )
            payment_record = payment.mappings().one_or_none()
            if payment_record is not None and payment_record["status"] != "pending":
                raise Conflict(
                    "This decision cannot be undone because the payment is already confirmed"
                )
        await session.execute(
            text(
                "UPDATE bank_statement_lines SET status='unmatched', matched_payment_id=NULL, ignored_note=NULL, decided_by=NULL, updated_at=now() WHERE cooperative_id=:cid AND id=:id"
            ),
            {"cid": current.cooperative_id, "id": line_id},
        )
        return {"line_id": str(line_id), "status": "unmatched"}
