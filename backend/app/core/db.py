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

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_SSL_REQUIRED = {"require", "verify-ca", "verify-full"}


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
    return create_async_engine(
        async_url,
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=settings.db_pool_size if pooled else 2,
        max_overflow=settings.db_max_overflow if pooled else 0,
    )


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
async def transaction(cooperative_id: uuid.UUID | None = None) -> AsyncIterator[AsyncSession]:
    """One explicit transaction, optionally scoped to a cooperative (tenant).

    The tenant id is applied with set_config(..., true), i.e. SET LOCAL semantics, so it
    disappears at COMMIT/ROLLBACK and can never leak to the next user of a pooled
    server connection. Row-level security policies read ``app.cooperative_id``.
    """
    get_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as session, session.begin():
        if cooperative_id is not None:
            await session.execute(
                text("SELECT set_config('app.cooperative_id', :cid, true)"),
                {"cid": str(cooperative_id)},
            )
        yield session
