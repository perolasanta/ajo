"""Reconcile application-role ACLs after privilege-free restores.

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM ajo_app")
    op.execute("GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA public TO ajo_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ajo_app")
    op.execute("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO ajo_app")
    op.execute("""
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
    """)
    op.execute("""
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
    """)
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ajo_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO ajo_app"
    )
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO ajo_app")


def downgrade() -> None:
    # The grants are a safety repair and are intentionally not removed on downgrade.
    pass
