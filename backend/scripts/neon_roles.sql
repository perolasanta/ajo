-- Run ONCE on the Neon `main` branch, in database `ajo`, as the OWNER role
-- (the project's default owner, e.g. neondb_owner), using the Neon SQL Editor or psql.
--
-- WHY SQL AND NOT THE NEON CONSOLE: roles created in the console, CLI or API are members of
-- neon_superuser, which has BYPASSRLS. Row-level security would silently not apply to them.
-- Roles created with plain SQL are ordinary roles, which is what the app must use.
--
-- 1) Replace both CHANGE_ME values with long random passwords (e.g. `openssl rand -base64 32`).
-- 2) Do this BEFORE creating the staging/dev branches (roles are copied into child branches),
--    then reset those passwords on the child branches.

CREATE ROLE ajo_app      LOGIN PASSWORD 'CHANGE_ME_APP'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE ajo_readonly LOGIN PASSWORD 'CHANGE_ME_READONLY'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

GRANT CONNECT ON DATABASE ajo TO ajo_app, ajo_readonly;
GRANT USAGE ON SCHEMA public TO ajo_app, ajo_readonly;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Tables created later by the owner start read-only for both roles.
-- Each migration grants INSERT/UPDATE explicitly, table by table (least privilege).
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ajo_app, ajo_readonly;

-- Safety nets for the app role (transaction-pooling friendly: role-level, not session SET).
ALTER ROLE ajo_app SET statement_timeout = '30s';
ALTER ROLE ajo_app SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE ajo_app SET lock_timeout = '10s';
ALTER ROLE ajo_readonly SET statement_timeout = '60s';
