# Ajo

Cooperative management and loan administration SaaS. Read `docs/context.md` first: it is the source of truth.

## Layout
```
backend/    FastAPI, SQLAlchemy async, Alembic, tests, Neon scripts
frontend/   React + TypeScript + Vite + Tailwind (officer portal, member page)
ops/        docker-compose, Caddyfile, backup script, runbooks
docs/       context.md, neon.md (setup guide + learning log), adr/
```

## First run
1. `cp .env.example .env` and follow `docs/neon.md` (create project, roles, branches, connection strings).
2. `make setup`
3. `make migrate`  then  `make smoke`  (all critical checks must pass)
4. `make api` (http://localhost:8000/api/docs) and `make web` (http://localhost:5173)

## Daily commands
`make test` unit tests, `make lint` ruff + mypy, `make migrate`, `make smoke`.

## Rules (short)
No float money. No UPDATE/DELETE on ledger or audit. No financial logic outside services. Tenant context only via `app.core.db.transaction()`. Every migration rehearsed on a Neon branch.
