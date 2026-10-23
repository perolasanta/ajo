"""Verify the Neon (or any Postgres) setup matches what Ajo assumes.

Usage (from /backend, with .env or exported variables):
    python scripts/neon_smoke_test.py

Checks:
  1. Both connections work; shows role, server version, host.
  2. The app role is NOT superuser, NOT BYPASSRLS, NOT in neon_superuser (RLS must apply).
  3. Pooled behaviour: tenant context via set_config(...,true) never leaks between transactions,
     pg_advisory_xact_lock works, parameterised queries work repeatedly.
  4. RLS isolates tenants (runs in a transaction that is ROLLED BACK, leaving no data).
  5. Latency from THIS machine (run it on the Frankfurt VPS for the real number), incl. first query.
Exit code is non-zero if any critical check fails.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time
import uuid
from pathlib import Path  # noqa: E402
from urllib.parse import urlparse

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # repo-root .env
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.db import asyncpg_dsn  # noqa: E402

FAILS: list[str] = []


def ok(msg: str) -> None:
    print(f"  PASS  {msg}")


def bad(msg: str) -> None:
    print(f"  FAIL  {msg}")
    FAILS.append(msg)


def check(cond: bool, msg: str) -> None:
    (ok if cond else bad)(msg)


async def connect(url: str, *, cache: bool) -> asyncpg.Connection:
    return await asyncpg.connect(asyncpg_dsn(url), statement_cache_size=100 if cache else 0)


async def main() -> int:
    pooled = os.environ.get("DATABASE_URL", "")
    direct = os.environ.get("DATABASE_URL_DIRECT", "")
    if not pooled or not direct:
        print("Set DATABASE_URL (pooled, ajo_app) and DATABASE_URL_DIRECT (owner).")
        return 2

    print("\n1. Connections")
    t0 = time.perf_counter()
    d = await connect(direct, cache=False)
    first_direct_ms = (time.perf_counter() - t0) * 1000
    a = await connect(pooled, cache=False)
    for label, conn, url in (("direct", d, direct), ("pooled", a, pooled)):
        row = await conn.fetchrow("select current_user, current_setting('server_version') v")
        host = urlparse(url).hostname
        print(f"  {label}: user={row['current_user']} pg={row['v']} host={host}")
    check(
        "-pooler" in (urlparse(pooled).hostname or "")
        or "localhost" in pooled
        or "127.0.0.1" in pooled,
        "DATABASE_URL uses the -pooler host (or is local)",
    )
    check(
        "-pooler" not in (urlparse(direct).hostname or ""),
        "DATABASE_URL_DIRECT is NOT the pooler host",
    )
    print(
        f"  first direct connect: {first_direct_ms:.0f} ms (a cold Neon compute makes this large)"
    )

    print("\n2. App role privileges (RLS must apply to it)")
    r = await a.fetchrow(
        "select rolsuper, rolbypassrls, rolcreaterole, rolcreatedb from pg_roles where rolname = current_user"
    )
    check(not r["rolsuper"], "app role is not superuser")
    check(
        not r["rolbypassrls"],
        "app role does NOT bypass RLS (create it with SQL, not in the console)",
    )
    check(
        not r["rolcreaterole"] and not r["rolcreatedb"], "app role cannot create roles or databases"
    )
    member = await a.fetchval(
        "select count(*) from pg_auth_members m join pg_roles g on g.oid = m.roleid "
        "join pg_roles u on u.oid = m.member where u.rolname = current_user and g.rolname = 'neon_superuser'"
    )
    check(member == 0, "app role is not a member of neon_superuser")

    print("\n3. Transaction-pooling behaviour")
    leaked = 0
    for i in range(30):
        async with a.transaction():
            await a.execute("select set_config('app.cooperative_id', $1, true)", f"tenant-{i}")
            inside = await a.fetchval("select current_setting('app.cooperative_id', true)")
            assert inside == f"tenant-{i}"
        after = await a.fetchval("select current_setting('app.cooperative_id', true)")
        if after not in (None, ""):
            leaked += 1
    check(leaked == 0, "tenant context (set_config local) does not leak after COMMIT (30 rounds)")
    async with a.transaction():
        got = await a.fetchval("select pg_try_advisory_xact_lock(424242)")
    check(bool(got), "pg_advisory_xact_lock works inside a transaction")
    cached = await connect(pooled, cache=True)
    try:
        for i in range(25):
            v = await cached.fetchval("select $1::int + 1", i)
            assert v == i + 1
        ok(
            "parameterised queries repeat fine WITH asyncpg's prepared cache enabled "
            "(you may set DB_DISABLE_PREPARED_CACHE=false)"
        )
    except Exception as exc:  # noqa: BLE001
        print(
            f"  NOTE  prepared cache on pooled connection failed ({exc}); keep DB_DISABLE_PREPARED_CACHE=true"
        )
    finally:
        await cached.close()

    print("\n4. Row-level security (rolled back, no data kept)")
    has_table = await a.fetchval("select to_regclass('public.cooperatives') is not null")
    if not has_table:
        print("  SKIP  run `alembic upgrade head` first")
    else:
        id_a, id_b = uuid.uuid4(), uuid.uuid4()
        tx = a.transaction()
        await tx.start()
        try:
            await a.execute("select set_config('app.cooperative_id', $1, true)", str(id_a))
            await a.execute(
                "insert into cooperatives (id, name, slug) values ($1,'A',$2)", id_a, f"a-{id_a}"
            )
            await a.execute("select set_config('app.cooperative_id', $1, true)", str(id_b))
            await a.execute(
                "insert into cooperatives (id, name, slug) values ($1,'B',$2)", id_b, f"b-{id_b}"
            )
            await a.execute("select set_config('app.cooperative_id', $1, true)", str(id_a))
            seen = await a.fetch("select id from cooperatives")
            check([x["id"] for x in seen] == [id_a], "tenant A sees only tenant A")
            await a.execute("select set_config('app.cooperative_id', '', true)")
            none = await a.fetchval("select count(*) from cooperatives")
            check(none == 0, "no tenant context means no rows")
            await a.execute("select set_config('app.cooperative_id', $1, true)", str(id_a))
            try:
                async with a.transaction():  # savepoint
                    await a.execute(
                        "insert into cooperatives (id, name, slug) values ($1,'X',$2)",
                        uuid.uuid4(),
                        f"x-{uuid.uuid4()}",
                    )
                bad("tenant A could insert a row for another tenant")
            except asyncpg.PostgresError:
                ok("tenant A cannot insert a row outside its own tenant")
        finally:
            await tx.rollback()

    print("\n5. Latency from this machine")
    for label, conn in (("direct", d), ("pooled", a)):
        samples = []
        for _ in range(20):
            t = time.perf_counter()
            await conn.fetchval("select 1")
            samples.append((time.perf_counter() - t) * 1000)
        samples.sort()
        print(
            f"  {label}: p50={statistics.median(samples):.1f} ms  p95={samples[int(len(samples) * 0.95) - 1]:.1f} ms"
        )

    await d.close()
    await a.close()
    print("\nRESULT:", "FAILED" if FAILS else "all critical checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
