"""policy versions, modules and loan products

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY {table}_cooperative_isolation ON {table}
        USING (cooperative_id = app_current_cooperative_id())
        WITH CHECK (cooperative_id = app_current_cooperative_id())"""
    )


def upgrade() -> None:
    op.execute(
        """ALTER TABLE cooperatives
        ADD COLUMN operating_model text NOT NULL DEFAULT 'loan_cooperative'
          CHECK (operating_model IN ('loan_cooperative','thrift_society','rotating_ajo')),
        ADD COLUMN registration_number text,
        ADD COLUMN registered_name text,
        ADD COLUMN registration_authority text,
        ADD COLUMN registration_state text,
        ADD COLUMN registered_at date"""
    )
    op.execute(
        """CREATE TABLE cooperative_modules (
          cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
          module text NOT NULL CHECK (module IN ('loans','thrift','rotating_ajo','administration')),
          enabled boolean NOT NULL DEFAULT true,
          enabled_at timestamptz NOT NULL DEFAULT now(),
          disabled_at timestamptz,
          PRIMARY KEY (cooperative_id, module),
          CHECK ((enabled AND disabled_at IS NULL) OR (NOT enabled AND disabled_at IS NOT NULL))
        )"""
    )
    op.execute(
        """CREATE TABLE cooperative_policy_versions (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
          version integer NOT NULL CHECK (version >= 1),
          status text NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft','proposed','approved','rejected','superseded')),
          effective_from date NOT NULL,
          effective_to date,
          proposed_by uuid NOT NULL,
          approved_by uuid,
          salary_basis text NOT NULL CHECK (salary_basis IN ('net','gross')),
          deduction_cap_bps integer NOT NULL CHECK (deduction_cap_bps BETWEEN 0 AND 10000),
          max_active_loans integer NOT NULL CHECK (max_active_loans >= 1),
          guarantors_required integer NOT NULL CHECK (guarantors_required >= 0),
          min_savings_kobo bigint NOT NULL CHECK (min_savings_kobo >= 0),
          loan_to_savings_multiple_x100 integer NOT NULL CHECK (loan_to_savings_multiple_x100 >= 0),
          interest_method text NOT NULL CHECK (interest_method IN ('reducing_balance','flat')),
          confirmation_expiry_minutes integer NOT NULL DEFAULT 1440 CHECK (confirmation_expiry_minutes > 0),
          otp_validity_seconds integer NOT NULL DEFAULT 300 CHECK (otp_validity_seconds > 0),
          settings jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(settings) = 'object'),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (cooperative_id, id),
          UNIQUE (cooperative_id, version),
          CHECK (effective_to IS NULL OR effective_to > effective_from),
          CHECK (status <> 'approved' OR (approved_by IS NOT NULL AND approved_by <> proposed_by)),
          FOREIGN KEY (cooperative_id, proposed_by) REFERENCES users(cooperative_id, id),
          FOREIGN KEY (cooperative_id, approved_by) REFERENCES users(cooperative_id, id)
        )"""
    )
    op.execute(
        "CREATE INDEX cooperative_policy_effective_idx ON cooperative_policy_versions "
        "(cooperative_id, effective_from, effective_to) WHERE status = 'approved'"
    )
    op.execute(
        """CREATE TABLE policy_change_log (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
          policy_version_id uuid,
          action text NOT NULL,
          actor_user_id uuid NOT NULL,
          reason text NOT NULL,
          previous_values jsonb,
          new_values jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (cooperative_id, policy_version_id)
            REFERENCES cooperative_policy_versions(cooperative_id, id),
          FOREIGN KEY (cooperative_id, actor_user_id) REFERENCES users(cooperative_id, id)
        )"""
    )
    op.execute(
        """CREATE TABLE loan_products (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
          code text NOT NULL,
          name text NOT NULL,
          interest_method text NOT NULL CHECK (interest_method IN ('reducing_balance','flat')),
          annual_rate_bps integer NOT NULL CHECK (annual_rate_bps BETWEEN 0 AND 100000),
          min_amount_kobo bigint NOT NULL CHECK (min_amount_kobo >= 0),
          max_amount_kobo bigint NOT NULL CHECK (max_amount_kobo >= min_amount_kobo),
          min_tenor_months integer NOT NULL CHECK (min_tenor_months >= 1),
          max_tenor_months integer NOT NULL CHECK (max_tenor_months >= min_tenor_months),
          fee_bps integer NOT NULL DEFAULT 0 CHECK (fee_bps BETWEEN 0 AND 100000),
          guarantors_required integer NOT NULL CHECK (guarantors_required >= 0),
          status text NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft','proposed','approved','rejected','superseded')),
          supersedes_id uuid,
          created_at timestamptz NOT NULL DEFAULT now(),
          approved_at timestamptz,
          PRIMARY KEY (cooperative_id, id),
          FOREIGN KEY (cooperative_id, supersedes_id) REFERENCES loan_products(cooperative_id, id),
          CHECK (supersedes_id IS NULL OR supersedes_id <> id)
        )"""
    )
    op.execute(
        "CREATE UNIQUE INDEX loan_products_approved_code_idx ON loan_products(cooperative_id, code) "
        "WHERE status = 'approved'"
    )

    for table in (
        "cooperative_modules",
        "cooperative_policy_versions",
        "policy_change_log",
        "loan_products",
    ):
        _tenant_rls(table)

    op.execute(
        """CREATE FUNCTION protect_policy_version() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE overlaps_existing boolean;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'policy versions cannot be deleted' USING ERRCODE = '55000';
          END IF;
          IF TG_OP = 'UPDATE' AND OLD.status = 'approved' THEN
            IF NEW.status <> 'superseded' OR NEW.effective_to IS NULL OR
              (to_jsonb(NEW) - ARRAY['status','effective_to']) IS DISTINCT FROM
              (to_jsonb(OLD) - ARRAY['status','effective_to']) THEN
              RAISE EXCEPTION 'approved policy versions are immutable' USING ERRCODE = '55000';
            END IF;
          END IF;
          IF NEW.status = 'approved' THEN
            PERFORM pg_advisory_xact_lock(hashtextextended(NEW.cooperative_id::text, 4004));
            SELECT EXISTS (
              SELECT 1 FROM cooperative_policy_versions p
              WHERE p.cooperative_id = NEW.cooperative_id AND p.status = 'approved'
                AND p.id <> NEW.id
                AND daterange(p.effective_from, p.effective_to, '[)') &&
                    daterange(NEW.effective_from, NEW.effective_to, '[)')
            ) INTO overlaps_existing;
            IF overlaps_existing THEN
              RAISE EXCEPTION 'approved policy effective dates overlap' USING ERRCODE = '23P01';
            END IF;
          END IF;
          RETURN NEW;
        END $$"""
    )
    op.execute(
        """CREATE TRIGGER cooperative_policy_version_guard
        BEFORE INSERT OR UPDATE OR DELETE ON cooperative_policy_versions
        FOR EACH ROW EXECUTE FUNCTION protect_policy_version()"""
    )
    op.execute(
        """CREATE FUNCTION protect_approved_loan_product() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'loan products cannot be deleted' USING ERRCODE = '55000';
          END IF;
          IF TG_OP = 'UPDATE' AND OLD.status = 'approved' THEN
            IF NEW.status <> 'superseded' OR
              (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
              RAISE EXCEPTION 'approved loan products are immutable; create a superseding row'
                USING ERRCODE = '55000';
            END IF;
          END IF;
          RETURN NEW;
        END $$"""
    )
    op.execute(
        """CREATE TRIGGER loan_product_guard BEFORE UPDATE OR DELETE ON loan_products
        FOR EACH ROW EXECUTE FUNCTION protect_approved_loan_product()"""
    )
    op.execute(
        """CREATE FUNCTION reject_policy_log_mutation() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        BEGIN RAISE EXCEPTION 'policy_change_log is append-only' USING ERRCODE = '55000'; END $$"""
    )
    op.execute(
        """CREATE TRIGGER policy_change_log_no_update_delete BEFORE UPDATE OR DELETE ON policy_change_log
        FOR EACH STATEMENT EXECUTE FUNCTION reject_policy_log_mutation()"""
    )
    op.execute(
        """CREATE TRIGGER policy_change_log_no_truncate BEFORE TRUNCATE ON policy_change_log
        FOR EACH STATEMENT EXECUTE FUNCTION reject_policy_log_mutation()"""
    )
    op.execute(
        """CREATE FUNCTION prevent_module_delete() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        BEGIN RAISE EXCEPTION 'cooperative modules cannot be deleted' USING ERRCODE = '55000'; END $$"""
    )
    op.execute(
        """CREATE TRIGGER cooperative_modules_no_delete BEFORE DELETE OR TRUNCATE ON cooperative_modules
        FOR EACH STATEMENT EXECUTE FUNCTION prevent_module_delete()"""
    )
    op.execute(
        """DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ajo_app') THEN
            GRANT SELECT, INSERT, UPDATE ON cooperative_modules TO ajo_app;
            REVOKE DELETE, TRUNCATE ON cooperative_modules FROM ajo_app;
            GRANT SELECT, INSERT, UPDATE ON cooperative_policy_versions TO ajo_app;
            REVOKE DELETE, TRUNCATE ON cooperative_policy_versions FROM ajo_app;
            GRANT SELECT, INSERT ON policy_change_log TO ajo_app;
            REVOKE UPDATE, DELETE, TRUNCATE ON policy_change_log FROM ajo_app;
            GRANT SELECT, INSERT, UPDATE ON loan_products TO ajo_app;
            REVOKE DELETE, TRUNCATE ON loan_products FROM ajo_app;
          END IF;
        END $$"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE loan_products")
    op.execute("DROP TABLE policy_change_log")
    op.execute("DROP TABLE cooperative_policy_versions")
    op.execute("DROP TABLE cooperative_modules")
    op.execute(
        "ALTER TABLE cooperatives DROP COLUMN registration_number, DROP COLUMN registered_name, "
        "DROP COLUMN registration_authority, DROP COLUMN registration_state, DROP COLUMN registered_at, "
        "DROP COLUMN operating_model"
    )
    for function in (
        "prevent_module_delete",
        "reject_policy_log_mutation",
        "protect_approved_loan_product",
        "protect_policy_version",
    ):
        op.execute(f"DROP FUNCTION {function}()")
