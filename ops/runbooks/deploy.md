# Runbook: deploy (Frankfurt VPS)
1. `git pull` on the VPS in /opt/ajo.
2. Rehearse any new migration on the Neon `staging` branch first.
3. `cd frontend && npm ci && npm run build`
4. Copy the built frontend to the Nginx document root: `sudo mkdir -p /var/www/ajo && sudo cp -a dist/. /var/www/ajo/` (run from `frontend/`).
5. Install `ops/nginx/ajo.conf.example` as a separate Nginx virtual host, substituting your domain and the certificate paths managed by the existing certbot setup. Do not replace the existing Nginx configuration.
6. `cd ../ops && docker compose --env-file ../.env up -d --build` (the API is published only on `127.0.0.1:8000`; Nginx owns public ports 80/443).
7. Run migrations with the DIRECT URL: `docker compose run --rm api alembic upgrade head`
8. Validate configuration with `sudo nginx -t`, then reload Nginx with `sudo systemctl reload nginx`.
9. Check `/api/v1/health` and `/api/v1/health/db` through the configured virtual host.
Rollback: previous image tag plus `alembic downgrade -1` only if the migration is reversible; otherwise restore a Neon branch.
