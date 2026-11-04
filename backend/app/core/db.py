"""Database access for Neon (or any Postgres).

Two kinds of connection (see docs/neon.md):
  * pooled  (-pooler host, role ajo_app)  -> API and worker, PgBouncer transaction mode
  * direct  (owner role)                  -> migrations, scheduler, pg_dump

Transaction-mode pooling rules that this module enforces by design:
  * never rely on session state (SET, search_path, session advisory locks)
  * tenant context is set with set_config(..., is_local => true) INSIDE a transaction
  * use pg_advisory_xact_lock (transaction scoped) on pooled connections
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

import asyncpg
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_SSL_REQUIRED = {"require"}
_SSL_VERIFICATION_MODES = {"verify-ca", "verify-full"}


@dataclass
class RequestDbTiming:
    sql_statements: int = 0
    database_time_ms: float = 0.0
    new_connections: int = 0
    connection_acquire_time_ms: float = 0.0

    def log_fields(self) -> dict[str, int | float]:
        return {
            "db_sql_statements": self.sql_statements,
            "db_time_ms": round(self.database_time_ms, 2),
            "db_new_connections": self.new_connections,
            "db_connection_acquire_ms": round(self.connection_acquire_time_ms, 2),
        }


_request_db_timing: ContextVar[RequestDbTiming | None] = ContextVar(
    "request_db_timing", default=None
)


def begin_request_db_timing() -> Token[RequestDbTiming | None]:
    timing = RequestDbTiming() if os.getenv("AJO_DB_TIMING") == "1" else None
    return _request_db_timing.set(timing)


def end_request_db_timing(token: Token[RequestDbTiming | None]) -> RequestDbTiming | None:
    timing = _request_db_timing.get()
    _request_db_timing.reset(token)
    return timing


def _record_sql(duration_seconds: float) -> None:
    timing = _request_db_timing.get()
    if timing is not None:
        timing.sql_statements += 1
        timing.database_time_ms += duration_seconds * 1000


def _record_new_connection() -> None:
    timing = _request_db_timing.get()
    if timing is not None:
        timing.new_connections += 1


def record_connection_acquire(duration_seconds: float) -> None:
    timing = _request_db_timing.get()
    if timing is not None:
        timing.connection_acquire_time_ms += duration_seconds * 1000


def _install_timing_listeners(engine: AsyncEngine) -> None:
    sync_engine = engine.sync_engine

    def before_cursor_execute(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if _request_db_timing.get() is not None:
            context._ajo_db_timing_started = perf_counter()

    def after_cursor_execute(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        started = getattr(context, "_ajo_db_timing_started", None)
        if started is not None:
            _record_sql(perf_counter() - started)

    def on_connect(dbapi_connection: Any, connection_record: Any) -> None:
        _record_new_connection()

    event.listen(sync_engine, "before_cursor_execute", before_cursor_execute)
    event.listen(sync_engine, "after_cursor_execute", after_cursor_execute)
    event.listen(sync_engine, "connect", on_connect)


def validate_application_role_identity(
    *,
    current_user: str,
    is_database_owner: bool,
    bypasses_rls: bool,
    member_of_bypass_role: bool,
) -> None:
    """Reject credentials that would defeat tenant RLS for API/worker processes."""
    if is_database_owner:
        raise RuntimeError(
            f"database role {current_user!r} must not be the database owner for an application process"
        )
    if bypasses_rls or member_of_bypass_role:
        raise RuntimeError(
            f"database role {current_user!r} must not bypass row-level security for an application process"
        )


async def check_application_role(url: str) -> None:
    """Verify the configured connection role is not an owner or RLS bypass role."""
    if not url:
        raise RuntimeError("DATABASE_URL is required for an application process")
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        row = await conn.fetchrow(
            """
            SELECT current_user,
                   (r.oid = d.datdba) AS is_database_owner,
                   r.rolsuper OR r.rolbypassrls AS bypasses_rls,
                   EXISTS (
                       SELECT 1
                       FROM pg_roles bypass_role
                       WHERE bypass_role.rolbypassrls
                         AND pg_has_role(current_user, bypass_role.oid, 'member')
                   ) AS member_of_bypass_role
            FROM pg_roles r
            CROSS JOIN pg_database d
            WHERE r.rolname = current_user AND d.datname = current_database()
            """
        )
    finally:
        await conn.close()
    if row is None:
        raise RuntimeError("could not inspect the configured database role")
    validate_application_role_identity(
        current_user=row["current_user"],
        is_database_owner=row["is_database_owner"],
        bypasses_rls=row["bypasses_rls"],
        member_of_bypass_role=row["member_of_bypass_role"],
    )


def split_url(url: str) -> tuple[str, str | None]:
    """Return (url without libpq-only params, sslmode).

    Neon connection strings carry ``sslmode`` and sometimes ``channel_binding``.
    asyncpg rejects ``channel_binding`` and SQLAlchemy+asyncpg ignores ``sslmode``,
    so we strip both and pass SSL explicitly.
    """
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query))
    sslmode = params.pop("sslmode", None)
    params.pop("channel_binding", None)
    return parsed._replace(query=urlencode(params)).geturl(), sslmode


def asyncpg_dsn(url: str) -> str:
    """DSN for raw asyncpg use (scripts). Keeps sslmode, drops channel_binding."""
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query))
    params.pop("channel_binding", None)
    return parsed._replace(query=urlencode(params)).geturl()


def to_sqlalchemy_async_url(
    url: str, *, disable_prepared_cache: bool = True
) -> tuple[str, dict[str, Any]]:
    clean, sslmode = split_url(url)
    parsed = urlparse(clean)
    params = dict(parse_qsl(parsed.query))
    connect_args: dict[str, Any] = {}
    if sslmode in _SSL_VERIFICATION_MODES:
        raise ValueError(
            f"sslmode={sslmode} is not supported by this adapter; certificate verification "
            "is not enforced by the SQLAlchemy asyncpg URL conversion"
        )
    if sslmode in _SSL_REQUIRED:
        connect_args["ssl"] = "require"
    if disable_prepared_cache:
        params["prepared_statement_cache_size"] = "0"
        connect_args["statement_cache_size"] = 0
    final = parsed._replace(scheme="postgresql+asyncpg", query=urlencode(params)).geturl()
    return final, connect_args


def make_engine(url: str, *, pooled: bool, application_name: str = "ajo-api") -> AsyncEngine:
    settings = get_settings()
    async_url, connect_args = to_sqlalchemy_async_url(
        url, disable_prepared_cache=settings.db_disable_prepared_cache if pooled else False
    )
    connect_args["server_settings"] = {"application_name": application_name}
    engine = create_async_engine(
        async_url,
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=settings.db_pool_size if pooled else 2,
        max_overflow=settings.db_max_overflow if pooled else 0,
    )
    _install_timing_listeners(engine)
    return engine


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not set")
        _engine = make_engine(settings.database_url, pooled=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine, _sessionmaker = None, None


@asynccontextmanager
async def connection() -> AsyncIterator[Any]:
    started = perf_counter()
    async with get_engine().connect() as conn:
        record_connection_acquire(perf_counter() - started)
        yield conn


@asynccontextmanager
async def transaction(cooperative_id: uuid.UUID | None = None) -> AsyncIterator[AsyncSession]:
    """One explicit transaction, optionally scoped to a cooperative (tenant).

    The tenant id is applied with set_config(..., true), i.e. SET LOCAL semantics, so it
    disappears at COMMIT/ROLLBACK and can never leak to the next user of a pooled
    server connection. Row-level security policies read ``app.cooperative_id``.
    """
    get_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as session, session.begin():
        if _request_db_timing.get() is not None:
            started = perf_counter()
            await session.connection()
            record_connection_acquire(perf_counter() - started)
        if cooperative_id is not None:
            await session.execute(
                text("SELECT set_config('app.cooperative_id', :cid, true)"),
                {"cid": str(cooperative_id)},
            )
        yield session


async def lock_cooperative_member(
    session: AsyncSession, cooperative_id: uuid.UUID, member_id: uuid.UUID
) -> None:
    """Serialize financial mutations for one member within one cooperative."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 5))"),
        {"lock_key": f"{cooperative_id}:{member_id}"},
    )
