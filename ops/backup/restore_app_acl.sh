#!/usr/bin/env bash
# Restore the application role ACLs after pg_restore --no-privileges.
# Run as a database owner with DATABASE_URL_DIRECT set; this script never needs
# an application credential and does not grant owner or BYPASSRLS privileges.
set -euo pipefail
: "${DATABASE_URL_DIRECT:?DATABASE_URL_DIRECT is required}"

psql --set=ON_ERROR_STOP=1 "$DATABASE_URL_DIRECT" <<'SQL'
DO $$
BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO ajo_app', current_database());
END $$;
GRANT USAGE ON SCHEMA public TO ajo_app;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM ajo_app;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA public TO ajo_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ajo_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO ajo_app;

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'cooperatives', 'users', 'user_sessions', 'recovery_codes', 'auth_rate_limits',
    'cooperative_modules', 'cooperative_policy_versions', 'loan_products',
    'savings_locks', 'withdrawal_requests', 'loan_applications', 'loans',
    'member_consents', 'consent_rate_limits', 'disputes', 'notifications',
    'disbursements', 'repayments', 'repayment_allocations', 'payroll_import_profiles',
    'payroll_runs', 'payroll_items'
  ] LOOP
    IF to_regclass('public.' || table_name) IS NOT NULL THEN
      EXECUTE format('GRANT UPDATE ON TABLE public.%I TO ajo_app', table_name);
    END IF;
  END LOOP;
END $$;

-- These tables are the only current operational tables with application deletes.
DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'user_roles', 'user_sessions', 'recovery_codes', 'auth_rate_limits'
  ] LOOP
    IF to_regclass('public.' || table_name) IS NOT NULL THEN
      EXECUTE format('GRANT DELETE ON TABLE public.%I TO ajo_app', table_name);
    END IF;
  END LOOP;
END $$;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ajo_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO ajo_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO ajo_app;
SQL

echo "application ACL restore ok"
