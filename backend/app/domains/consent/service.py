"""Public, tenant-scoped member consent operations."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from cryptography.fernet import Fernet
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import transaction
from app.core.errors import NotFound
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


async def view(slug: str, token: str) -> dict[str, Any]:
    pair = await _lookup(slug, token)
    if pair is None:
        raise NotFound("Confirmation unavailable")
    cid, token_hash = pair
    async with transaction(cid) as session:
        row = (await session.execute(text("""SELECT c.id,c.kind,c.status,c.expires_at,s.terms,
          a.member_id,a.current_terms_snapshot_id,b.last4,m.full_name
          FROM member_consents c JOIN loan_applications a ON a.cooperative_id=c.cooperative_id AND a.id=c.application_id
          LEFT JOIN loan_terms_snapshots s ON s.cooperative_id=a.cooperative_id AND s.id=a.current_terms_snapshot_id
          LEFT JOIN bank_accounts b ON b.cooperative_id=a.cooperative_id AND b.id=a.destination_bank_account_id
          JOIN members m ON m.cooperative_id=c.cooperative_id AND m.id=a.member_id
          WHERE c.cooperative_id=:cid AND c.token_hash=:token"""), {"cid": cid, "token": token_hash})).mappings().one_or_none()
        if row is None or row["status"] != "pending" or row["expires_at"] <= datetime.now(UTC):
            raise NotFound("Confirmation unavailable")
        terms = dict(row["terms"] or {})
        return {"kind": row["kind"], "applicant": row["full_name"], "terms": terms,
                "destination_account": f"****{row['last4']}" if row["last4"] else None,
                "expires_at": row["expires_at"].isoformat()}


async def send_otp(slug: str, token: str, ip: str, sender: OtpSender) -> None:
    pair = await _lookup(slug, token)
    if pair is None:
        raise NotFound("Confirmation unavailable")
    cid, token_hash = pair
    async with transaction(cid) as session:
        row = (await session.execute(text("""SELECT c.id,c.member_id,c.status,c.expires_at,
          p.otp_validity_seconds,m.phone_e164 FROM member_consents c
          JOIN loan_applications a ON a.cooperative_id=c.cooperative_id AND a.id=c.application_id
          JOIN cooperative_policy_versions p ON p.cooperative_id=a.cooperative_id AND p.id=a.policy_version_id
          JOIN members m ON m.cooperative_id=c.cooperative_id AND m.id=c.member_id
          WHERE c.cooperative_id=:cid AND c.token_hash=:token FOR UPDATE OF c"""),
          {"cid": cid, "token": token_hash})).mappings().one_or_none()
        if row is None or row["status"] != "pending" or row["expires_at"] <= datetime.now(UTC):
            raise NotFound("Confirmation unavailable")
        code = f"{secrets.randbelow(1_000_000):06d}"
        await session.execute(text("""UPDATE member_consents SET otp_hash=:hash,
          otp_expires_at=now()+(:seconds * interval '1 second'),otp_attempts=0,channel='sms'
          WHERE cooperative_id=:cid AND id=:id"""),
          {"hash": _otp_hash(row["id"], code), "seconds": row["otp_validity_seconds"], "cid": cid, "id": row["id"]})
    # Delivery happens outside the database transaction; sender failures do not leak token data.
    await sender.send(row["phone_e164"], code)


async def decide(slug: str, token: str, code: str, decision: str, ip: str, user_agent: str) -> str:
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
        if row["otp_attempts"] >= 5 or row["otp_hash"] is None or row["otp_expires_at"] is None or row["otp_expires_at"] <= datetime.now(UTC):
            raise NotFound("Confirmation unavailable")
        if not hmac.compare_digest(row["otp_hash"], _otp_hash(row["id"], code)):
            await session.execute(text("UPDATE member_consents SET otp_attempts=otp_attempts+1 WHERE cooperative_id=:cid AND id=:id"), {"cid": cid, "id": row["id"]})
            return "unavailable"
        if row["kind"] == "final_terms" and not hmac.compare_digest(row["terms_hash"] or b"", row["terms_sha256"] or b""):
            await session.execute(text("UPDATE member_consents SET status='invalidated' WHERE cooperative_id=:cid AND id=:id"), {"cid": cid, "id": row["id"]})
            return "terms_changed"
        status = "confirmed" if decision == "confirm" else "rejected"
        await session.execute(text("""UPDATE member_consents SET status=:status,otp_hash=NULL,
          decided_at=now(),channel='sms',ip=CAST(:ip AS inet),user_agent=:ua
          WHERE cooperative_id=:cid AND id=:id"""), {"status": status, "ip": ip, "ua": user_agent, "cid": cid, "id": row["id"]})
        if decision == "confirm":
            target = "under_review" if row["kind"] == "applicant_ack" else "ready_for_disbursement"
            await session.execute(text("UPDATE loan_applications SET status=:status,updated_at=now() WHERE cooperative_id=:cid AND id=:id"), {"status": target, "cid": cid, "id": row["application_id"]})
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
