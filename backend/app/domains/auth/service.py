"""Staff login, TOTP enrollment, session and role-management services."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pyotp
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import get_engine, transaction
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.security import (
    decrypt_mfa_secret,
    encrypt_mfa_secret,
    hash_csrf_token,
    hash_session_token,
    new_csrf_token,
    new_recovery_codes,
    new_session_token,
    new_totp_secret,
    rate_limit_key,
    recovery_code_hash,
    verify_dummy_password,
    verify_password,
    verify_totp,
)
from app.domains.audit.service import append_audit

GENERIC_LOGIN_ERROR = "Invalid credentials or sign-in unavailable"
SESSION_COOKIE_SEPARATOR = "~"
VALID_ROLES = {"officer", "committee", "treasurer", "admin", "auditor"}


@dataclass(frozen=True)
class LoginResult:
    data: dict[str, Any]
    session_cookie: str | None = None
    session_max_age: int | None = None
    csrf_token: str | None = None
    clear_session: bool = False


@dataclass(frozen=True)
class CurrentSession:
    cooperative_id: uuid.UUID
    slug: str
    session_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    roles: tuple[str, ...]
    csrf_hash: bytes


async def resolve_tenant_slug(slug: str) -> tuple[uuid.UUID, str] | None:
    """Resolve the public slug before tenant context; the directory contains no PII."""
    async with get_engine().connect() as connection:
        result = await connection.execute(
            text("SELECT cooperative_id, status FROM tenant_directory WHERE slug = :slug"),
            {"slug": slug.lower().strip()},
        )
        row = result.one_or_none()
        if row is None:
            return None
        return row[0], row[1]


def _client_ip(ip: str | None) -> str:
    return ip or "127.0.0.1"


async def _is_rate_limited(session: AsyncSession, key_hash: bytes) -> bool:
    row = await session.execute(
        text("SELECT locked_until FROM auth_rate_limits WHERE request_key_hash = :key_hash"),
        {"key_hash": key_hash},
    )
    locked_until = row.scalar_one_or_none()
    return locked_until is not None and locked_until > datetime.now(UTC)


async def _record_failed_attempt(
    session: AsyncSession, key_hash: bytes, settings: Settings
) -> tuple[int, datetime | None]:
    result = await session.execute(
        text(
            """
            INSERT INTO auth_rate_limits (
                request_key_hash, failed_count, window_started_at, locked_until, updated_at
            ) VALUES (
                :key_hash, 1, now(),
                CASE WHEN 1 >= :limit_count
                     THEN now() + make_interval(mins => :lock_minutes) END,
                now()
            )
            ON CONFLICT (request_key_hash) DO UPDATE SET
                failed_count = CASE
                    WHEN auth_rate_limits.window_started_at <
                         now() - make_interval(mins => :window_minutes)
                    THEN 1 ELSE auth_rate_limits.failed_count + 1 END,
                window_started_at = CASE
                    WHEN auth_rate_limits.window_started_at <
                         now() - make_interval(mins => :window_minutes)
                    THEN now() ELSE auth_rate_limits.window_started_at END,
                locked_until = CASE
                    WHEN CASE
                        WHEN auth_rate_limits.window_started_at <
                             now() - make_interval(mins => :window_minutes)
                        THEN 1 ELSE auth_rate_limits.failed_count + 1 END >= :limit_count
                    THEN now() + make_interval(mins => :lock_minutes)
                    ELSE auth_rate_limits.locked_until END,
                updated_at = now()
            RETURNING failed_count, locked_until
            """
        ),
        {
            "key_hash": key_hash,
            "limit_count": settings.login_rate_limit_attempts,
            "lock_minutes": settings.login_lockout_minutes,
            "window_minutes": settings.login_rate_limit_window_minutes,
        },
    )
    row = result.one()
    return row[0], row[1]


async def _clear_rate_limit(session: AsyncSession, key_hash: bytes) -> None:
    await session.execute(
        text(
            "UPDATE auth_rate_limits SET failed_count = 0, locked_until = NULL, "
            "window_started_at = now(), updated_at = now() WHERE request_key_hash = :key_hash"
        ),
        {"key_hash": key_hash},
    )


async def _create_session(
    session: AsyncSession,
    *,
    cooperative_id: uuid.UUID,
    user_id: uuid.UUID,
    slug: str,
    ip: str | None,
    user_agent: str | None,
    pending: bool,
    settings: Settings,
) -> tuple[str, str, int]:
    raw_token = new_session_token()
    csrf_token = new_csrf_token()
    max_age = settings.session_absolute_hours * 60 * 60
    await session.execute(
        text(
            """
            INSERT INTO user_sessions (
                cooperative_id, user_id, token_hash, csrf_token_hash,
                expires_at, last_seen_at, ip, user_agent, mfa_pending, authenticated_at
            ) VALUES (
                :cooperative_id, :user_id, :token_hash, :csrf_token_hash,
                now() + make_interval(hours => :absolute_hours), now(),
                CAST(:ip AS inet), :user_agent, :pending,
                CASE WHEN :pending THEN NULL ELSE now() END
            )
            """
        ),
        {
            "cooperative_id": cooperative_id,
            "user_id": user_id,
            "token_hash": hash_session_token(raw_token),
            "csrf_token_hash": hash_csrf_token(csrf_token),
            "absolute_hours": settings.session_absolute_hours,
            "ip": _client_ip(ip),
            "user_agent": (user_agent or "")[:1000],
            "pending": pending,
        },
    )
    return f"{slug}{SESSION_COOKIE_SEPARATOR}{raw_token}", csrf_token, max_age


def _roles_text(roles: list[str]) -> str | None:
    return ",".join(sorted(roles)) if roles else None


async def _audit_auth(
    session: AsyncSession,
    *,
    cooperative_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    role: str | None,
    action: str,
    ip: str | None,
    summary: dict[str, Any],
) -> None:
    await append_audit(
        session,
        cooperative_id=cooperative_id,
        actor_user_id=actor_user_id,
        role=role,
        action=action,
        entity_type="user",
        entity_id=str(actor_user_id) if actor_user_id else None,
        summary=summary,
        channel="web",
        ip=ip,
    )


async def login(
    *,
    slug: str,
    email: str,
    password: str,
    totp_code: str | None,
    recovery_code: str | None,
    ip: str | None,
    user_agent: str | None,
) -> LoginResult:
    settings = get_settings()
    normalized_slug = slug.strip().lower()
    normalized_email = email.strip().lower()
    ip_value = _client_ip(ip)
    request_key = rate_limit_key(settings.secret_key, normalized_slug, normalized_email, ip_value)
    tenant = await resolve_tenant_slug(normalized_slug)

    if tenant is None:
        verify_dummy_password(password)
        async with transaction() as session:
            if not await _is_rate_limited(session, request_key):
                await _record_failed_attempt(session, request_key, settings)
        return LoginResult({"error": "invalid_credentials", "message": GENERIC_LOGIN_ERROR})

    cooperative_id, tenant_status = tenant
    async with transaction(cooperative_id) as session:
        if tenant_status != "active":
            verify_dummy_password(password)
            await _audit_auth(
                session,
                cooperative_id=cooperative_id,
                actor_user_id=None,
                role=None,
                action="login_failure",
                ip=ip,
                summary={"reason": "tenant_unavailable"},
            )
            return LoginResult({"error": "invalid_credentials", "message": GENERIC_LOGIN_ERROR})
        if await _is_rate_limited(session, request_key):
            verify_dummy_password(password)
            await _audit_auth(
                session,
                cooperative_id=cooperative_id,
                actor_user_id=None,
                role=None,
                action="login_lockout",
                ip=ip,
                summary={"reason": "rate_limited"},
            )
            return LoginResult({"error": "invalid_credentials", "message": GENERIC_LOGIN_ERROR})

        user_result = await session.execute(
            text(
                """
                SELECT id, email, password_hash, status, mfa_secret_encrypted,
                       mfa_enabled_at, failed_login_count, locked_until
                  FROM users
                 WHERE cooperative_id = :cooperative_id AND lower(email) = :email
                 FOR UPDATE
                """
            ),
            {"cooperative_id": cooperative_id, "email": normalized_email},
        )
        user = user_result.mappings().one_or_none()
        if user is None:
            verify_dummy_password(password)
            valid_password = False
            user_id = None
        else:
            user_id = user["id"]
            if user["locked_until"] is not None and user["locked_until"] > datetime.now(UTC):
                verify_dummy_password(password)
                await _audit_auth(
                    session,
                    cooperative_id=cooperative_id,
                    actor_user_id=user_id,
                    role=None,
                    action="login_lockout",
                    ip=ip,
                    summary={"reason": "account_locked"},
                )
                return LoginResult({"error": "invalid_credentials", "message": GENERIC_LOGIN_ERROR})
            valid_password = verify_password(user["password_hash"], password)

        if user is None or user["status"] != "active" or not valid_password:
            failed_count, locked_until = await _record_failed_attempt(
                session, request_key, settings
            )
            if user is not None and user_id is not None:
                user_failed_count = int(user["failed_login_count"]) + 1
                locked = user_failed_count >= settings.login_max_attempts
                await session.execute(
                    text(
                        """
                        UPDATE users
                           SET failed_login_count = :failed_count,
                               locked_until = CASE WHEN :locked
                                   THEN now() + make_interval(mins => :lock_minutes)
                                   ELSE locked_until END
                         WHERE cooperative_id = :cooperative_id AND id = :user_id
                        """
                    ),
                    {
                        "failed_count": user_failed_count,
                        "locked": locked,
                        "lock_minutes": settings.login_lockout_minutes,
                        "cooperative_id": cooperative_id,
                        "user_id": user_id,
                    },
                )
            lockout = (
                (
                    user is not None
                    and user_id is not None
                    and int(user["failed_login_count"]) + 1 >= settings.login_max_attempts
                )
                or (locked_until is not None)
                or failed_count >= settings.login_rate_limit_attempts
            )
            await _audit_auth(
                session,
                cooperative_id=cooperative_id,
                actor_user_id=user_id,
                role=None,
                action="login_lockout" if lockout else "login_failure",
                ip=ip,
                summary={"reason": "invalid_credentials"},
            )
            return LoginResult({"error": "invalid_credentials", "message": GENERIC_LOGIN_ERROR})

        assert user is not None and user_id is not None
        roles_result = await session.execute(
            text(
                "SELECT role FROM user_roles WHERE cooperative_id = :cooperative_id "
                "AND user_id = :user_id ORDER BY role"
            ),
            {"cooperative_id": cooperative_id, "user_id": user_id},
        )
        roles = [row[0] for row in roles_result]
        if not roles:
            await _audit_auth(
                session,
                cooperative_id=cooperative_id,
                actor_user_id=user_id,
                role=None,
                action="login_failure",
                ip=ip,
                summary={"reason": "no_staff_role"},
            )
            return LoginResult({"error": "invalid_credentials", "message": GENERIC_LOGIN_ERROR})

        await session.execute(
            text(
                "UPDATE users SET failed_login_count = 0, locked_until = NULL "
                "WHERE cooperative_id = :cooperative_id AND id = :user_id"
            ),
            {"cooperative_id": cooperative_id, "user_id": user_id},
        )
        await _clear_rate_limit(session, request_key)

        if user["mfa_enabled_at"] is None:
            if user["mfa_secret_encrypted"] is None:
                secret = new_totp_secret()
                await session.execute(
                    text(
                        "UPDATE users SET mfa_secret_encrypted = :encrypted "
                        "WHERE cooperative_id = :cooperative_id AND id = :user_id"
                    ),
                    {
                        "encrypted": encrypt_mfa_secret(secret),
                        "cooperative_id": cooperative_id,
                        "user_id": user_id,
                    },
                )
            else:
                secret = decrypt_mfa_secret(user["mfa_secret_encrypted"])
            cookie, csrf, max_age = await _create_session(
                session,
                cooperative_id=cooperative_id,
                user_id=user_id,
                slug=normalized_slug,
                ip=ip,
                user_agent=user_agent,
                pending=True,
                settings=settings,
            )
            await _audit_auth(
                session,
                cooperative_id=cooperative_id,
                actor_user_id=user_id,
                role=_roles_text(roles),
                action="mfa_enrollment_started",
                ip=ip,
                summary={},
            )
            uri = pyotp.TOTP(secret).provisioning_uri(name=user["email"], issuer_name="Ajo")
            return LoginResult(
                {"mfa_enrollment_required": True, "provisioning_uri": uri},
                cookie,
                max_age,
                csrf,
            )

        if totp_code is None and recovery_code is None:
            cookie, csrf, max_age = await _create_session(
                session,
                cooperative_id=cooperative_id,
                user_id=user_id,
                slug=normalized_slug,
                ip=ip,
                user_agent=user_agent,
                pending=True,
                settings=settings,
            )
            await _audit_auth(
                session,
                cooperative_id=cooperative_id,
                actor_user_id=user_id,
                role=_roles_text(roles),
                action="mfa_challenge_started",
                ip=ip,
                summary={},
            )
            return LoginResult({"mfa_required": True}, cookie, max_age, csrf)

        secret = decrypt_mfa_secret(user["mfa_secret_encrypted"])
        valid_factor = bool(totp_code and verify_totp(secret, totp_code))
        if recovery_code is not None and not valid_factor:
            recovery = await session.execute(
                text(
                    """
                    UPDATE recovery_codes SET used_at = now()
                     WHERE cooperative_id = :cooperative_id AND user_id = :user_id
                       AND code_hash = :code_hash AND used_at IS NULL
                    RETURNING id
                    """
                ),
                {
                    "cooperative_id": cooperative_id,
                    "user_id": user_id,
                    "code_hash": recovery_code_hash(recovery_code),
                },
            )
            valid_factor = recovery.scalar_one_or_none() is not None

        if not valid_factor:
            failed_count, locked_until = await _record_failed_attempt(
                session, request_key, settings
            )
            user_failed_count = int(user["failed_login_count"]) + 1
            locked = user_failed_count >= settings.login_max_attempts
            await session.execute(
                text(
                    """
                    UPDATE users SET failed_login_count = :failed_count,
                        locked_until = CASE WHEN :locked
                            THEN now() + make_interval(mins => :lock_minutes)
                            ELSE locked_until END
                     WHERE cooperative_id = :cooperative_id AND id = :user_id
                    """
                ),
                {
                    "failed_count": user_failed_count,
                    "locked": locked,
                    "lock_minutes": settings.login_lockout_minutes,
                    "cooperative_id": cooperative_id,
                    "user_id": user_id,
                },
            )
            lockout = (
                locked
                or locked_until is not None
                or failed_count >= settings.login_rate_limit_attempts
            )
            await _audit_auth(
                session,
                cooperative_id=cooperative_id,
                actor_user_id=user_id,
                role=_roles_text(roles),
                action="login_lockout" if lockout else "login_failure",
                ip=ip,
                summary={"reason": "invalid_second_factor"},
            )
            return LoginResult({"error": "invalid_credentials", "message": GENERIC_LOGIN_ERROR})

        cookie, csrf, max_age = await _create_session(
            session,
            cooperative_id=cooperative_id,
            user_id=user_id,
            slug=normalized_slug,
            ip=ip,
            user_agent=user_agent,
            pending=False,
            settings=settings,
        )
        await _audit_auth(
            session,
            cooperative_id=cooperative_id,
            actor_user_id=user_id,
            role=_roles_text(roles),
            action="login_success",
            ip=ip,
            summary={"mfa_method": "recovery_code" if recovery_code else "totp"},
        )
        return LoginResult({"authenticated": True, "roles": roles}, cookie, max_age, csrf)


def split_session_cookie(value: str | None) -> tuple[str, str] | None:
    if value is None or SESSION_COOKIE_SEPARATOR not in value:
        return None
    slug, token = value.split(SESSION_COOKIE_SEPARATOR, 1)
    if not slug or not token:
        return None
    return slug, token


async def load_session(cookie_value: str | None, *, require_authenticated: bool) -> CurrentSession:
    parts = split_session_cookie(cookie_value)
    if parts is None:
        raise Forbidden("Authentication required")
    slug, token = parts
    tenant = await resolve_tenant_slug(slug)
    if tenant is None or tenant[1] != "active":
        raise Forbidden("Authentication required")
    cooperative_id = tenant[0]
    settings = get_settings()
    async with transaction(cooperative_id) as session:
        result = await session.execute(
            text(
                """
                SELECT s.id AS session_id, s.user_id, s.csrf_token_hash, s.expires_at,
                       s.last_seen_at, s.revoked_at, s.mfa_pending, s.authenticated_at,
                       u.email, u.status
                  FROM user_sessions AS s
                  JOIN users AS u ON u.cooperative_id = s.cooperative_id AND u.id = s.user_id
                 WHERE s.cooperative_id = :cooperative_id AND s.token_hash = :token_hash
                 FOR UPDATE OF s
                """
            ),
            {"cooperative_id": cooperative_id, "token_hash": hash_session_token(token)},
        )
        row = result.mappings().one_or_none()
        now = datetime.now(UTC)
        if (
            row is None
            or row["revoked_at"] is not None
            or row["expires_at"] <= now
            or row["last_seen_at"] <= now - timedelta(minutes=settings.session_idle_minutes)
            or row["status"] != "active"
            or (require_authenticated and (row["mfa_pending"] or row["authenticated_at"] is None))
        ):
            if row is not None and row["revoked_at"] is None:
                await session.execute(
                    text(
                        "UPDATE user_sessions SET revoked_at = now() "
                        "WHERE cooperative_id = :cooperative_id AND id = :session_id"
                    ),
                    {"cooperative_id": cooperative_id, "session_id": row["session_id"]},
                )
            raise Forbidden("Authentication required")
        await session.execute(
            text(
                "UPDATE user_sessions SET last_seen_at = now() "
                "WHERE cooperative_id = :cooperative_id AND id = :session_id"
            ),
            {"cooperative_id": cooperative_id, "session_id": row["session_id"]},
        )
        roles_result = await session.execute(
            text(
                "SELECT role FROM user_roles WHERE cooperative_id = :cooperative_id "
                "AND user_id = :user_id ORDER BY role"
            ),
            {"cooperative_id": cooperative_id, "user_id": row["user_id"]},
        )
        roles = tuple(value[0] for value in roles_result)
        return CurrentSession(
            cooperative_id,
            slug,
            row["session_id"],
            row["user_id"],
            row["email"],
            roles,
            bytes(row["csrf_token_hash"]),
        )


async def complete_mfa_enrollment(
    cookie_value: str | None, code: str, ip: str | None
) -> tuple[list[str], CurrentSession | None]:
    parts = split_session_cookie(cookie_value)
    if parts is None:
        raise Forbidden("MFA enrollment is unavailable")
    slug, token = parts
    tenant = await resolve_tenant_slug(slug)
    if tenant is None:
        raise Forbidden("MFA enrollment is unavailable")
    cooperative_id = tenant[0]
    settings = get_settings()
    failure = False
    codes: list[str] = []
    current: CurrentSession | None = None
    async with transaction(cooperative_id) as session:
        result = await session.execute(
            text(
                """
                SELECT s.id AS session_id, s.user_id, s.mfa_pending,
                       s.authenticated_at, s.expires_at, s.last_seen_at,
                       u.email, u.mfa_secret_encrypted, u.mfa_enabled_at, u.locked_until
                  FROM user_sessions AS s
                  JOIN users AS u ON u.cooperative_id = s.cooperative_id AND u.id = s.user_id
                 WHERE s.cooperative_id = :cooperative_id AND s.token_hash = :token_hash
                   AND s.revoked_at IS NULL
                 FOR UPDATE OF s, u
                """
            ),
            {"cooperative_id": cooperative_id, "token_hash": hash_session_token(token)},
        )
        row = result.mappings().one_or_none()
        now = datetime.now(UTC)
        if (
            row is None
            or not row["mfa_pending"]
            or row["authenticated_at"] is not None
            or row["expires_at"] <= now
            or row["last_seen_at"] <= now - timedelta(minutes=settings.session_idle_minutes)
            or row["mfa_enabled_at"] is not None
            or row["mfa_secret_encrypted"] is None
            or (row["locked_until"] is not None and row["locked_until"] > now)
        ):
            failure = True
            await _audit_auth(
                session,
                cooperative_id=cooperative_id,
                actor_user_id=row["user_id"] if row is not None else None,
                role=None,
                action=(
                    "login_lockout"
                    if row is not None
                    and row["locked_until"] is not None
                    and row["locked_until"] > now
                    else "login_failure"
                ),
                ip=ip,
                summary={"reason": "invalid_mfa_enrollment_session"},
            )
        elif not verify_totp(decrypt_mfa_secret(row["mfa_secret_encrypted"]), code):
            failure = True
            failed_count = await session.scalar(
                text(
                    "UPDATE users SET failed_login_count = failed_login_count + 1, "
                    "locked_until = CASE WHEN failed_login_count + 1 >= :max_attempts "
                    "THEN now() + make_interval(mins => :lock_minutes) ELSE locked_until END "
                    "WHERE cooperative_id = :cooperative_id AND id = :user_id "
                    "RETURNING failed_login_count"
                ),
                {
                    "max_attempts": settings.login_max_attempts,
                    "lock_minutes": settings.login_lockout_minutes,
                    "cooperative_id": cooperative_id,
                    "user_id": row["user_id"],
                },
            )
            await session.execute(
                text(
                    "UPDATE user_sessions SET revoked_at = now() "
                    "WHERE cooperative_id = :cooperative_id AND id = :session_id "
                    "AND :failed_count >= :max_attempts"
                ),
                {
                    "cooperative_id": cooperative_id,
                    "session_id": row["session_id"],
                    "failed_count": failed_count,
                    "max_attempts": settings.login_max_attempts,
                },
            )
            await _audit_auth(
                session,
                cooperative_id=cooperative_id,
                actor_user_id=row["user_id"],
                role=None,
                action=(
                    "login_lockout"
                    if failed_count is not None and failed_count >= settings.login_max_attempts
                    else "mfa_enrollment_failure"
                ),
                ip=ip,
                summary={"reason": "invalid_code"},
            )
        else:
            codes = new_recovery_codes()
            await session.execute(
                text(
                    "UPDATE users SET mfa_enabled_at = now(), failed_login_count = 0, "
                    "locked_until = NULL WHERE cooperative_id = :cooperative_id AND id = :user_id"
                ),
                {"cooperative_id": cooperative_id, "user_id": row["user_id"]},
            )
            for code_value in codes:
                await session.execute(
                    text(
                        "INSERT INTO recovery_codes (cooperative_id, user_id, code_hash) "
                        "VALUES (:cooperative_id, :user_id, :code_hash)"
                    ),
                    {
                        "cooperative_id": cooperative_id,
                        "user_id": row["user_id"],
                        "code_hash": recovery_code_hash(code_value),
                    },
                )
            await session.execute(
                text(
                    "UPDATE user_sessions SET mfa_pending = false, authenticated_at = now(), "
                    "last_seen_at = now() WHERE cooperative_id = :cooperative_id "
                    "AND id = :session_id"
                ),
                {"cooperative_id": cooperative_id, "session_id": row["session_id"]},
            )
            roles_result = await session.execute(
                text(
                    "SELECT role FROM user_roles WHERE cooperative_id = :cooperative_id "
                    "AND user_id = :user_id ORDER BY role"
                ),
                {"cooperative_id": cooperative_id, "user_id": row["user_id"]},
            )
            roles = [value[0] for value in roles_result]
            await _audit_auth(
                session,
                cooperative_id=cooperative_id,
                actor_user_id=row["user_id"],
                role=_roles_text(roles),
                action="mfa_enrollment_completed",
                ip=ip,
                summary={"recovery_codes_issued": len(codes)},
            )
            await _audit_auth(
                session,
                cooperative_id=cooperative_id,
                actor_user_id=row["user_id"],
                role=_roles_text(roles),
                action="login_success",
                ip=ip,
                summary={"mfa_method": "totp"},
            )
            current = CurrentSession(
                cooperative_id,
                slug,
                row["session_id"],
                row["user_id"],
                row["email"],
                tuple(roles),
                b"",
            )
    if failure or current is None:
        return [], None
    return codes, current


async def verify_pending_mfa(
    cookie_value: str | None, code: str, ip: str | None
) -> CurrentSession | None:
    parts = split_session_cookie(cookie_value)
    if parts is None:
        raise Forbidden("MFA verification is unavailable")
    slug, token = parts
    tenant = await resolve_tenant_slug(slug)
    if tenant is None:
        raise Forbidden("MFA verification is unavailable")
    cooperative_id = tenant[0]
    settings = get_settings()
    failure = False
    current: CurrentSession | None = None
    async with transaction(cooperative_id) as session:
        result = await session.execute(
            text(
                """
                SELECT s.id AS session_id, s.user_id, s.csrf_token_hash, s.mfa_pending,
                       s.authenticated_at, s.expires_at, s.last_seen_at,
                       u.email, u.mfa_secret_encrypted, u.mfa_enabled_at, u.locked_until
                  FROM user_sessions AS s
                  JOIN users AS u ON u.cooperative_id = s.cooperative_id AND u.id = s.user_id
                 WHERE s.cooperative_id = :cooperative_id AND s.token_hash = :token_hash
                   AND s.revoked_at IS NULL
                 FOR UPDATE OF s, u
                """
            ),
            {"cooperative_id": cooperative_id, "token_hash": hash_session_token(token)},
        )
        row = result.mappings().one_or_none()
        now = datetime.now(UTC)
        invalid = (
            row is None
            or not row["mfa_pending"]
            or row["authenticated_at"] is not None
            or row["expires_at"] <= now
            or row["last_seen_at"] <= now - timedelta(minutes=settings.session_idle_minutes)
            or row["mfa_enabled_at"] is None
            or row["mfa_secret_encrypted"] is None
            or (row["locked_until"] is not None and row["locked_until"] > now)
            or not verify_totp(decrypt_mfa_secret(row["mfa_secret_encrypted"]), code)
        )
        if invalid:
            failure = True
            failed_count: int | None = None
            if row is not None:
                failed_count = await session.scalar(
                    text(
                        "UPDATE users SET failed_login_count = failed_login_count + 1, "
                        "locked_until = CASE WHEN failed_login_count + 1 >= :max_attempts "
                        "THEN now() + make_interval(mins => :lock_minutes) ELSE locked_until END "
                        "WHERE cooperative_id = :cooperative_id AND id = :user_id "
                        "RETURNING failed_login_count"
                    ),
                    {
                        "max_attempts": settings.login_max_attempts,
                        "lock_minutes": settings.login_lockout_minutes,
                        "cooperative_id": cooperative_id,
                        "user_id": row["user_id"],
                    },
                )
                if failed_count is not None and failed_count >= settings.login_max_attempts:
                    await session.execute(
                        text(
                            "UPDATE user_sessions SET revoked_at = now() "
                            "WHERE cooperative_id = :cooperative_id AND id = :session_id"
                        ),
                        {"cooperative_id": cooperative_id, "session_id": row["session_id"]},
                    )
            await _audit_auth(
                session,
                cooperative_id=cooperative_id,
                actor_user_id=row["user_id"] if row is not None else None,
                role=None,
                action=(
                    "login_lockout"
                    if failed_count is not None and failed_count >= settings.login_max_attempts
                    else "login_failure"
                ),
                ip=ip,
                summary={"reason": "invalid_second_factor"},
            )
        else:
            assert row is not None
            await session.execute(
                text(
                    "UPDATE user_sessions SET mfa_pending = false, authenticated_at = now(), "
                    "last_seen_at = now() WHERE cooperative_id = :cooperative_id "
                    "AND id = :session_id"
                ),
                {"cooperative_id": cooperative_id, "session_id": row["session_id"]},
            )
            roles_result = await session.execute(
                text(
                    "SELECT role FROM user_roles WHERE cooperative_id = :cooperative_id "
                    "AND user_id = :user_id ORDER BY role"
                ),
                {"cooperative_id": cooperative_id, "user_id": row["user_id"]},
            )
            roles = [value[0] for value in roles_result]
            await _audit_auth(
                session,
                cooperative_id=cooperative_id,
                actor_user_id=row["user_id"],
                role=_roles_text(roles),
                action="login_success",
                ip=ip,
                summary={"mfa_method": "totp"},
            )
            current = CurrentSession(
                cooperative_id,
                slug,
                row["session_id"],
                row["user_id"],
                row["email"],
                tuple(roles),
                bytes(row["csrf_token_hash"]),
            )
    return None if failure else current


async def logout(current: CurrentSession, ip: str | None, *, revoke_all: bool = False) -> None:
    async with transaction(current.cooperative_id) as session:
        if revoke_all:
            await session.execute(
                text(
                    "UPDATE user_sessions SET revoked_at = now() "
                    "WHERE cooperative_id = :cooperative_id AND user_id = :user_id "
                    "AND revoked_at IS NULL"
                ),
                {"cooperative_id": current.cooperative_id, "user_id": current.user_id},
            )
            action = "sessions_revoked_all"
        else:
            await session.execute(
                text(
                    "UPDATE user_sessions SET revoked_at = now() "
                    "WHERE cooperative_id = :cooperative_id AND id = :session_id "
                    "AND revoked_at IS NULL"
                ),
                {"cooperative_id": current.cooperative_id, "session_id": current.session_id},
            )
            action = "logout"
        await _audit_auth(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            role=_roles_text(list(current.roles)),
            action=action,
            ip=ip,
            summary={},
        )


async def _lock_cooperative_role_changes(session: AsyncSession, cooperative_id: uuid.UUID) -> None:
    """Serialize all role proposals and approvals within one cooperative."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:cooperative_id, 0))"),
        {"cooperative_id": str(cooperative_id)},
    )


async def _current_user_roles(
    session: AsyncSession, cooperative_id: uuid.UUID, user_id: uuid.UUID
) -> list[str]:
    result = await session.execute(
        text(
            "SELECT r.role FROM user_roles r JOIN users u "
            "ON u.cooperative_id = r.cooperative_id AND u.id = r.user_id "
            "WHERE r.cooperative_id = :cooperative_id AND r.user_id = :user_id "
            "AND u.status = 'active' ORDER BY r.role"
        ),
        {"cooperative_id": cooperative_id, "user_id": user_id},
    )
    return [row[0] for row in result]


async def _require_current_admin(session: AsyncSession, current: CurrentSession) -> list[str]:
    roles = await _current_user_roles(session, current.cooperative_id, current.user_id)
    if "admin" not in roles:
        raise Forbidden("Admin role required")
    return roles


async def _active_admin_count(session: AsyncSession, cooperative_id: uuid.UUID) -> int:
    result = await session.execute(
        text(
            "SELECT count(DISTINCT r.user_id) FROM user_roles r JOIN users u "
            "ON u.cooperative_id = r.cooperative_id AND u.id = r.user_id "
            "WHERE r.cooperative_id = :cooperative_id AND r.role = 'admin' "
            "AND u.status = 'active'"
        ),
        {"cooperative_id": cooperative_id},
    )
    return int(result.scalar_one())


async def _audit_role_change(
    session: AsyncSession,
    *,
    cooperative_id: uuid.UUID,
    proposal_id: uuid.UUID,
    target_user_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    actor_roles: list[str],
    event: str,
    role: str,
    action: str,
    reason: str,
    ip: str | None,
    detail: str | None = None,
) -> None:
    summary: dict[str, Any] = {
        "proposal_id": str(proposal_id),
        "actor_user_id": str(actor_user_id) if actor_user_id else None,
        "target_user_id": str(target_user_id),
        "role": role,
        "action": action,
        "old_role": role if action == "remove" else None,
        "new_role": role if action == "add" else None,
        "reason": reason,
    }
    if detail is not None:
        summary["detail"] = detail
    await append_audit(
        session,
        cooperative_id=cooperative_id,
        actor_user_id=actor_user_id,
        role=_roles_text(actor_roles) if actor_roles else None,
        action=f"role_change_{event}",
        entity_type="user",
        entity_id=str(target_user_id),
        summary=summary,
        channel="system" if event == "expired" else "web",
        ip=ip,
        reason=reason,
    )


async def _expire_due_role_change_proposals(
    session: AsyncSession, cooperative_id: uuid.UUID
) -> None:
    result = await session.execute(
        text(
            "UPDATE role_change_proposals SET status = 'expired', decided_at = now(), "
            "decided_by = NULL WHERE cooperative_id = :cooperative_id "
            "AND status = 'pending' AND expires_at <= now() "
            "RETURNING id, target_user_id, role, action, reason"
        ),
        {"cooperative_id": cooperative_id},
    )
    for row in result.mappings():
        await _audit_role_change(
            session,
            cooperative_id=cooperative_id,
            proposal_id=row["id"],
            target_user_id=row["target_user_id"],
            actor_user_id=None,
            actor_roles=[],
            event="expired",
            role=row["role"],
            action=row["action"],
            reason=row["reason"],
            ip=None,
        )


async def propose_role_change(
    current: CurrentSession,
    target_user_id: uuid.UUID,
    role: str,
    action: str,
    reason: str,
    ip: str | None,
) -> uuid.UUID:
    if role not in VALID_ROLES:
        raise ValueError("Invalid role")
    if action not in {"add", "remove"}:
        raise ValueError("Invalid role-change action")
    reason = reason.strip()
    if not reason or len(reason) > 500:
        raise Conflict("A reason of 1 to 500 characters is required")

    async with transaction(current.cooperative_id) as session:
        await _lock_cooperative_role_changes(session, current.cooperative_id)
        actor_roles = await _require_current_admin(session, current)
        await _expire_due_role_change_proposals(session, current.cooperative_id)
        if await _active_admin_count(session, current.cooperative_id) < 2:
            raise Conflict("At least two active admins are required for maker-checker role changes")

        target = await session.execute(
            text("SELECT 1 FROM users WHERE cooperative_id = :cooperative_id AND id = :user_id"),
            {"cooperative_id": current.cooperative_id, "user_id": target_user_id},
        )
        if target.scalar_one_or_none() is None:
            raise NotFound("User not found")
        current_role = await session.execute(
            text(
                "SELECT 1 FROM user_roles WHERE cooperative_id = :cooperative_id "
                "AND user_id = :user_id AND role = :role"
            ),
            {
                "cooperative_id": current.cooperative_id,
                "user_id": target_user_id,
                "role": role,
            },
        )
        already_has_role = current_role.scalar_one_or_none() is not None
        if action == "add" and already_has_role:
            raise Conflict("Target already holds the proposed role")
        if action == "remove" and not already_has_role:
            raise Conflict("Target does not hold the role proposed for removal")

        duplicate = await session.execute(
            text(
                "SELECT 1 FROM role_change_proposals WHERE cooperative_id = :cooperative_id "
                "AND target_user_id = :target_user_id AND role = :role AND action = :action "
                "AND status = 'pending' AND expires_at > now() LIMIT 1"
            ),
            {
                "cooperative_id": current.cooperative_id,
                "target_user_id": target_user_id,
                "role": role,
                "action": action,
            },
        )
        if duplicate.scalar_one_or_none() is not None:
            raise Conflict("A pending proposal already exists for this target, role and action")

        proposal_result = await session.execute(
            text(
                "INSERT INTO role_change_proposals "
                "(cooperative_id, target_user_id, proposer_user_id, role, action, reason) "
                "VALUES (:cooperative_id, :target_user_id, :proposer_user_id, :role, :action, :reason) "
                "RETURNING id"
            ),
            {
                "cooperative_id": current.cooperative_id,
                "target_user_id": target_user_id,
                "proposer_user_id": current.user_id,
                "role": role,
                "action": action,
                "reason": reason,
            },
        )
        proposal_id = uuid.UUID(str(proposal_result.scalar_one()))
        await _audit_role_change(
            session,
            cooperative_id=current.cooperative_id,
            proposal_id=proposal_id,
            target_user_id=target_user_id,
            actor_user_id=current.user_id,
            actor_roles=actor_roles,
            event="proposed",
            role=role,
            action=action,
            reason=reason,
            ip=ip,
        )
    return proposal_id


async def approve_role_change(
    current: CurrentSession, proposal_id: uuid.UUID, ip: str | None
) -> None:
    failure: Conflict | None = None
    async with transaction(current.cooperative_id) as session:
        await _lock_cooperative_role_changes(session, current.cooperative_id)
        actor_roles = await _require_current_admin(session, current)
        proposal_result = await session.execute(
            text(
                "SELECT target_user_id, proposer_user_id, role, action, reason, status "
                "FROM role_change_proposals WHERE cooperative_id = :cooperative_id "
                "AND id = :proposal_id FOR UPDATE"
            ),
            {"cooperative_id": current.cooperative_id, "proposal_id": proposal_id},
        )
        proposal = proposal_result.mappings().one_or_none()
        if proposal is None:
            raise NotFound("Role-change proposal not found")
        target_user_id = proposal["target_user_id"]
        if proposal["proposer_user_id"] == current.user_id:
            raise Forbidden("The proposer cannot approve their own proposal")
        if proposal["action"] == "add" and target_user_id == current.user_id:
            raise Forbidden("The target cannot approve a role addition")

        await _expire_due_role_change_proposals(session, current.cooperative_id)
        refreshed = await session.execute(
            text(
                "SELECT status FROM role_change_proposals WHERE cooperative_id = :cooperative_id "
                "AND id = :proposal_id"
            ),
            {"cooperative_id": current.cooperative_id, "proposal_id": proposal_id},
        )
        status = refreshed.scalar_one()
        if status == "expired":
            failure = Conflict("Role-change proposal has expired")
        elif status != "pending":
            failure = Conflict("Role-change proposal is no longer pending")
        else:
            target_result = await session.execute(
                text(
                    "SELECT status FROM users WHERE cooperative_id = :cooperative_id "
                    "AND id = :target_user_id FOR UPDATE"
                ),
                {"cooperative_id": current.cooperative_id, "target_user_id": target_user_id},
            )
            target_user = target_result.one_or_none()
            role_result = await session.execute(
                text(
                    "SELECT 1 FROM user_roles WHERE cooperative_id = :cooperative_id "
                    "AND user_id = :target_user_id AND role = :role"
                ),
                {
                    "cooperative_id": current.cooperative_id,
                    "target_user_id": target_user_id,
                    "role": proposal["role"],
                },
            )
            role_present = role_result.scalar_one_or_none() is not None
            stale = (
                target_user is None
                or (proposal["action"] == "add" and role_present)
                or (proposal["action"] == "remove" and not role_present)
            )
            if stale:
                await session.execute(
                    text(
                        "UPDATE role_change_proposals SET status = 'rejected', decided_at = now(), "
                        "decided_by = :actor_user_id WHERE cooperative_id = :cooperative_id "
                        "AND id = :proposal_id"
                    ),
                    {
                        "actor_user_id": current.user_id,
                        "cooperative_id": current.cooperative_id,
                        "proposal_id": proposal_id,
                    },
                )
                await _audit_role_change(
                    session,
                    cooperative_id=current.cooperative_id,
                    proposal_id=proposal_id,
                    target_user_id=target_user_id,
                    actor_user_id=current.user_id,
                    actor_roles=actor_roles,
                    event="rejected",
                    role=proposal["role"],
                    action=proposal["action"],
                    reason=proposal["reason"],
                    ip=ip,
                    detail="stale",
                )
                failure = Conflict("Role-change proposal is stale; it has been rejected")
            elif proposal["action"] == "remove" and proposal["role"] == "admin":
                admin_count = await _active_admin_count(session, current.cooperative_id)
                if admin_count <= 1:
                    await session.execute(
                        text(
                            "UPDATE role_change_proposals SET status = 'rejected', decided_at = now(), "
                            "decided_by = :actor_user_id WHERE cooperative_id = :cooperative_id "
                            "AND id = :proposal_id"
                        ),
                        {
                            "actor_user_id": current.user_id,
                            "cooperative_id": current.cooperative_id,
                            "proposal_id": proposal_id,
                        },
                    )
                    await _audit_role_change(
                        session,
                        cooperative_id=current.cooperative_id,
                        proposal_id=proposal_id,
                        target_user_id=target_user_id,
                        actor_user_id=current.user_id,
                        actor_roles=actor_roles,
                        event="rejected",
                        role=proposal["role"],
                        action=proposal["action"],
                        reason=proposal["reason"],
                        ip=ip,
                        detail="last_active_admin",
                    )
                    failure = Conflict("Cannot remove the last active admin role")
            if failure is None:
                if proposal["action"] == "add":
                    await session.execute(
                        text(
                            "INSERT INTO user_roles (cooperative_id, user_id, role) "
                            "VALUES (:cooperative_id, :target_user_id, :role)"
                        ),
                        {
                            "cooperative_id": current.cooperative_id,
                            "target_user_id": target_user_id,
                            "role": proposal["role"],
                        },
                    )
                else:
                    await session.execute(
                        text(
                            "DELETE FROM user_roles WHERE cooperative_id = :cooperative_id "
                            "AND user_id = :target_user_id AND role = :role"
                        ),
                        {
                            "cooperative_id": current.cooperative_id,
                            "target_user_id": target_user_id,
                            "role": proposal["role"],
                        },
                    )
                await session.execute(
                    text(
                        "UPDATE user_sessions SET revoked_at = now() "
                        "WHERE cooperative_id = :cooperative_id AND user_id = :target_user_id "
                        "AND revoked_at IS NULL"
                    ),
                    {"cooperative_id": current.cooperative_id, "target_user_id": target_user_id},
                )
                await session.execute(
                    text(
                        "UPDATE role_change_proposals SET status = 'approved', decided_at = now(), "
                        "decided_by = :actor_user_id WHERE cooperative_id = :cooperative_id "
                        "AND id = :proposal_id"
                    ),
                    {
                        "actor_user_id": current.user_id,
                        "cooperative_id": current.cooperative_id,
                        "proposal_id": proposal_id,
                    },
                )
                await _audit_role_change(
                    session,
                    cooperative_id=current.cooperative_id,
                    proposal_id=proposal_id,
                    target_user_id=target_user_id,
                    actor_user_id=current.user_id,
                    actor_roles=actor_roles,
                    event="approved",
                    role=proposal["role"],
                    action=proposal["action"],
                    reason=proposal["reason"],
                    ip=ip,
                )
    if failure is not None:
        raise failure


async def reject_role_change(
    current: CurrentSession, proposal_id: uuid.UUID, ip: str | None
) -> None:
    failure: Conflict | None = None
    async with transaction(current.cooperative_id) as session:
        await _lock_cooperative_role_changes(session, current.cooperative_id)
        actor_roles = await _require_current_admin(session, current)
        proposal_result = await session.execute(
            text(
                "SELECT target_user_id, role, action, reason, status "
                "FROM role_change_proposals WHERE cooperative_id = :cooperative_id "
                "AND id = :proposal_id FOR UPDATE"
            ),
            {"cooperative_id": current.cooperative_id, "proposal_id": proposal_id},
        )
        proposal = proposal_result.mappings().one_or_none()
        if proposal is None:
            raise NotFound("Role-change proposal not found")
        await _expire_due_role_change_proposals(session, current.cooperative_id)
        status_result = await session.execute(
            text(
                "SELECT status FROM role_change_proposals WHERE cooperative_id = :cooperative_id "
                "AND id = :proposal_id"
            ),
            {"cooperative_id": current.cooperative_id, "proposal_id": proposal_id},
        )
        if status_result.scalar_one() == "expired":
            failure = Conflict("Role-change proposal has expired")
        elif proposal["status"] != "pending":
            failure = Conflict("Role-change proposal is no longer pending")
        else:
            await session.execute(
                text(
                    "UPDATE role_change_proposals SET status = 'rejected', decided_at = now(), "
                    "decided_by = :actor_user_id WHERE cooperative_id = :cooperative_id "
                    "AND id = :proposal_id"
                ),
                {
                    "actor_user_id": current.user_id,
                    "cooperative_id": current.cooperative_id,
                    "proposal_id": proposal_id,
                },
            )
            await _audit_role_change(
                session,
                cooperative_id=current.cooperative_id,
                proposal_id=proposal_id,
                target_user_id=proposal["target_user_id"],
                actor_user_id=current.user_id,
                actor_roles=actor_roles,
                event="rejected",
                role=proposal["role"],
                action=proposal["action"],
                reason=proposal["reason"],
                ip=ip,
            )
    if failure is not None:
        raise failure


async def cancel_role_change(
    current: CurrentSession, proposal_id: uuid.UUID, ip: str | None
) -> None:
    failure: Conflict | None = None
    async with transaction(current.cooperative_id) as session:
        await _lock_cooperative_role_changes(session, current.cooperative_id)
        proposal_result = await session.execute(
            text(
                "SELECT target_user_id, proposer_user_id, role, action, reason, status "
                "FROM role_change_proposals WHERE cooperative_id = :cooperative_id "
                "AND id = :proposal_id FOR UPDATE"
            ),
            {"cooperative_id": current.cooperative_id, "proposal_id": proposal_id},
        )
        proposal = proposal_result.mappings().one_or_none()
        if proposal is None:
            raise NotFound("Role-change proposal not found")
        if proposal["proposer_user_id"] != current.user_id:
            raise Forbidden("Only the proposer can cancel this proposal")
        await _expire_due_role_change_proposals(session, current.cooperative_id)
        status_result = await session.execute(
            text(
                "SELECT status FROM role_change_proposals WHERE cooperative_id = :cooperative_id "
                "AND id = :proposal_id"
            ),
            {"cooperative_id": current.cooperative_id, "proposal_id": proposal_id},
        )
        if status_result.scalar_one() == "expired":
            failure = Conflict("Role-change proposal has expired")
        elif proposal["status"] != "pending":
            failure = Conflict("Role-change proposal is no longer pending")
        else:
            await session.execute(
                text(
                    "UPDATE role_change_proposals SET status = 'cancelled', decided_at = now(), "
                    "decided_by = :actor_user_id WHERE cooperative_id = :cooperative_id "
                    "AND id = :proposal_id"
                ),
                {
                    "actor_user_id": current.user_id,
                    "cooperative_id": current.cooperative_id,
                    "proposal_id": proposal_id,
                },
            )
            actor_roles = await _current_user_roles(
                session, current.cooperative_id, current.user_id
            )
            await _audit_role_change(
                session,
                cooperative_id=current.cooperative_id,
                proposal_id=proposal_id,
                target_user_id=proposal["target_user_id"],
                actor_user_id=current.user_id,
                actor_roles=actor_roles,
                event="cancelled",
                role=proposal["role"],
                action=proposal["action"],
                reason=proposal["reason"],
                ip=ip,
            )
    if failure is not None:
        raise failure
