# Runbook: deploy (Frankfurt VPS)

The VPS holds only `/opt/ajo`, the Ajo Nginx vhost, and its certificate. Other Ajo components run from prebuilt GHCR images.

1. Copy `ops/docker-compose.prod.yml` to `/opt/ajo/docker-compose.yml`.
2. Create `/opt/ajo/.env` with production environment variables, `AJO_IMAGE_OWNER`, and optionally `AJO_TAG` (defaults to `staging`). Keep it readable only by the deploy user.
3. Install `ops/nginx/ajo.conf.example` as a separate host vhost, uncomment/set the Certbot TLS paths, then run `sudo nginx -t` and `sudo systemctl reload nginx`. Do not replace existing Nginx configuration.
4. `cd /opt/ajo && docker login ghcr.io`
5. `docker compose pull`
6. Run migrations before starting the new services: `docker compose run --rm api alembic upgrade head`
7. `docker compose up -d`
8. Check `docker compose ps`, `curl --fail https://ajo.petbell.com.ng/api/v1/health`, and `curl --fail https://ajo.petbell.com.ng/api/v1/health/db`.

## Rollback

Set `AJO_TAG` in `/opt/ajo/.env` to a previous image SHA, then run `docker compose pull && docker compose up -d`. Do not downgrade a migration unless it is explicitly reversible.

## Removal

Run `cd /opt/ajo && docker compose down`, then remove the separate Ajo Nginx vhost and validate/reload Nginx with `sudo nginx -t && sudo systemctl reload nginx`.
