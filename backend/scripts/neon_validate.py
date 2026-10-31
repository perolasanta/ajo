"""Validate Ajo's PostgreSQL assumptions against a disposable target branch.

This script reads only the explicitly exported URLs below; it never loads .env and never
prints a URL, password, row contents, or SQL error text. It performs only rolled-back
fixture writes, advisory-lock probes, catalog reads, and a rolled-back app-role smoke write.

Required environment:
    DATABASE_URL                   pooled target URL, user ajo_app
    DATABASE_URL_DIRECT            direct target URL, owner
    DATABASE_URL_SCHEDULER         direct target URL, user ajo_scheduler
    NEON_VALIDATE_REFERENCE_URL    direct URL to a fresh LOCAL reference database at Alembic head

Example:
    python scripts/neon_validate.py --confirm-disposable-branch batch-5-check --phase before-restore

The reference database must be built locally from the checked-in Alembic migrations at the
same PostgreSQL major version as the target. Read docs/neon.md before using this script.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.core.db import asyncpg_dsn, validate_application_role_identity  # noqa: E402

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
SCHEDULER_LOCK_KEY = 727_001


class ValidationFailure(Exception):
    """A safe-to-print validation failure without connection or row data."""


class Reporter:
    def __init__(self) -> None:
        self.failures = 0

    def result(self, number: int, title: str, passed: bool, detail: str) -> None:
        label = "PASS" if passed else "FAIL"
        print(f"{label} {number}. {title}: {detail}")
        if not passed:
            self.failures += 1

    async def run(
        self,
        number: int,
        title: str,
        operation: Callable[[], Awaitable[str]],
    ) -> None:
        try:
            detail = await operation()
        except ValidationFailure as exc:
            self.result(number, title, False, str(exc))
        except Exception as exc:  # Never print driver messages, which may contain URLs.
            self.result(number, title, False, f"operation unavailable ({type(exc).__name__})")
        else:
            self.result(number, title, True, detail)


def required_url(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValidationFailure(f"required environment variable {name} is missing")
    parsed = urlparse(value)
    if parsed.scheme not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        raise ValidationFailure(f"{name} must be a PostgreSQL URL")
    return value


def target_urls() -> tuple[str, str, str]:
    urls = (
        required_url("DATABASE_URL"),
        required_url("DATABASE_URL_DIRECT"),
        required_url("DATABASE_URL_SCHEDULER"),
    )
    parsed = [urlparse(value) for value in urls]
    hosts = [(item.hostname or "").lower() for item in parsed]
    databases = [item.path.rstrip("/") for item in parsed]
    if len(set(databases)) != 1:
        raise ValidationFailure("all three target URLs must point to the same database")
    local = all(host in LOCAL_HOSTS for host in hosts)
    if local:
        return urls
    normalized = [host.replace("-pooler.", ".") for host in hosts]
    if len(set(normalized)) != 1:
        raise ValidationFailure(
            "pooled, owner, and scheduler URLs must target the same Neon endpoint"
        )
    if "-pooler" not in hosts[0]:
        raise ValidationFailure("DATABASE_URL must use the transaction-pooler host")
    if "-pooler" in hosts[1] or "-pooler" in hosts[2]:
        raise ValidationFailure(
            "DATABASE_URL_DIRECT and DATABASE_URL_SCHEDULER must be direct hosts"
        )
    return urls


def reference_url() -> str:
    value = required_url("NEON_VALIDATE_REFERENCE_URL")
    host = (urlparse(value).hostname or "").lower()
    if host not in LOCAL_HOSTS:
        raise ValidationFailure(
            "NEON_VALIDATE_REFERENCE_URL must point to local PostgreSQL, not a remote database"
        )
    return value


def ensure_distinct_reference(target: str, reference: str) -> None:
    target_parts = urlparse(target)
    reference_parts = urlparse(reference)
    same_host = (target_parts.hostname or "").lower() == (reference_parts.hostname or "").lower()
    same_database = target_parts.path.rstrip("/") == reference_parts.path.rstrip("/")
    if same_host and same_database:
        raise ValidationFailure("reference URL must be a separate database from the target")


async def connect(url: str, *, pooled: bool = False) -> asyncpg.Connection:
    parsed = urlparse(url)
    if parsed.scheme == "postgresql+asyncpg":
        url = parsed._replace(scheme="postgresql").geturl()
    return await asyncpg.connect(
        asyncpg_dsn(url),
        timeout=12,
        statement_cache_size=0 if pooled else 100,
        server_settings={"application_name": "ajo-neon-validate"},
    )


async def current_migration_heads(conn: asyncpg.Connection) -> set[str]:
    exists = await conn.fetchval("SELECT to_regclass('public.alembic_version') IS NOT NULL")
    if not exists:
        return set()
    rows = await conn.fetch("SELECT version_num FROM public.alembic_version ORDER BY version_num")
    return {str(row["version_num"]) for row in rows}


async def catalog_snapshot(conn: asyncpg.Connection) -> dict[str, list[dict[str, object]]]:
    queries = {
        "relations": """
            SELECT c.relname, c.relkind, c.relpersistence, c.relispartition,
                   c.relrowsecurity, c.relforcerowsecurity
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','S','f')
             ORDER BY c.relname
        """,
        "columns": """
            SELECT c.relname, a.attnum, a.attname,
                   pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
                   a.attnotnull, a.attidentity, a.attgenerated,
                   pg_catalog.pg_get_expr(d.adbin, d.adrelid) AS default_expr,
                   co.collname AS collation
              FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace
              LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
              LEFT JOIN pg_collation co ON co.oid = a.attcollation
             WHERE n.nspname = 'public' AND a.attnum > 0 AND NOT a.attisdropped
               AND c.relkind IN ('r','p','v','m','f')
             ORDER BY c.relname, a.attnum
        """,
        "constraints": """
            SELECT c.relname, con.conname, con.contype,
                   pg_catalog.pg_get_constraintdef(con.oid, true) AS definition,
                   con.convalidated, con.condeferrable, con.condeferred
              FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public'
             ORDER BY c.relname, con.conname
        """,
        "indexes": """
            SELECT tablename, indexname, indexdef FROM pg_indexes
             WHERE schemaname = 'public' ORDER BY tablename, indexname
        """,
        "policies": """
            SELECT tablename, policyname, permissive, roles, cmd, qual, with_check
              FROM pg_policies WHERE schemaname = 'public'
             ORDER BY tablename, policyname
        """,
        "routines": """
            SELECT p.proname, p.prokind, pg_catalog.pg_get_function_identity_arguments(p.oid) AS args,
                   pg_catalog.pg_get_function_result(p.oid) AS result_type,
                   l.lanname, p.provolatile, p.proisstrict, p.prosecdef,
                   pg_catalog.pg_get_functiondef(p.oid) AS definition
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
              JOIN pg_language l ON l.oid = p.prolang
             WHERE n.nspname = 'public' ORDER BY p.proname, args
        """,
        "triggers": """
            SELECT c.relname AS table_name, t.tgname,
                   pg_catalog.pg_get_triggerdef(t.oid, true) AS definition
              FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND NOT t.tgisinternal
             ORDER BY c.relname, t.tgname
        """,
        "types": """
            SELECT t.typname, t.typtype, t.typnotnull,
                   CASE WHEN t.typtype = 'd'
                        THEN pg_catalog.format_type(t.typbasetype, t.typtypmod) END AS domain_base,
                   ARRAY(SELECT e.enumlabel FROM pg_enum e
                          WHERE e.enumtypid = t.oid ORDER BY e.enumsortorder) AS enum_values,
                   ARRAY(SELECT con.conname || ':' || pg_catalog.pg_get_constraintdef(con.oid, true)
                           FROM pg_constraint con WHERE con.contypid = t.oid ORDER BY con.conname)
                       AS domain_constraints
              FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
             WHERE n.nspname = 'public' AND t.typtype IN ('d','e')
             ORDER BY t.typname
        """,
        "sequences": """
            SELECT c.relname, s.seqtypid::regtype::text AS data_type,
                   s.seqstart, s.seqincrement, s.seqmax, s.seqmin, s.seqcache, s.seqcycle
              FROM pg_sequence s JOIN pg_class c ON c.oid = s.seqrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' ORDER BY c.relname
        """,
        "views": """
            SELECT c.relname, c.relkind, pg_catalog.pg_get_viewdef(c.oid, true) AS definition
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind IN ('v','m') ORDER BY c.relname
        """,
    }
    snapshot: dict[str, list[dict[str, object]]] = {}
    for category, query in queries.items():
        rows = await conn.fetch(query)
        snapshot[category] = [{key: value for key, value in row.items()} for row in rows]
    return snapshot


def fingerprint(snapshot: dict[str, list[dict[str, object]]]) -> str:
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def differing_categories(
    actual: dict[str, list[dict[str, object]]], expected: dict[str, list[dict[str, object]]]
) -> list[str]:
    return [category for category in expected if actual.get(category) != expected[category]]


async def role_identity(conn: asyncpg.Connection) -> asyncpg.Record:
    row = await conn.fetchrow(
        """
        SELECT current_user AS role_name, r.rolsuper, r.rolbypassrls,
               r.rolcreaterole, r.rolcreatedb, r.oid = d.datdba AS is_database_owner,
               EXISTS (
                   SELECT 1 FROM pg_roles bypass_role
                    WHERE bypass_role.rolbypassrls
                      AND pg_has_role(current_user, bypass_role.oid, 'member')
               ) AS member_of_bypass_role
          FROM pg_roles r CROSS JOIN pg_database d
         WHERE r.rolname = current_user AND d.datname = current_database()
        """
    )
    if row is None:
        raise ValidationFailure("could not inspect the connected role")
    return row


async def check_schema() -> str:
    _, direct, _ = target_urls()
    ref = reference_url()
    ensure_distinct_reference(direct, ref)
    if not urlparse(direct).path.lstrip("/"):
        raise ValidationFailure("target direct URL has no database name")

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    expected_heads = set(ScriptDirectory.from_config(config).get_heads())
    target_conn = await connect(direct)
    ref_conn = await connect(ref)
    try:
        target_major = int(await target_conn.fetchval("SHOW server_version_num")) // 10000
        ref_major = int(await ref_conn.fetchval("SHOW server_version_num")) // 10000
        if target_major != ref_major:
            raise ValidationFailure(
                f"target/reference PostgreSQL major versions differ ({target_major} vs {ref_major})"
            )
        target_heads = await current_migration_heads(target_conn)
        ref_heads = await current_migration_heads(ref_conn)
        if target_heads != expected_heads:
            raise ValidationFailure(
                f"target Alembic version {sorted(target_heads)} != repository head {sorted(expected_heads)}"
            )
        if ref_heads != expected_heads:
            raise ValidationFailure(
                f"local reference is not fresh at repository head {sorted(expected_heads)}"
            )
        actual = await catalog_snapshot(target_conn)
        expected = await catalog_snapshot(ref_conn)
        differences = differing_categories(actual, expected)
        if differences:
            raise ValidationFailure(
                "schema differs from fresh migration reference in: " + ", ".join(differences)
            )
        return (
            f"schema fingerprint {fingerprint(actual)[:12]} matches local migration reference; "
            f"target and reference Alembic heads are {sorted(expected_heads)}"
        )
    finally:
        await target_conn.close()
        await ref_conn.close()


async def check_rls() -> str:
    pooled, _, _ = target_urls()
    local_target = (urlparse(pooled).hostname or "").lower() in LOCAL_HOSTS
    conn = await connect(pooled, pooled=True)
    try:
        uncovered = await conn.fetchval(
            """
            SELECT count(*) FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
               AND c.relname <> 'tenant_directory'
               AND EXISTS (
                   SELECT 1 FROM pg_attribute a
                    WHERE a.attrelid = c.oid AND a.attname = 'cooperative_id'
                      AND a.attnum > 0 AND NOT a.attisdropped
               ) AND NOT c.relrowsecurity
            """
        )
        if int(uncovered) != 0:
            raise ValidationFailure(f"{uncovered} tenant tables lack row-level security")

        tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SELECT set_config('app.cooperative_id', $1, true)", str(tenant_a))
            await conn.execute(
                "INSERT INTO cooperatives (id, name, slug) VALUES ($1, 'validation-a', $2)",
                tenant_a,
                f"validate-a-{tenant_a.hex}",
            )
            await conn.execute("SELECT set_config('app.cooperative_id', $1, true)", str(tenant_b))
            await conn.execute(
                "INSERT INTO cooperatives (id, name, slug) VALUES ($1, 'validation-b', $2)",
                tenant_b,
                f"validate-b-{tenant_b.hex}",
            )
            for active, hidden in ((tenant_a, tenant_b), (tenant_b, tenant_a)):
                await conn.execute("SELECT set_config('app.cooperative_id', $1, true)", str(active))
                visible = await conn.fetch("SELECT id FROM cooperatives ORDER BY id")
                ids = {row["id"] for row in visible}
                if ids != {active}:
                    raise ValidationFailure("a tenant could read another tenant's cooperative row")
                cross_read = await conn.fetchval(
                    "SELECT count(*) FROM cooperatives WHERE id = $1", hidden
                )
                if int(cross_read) != 0:
                    raise ValidationFailure(
                        "cross-tenant row was visible through a direct predicate"
                    )
        finally:
            await tx.rollback()

        for index in range(20):
            tenant_id = uuid.uuid4()
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.cooperative_id', $1, true)", str(tenant_id)
                )
                value = await conn.fetchval("SELECT current_setting('app.cooperative_id', true)")
                if value != str(tenant_id):
                    raise ValidationFailure("transaction-local tenant context was not set")
            async with conn.transaction():
                value = await conn.fetchval("SELECT current_setting('app.cooperative_id', true)")
                if value not in (None, ""):
                    raise ValidationFailure(
                        f"tenant context leaked after pooled transaction reuse at round {index + 1}"
                    )
        if local_target:
            return "tenant A/B isolation and 20 transaction reuses passed on local PostgreSQL; Neon PgBouncer remains unverified"
        return "tenant A/B isolation passed and transaction-local context cleared on 20 Neon-pooler transaction reuses"
    finally:
        await conn.close()


async def check_roles() -> str:
    pooled, _, scheduler_url = target_urls()
    app_conn = await connect(pooled, pooled=True)
    scheduler_conn = await connect(scheduler_url)
    try:
        app = await role_identity(app_conn)
        scheduler = await role_identity(scheduler_conn)
        for name, row, expected in (
            ("app", app, "ajo_app"),
            ("scheduler", scheduler, "ajo_scheduler"),
        ):
            if row["role_name"] != expected:
                raise ValidationFailure(
                    f"{name} URL authenticated as {row['role_name']!r}, expected {expected}"
                )
            if (
                row["is_database_owner"]
                or row["rolsuper"]
                or row["rolbypassrls"]
                or row["member_of_bypass_role"]
            ):
                raise ValidationFailure(f"{name} role is owner, superuser, or can bypass RLS")
            if row["rolcreaterole"] or row["rolcreatedb"]:
                raise ValidationFailure(f"{name} role can create roles or databases")

        rejected = 0
        for owner, bypass, member in (
            (True, False, False),
            (False, True, False),
            (False, False, True),
        ):
            try:
                validate_application_role_identity(
                    current_user="validation-role",
                    is_database_owner=owner,
                    bypasses_rls=bypass,
                    member_of_bypass_role=member,
                )
            except RuntimeError:
                rejected += 1
        if rejected != 3:
            raise ValidationFailure("startup role guard did not reject all unsafe role cases")
        validate_application_role_identity(
            current_user="ajo_app",
            is_database_owner=False,
            bypasses_rls=False,
            member_of_bypass_role=False,
        )
        return "ajo_app and ajo_scheduler are non-owner/non-bypass roles; startup guard rejects owner and bypass cases"
    finally:
        await app_conn.close()
        await scheduler_conn.close()


async def check_xact_lock(
    conn_a: asyncpg.Connection,
    conn_b: asyncpg.Connection,
    label: str,
    lock_sql: str,
    try_sql: str,
    lock_argument: str,
) -> None:
    tx_a = conn_a.transaction()
    tx_b = conn_b.transaction()
    await tx_a.start()
    await tx_b.start()
    await conn_a.execute(lock_sql, lock_argument)
    acquired_while_held = await conn_b.fetchval(try_sql, lock_argument)
    if acquired_while_held:
        raise ValidationFailure(f"{label} lock did not serialize competing pooled transactions")
    await tx_a.commit()
    acquired_after_release = await conn_b.fetchval(try_sql, lock_argument)
    if not acquired_after_release:
        raise ValidationFailure(f"{label} lock remained held after transaction completion")
    await tx_b.commit()


async def check_locks() -> str:
    pooled, _, scheduler_url = target_urls()
    local_target = (urlparse(pooled).hostname or "").lower() in LOCAL_HOSTS
    conn_a = await connect(pooled, pooled=True)
    conn_b = await connect(pooled, pooled=True)
    scheduler_a: asyncpg.Connection | None = None
    scheduler_b: asyncpg.Connection | None = None
    try:
        member_key = f"validation:{uuid.uuid4()}:{uuid.uuid4()}"
        await check_xact_lock(
            conn_a,
            conn_b,
            "per-member withdrawal",
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 5))",
            "SELECT pg_try_advisory_xact_lock(hashtextextended($1, 5))",
            member_key,
        )
        tenant_key = str(uuid.uuid4())
        await check_xact_lock(
            conn_a,
            conn_b,
            "per-tenant audit/withdrawal",
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            "SELECT pg_try_advisory_xact_lock(hashtextextended($1, 0))",
            tenant_key,
        )

        scheduler_a = await connect(scheduler_url)
        scheduler_b = await connect(scheduler_url)
        if (
            await scheduler_a.fetchval("SELECT pg_try_advisory_lock($1)", SCHEDULER_LOCK_KEY)
            is not True
        ):
            raise ValidationFailure(
                "scheduler session lock is already held; stop the scheduler before validation"
            )
        if await scheduler_b.fetchval("SELECT pg_try_advisory_lock($1)", SCHEDULER_LOCK_KEY):
            raise ValidationFailure("a second scheduler connection acquired the held session lock")
        await scheduler_a.close()
        scheduler_a = None
        if (
            await scheduler_b.fetchval("SELECT pg_try_advisory_lock($1)", SCHEDULER_LOCK_KEY)
            is not True
        ):
            raise ValidationFailure(
                "scheduler session lock was not released when its direct connection closed"
            )
        await scheduler_b.execute("SELECT pg_advisory_unlock($1)", SCHEDULER_LOCK_KEY)
        if local_target:
            return "member/tenant locks serialize local transactions; scheduler session lock releases on direct disconnect; Neon pooler remains unverified"
        return "member/tenant locks serialize pooled transactions; scheduler session lock releases on direct disconnect"
    finally:
        await conn_a.close()
        await conn_b.close()
        if scheduler_a is not None:
            await scheduler_a.close()
        if scheduler_b is not None:
            await scheduler_b.close()


async def privilege_snapshot(conn: asyncpg.Connection) -> dict[str, list[tuple[str, str]]]:
    result: dict[str, list[tuple[str, str]]] = {
        key: [] for key in ("database", "schema", "tables", "columns", "sequences", "functions")
    }
    db_privileges = ("CONNECT", "CREATE", "TEMPORARY")
    for privilege in db_privileges:
        allowed = await conn.fetchval(
            "SELECT has_database_privilege('ajo_app'::name, current_database(), $1::text)",
            privilege,
        )
        if allowed:
            result["database"].append(("current_database", privilege))
    for privilege in ("USAGE", "CREATE"):
        allowed = await conn.fetchval(
            "SELECT has_schema_privilege('ajo_app'::name, 'public'::name, $1::text)",
            privilege,
        )
        if allowed:
            result["schema"].append(("public", privilege))
    table_rows = await conn.fetch(
        """
        SELECT c.relname, c.relkind FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','f')
         ORDER BY c.relname
        """
    )
    for row in table_rows:
        name = str(row["relname"])
        for privilege in (
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "REFERENCES",
            "TRIGGER",
        ):
            if await conn.fetchval(
                "SELECT has_table_privilege('ajo_app', to_regclass('public.' || $1), $2)",
                name,
                privilege,
            ):
                result["tables"].append((name, privilege))
    column_rows = await conn.fetch(
        """
        SELECT c.oid AS table_oid, c.relname AS table_name, a.attnum, a.attname
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_attribute a ON a.attrelid = c.oid
         WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','f')
           AND a.attnum > 0 AND NOT a.attisdropped
         ORDER BY c.relname, a.attnum
        """
    )
    for row in column_rows:
        name = f"{row['table_name']}.{row['attname']}"
        for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
            if await conn.fetchval(
                "SELECT has_column_privilege('ajo_app'::name, $1::oid, $2::smallint, $3::text)",
                row["table_oid"],
                row["attnum"],
                privilege,
            ):
                result["columns"].append((name, privilege))
    sequence_rows = await conn.fetch(
        """
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind = 'S' ORDER BY c.relname
        """
    )
    for row in sequence_rows:
        name = str(row["relname"])
        for privilege in ("USAGE", "SELECT", "UPDATE"):
            if await conn.fetchval(
                "SELECT has_sequence_privilege('ajo_app', to_regclass('public.' || $1), $2)",
                name,
                privilege,
            ):
                result["sequences"].append((name, privilege))
    function_rows = await conn.fetch(
        """
        SELECT p.oid, p.proname, pg_catalog.pg_get_function_identity_arguments(p.oid) AS args
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' ORDER BY p.proname, args
        """
    )
    for row in function_rows:
        if await conn.fetchval(
            "SELECT has_function_privilege('ajo_app'::name, $1::oid, 'EXECUTE'::text)",
            row["oid"],
        ):
            result["functions"].append((f"{row['proname']}({row['args']})", "EXECUTE"))
    return {key: sorted(values) for key, values in result.items()}


async def check_grants(phase: str) -> str:
    _, direct, _ = target_urls()
    ref = reference_url()
    ensure_distinct_reference(direct, ref)
    target_conn = await connect(direct)
    ref_conn = await connect(ref)
    try:
        actual = await privilege_snapshot(target_conn)
        expected = await privilege_snapshot(ref_conn)
        extras: list[str] = []
        missing: list[str] = []
        for category in expected:
            expected_set = set(expected[category])
            actual_set = set(actual[category])
            extras.extend(
                f"{category}:{obj}:{priv}" for obj, priv in sorted(actual_set - expected_set)
            )
            missing.extend(
                f"{category}:{obj}:{priv}" for obj, priv in sorted(expected_set - actual_set)
            )
        if extras or missing:
            summary = []
            if extras:
                summary.append(f"extra={extras[:8]}")
            if missing:
                summary.append(f"missing={missing[:8]}")
            raise ValidationFailure(
                "ajo_app effective privileges differ from migration reference: "
                + "; ".join(summary)
            )
        return f"ajo_app effective table/sequence/function and connect/schema grants match fresh reference ({phase})"
    finally:
        await target_conn.close()
        await ref_conn.close()


async def check_app_smoke() -> str:
    pooled, _, _ = target_urls()
    conn = await connect(pooled, pooled=True)
    try:
        count = await conn.fetchval("SELECT count(*) FROM cooperatives")
        if count is None:
            raise ValidationFailure("app-role read smoke query returned no result")
        tenant_id = uuid.uuid4()
        slug = f"restore-smoke-{tenant_id.hex}"
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.cooperative_id', $1, true)", str(tenant_id))
            await conn.execute(
                "INSERT INTO cooperatives (id, name, slug) VALUES ($1, 'restore smoke', $2)",
                tenant_id,
                slug,
            )
            seen = await conn.fetchval("SELECT count(*) FROM cooperatives WHERE id = $1", tenant_id)
            if int(seen) != 1:
                raise ValidationFailure(
                    "app-role tenant-scoped write/read smoke did not see its own row"
                )
        return "app-role read succeeded; tenant-scoped insert/read succeeded and rolled back"
    finally:
        await conn.close()


async def async_main(args: argparse.Namespace) -> int:
    reporter = Reporter()
    label = str(args.confirm_disposable_branch).strip()
    if not label or label.lower() in {"main", "prod", "production", "live"}:
        print(
            "FAIL target confirmation: provide the name of a disposable branch, never main/production"
        )
        return 2
    print(
        f"Ajo validation target label: {label}; phase: {args.phase}; connection strings are never printed"
    )
    if (urlparse(os.environ.get("DATABASE_URL", "")).hostname or "").lower() in LOCAL_HOSTS:
        print(
            "NOTE: local PostgreSQL is direct; PASS results do not prove Neon PgBouncer behavior."
        )
    checks: tuple[tuple[int, str, Callable[[], Awaitable[str]]], ...] = (
        (1, "migration head and schema reference", check_schema),
        (2, "pooled RLS isolation and connection reuse", check_rls),
        (3, "app/scheduler role safety and startup guard", check_roles),
        (4, "transaction and scheduler session advisory locks", check_locks),
        (5, f"effective grants ({args.phase})", lambda: check_grants(args.phase)),
        (6, f"app read/write restore smoke ({args.phase})", check_app_smoke),
    )
    for number, title, operation in checks:
        await reporter.run(number, title, operation)
    print(
        f"RESULT: {'FAIL' if reporter.failures else 'PASS'} ({reporter.failures} failed check(s))"
    )
    return 1 if reporter.failures else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-disposable-branch",
        required=True,
        metavar="BRANCH_LABEL",
        help="explicitly confirm this target is a disposable branch; its identity is not verified by the script",
    )
    parser.add_argument(
        "--phase",
        choices=("before-restore", "after-restore"),
        default="before-restore",
        help="label the grant/smoke results for the backup restore drill report",
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(async_main(parse_args())))
    except ValidationFailure as exc:
        print(f"FAIL preflight: {exc}")
        sys.exit(2)
