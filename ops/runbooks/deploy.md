# Runbook: deploy (Frankfurt VPS)
1. `git pull` on the VPS in /opt/ajo.
2. Rehearse any new migration on the Neon `staging` branch first.
3. `cd frontend && npm ci && npm run build`
4. `cd ops && docker compose --env-file ../.env up -d --build`
5. Run migrations with the DIRECT URL: `docker compose run --rm api alembic upgrade head`
6. Check `/api/v1/health` and `/api/v1/health/db`.
Rollback: previous image tag plus `alembic downgrade -1` only if the migration is reversible; otherwise restore a Neon branch.
