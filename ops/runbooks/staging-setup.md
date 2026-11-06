# Runbook: set up a staging environment from scratch

Audience: the operator (not a developer). Result: Ajo running at `https://ajo.petbell.com.ng`
on the Frankfurt VPS, from prebuilt GHCR images, against a Neon `staging` branch with fake
demo data.

For routine deploys after this is set up, use `deploy.md`. For backups, use `restore.md`.

**Rules for this runbook**

- Nothing is built on a laptop. GitHub Actions builds images; the VPS only pulls them.
- Staging holds fake data only. No real member data until the independent security review
  and the data-protection (NDPA) check are done.
- Secrets go from a password manager into files on the VPS. Never paste them into chats,
  tickets or commits.
- Never run `docker compose config` without `--quiet`. It prints every secret in `.env`.

---

## 0. Prerequisites

| Item | Check |
|---|---|
| Images exist | GitHub profile, Packages: `ajo-backend` and `ajo-frontend` with tag `staging` |
| CI green on `main` | `gh run list --branch main -L 3` |
| VPS has Docker, Nginx, certbot | `docker --version && nginx -v && certbot --version` |
| Password manager open | You will store 3 database passwords, a GHCR token, and the app keys |

---

## 1. DNS

In the DNS provider for `petbell.com.ng`, create an **A record**:

- Name: `ajo`
- Value: the VPS public IP
- If the provider is Cloudflare: **DNS only** (grey cloud). The orange proxy hides the real
  client address from the app and complicates certificates.

Check from any computer (it must print the VPS IP, not a Cloudflare address):

```bash
dig +short ajo.petbell.com.ng
```

---

## 2. Staging database (Neon)

### 2.1 Create the branch and database

In the Neon console: create a branch called `staging`, then a database called `ajo_staging`
on it. **Do not create roles in the console.** Console-created roles can bypass row-level
security. Roles are created with SQL below.

Roles are copied into child branches, so `ajo_app` and `ajo_readonly` may already exist.
The SQL below handles both cases.

### 2.2 Create the roles and passwords

Use the **direct** (no `-pooler`) connection string of the **owner** role, database
`ajo_staging`. Enter it without echoing it, in the terminal you will use for the next steps:

```bash
read -s OWNER_URL
export OWNER_URL
psql "$OWNER_URL" -Atc "select current_database()"     # must print ajo_staging
```

Generate passwords and apply. The quoted heredoc and `psql -v` variables are deliberate:
they stop the shell from mangling `$$` and keep passwords out of the script text.

```bash
APP_PW=$(openssl rand -hex 24); SCH_PW=$(openssl rand -hex 24); RO_PW=$(openssl rand -hex 24)
psql "$OWNER_URL" -v ON_ERROR_STOP=1 \
  -v app_pw="$APP_PW" -v sch_pw="$SCH_PW" -v ro_pw="$RO_PW" <<'SQL' \
  && echo "app:$APP_PW  scheduler:$SCH_PW  readonly:$RO_PW"
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ajo_app') THEN
    CREATE ROLE ajo_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ajo_readonly') THEN
    CREATE ROLE ajo_readonly LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ajo_scheduler') THEN
    CREATE ROLE ajo_scheduler LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
END
$$;
ALTER ROLE ajo_app       PASSWORD :'app_pw';
ALTER ROLE ajo_scheduler PASSWORD :'sch_pw';
ALTER ROLE ajo_readonly  PASSWORD :'ro_pw';
GRANT CONNECT ON DATABASE ajo_staging TO ajo_app, ajo_readonly, ajo_scheduler;
GRANT USAGE ON SCHEMA public TO ajo_app, ajo_readonly;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ajo_app, ajo_readonly;
ALTER ROLE ajo_app SET statement_timeout = '30s';
ALTER ROLE ajo_app SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE ajo_app SET lock_timeout = '10s';
ALTER ROLE ajo_readonly SET statement_timeout = '60s';
SQL
```

Copy the three passwords into the password manager **now**, then run `clear`.

### 2.3 Verify no role can bypass row-level security

```bash
psql "$OWNER_URL" -Atc "select rolname, rolsuper, rolbypassrls from pg_roles where rolname like 'ajo%' order by 1"
```

Expect three rows ending `|f|f`. If any shows `t`, stop and fix before continuing.

---

## 3. The VPS

### 3.1 Folder and compose file

From your laptop (repo root):

```bash
scp ops/docker-compose.prod.yml <user>@<vps-ip>:/tmp/docker-compose.yml
```

On the VPS:

```bash
sudo mkdir -p /opt/ajo && sudo chown $USER /opt/ajo
mv /tmp/docker-compose.yml /opt/ajo/docker-compose.yml
```

### 3.2 Pull access to the private images

On GitHub: Settings, Developer settings, Personal access tokens (classic). Create a token
with **only `read:packages`** and a 90-day expiry. Put it in the password manager. On the VPS:

```bash
docker login ghcr.io -u <github-username>      # paste the token at the prompt
```

### 3.3 Create `/opt/ajo/.env`

```bash
cd /opt/ajo
umask 077
cat > .env <<EOF
AJO_IMAGE_OWNER=perolasanta
AJO_TAG=staging
APP_ENV=staging
APP_NAME=ajo
PUBLIC_BASE_URL=https://ajo.petbell.com.ng
TRUSTED_PROXY_IPS=
SMS_ENABLED=false
SECRET_KEY=$(openssl rand -hex 32)
ENCRYPTION_KEY=$(openssl rand -base64 32 | tr '+/' '-_')
DATABASE_URL=
DATABASE_URL_DIRECT=
DATABASE_URL_SCHEDULER=
EOF
nano .env
```

Fill the three database lines. Each is a connection string for database `ajo_staging`:

| Variable | User | Host type |
|---|---|---|
| `DATABASE_URL` | `ajo_app` | **pooler** host (`-pooler` in the name) |
| `DATABASE_URL_DIRECT` | the owner role | **direct** host |
| `DATABASE_URL_SCHEDULER` | `ajo_scheduler` | **direct** host |

Common mistake: copying the owner line for the scheduler and changing only the password.
The username must also change to `ajo_scheduler`.

Save in `nano` with Ctrl+O, Enter, Ctrl+X. `APP_ENV` accepts `development`, `staging`
or `production`.

---

## 4. Nginx and the certificate

Ajo gets its **own** file, so a mistake cannot break the other apps on the same server.
The server uses the `sites-available` / `sites-enabled` pattern.

### 4.1 HTTP block first (certbot needs it)

```bash
sudo mkdir -p /var/www/html
sudo tee /etc/nginx/sites-available/ajo.conf >/dev/null <<'EOF'
server {
    listen 80;
    server_name ajo.petbell.com.ng;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://$host$request_uri; }
}
EOF
sudo ln -s /etc/nginx/sites-available/ajo.conf /etc/nginx/sites-enabled/ajo.conf
sudo nginx -t && sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/www/html -d ajo.petbell.com.ng
```

If `nginx -t` fails, do not reload. Nginx keeps serving the old config until a reload
succeeds, so the other apps stay up.

### 4.2 Add the HTTPS block

From your laptop:

```bash
scp ops/nginx/ajo.conf.example <user>@<vps-ip>:/tmp/ajo-https.conf
```

On the VPS:

```bash
sed -i 's|^    # ssl_certificate|    ssl_certificate|' /tmp/ajo-https.conf
grep -n "ssl_certificate" /tmp/ajo-https.conf        # two uncommented lines expected
sudo bash -c 'cat /tmp/ajo-https.conf >> /etc/nginx/sites-available/ajo.conf'
sudo nginx -t && sudo systemctl reload nginx
```

### 4.3 Lock the API docs behind a password (staging)

```bash
sudo apt-get install -y apache2-utils
sudo htpasswd -c /etc/nginx/ajo.htpasswd ajo
sudo nano /etc/nginx/sites-available/ajo.conf
```

In the file, uncomment the three `location = /api/docs`, `/api/redoc` and
`/api/openapi.json` blocks. **Remove the `# ` from every line of each block, including its
closing `}`.** Uncommenting only some lines nests the blocks and Nginx rejects the file with
"cannot be inside the exact location". Then:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## 5. First start

```bash
cd /opt/ajo
docker compose pull
docker compose --profile migration run --rm migration
docker compose --profile migration run --rm migration alembic current    # expect: <revision> (head)
docker compose up -d
docker compose ps
```

All four services (api, worker, scheduler, web) should reach `healthy` or `Up`.

### 5.1 Tell the API which proxy address to trust

Nginx runs on the host, so the API sees it as the Docker network gateway. Get that address:

```bash
docker network inspect perolasanta-ajo-prod_net --format '{{(index .IPAM.Config 0).Gateway}}'
```

Put the printed address (for example `172.x.x.1`) after `TRUSTED_PROXY_IPS=` in `.env`, then:

```bash
docker compose up -d --force-recreate api
grep -c '^TRUSTED_PROXY_IPS=.' .env       # must print 1
```

Until this is set, login throttling cannot tell users apart.

---

## 6. Demo data (fake only)

The `migration` service carries the owner connection, so the CLI runs through it. Use a new
slug each time: a failed seed run burns its slug.

```bash
docker compose --profile migration run --rm migration python -m app.cli create-cooperative \
  --slug demo1 --name "Demo Cooperative" --admin-email <your-email> --full-name "<Your Name>"
docker compose --profile migration run --rm migration python -m app.cli seed-demo \
  --slug demo1 --yes-fake-data
```

Save any one-time credentials the commands print in the password manager.

---

## 7. Verify

From any computer:

```bash
curl -s https://ajo.petbell.com.ng/api/v1/health        # {"status":"ok",...}
curl -s https://ajo.petbell.com.ng/api/v1/health/db     # {"status":"ok"}
curl -sI https://ajo.petbell.com.ng | head -3           # HTTP/2 200
curl -sI https://ajo.petbell.com.ng/api/docs | head -1  # 401 (password required)
```

Then in a browser, log in as each role. Use a **fresh private window per user** and **one
authenticator app** for all demo users. Check:

- Every role lands on the Dashboard.
- The menu shows only pages the role may open (auditor: no Payroll; committee: no Members).
- Menu badges show sensible counts.
- Pages load in a second or two.

---

## 8. Updating staging later

Every merge to `main` rebuilds the `staging` images on GitHub. To deploy the latest:

```bash
cd /opt/ajo
docker compose pull
docker compose --profile migration run --rm migration
docker compose up -d
```

Always **pull, migrate, then up**. Then repeat the two `curl` health checks.

## 9. Rollback

Set `AJO_TAG` in `.env` to a previous image commit ID, then:

```bash
docker compose pull && docker compose up -d
```

Do not downgrade a migration unless it is explicitly reversible.

## 10. Removal

```bash
cd /opt/ajo && docker compose down
sudo rm /etc/nginx/sites-enabled/ajo.conf
sudo nginx -t && sudo systemctl reload nginx
```

---

## Troubleshooting (problems seen during the first setup)

| Symptom | Cause | Fix |
|---|---|---|
| API will not start: `Invalid args for response field` | Code bug in an old image | Update to the latest `main` image |
| `docker compose ps` shows scheduler restarting; log says `password authentication failed for user 'neondb_owner'` | Scheduler URL still uses the owner username | Set the username to `ajo_scheduler` and its password; `docker compose up -d --force-recreate scheduler` |
| `DATABASE_URL is not set` when running a CLI command | `migration` service was not given the app URL | Use the latest compose file (it passes `DATABASE_URL`), or add an override file |
| `role "ajo_app" already exists` | Child branch inherited roles | Use the idempotent SQL in section 2.2 |
| `nginx: location "/api/redoc" cannot be inside the exact location` | Docs blocks uncommented unevenly | Uncomment every line of each block including `}` |
| `dig` shows Cloudflare addresses | Record is proxied | Set the record to DNS only |
| Logins fail at random, or 25 s page loads | Testing from a laptop far from the database | Test on the VPS, next to the database |
| `/health` says `"version":"unknown"` | `GIT_SHA` not set | Cosmetic |
| Image build fails: `Cache export is not supported for the docker driver` | Workflow lacked the buildx setup step | Fixed in `images.yml` |
