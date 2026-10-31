# Ajo

Cooperative management and loan administration SaaS. Read `docs/context.md` first: it is the source of truth.

## Layout
```
backend/    FastAPI, SQLAlchemy async, Alembic, tests, Neon scripts
frontend/   React + TypeScript + Vite + Tailwind (officer portal, member page)
ops/        Production image Compose, development Compose, host Nginx vhost example, runbooks
docs/       context.md, neon.md (setup guide + learning log), adr/
```

## First run
1. For local development with PostgreSQL, follow [`docs/local-development.md`](docs/local-development.md). For Neon setup, follow [`docs/neon.md`](docs/neon.md) and configure the appropriate branch and roles.
2. `make setup`
3. `make migrate`  then  `make smoke`  (all critical checks must pass)
4. `make api` (http://localhost:8000/api/docs) and `make web` (http://localhost:5173)

## Daily commands
`make test` unit tests, `make lint` ruff + mypy, `make migrate`, `make smoke`.

## VPS deployment
The VPS's existing Nginx and certbot setup terminate TLS. Ajo runs solely from prebuilt GHCR images via `ops/docker-compose.prod.yml`; only `/opt/ajo`, the separate Ajo vhost, and its certificate live on the host. Follow [`ops/runbooks/deploy.md`](ops/runbooks/deploy.md). The API and frontend bind only to loopback ports. Validate with `nginx -t` before reloading and never replace the existing Nginx configuration.

## Rules (short)
No float money. No UPDATE/DELETE on ledger or audit. No financial logic outside services. Tenant context only via `app.core.db.transaction()`. Every migration rehearsed on a Neon branch.
