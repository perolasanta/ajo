"""Public, tenant-scoped member consent operations."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from cryptography.fernet import Fernet
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import transaction
from app.core.errors import NotFound, TooManyRequests
from app.domains.audit.service import append_audit
from app.domains.auth.service import resolve_tenant_slug


class OtpSender(Protocol):
    async def send(self, destination: str, code: str) -> None: ...


class ConsoleOtpSender:
    async def send(self, destination: str, code: str) -> None:
        if get_settings().app_env != "development":
            raise RuntimeError("Console OTP delivery is development-only")
        print(f"Ajo confirmation OTP for {destination}: {code}")


def _otp_hash(consent_id: uuid.UUID, code: str) -> bytes:
    key = get_settings().secret_key.encode()
    return hmac.digest(key, f"{consent_id}:{code}".encode(), "sha256")


def _encrypt(payload: dict[str, Any]) -> str:
    key = get_settings().encryption_key
    if not key:
        raise RuntimeError("ENCRYPTION_KEY is required for notification outbox")
    return Fernet(key.encode()).encrypt(json.dumps(payload).encode()).decode("ascii")


async def create_consent(
    session: Any, *, cooperative_id: uuid.UUID, application_id: uuid.UUID,
    member_id: uuid.UUID, kind: str, terms_hash: bytes | None, slug: str,
    delivery_observer: Callable[[str], None] | None = None,
) -> uuid.UUID:
    row = (await session.execute(text("""SELECT confirmation_expiry_minutes FROM cooperative_policy_versions
        WHERE cooperative_id=:cid AND id=(SELECT policy_version_id FROM loan_applications
        WHERE cooperative_id=:cid AND id=:app)"""), {"cid": cooperative_id, "app": application_id})).one()
    token = secrets.token_bytes(32)
    consent_id = uuid.uuid4()
    expires = datetime.now(UTC) + timedelta(minutes=row.confirmation_expiry_minutes)
    await session.execute(text("""INSERT INTO member_consents
        (id,cooperative_id,application_id,member_id,kind,terms_hash,token_hash,expires_at,consent_text_version)
        VALUES(:id,:cid,:app,:member,:kind,:terms,:token,:expires,'v1')"""),
        {"id": consent_id, "cid": cooperative_id, "app": application_id, "member": member_id,
         "kind": kind, "terms": terms_hash, "token": hashlib.sha256(token).digest(), "expires": expires})
    await session.execute(text("""INSERT INTO notifications(cooperative_id,member_id,kind,payload_encrypted)
        VALUES(:cid,:member,'confirmation_request',:payload)"""),
        {"cid": cooperative_id, "member": member_id,
         "payload": _encrypt({"url": f"/api/v1/confirm/{slug}/{token.hex()}", "consent_id": str(consent_id)})})
    # The observer is only for trusted in-process delivery adapters (the demo seeder).
    # It is invoked before the transaction commits and must never persist or log the token.
    if delivery_observer is not None:
        delivery_observer(token.hex())
    return consent_id


async def _lookup(slug: str, token: str) -> tuple[uuid.UUID, bytes] | None:
    tenant = await resolve_tenant_slug(slug)
    if tenant is None or tenant[1] != "active":
        return None
    try:
        raw = bytes.fromhex(token)
        if len(raw) != 32:
            return None
    except ValueError:
        return None
    return tenant[0], hashlib.sha256(raw).digest()


async def _consume_otp_send_limits(
    session: Any,
    cid: uuid.UUID,
    consent_id: uuid.UUID,
    destination: str,
    ip: str,
) -> None:
    secret = get_settings().secret_key.encode()
    try:
        normalized_ip = ipaddress.ip_address(ip).compressed
    except ValueError:
        normalized_ip = ip.strip()
    values = (
        ("consent", str(consent_id), 3),
        ("destination", destination.strip(), 3),
        ("ip", normalized_ip, 10),
    )
    checks = [
        (dimension, hmac.digest(secret, f"{dimension}:{value}".encode(), "sha256"), limit)
        for dimension, value, limit in values
    ]
    checks.sort(key=lambda check: (check[0], check[1]))
    for dimension, key_hash, _ in checks:
        lock_key = f"consent-otp:{cid}:{dimension}:{key_hash.hex()}"
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )

    counters: dict[str, tuple[bytes, int, Any]] = {}
    for dimension, key_hash, limit in checks:
        row = (
            await session.execute(
                text("""SELECT window_started_at,attempts FROM consent_rate_limits
                  WHERE cooperative_id=:cid AND dimension=:dimension AND key_hash=:key_hash
                    AND window_started_at >= clock_timestamp() - interval '1 hour'
                  ORDER BY window_started_at DESC LIMIT 1 FOR UPDATE"""),
                {"cid": cid, "dimension": dimension, "key_hash": key_hash},
            )
        ).mappings().one_or_none()
        if row is not None and row["attempts"] >= limit:
            raise TooManyRequests("OTP send limit exceeded")
        counters[dimension] = (key_hash, limit, row)

    for dimension, (key_hash, _, row) in counters.items():
        if row is None:
            await session.execute(
                text("""INSERT INTO consent_rate_limits
                  (cooperative_id,dimension,key_hash,window_started_at,attempts)
                  VALUES(:cid,:dimension,:key_hash,clock_timestamp(),1)"""),
                {"cid": cid, "dimension": dimension, "key_hash": key_hash},
            )
        else:
            await session.execute(
                text("""UPDATE consent_rate_limits SET attempts=attempts+1
                  WHERE cooperative_id=:cid AND dimension=:dimension AND key_hash=:key_hash
                    AND window_started_at=:window_started_at"""),
                {
                    "cid": cid,
                    "dimension": dimension,
                    "key_hash": key_hash,
                    "window_started_at": row["window_started_at"],
                },
            )


async def view(slug: str, token: str) -> dict[str, Any]:
    pair = await _lookup(slug, token)
    if pair is None:
        raise NotFound("Confirmation unavailable")
    cid, token_hash = pair
    async with transaction(cid) as session:
        row = (await session.execute(text("""SELECT c.kind,c.status,c.expires_at,co.name AS cooperative_name
          FROM member_consents c JOIN cooperatives co ON co.id=c.cooperative_id
          WHERE c.cooperative_id=:cid AND c.token_hash=:token"""),
          {"cid": cid, "token": token_hash})).mappings().one_or_none()
        if row is None or row["status"] != "pending" or row["expires_at"] <= datetime.now(UTC):
            raise NotFound("Confirmation unavailable")
        return {
            "cooperative_name": row["cooperative_name"],
            "kind": row["kind"],
            "expires_at": row["expires_at"].isoformat(),
        }


async def send_otp(slug: str, token: str, ip: str, sender: OtpSender) -> None:
    pair = await _lookup(slug, token)
    if pair is None:
        raise NotFound("Confirmation unavailable")
    cid, token_hash = pair
    async with transaction(cid) as session:
        row = (await session.execute(text("""SELECT c.id,c.member_id,c.status,c.expires_at,
          c.otp_attempts,p.otp_validity_seconds,m.phone_e164 FROM member_consents c
          JOIN loan_applications a ON a.cooperative_id=c.cooperative_id AND a.id=c.application_id
          JOIN cooperative_policy_versions p ON p.cooperative_id=a.cooperative_id AND p.id=a.policy_version_id
          JOIN members m ON m.cooperative_id=c.cooperative_id AND m.id=c.member_id
          WHERE c.cooperative_id=:cid AND c.token_hash=:token FOR UPDATE OF c"""),
          {"cid": cid, "token": token_hash})).mappings().one_or_none()
        if (row is None or row["status"] != "pending" or row["otp_attempts"] >= 5
                or row["expires_at"] <= datetime.now(UTC)):
            raise NotFound("Confirmation unavailable")
        await _consume_otp_send_limits(session, cid, row["id"], row["phone_e164"], ip)
        code = f"{secrets.randbelow(1_000_000):06d}"
        await session.execute(text("""UPDATE member_consents SET otp_hash=:hash,
          otp_expires_at=now()+(:seconds * interval '1 second'),channel='sms'
          WHERE cooperative_id=:cid AND id=:id"""),
          {"hash": _otp_hash(row["id"], code), "seconds": row["otp_validity_seconds"], "cid": cid, "id": row["id"]})
    # Delivery happens outside the database transaction; sender failures do not leak token data.
    await sender.send(row["phone_e164"], code)


async def verify_otp(slug: str, token: str, code: str, ip: str) -> dict[str, Any] | None:
    pair = await _lookup(slug, token)
    if pair is None:
        raise NotFound("Confirmation unavailable")
    cid, token_hash = pair
    async with transaction(cid) as session:
        row = (await session.execute(text("""SELECT c.id,c.kind,c.status,c.expires_at,c.otp_attempts,
          c.otp_hash,c.otp_expires_at,c.terms_hash,a.status AS application_status,
          a.current_terms_snapshot_id,s.terms,s.terms_sha256,a.member_id,b.last4,m.full_name,
          co.name AS cooperative_name
          FROM member_consents c JOIN loan_applications a ON a.cooperative_id=c.cooperative_id AND a.id=c.application_id
          LEFT JOIN loan_terms_snapshots s ON s.cooperative_id=a.cooperative_id AND s.id=a.current_terms_snapshot_id
          LEFT JOIN bank_accounts b ON b.cooperative_id=a.cooperative_id AND b.id=a.destination_bank_account_id
          JOIN members m ON m.cooperative_id=c.cooperative_id AND m.id=a.member_id
          JOIN cooperatives co ON co.id=c.cooperative_id
          WHERE c.cooperative_id=:cid AND c.token_hash=:token FOR UPDATE OF c,a"""),
          {"cid": cid, "token": token_hash})).mappings().one_or_none()
        if row is None or row["status"] != "pending" or row["expires_at"] <= datetime.now(UTC):
            raise NotFound("Confirmation unavailable")
        if (row["otp_attempts"] >= 5 or row["otp_hash"] is None or row["otp_expires_at"] is None
                or row["otp_expires_at"] <= datetime.now(UTC)):
            return None
        if not hmac.compare_digest(row["otp_hash"], _otp_hash(row["id"], code)):
            await session.execute(text("""UPDATE member_consents SET otp_attempts=otp_attempts+1,
              status=CASE WHEN otp_attempts+1 >= 5 THEN 'invalidated' ELSE status END,
              otp_hash=CASE WHEN otp_attempts+1 >= 5 THEN NULL ELSE otp_hash END
              WHERE cooperative_id=:cid AND id=:id"""), {"cid": cid, "id": row["id"]})
            return None
        if row["kind"] in {"final_terms", "beneficiary"} and not hmac.compare_digest(
            row["terms_hash"] or b"", row["terms_sha256"] or b""
        ):
            await session.execute(text("UPDATE member_consents SET status='invalidated' WHERE cooperative_id=:cid AND id=:id"),
                                  {"cid": cid, "id": row["id"]})
            return None
        await session.execute(
            text("UPDATE member_consents SET otp_hash=NULL WHERE cooperative_id=:cid AND id=:id"),
            {"cid": cid, "id": row["id"]},
        )
        return {
            "cooperative_name": row["cooperative_name"],
            "kind": row["kind"],
            "applicant": row["full_name"],
            "terms": dict(row["terms"] or {}),
            "destination_account": f"****{row['last4']}" if row["last4"] else None,
            "expires_at": row["expires_at"].isoformat(),
        }


async def decide(slug: str, token: str, decision: str, ip: str, user_agent: str) -> str:
    pair = await _lookup(slug, token)
    if pair is None or decision not in {"confirm", "reject"}:
        raise NotFound("Confirmation unavailable")
    cid, token_hash = pair
    async with transaction(cid) as session:
        row = (await session.execute(text("""SELECT c.*,a.status AS application_status,
          a.current_terms_snapshot_id,s.terms_sha256,l.id AS loan_id
          FROM member_consents c JOIN loan_applications a ON a.cooperative_id=c.cooperative_id AND a.id=c.application_id
          LEFT JOIN loan_terms_snapshots s ON s.cooperative_id=a.cooperative_id AND s.id=a.current_terms_snapshot_id
          LEFT JOIN loans l ON l.cooperative_id=a.cooperative_id AND l.application_id=a.id
          WHERE c.cooperative_id=:cid AND c.token_hash=:token FOR UPDATE OF c,a"""),
          {"cid": cid, "token": token_hash})).mappings().one_or_none()
        if row is None or row["status"] != "pending" or row["expires_at"] <= datetime.now(UTC):
            raise NotFound("Confirmation unavailable")
        if (row["otp_attempts"] >= 5 or row["otp_hash"] is not None or row["otp_expires_at"] is None
                or row["otp_expires_at"] <= datetime.now(UTC)):
            raise NotFound("Confirmation unavailable")
        if row["kind"] in {"final_terms", "beneficiary"} and not hmac.compare_digest(
            row["terms_hash"] or b"", row["terms_sha256"] or b""
        ):
            await session.execute(text("UPDATE member_consents SET status='invalidated' WHERE cooperative_id=:cid AND id=:id"), {"cid": cid, "id": row["id"]})
            return "terms_changed"
        status = "confirmed" if decision == "confirm" else "rejected"
        await session.execute(text("""UPDATE member_consents SET status=:status,otp_hash=NULL,otp_expires_at=NULL,
          decided_at=now(),channel='sms',ip=CAST(:ip AS inet),user_agent=:ua
          WHERE cooperative_id=:cid AND id=:id"""), {"status": status, "ip": ip, "ua": user_agent, "cid": cid, "id": row["id"]})
        if decision == "confirm":
            target = "under_review" if row["kind"] == "applicant_ack" else "ready_for_disbursement"
            await session.execute(text("UPDATE loan_applications SET status=:status,updated_at=now() WHERE cooperative_id=:cid AND id=:id"), {"status": target, "cid": cid, "id": row["application_id"]})
            if row["kind"] == "final_terms":
                await session.execute(text("UPDATE loans SET status='ready_for_disbursement' WHERE cooperative_id=:cid AND application_id=:id"), {"cid": cid, "id": row["application_id"]})
            await append_audit(session, cooperative_id=cid, actor_user_id=None, role=None,
                action="member_consent_confirmed", entity_type="loan_application", entity_id=str(row["application_id"]),
                summary={"consent_id": str(row["id"]), "kind": row["kind"]}, channel="sms", ip=ip)
        else:
            await session.execute(text("""INSERT INTO disputes(cooperative_id,application_id,loan_id,member_id,
              opened_via,reason) VALUES(:cid,:app,:loan,:member,'signed_link','This is not mine')"""),
              {"cid": cid, "app": row["application_id"], "loan": row["loan_id"], "member": row["member_id"]})
            await session.execute(text("UPDATE loan_applications SET status='disputed',updated_at=now() WHERE cooperative_id=:cid AND id=:id"), {"cid": cid, "id": row["application_id"]})
            if row["loan_id"]:
                await session.execute(text("UPDATE loans SET status='disputed',deductions_paused=true WHERE cooperative_id=:cid AND id=:id"), {"cid": cid, "id": row["loan_id"]})
            await session.execute(text("INSERT INTO fraud_alerts(cooperative_id,rule_code,entity_type,entity_id,summary) VALUES(:cid,'consent_dispute','loan_application',:id,'{}'::jsonb)"), {"cid": cid, "id": row["application_id"]})
            await append_audit(session, cooperative_id=cid, actor_user_id=None, role=None,
                action="member_consent_rejected", entity_type="loan_application", entity_id=str(row["application_id"]),
                summary={"consent_id": str(row["id"]), "dispute_opened": True}, channel="signed_link", ip=ip)
    return "recorded"
