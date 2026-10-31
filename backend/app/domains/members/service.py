"""Tenant-scoped member and savings operations."""

from __future__ import annotations

import csv
import hmac
import io
import json
import uuid
from datetime import date
from typing import Any

from sqlalchemy import text

from app.core.db import lock_cooperative_member, transaction
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.security import _fernet
from app.domains.audit.service import append_audit
from app.domains.auth.service import CurrentSession


def encrypt_account_number(account_number: str) -> str:
    digits = "".join(character for character in account_number if character.isdigit())
    if len(digits) < 4:
        raise ValueError("bank account number is invalid")
    return _fernet().encrypt(digits.encode()).decode("ascii")


async def record_contribution(
    current: CurrentSession,
    member_id: uuid.UUID,
    kind: str,
    amount_kobo: int,
    idempotency_key: str,
) -> uuid.UUID:
    if not set(current.roles).intersection({"officer", "treasurer", "admin"}):
        raise Forbidden("Officer, treasurer or admin role required")
    if kind not in {"savings", "shares", "dues"} or amount_kobo <= 0:
        raise ValueError("contribution is invalid")
    if not idempotency_key.strip():
        raise ValueError("idempotency key is required")
    async with transaction(current.cooperative_id) as session:
        request_payload = {
            "operation": "member_contribution",
            "member_id": str(member_id),
            "kind": kind,
            "amount_kobo": amount_kobo,
        }
        payload_json = json.dumps(request_payload, separators=(",", ":"))
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 12001))"),
            {"key": f"{current.cooperative_id}:contribution:{idempotency_key}"},
        )
        request_hash = await session.scalar(
            text("SELECT canonical_request_hash(CAST(:payload AS jsonb))"),
            {"payload": payload_json},
        )
        existing = (
            (
                await session.execute(
                    text(
                        "SELECT id,request_hash FROM journal_entries WHERE cooperative_id=:cid AND idempotency_key=:key"
                    ),
                    {"cid": current.cooperative_id, "key": idempotency_key},
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            if not hmac.compare_digest(bytes(existing["request_hash"]), bytes(request_hash)):
                raise Conflict("Idempotency key was reused with a different payload")
            return uuid.UUID(str(existing["id"]))
        accounts = await session.execute(
            text(
                "SELECT code,id FROM accounts WHERE cooperative_id=:cid AND code IN ('bank', :target)"
            ),
            {
                "cid": current.cooperative_id,
                "target": {
                    "savings": "member_savings",
                    "shares": "member_shares",
                    "dues": "dues_income",
                }[kind],
            },
        )
        ids = {row.code: row.id for row in accounts}
        if len(ids) != 2:
            raise Conflict("Default chart of accounts is incomplete")
        entry = (
            await session.execute(
                text("""SELECT post_journal_entry_with_payload(
            :cid,:effective,'member_contribution',:member,:description,:actor,:key,
            CAST(:lines AS jsonb),CAST(:request_payload AS jsonb),NULL)"""),
                {
                    "cid": current.cooperative_id,
                    "effective": date.today(),
                    "member": member_id,
                    "description": f"Member {kind} contribution",
                    "actor": current.user_id,
                    "key": idempotency_key,
                    "request_payload": payload_json,
                    "lines": json.dumps(
                        [
                            {
                                "account_id": str(ids["bank"]),
                                "member_id": str(member_id),
                                "debit_kobo": amount_kobo,
                            },
                            {
                                "account_id": str(
                                    ids[
                                        {
                                            "savings": "member_savings",
                                            "shares": "member_shares",
                                            "dues": "dues_income",
                                        }[kind]
                                    ]
                                ),
                                "member_id": str(member_id),
                                "credit_kobo": amount_kobo,
                            },
                        ]
                    ),
                },
            )
        ).scalar_one()
        await append_audit(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            role=current.roles[0] if current.roles else None,
            action="member_contribution_recorded",
            entity_type="journal_entry",
            entity_id=str(entry),
            summary={"kind": kind, "amount_kobo": amount_kobo, "member_id": str(member_id)},
            channel="api",
            ip=None,
        )
        return uuid.UUID(str(entry))


async def member_balances(cooperative_id: uuid.UUID, member_id: uuid.UUID) -> dict[str, int]:
    async with transaction(cooperative_id) as session:
        result = await session.execute(
            text("""SELECT a.code,
            coalesce(sum(l.credit_kobo-l.debit_kobo),0)::bigint AS amount
          FROM journal_lines l JOIN accounts a
            ON a.cooperative_id=l.cooperative_id AND a.id=l.account_id
         WHERE l.cooperative_id=:cid AND l.member_id=:mid
           AND a.code IN ('member_savings','member_shares','dues_income') GROUP BY a.code"""),
            {"cid": cooperative_id, "mid": member_id},
        )
        amounts = {row.code: int(row.amount) for row in result}
        locked = await session.execute(
            text("""SELECT coalesce(sum(amount_kobo),0)::bigint
          FROM savings_locks WHERE cooperative_id=:cid AND member_id=:mid AND released_at IS NULL"""),
            {"cid": cooperative_id, "mid": member_id},
        )
        savings = amounts.get("member_savings", 0)
        locked_amount = int(locked.scalar_one())
        return {
            "savings_kobo": savings,
            "shares_kobo": amounts.get("member_shares", 0),
            "dues_paid_kobo": amounts.get("dues_income", 0),
            "locked_kobo": locked_amount,
            "available_kobo": savings - locked_amount,
        }


async def request_withdrawal(
    current: CurrentSession, member_id: uuid.UUID, amount_kobo: int, idempotency_key: str
) -> uuid.UUID:
    if not set(current.roles).intersection({"officer", "treasurer", "admin"}):
        raise Forbidden("Officer, treasurer or admin role required")
    if amount_kobo <= 0:
        raise ValueError("amount must be positive")
    if not idempotency_key.strip():
        raise ValueError("idempotency key is required")
    async with transaction(current.cooperative_id) as session:
        payload_json = json.dumps(
            {
                "operation": "withdrawal_request",
                "member_id": str(member_id),
                "amount_kobo": amount_kobo,
            },
            separators=(",", ":"),
        )
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 12002))"),
            {"key": f"{current.cooperative_id}:withdrawal:{idempotency_key}"},
        )
        request_hash = await session.scalar(
            text("SELECT canonical_request_hash(CAST(:payload AS jsonb))"),
            {"payload": payload_json},
        )
        existing = (
            (
                await session.execute(
                    text(
                        "SELECT id,request_hash FROM withdrawal_requests WHERE cooperative_id=:cid AND idempotency_key=:key"
                    ),
                    {"cid": current.cooperative_id, "key": idempotency_key},
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            if not hmac.compare_digest(bytes(existing["request_hash"]), bytes(request_hash)):
                raise Conflict("Idempotency key was reused with a different payload")
            return uuid.UUID(str(existing["id"]))
        await lock_cooperative_member(session, current.cooperative_id, member_id)
        balance = (
            await session.execute(
                text("""SELECT coalesce(sum(l.credit_kobo-l.debit_kobo),0)
          FROM journal_lines l JOIN accounts a ON a.cooperative_id=l.cooperative_id AND a.id=l.account_id
          WHERE l.cooperative_id=:cid AND l.member_id=:mid AND a.code='member_savings'"""),
                {"cid": current.cooperative_id, "mid": member_id},
            )
        ).scalar_one()
        locked = (
            await session.execute(
                text("""SELECT coalesce(sum(amount_kobo),0) FROM savings_locks
          WHERE cooperative_id=:cid AND member_id=:mid AND released_at IS NULL"""),
                {"cid": current.cooperative_id, "mid": member_id},
            )
        ).scalar_one()
        if amount_kobo > int(balance) - int(locked):
            raise Conflict("Withdrawal exceeds available savings")
        result = await session.execute(
            text("""INSERT INTO withdrawal_requests
          (cooperative_id,member_id,amount_kobo,idempotency_key,request_hash,requested_by)
          VALUES (:cid,:mid,:amount,:key,:request_hash,:actor) RETURNING id"""),
            {
                "cid": current.cooperative_id,
                "mid": member_id,
                "amount": amount_kobo,
                "key": idempotency_key,
                "request_hash": request_hash,
                "actor": current.user_id,
            },
        )
        request_id = result.scalar_one_or_none()
        assert request_id is not None
        await append_audit(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            role=current.roles[0] if current.roles else None,
            action="withdrawal_requested",
            entity_type="withdrawal_request",
            entity_id=str(request_id),
            summary={"amount_kobo": amount_kobo, "member_id": str(member_id)},
            channel="api",
            ip=None,
        )
        return uuid.UUID(str(request_id))


async def approve_withdrawal(current: CurrentSession, request_id: uuid.UUID) -> uuid.UUID:
    if current.user_id is None or not set(current.roles).intersection({"treasurer", "admin"}):
        raise Forbidden("Treasurer or admin role required")
    async with transaction(current.cooperative_id) as session:
        row = (
            await session.execute(
                text("""SELECT id,member_id,amount_kobo,requested_by,status
          FROM withdrawal_requests WHERE cooperative_id=:cid AND id=:id FOR UPDATE"""),
                {"cid": current.cooperative_id, "id": request_id},
            )
        ).one_or_none()
        if row is None:
            raise NotFound("Withdrawal request not found")
        if row.requested_by == current.user_id:
            raise Conflict("A different user must approve the withdrawal")
        if row.status == "approved":
            return uuid.UUID(
                str(
                    (
                        await session.execute(
                            text(
                                "SELECT journal_entry_id FROM withdrawal_requests WHERE cooperative_id=:cid AND id=:id"
                            ),
                            {"cid": current.cooperative_id, "id": request_id},
                        )
                    ).scalar_one()
                )
            )
        await lock_cooperative_member(session, current.cooperative_id, row.member_id)
        balance = (
            await session.execute(
                text("""SELECT coalesce(sum(l.credit_kobo-l.debit_kobo),0)
          FROM journal_lines l JOIN accounts a ON a.cooperative_id=l.cooperative_id AND a.id=l.account_id
          WHERE l.cooperative_id=:cid AND l.member_id=:mid AND a.code='member_savings'"""),
                {"cid": current.cooperative_id, "mid": row.member_id},
            )
        ).scalar_one()
        locked = (
            await session.execute(
                text(
                    "SELECT coalesce(sum(amount_kobo),0) FROM savings_locks WHERE cooperative_id=:cid AND member_id=:mid AND released_at IS NULL"
                ),
                {"cid": current.cooperative_id, "mid": row.member_id},
            )
        ).scalar_one()
        if int(row.amount_kobo) > int(balance) - int(locked):
            raise Conflict("Withdrawal exceeds available savings")
        accounts = (
            await session.execute(
                text(
                    "SELECT code,id FROM accounts WHERE cooperative_id=:cid AND code IN ('bank','member_savings')"
                ),
                {"cid": current.cooperative_id},
            )
        ).all()
        ids = {item.code: item.id for item in accounts}
        entry = (
            await session.execute(
                text("""SELECT post_journal_entry(:cid,:effective,'withdrawal',:source,
          'Member savings withdrawal',:actor,:key,CAST(:lines AS jsonb),NULL)"""),
                {
                    "cid": current.cooperative_id,
                    "effective": date.today(),
                    "source": request_id,
                    "actor": current.user_id,
                    "key": f"withdrawal:{request_id}",
                    "lines": json.dumps(
                        [
                            {
                                "account_id": str(ids["member_savings"]),
                                "member_id": str(row.member_id),
                                "debit_kobo": row.amount_kobo,
                            },
                            {
                                "account_id": str(ids["bank"]),
                                "member_id": str(row.member_id),
                                "credit_kobo": row.amount_kobo,
                            },
                        ]
                    ),
                },
            )
        ).scalar_one()
        await session.execute(
            text(
                "UPDATE withdrawal_requests SET status='approved',approved_by=:actor,journal_entry_id=:entry,resolved_at=now() WHERE cooperative_id=:cid AND id=:id"
            ),
            {
                "actor": current.user_id,
                "entry": entry,
                "cid": current.cooperative_id,
                "id": request_id,
            },
        )
        await append_audit(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            role=current.roles[0],
            action="withdrawal_approved",
            entity_type="withdrawal_request",
            entity_id=str(request_id),
            summary={"amount_kobo": int(row.amount_kobo)},
            channel="api",
            ip=None,
        )
        return uuid.UUID(str(entry))


def validate_member_csv(csv_text: str) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    required = {"member_no", "full_name", "phone_e164"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        return [], [{"row": 1, "error": "CSV requires member_no, full_name, phone_e164 columns"}]
    valid: list[dict[str, str]] = []
    errors: list[dict[str, Any]] = []
    for line, row in enumerate(reader, start=2):
        normalized = {key: (value or "").strip() for key, value in row.items() if key}
        if (
            not normalized.get("member_no")
            or not normalized.get("full_name")
            or not normalized.get("phone_e164", "").startswith("+")
        ):
            errors.append(
                {"row": line, "error": "member_no, full_name and E.164 phone are required"}
            )
        else:
            valid.append(normalized)
    return valid, errors


async def import_members(current: CurrentSession, csv_text: str, confirm: bool) -> dict[str, Any]:
    if not set(current.roles).intersection({"officer", "admin"}):
        raise Forbidden("Officer or admin role required")
    rows, errors = validate_member_csv(csv_text)
    seen: set[str] = set()
    for index, row in enumerate(rows, start=2):
        if row["member_no"] in seen:
            errors.append({"row": index, "error": "duplicate member_no in CSV"})
        seen.add(row["member_no"])
    if not confirm or errors:
        return {"imported": 0, "errors": errors, "valid_rows": len(rows)}
    async with transaction(current.cooperative_id) as session:
        existing = await session.execute(
            text(
                "SELECT member_no FROM members WHERE cooperative_id=:cid AND member_no=ANY(:numbers)"
            ),
            {"cid": current.cooperative_id, "numbers": [row["member_no"] for row in rows]},
        )
        existing_numbers = {row.member_no for row in existing}
        if existing_numbers:
            return {
                "imported": 0,
                "errors": [{"row": 0, "error": "member_no already exists"}],
                "valid_rows": 0,
            }
        created: list[dict[str, str]] = []
        for row in rows:
            member_id = await session.scalar(
                text("""INSERT INTO members(cooperative_id,member_no,full_name,phone_e164,email,staff_no,employer,status,joined_on)
              VALUES(:cid,:no,:name,:phone,:email,:staff,:employer,'active',:joined) RETURNING id"""),
                {
                    "cid": current.cooperative_id,
                    "no": row["member_no"],
                    "name": row["full_name"],
                    "phone": row["phone_e164"],
                    "email": row.get("email") or None,
                    "staff": row.get("staff_no") or None,
                    "employer": row.get("employer") or None,
                    "joined": date.fromisoformat(row["joined_on"]) if row.get("joined_on") else None,
                },
            )
            created.append({"member_no": row["member_no"], "id": str(member_id)})
        await append_audit(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            role=current.roles[0] if current.roles else None,
            action="members_imported",
            entity_type="members",
            entity_id=None,
            summary={"count": len(rows)},
            channel="api",
            ip=None,
        )
    return {
        "imported": len(rows),
        "errors": [],
        "valid_rows": len(rows),
        "members": created,
    }
