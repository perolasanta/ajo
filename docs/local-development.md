# Local development with PostgreSQL

Use a disposable local PostgreSQL database for development and tests. Do not point local
tests at Neon. This guide uses the repository's development PostgreSQL Compose file,
which binds only to `127.0.0.1:55432`.

## 1. Start PostgreSQL and create a database

Start the local database with `make test-db-up` (requires Docker Compose). This target
starts PostgreSQL only; it does not run tests. Create a separate `ajo` database:

```sh
PGPASSWORD=pg psql -h 127.0.0.1 -p 55432 -U postgres -d postgres \
  -c 'CREATE DATABASE ajo'
```

The compose service has a disposable `postgres/pg` superuser for local setup. Never reuse
these credentials outside the local development database.

## 2. Create the app roles

The API and demo seeder use the least-privilege `ajo_app` role. Alembic and the cooperative
bootstrap CLI use the local database owner (`postgres`). Create the project roles once as
the local owner, in database `ajo`. The role script contains `CHANGE_ME` placeholders; use
a local-only password and substitute it in a temporary copy of the script (do not commit
that copy):

```sh
sed 's/CHANGE_ME_APP/local-app-password/; s/CHANGE_ME_READONLY/local-readonly-password/' \
  backend/scripts/neon_roles.sql > /tmp/ajo-local-roles.sql
PGPASSWORD=pg psql -h 127.0.0.1 -p 55432 -U postgres -d ajo \
  -v ON_ERROR_STOP=1 -f /tmp/ajo-local-roles.sql
rm /tmp/ajo-local-roles.sql
```

The script creates `ajo_app`, `ajo_readonly`, and `ajo_scheduler` as ordinary Postgres
roles without `BYPASSRLS`. Use `ajo_app` for normal API reads and writes; the readonly and
scheduler roles are reserved for their corresponding application functions.

## 3. Configure the application and migrate

Copy `.env.example` to `.env`, then set the local values below and generate local-only
`SECRET_KEY` and `ENCRYPTION_KEY` values. Keep `.env` untracked. The owner URL is direct
because migrations must not run as the app role; the runtime URL uses `ajo_app`.

```dotenv
APP_ENV=development
DATABASE_URL=postgresql://ajo_app:local-app-password@127.0.0.1:55432/ajo
DATABASE_URL_DIRECT=postgresql://postgres:pg@127.0.0.1:55432/ajo
```

Install dependencies with `make setup`, then apply the checked-in Alembic SQL migrations
to this local database with `make migrate`. Start the API and web client in separate
terminals with `make api` and `make web`.

## 4. Bootstrap and seed fictional data

Create a cooperative and its first admin through the CLI. It prints a one-time temporary
password; save it securely for local sign-in and complete MFA enrollment in the browser.

```sh
cd backend
.venv/bin/python -m app.cli create-cooperative \
  --slug local-demo --name 'Local Demo Cooperative' --admin-email admin@example.test
```

Optionally populate a fresh cooperative with explicitly fictional demo data. Run this
only against a local development database and pass the confirmation flag:

```sh
cd backend
.venv/bin/python -m app.cli seed-demo --slug local-demo --yes-fake-data
```

`seed-demo` refuses production mode and a cooperative that already contains data; use a
new local slug rather than attempting to resume a partial seed. Reset/destroy the disposable
database instead of manually deleting financial or audit records. Stop its PostgreSQL
service with `make test-db-down` when finished.
