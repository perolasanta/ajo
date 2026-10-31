#!/usr/bin/env bash
# Restore the application role ACLs after pg_restore --no-privileges.
# Run as a database owner with DATABASE_URL_DIRECT set; this script never needs
# an application credential and does not grant owner or BYPASSRLS privileges.
set -euo pipefail
: "${DATABASE_URL_DIRECT:?DATABASE_URL_DIRECT is required}"

psql --set=ON_ERROR_STOP=1 "$DATABASE_URL_DIRECT" <<'SQL'
DO $$
BEGIN
  EXECUTE format('REVOKE ALL PRIVILEGES ON DATABASE %I FROM ajo_app', current_database());
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO ajo_app', current_database());
END $$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON SCHEMA public FROM ajo_app;
GRANT USAGE ON SCHEMA public TO ajo_app;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM ajo_app;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM ajo_app;
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM ajo_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ajo_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ajo_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO ajo_app;

-- Migration 0011 granted INSERT on its then-existing tables. Later-created
-- tables receive SELECT by default and must be explicitly allowlisted here.
DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'accounts', 'alembic_version', 'approvals', 'audit_logs', 'auth_rate_limits',
    'bank_accounts', 'consent_rate_limits', 'cooperative_modules',
    'cooperative_policy_versions', 'cooperatives', 'disbursements', 'disputes',
    'fraud_alerts', 'guarantees', 'journal_entries', 'journal_lines',
    'loan_applications', 'loan_products', 'loan_schedule', 'loan_terms_snapshots',
    'loans', 'member_consents', 'members', 'notifications',
    'payroll_import_profiles', 'payroll_items', 'payroll_runs', 'policy_change_log',
    'recovery_codes', 'repayment_allocations', 'repayments', 'role_change_proposals',
    'salary_records', 'savings_locks', 'tenant_directory', 'user_roles',
    'user_sessions', 'users', 'withdrawal_requests'
  ] LOOP
    IF to_regclass('public.' || table_name) IS NOT NULL THEN
      EXECUTE format('GRANT INSERT ON TABLE public.%I TO ajo_app', table_name);
    END IF;
  END LOOP;
END $$;

-- Reset column ACLs too; table-level REVOKE does not remove column-specific grants.
DO $$
DECLARE acl_column record;
BEGIN
  FOR acl_column IN
    SELECT c.relname, a.attname
      FROM pg_attribute a
      JOIN pg_class c ON c.oid = a.attrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','f')
       AND a.attnum > 0 AND NOT a.attisdropped
  LOOP
    EXECUTE format(
      'REVOKE ALL PRIVILEGES (%I) ON TABLE public.%I FROM ajo_app',
      acl_column.attname, acl_column.relname
    );
  END LOOP;
END $$;

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'cooperatives', 'users', 'user_sessions', 'recovery_codes', 'auth_rate_limits',
    'cooperative_modules', 'cooperative_policy_versions', 'loan_products',
    'savings_locks', 'withdrawal_requests', 'loan_applications', 'loans',
    'member_consents', 'consent_rate_limits', 'disputes', 'notifications',
    'disbursements', 'repayments', 'repayment_allocations', 'payroll_import_profiles',
    'payroll_runs', 'payroll_items', 'role_change_proposals'
  ] LOOP
    IF to_regclass('public.' || table_name) IS NOT NULL THEN
      EXECUTE format('GRANT UPDATE ON TABLE public.%I TO ajo_app', table_name);
    END IF;
  END LOOP;
END $$;

-- Preserve the explicit least-privilege column grants from migrations 0015 and 0016.
GRANT UPDATE (activated_at) ON TABLE public.loan_schedule TO ajo_app;
GRANT UPDATE (staff_no) ON TABLE public.members TO ajo_app;
GRANT UPDATE (savings_backing_bps, savings_backing_amount_kobo)
  ON TABLE public.approvals TO ajo_app;

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
