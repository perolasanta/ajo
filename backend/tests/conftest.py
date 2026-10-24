"""Local, disposable PostgreSQL test database. Tests must never use Neon."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import asyncpg
import pytest

DEFAULT_TEST_DATABASE_URL = "postgresql://postgres:pg@localhost:55432/postgres"
DATABASE_NAME = "ajo_test"
APP_PASSWORD = "ajo_test_app_pw"
READONLY_PASSWORD = "ajo_test_readonly_pw"
ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"


def _database_url(base_url: str, database: str, user: str, password: str) -> str:
    parsed = urlsplit(base_url)
    host = parsed.hostname or "localhost"
    if host == "localhost":
        host = "127.0.0.1"
    port = f":{parsed.port}" if parsed.port is not None else ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{quote(user)}:{quote(password)}@{host}{port}"
    return urlunsplit((parsed.scheme, netloc, f"/{database}", parsed.query, ""))


TEST_DATABASE_URL = os.environ.setdefault("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
_parsed_test_url = urlsplit(TEST_DATABASE_URL)
if _parsed_test_url.hostname not in {"localhost", "127.0.0.1", "::1"}:
    raise RuntimeError(
        "TEST_DATABASE_URL must point to local PostgreSQL; Neon is forbidden in tests"
    )

TEST_ADMIN_URL = _database_url(
    TEST_DATABASE_URL,
    _parsed_test_url.path.lstrip("/") or "postgres",
    _parsed_test_url.username or "postgres",
    _parsed_test_url.password or "pg",
)
TEST_OWNER_URL = _database_url(
    TEST_DATABASE_URL,
    DATABASE_NAME,
    _parsed_test_url.username or "postgres",
    _parsed_test_url.password or "pg",
)
TEST_APP_URL = _database_url(TEST_DATABASE_URL, DATABASE_NAME, "ajo_app", APP_PASSWORD)

# Runtime application settings and Alembic must be local before test modules import the app.
os.environ["DATABASE_URL"] = TEST_APP_URL
os.environ["DATABASE_URL_DIRECT"] = TEST_OWNER_URL
os.environ["TEST_APP_DATABASE_URL"] = TEST_APP_URL


@dataclass(frozen=True)
class TestDatabase:
    app_url: str
    owner_url: str


@dataclass(frozen=True)
class TenantSeed:
    cooperative_id: uuid.UUID
    user_ids: tuple[uuid.UUID, ...]
    member_id: uuid.UUID | None


async def create_tenant(
    conn: asyncpg.Connection,
    *,
    cooperative_id: uuid.UUID | None = None,
    name: str = "Test cooperative",
    slug_prefix: str = "test",
    roles: tuple[str, ...] = ("officer", "treasurer"),
    include_member: bool = False,
    password_hash: str | None = None,
) -> TenantSeed:
    """Create a fresh tenant as ajo_app after setting its RLS context on the connection."""
    cooperative_id = cooperative_id or uuid.uuid4()
    # This must be session scoped and precede all tenant inserts, including the cooperative.
    await conn.execute("SELECT set_config('app.cooperative_id', $1, false)", str(cooperative_id))
    await conn.execute(
        "INSERT INTO cooperatives(id,name,slug) VALUES($1,$2,$3)",
        cooperative_id,
        name,
        f"{slug_prefix}-{cooperative_id.hex}",
    )
    users: list[uuid.UUID] = []
    for index, role in enumerate(roles):
        user_id = uuid.uuid4()
        users.append(user_id)
        await conn.execute(
            "INSERT INTO users(id,cooperative_id,email,password_hash) VALUES($1,$2,$3,$4)",
            user_id,
            cooperative_id,
            f"{role}-{index}-{cooperative_id.hex}@example.test",
            password_hash if password_hash is not None else "unused",
        )
        await conn.execute(
            "INSERT INTO user_roles(cooperative_id,user_id,role) VALUES($1,$2,$3)",
            cooperative_id,
            user_id,
            role,
        )
    member_id = None
    if include_member:
        member_id = uuid.uuid4()
        await conn.execute(
            """INSERT INTO members(id,cooperative_id,member_no,full_name,phone_e164)
               VALUES($1,$2,'M-1','Test Member','+2348000000000')""",
            member_id,
            cooperative_id,
        )
    return TenantSeed(cooperative_id, tuple(users), member_id)


async def _reset_database() -> None:
    admin_url = TEST_ADMIN_URL
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {DATABASE_NAME} WITH (FORCE)")
        for role in ("ajo_app", "ajo_readonly"):
            await conn.execute(f"DROP ROLE IF EXISTS {role}")
        await conn.execute(f"CREATE DATABASE {DATABASE_NAME}")
    finally:
        await conn.close()

    test_conn = await asyncpg.connect(TEST_OWNER_URL)
    try:
        roles_sql = (BACKEND / "scripts" / "neon_roles.sql").read_text()
        roles_sql = roles_sql.replace("CHANGE_ME_APP", APP_PASSWORD)
        roles_sql = roles_sql.replace("CHANGE_ME_READONLY", READONLY_PASSWORD)
        roles_sql = roles_sql.replace("DATABASE ajo TO", f"DATABASE {DATABASE_NAME} TO")
        await test_conn.execute(roles_sql)
    finally:
        await test_conn.close()

    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_APP_URL
    env["DATABASE_URL_DIRECT"] = TEST_OWNER_URL
    env["TEST_DATABASE_URL"] = TEST_DATABASE_URL
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "upgrade",
        "head",
        cwd=BACKEND,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            "Local test database migration failed:\n"
            + stdout.decode(errors="replace")
            + stderr.decode(errors="replace")
        )


async def _drop_database() -> None:
    conn = await asyncpg.connect(TEST_ADMIN_URL)
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {DATABASE_NAME} WITH (FORCE)")
        for role in ("ajo_app", "ajo_readonly"):
            await conn.execute(f"DROP ROLE IF EXISTS {role}")
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def test_database() -> TestDatabase:
    try:
        asyncio.run(_reset_database())
    except Exception:
        asyncio.run(_drop_database())
        raise
    yield TestDatabase(TEST_APP_URL, TEST_OWNER_URL)
    asyncio.run(_drop_database())


@pytest.fixture(scope="session", autouse=True)
def hypothesis_profiles() -> None:
    from hypothesis import settings

    settings.register_profile("default", max_examples=25, deadline=None)
    settings.register_profile("ci", max_examples=200, deadline=None)
    settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
