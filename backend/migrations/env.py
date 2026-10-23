"""Alembic runs on the DIRECT connection as the owner role (never the pooler)."""

import asyncio
import os
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.db import to_sqlalchemy_async_url

load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # repo-root .env
config = context.config
target_metadata = None  # raw SQL migrations; ledger rules live in SQL


def _url() -> tuple[str, dict]:
    raw = os.environ.get("DATABASE_URL_DIRECT") or config.get_main_option("sqlalchemy.url")
    if not raw:
        raise RuntimeError("Set DATABASE_URL_DIRECT (direct Neon URL, owner role)")
    return to_sqlalchemy_async_url(raw, disable_prepared_cache=False)


def run_migrations_offline() -> None:
    url, _ = _url()
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    url, connect_args = _url()
    engine = create_async_engine(url, connect_args=connect_args, poolclass=pool.NullPool)
    async with engine.connect() as conn:
        await conn.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
