# ADR 0001: Neon roles and connection model

Status: accepted, 23 Oct 2026.

Context: Ajo's tenant isolation uses Postgres row-level security. Neon roles created through the Console, CLI or API (including the default owner) are members of `neon_superuser`, which has BYPASSRLS. Neon's pooler is PgBouncer in transaction mode.

Decision:
1. The API and worker connect as `ajo_app`, created with SQL, with NOBYPASSRLS, through the pooled host.
2. Migrations, `pg_dump` and the scheduler connect as the owner role through the direct host.
3. Tenant context is applied with `set_config(..., true)` inside a transaction; locks are transaction-level.
4. The smoke test fails the build if `ajo_app` can bypass RLS.

Consequences: password management for `ajo_app` is manual (SQL, not the Console). Each Neon branch needs its own passwords. Postgres RLS is defence in depth; service-layer tenant checks remain mandatory.
