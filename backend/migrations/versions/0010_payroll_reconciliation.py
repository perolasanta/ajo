"""Payroll import profiles, runs, and reconciliation items.

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant ON {table} USING (cooperative_id=app_current_cooperative_id()) WITH CHECK (cooperative_id=app_current_cooperative_id())"
    )


def upgrade() -> None:
    op.execute("""CREATE TABLE payroll_import_profiles (
      id uuid NOT NULL DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
      version integer NOT NULL CHECK (version > 0), staff_no_column text NOT NULL, amount_column text NOT NULL,
      name_column text, header_row integer NOT NULL DEFAULT 1 CHECK (header_row > 0), created_by uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (cooperative_id,id),
      UNIQUE (cooperative_id,version), FOREIGN KEY (cooperative_id,created_by) REFERENCES users(cooperative_id,id)
    )""")
    op.execute("""CREATE TABLE payroll_runs (
      id uuid NOT NULL DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
      profile_id uuid NOT NULL, period_year integer NOT NULL CHECK (period_year BETWEEN 2000 AND 9999),
      period_month integer NOT NULL CHECK (period_month BETWEEN 1 AND 12),
      status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','exported','imported','posted','signed_off')),
      expected_total_kobo bigint NOT NULL DEFAULT 0 CHECK (expected_total_kobo >= 0),
      file_total_kobo bigint, bank_receipt_total_kobo bigint, file_sha256 bytea,
      created_by uuid NOT NULL, imported_by uuid, signed_off_by uuid, created_at timestamptz NOT NULL DEFAULT now(),
      imported_at timestamptz, posted_at timestamptz, signed_off_at timestamptz,
      PRIMARY KEY (cooperative_id,id), UNIQUE (cooperative_id,period_year,period_month),
      UNIQUE NULLS NOT DISTINCT (cooperative_id,period_year,period_month,file_sha256),
      FOREIGN KEY (cooperative_id,profile_id) REFERENCES payroll_import_profiles(cooperative_id,id),
      FOREIGN KEY (cooperative_id,created_by) REFERENCES users(cooperative_id,id),
      FOREIGN KEY (cooperative_id,imported_by) REFERENCES users(cooperative_id,id),
      FOREIGN KEY (cooperative_id,signed_off_by) REFERENCES users(cooperative_id,id)
    )""")
    op.execute("""CREATE TABLE payroll_items (
      id uuid NOT NULL DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id), run_id uuid NOT NULL,
      member_id uuid, staff_no_raw text NOT NULL, expected_loan_kobo bigint NOT NULL DEFAULT 0 CHECK (expected_loan_kobo >= 0),
      expected_savings_kobo bigint NOT NULL DEFAULT 0 CHECK (expected_savings_kobo >= 0),
      expected_dues_kobo bigint NOT NULL DEFAULT 0 CHECK (expected_dues_kobo >= 0), actual_kobo bigint,
      classification text NOT NULL CHECK (classification IN ('matched','short','over','missing','unknown_member','duplicate')),
      resolution_note text, resolved_by uuid, posted_repayment_id uuid, created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (cooperative_id,id), FOREIGN KEY (cooperative_id,run_id) REFERENCES payroll_runs(cooperative_id,id),
      FOREIGN KEY (cooperative_id,member_id) REFERENCES members(cooperative_id,id),
      FOREIGN KEY (cooperative_id,resolved_by) REFERENCES users(cooperative_id,id),
      FOREIGN KEY (cooperative_id,posted_repayment_id) REFERENCES repayments(cooperative_id,id)
    )""")
    for table in ("payroll_import_profiles", "payroll_runs", "payroll_items"):
        _rls(table)
    op.execute("CREATE INDEX payroll_items_run_idx ON payroll_items(cooperative_id,run_id)")
    op.execute("""CREATE FUNCTION payroll_signed_off_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
      BEGIN IF TG_OP IN ('UPDATE','DELETE') AND OLD.status='signed_off' THEN RAISE EXCEPTION 'signed-off payroll runs are immutable' USING ERRCODE='55000'; END IF; RETURN COALESCE(NEW,OLD); END $$""")
    op.execute(
        "CREATE TRIGGER payroll_runs_signed_off_guard BEFORE UPDATE OR DELETE ON payroll_runs FOR EACH ROW EXECUTE FUNCTION payroll_signed_off_guard()"
    )
    op.execute("""CREATE FUNCTION payroll_items_signed_off_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
      BEGIN IF EXISTS (SELECT 1 FROM payroll_runs r WHERE r.cooperative_id=COALESCE(NEW.cooperative_id,OLD.cooperative_id) AND r.id=COALESCE(NEW.run_id,OLD.run_id) AND r.status='signed_off') THEN RAISE EXCEPTION 'signed-off payroll items are immutable' USING ERRCODE='55000'; END IF; RETURN COALESCE(NEW,OLD); END $$""")
    op.execute(
        "CREATE TRIGGER payroll_items_signed_off_guard BEFORE INSERT OR UPDATE OR DELETE ON payroll_items FOR EACH ROW EXECUTE FUNCTION payroll_items_signed_off_guard()"
    )
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_app') THEN
      GRANT SELECT,INSERT,UPDATE ON payroll_import_profiles,payroll_runs,payroll_items TO ajo_app;
      REVOKE DELETE,TRUNCATE ON payroll_import_profiles,payroll_runs,payroll_items FROM ajo_app;
    END IF; END $$""")


def downgrade() -> None:
    op.execute("DROP TABLE payroll_items")
    op.execute("DROP TABLE payroll_runs")
    op.execute("DROP TABLE payroll_import_profiles")
    op.execute("DROP FUNCTION payroll_items_signed_off_guard()")
    op.execute("DROP FUNCTION payroll_signed_off_guard()")
