# Runbook: deploy (Frankfurt VPS)

The VPS holds only `/opt/ajo`, the Ajo Nginx vhost, and its certificate. Other Ajo components run from prebuilt GHCR images.

1. Copy `ops/docker-compose.prod.yml` to `/opt/ajo/docker-compose.yml`.
2. Create `/opt/ajo/.env` with production environment variables, `AJO_IMAGE_OWNER`, and optionally `AJO_TAG` (defaults to `staging`). Keep it readable only by the deploy user.
3. Install `ops/nginx/ajo.conf.example` as a separate host vhost, uncomment/set the Certbot TLS paths, then run `sudo nginx -t` and `sudo systemctl reload nginx`. Do not replace existing Nginx configuration.
4. `cd /opt/ajo && docker login ghcr.io`
5. `docker compose pull`
6. Run migrations before starting the new services through the owner-only one-shot service: `docker compose --profile migration run --rm migration`. The service also receives `DATABASE_URL` for `app.cli` commands.
7. `docker compose up -d`
8. Check `docker compose ps`, `curl --fail https://ajo.petbell.com.ng/api/v1/health`, and `curl --fail https://ajo.petbell.com.ng/api/v1/health/db`.

## Periodic database activity

| Component | Interval | Queries database? |
| --- | --- | --- |
| API healthcheck | 30 seconds | No; calls `/api/v1/health` |
| Worker outbox loop | Configurable, default 2 seconds when idle | Yes; scans tenants and claims outbox rows |
| Scheduler overdue-cash loop | Configurable, default 60 minutes | Yes; runs the scheduled job |
| Worker and scheduler healthchecks | 30 seconds | No; reads a local heartbeat file |
| Frontend notification summary | Configurable in the client, currently 60 seconds while visible | Yes; pauses when hidden and refreshes on visibility/focus |
| Migration service | One-shot, on deployment only | Yes; runs migrations |

`OUTBOX_POLL_INTERVAL_SECONDS` defaults to `2` and `SCHEDULER_INTERVAL_MINUTES` defaults to `60`, preserving current alert latency and schedule. The API healthcheck is process liveness only; use `/api/v1/health/db` manually for database readiness.

## Neon free plan compute budget

The Neon Free plan allows 100 CU-hours per project per month. A 0.25 CU compute running continuously uses approximately 6 CU-hours per day, or 180 CU-hours per month. Reaching the Free plan limit suspends compute until the next billing month.

Check current usage in the Neon console under the project’s **Usage** page. To restart the application after compute suspension on the VPS:

```bash
cd /opt/ajo && docker compose stop api worker scheduler
docker compose start api worker scheduler
```

Production systems that require always-on compute need a paid Neon plan.

To inspect overdue cash alerts without queueing any notifications, run:

```bash
docker compose exec scheduler python -m app.scheduler --run-once --as-of 2026-12-01 --dry-run
```

For staging-specific setup, see [staging-setup.md](staging-setup.md).

**Note:** The scheduler accepts any PostgreSQL URL scheme (`postgresql://`, `postgresql+psycopg://`, `postgresql+asyncpg://`, etc.) and always uses the asyncpg driver. No special configuration is required.

## Rollback

Set `AJO_TAG` in `/opt/ajo/.env` to a previous image SHA, then run `docker compose pull && docker compose up -d`. Do not downgrade a migration unless it is explicitly reversible.

## Removal

Run `cd /opt/ajo && docker compose down`, then remove the separate Ajo Nginx vhost and validate/reload Nginx with `sudo nginx -t && sudo systemctl reload nginx`.

## Repairing approved-but-unposted dividend runs (staging fix)

If a cooperative approved a dividend run before the fix in PR #87 (fix/dividends-ledger-and-periods),
the run will have `status='approved'` and no ledger entry.  Run the repair command once after deploying:

```bash
# Dry run first — prints counts without changing anything:
docker compose exec api python -m app.domains.dividends.repair --dry-run

# Apply:
docker compose exec api python -m app.domains.dividends.repair
```

The command is idempotent: running it twice changes nothing on the second run.
Requires `DATABASE_URL_DIRECT` in the container environment (set in `.env`).
