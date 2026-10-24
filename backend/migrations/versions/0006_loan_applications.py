"""Loan applications, terms, schedules, guarantees and committee decisions.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant ON {table} USING "
        "(cooperative_id = app_current_cooperative_id()) WITH CHECK "
        "(cooperative_id = app_current_cooperative_id())"
    )


def upgrade() -> None:
    op.execute(
        """CREATE TABLE loan_applications (
          id uuid NOT NULL DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
          member_id uuid NOT NULL, beneficiary_member_id uuid, product_id uuid NOT NULL,
          policy_version_id uuid NOT NULL, submitted_by uuid NOT NULL, staff_submitted boolean NOT NULL DEFAULT true,
          destination_bank_account_id uuid, principal_kobo bigint NOT NULL CHECK (principal_kobo > 0),
          tenor_months integer NOT NULL CHECK (tenor_months > 0), annual_rate_bps integer NOT NULL CHECK (annual_rate_bps BETWEEN 0 AND 100000),
          status text NOT NULL DEFAULT 'draft' CHECK (status IN (
            'draft','submitted','awaiting_applicant_confirmation','under_review','approved',
            'awaiting_borrower_final_confirmation','ready_for_disbursement','disbursed','repaying','closed',
            'rejected','withdrawn','expired','disbursement_failed','restructured','written_off','disputed')),
          partial_check boolean NOT NULL DEFAULT false, current_terms_snapshot_id uuid,
          created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (cooperative_id,id),
          FOREIGN KEY (cooperative_id,member_id) REFERENCES members(cooperative_id,id),
          FOREIGN KEY (cooperative_id,beneficiary_member_id) REFERENCES members(cooperative_id,id),
          FOREIGN KEY (cooperative_id,product_id) REFERENCES loan_products(cooperative_id,id),
          FOREIGN KEY (cooperative_id,policy_version_id) REFERENCES cooperative_policy_versions(cooperative_id,id),
          FOREIGN KEY (cooperative_id,submitted_by) REFERENCES users(cooperative_id,id),
          FOREIGN KEY (cooperative_id,destination_bank_account_id) REFERENCES bank_accounts(cooperative_id,id)
        )"""
    )
    op.execute(
        """CREATE TABLE loan_terms_snapshots (
          id uuid NOT NULL DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
          application_id uuid NOT NULL, terms jsonb NOT NULL CHECK (jsonb_typeof(terms)='object'),
          terms_sha256 bytea NOT NULL CHECK (octet_length(terms_sha256)=32),
          created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (cooperative_id,id),
          FOREIGN KEY (cooperative_id,application_id) REFERENCES loan_applications(cooperative_id,id),
          FOREIGN KEY (cooperative_id,created_by) REFERENCES users(cooperative_id,id),
          UNIQUE (cooperative_id,application_id,id)
        )"""
    )
    op.execute(
        """ALTER TABLE loan_applications ADD CONSTRAINT loan_app_current_snapshot_fk
          FOREIGN KEY (cooperative_id,id,current_terms_snapshot_id)
          REFERENCES loan_terms_snapshots(cooperative_id,application_id,id)"""
    )
    op.execute(
        """CREATE TABLE loans (
          id uuid NOT NULL DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
          application_id uuid NOT NULL, member_id uuid NOT NULL, product_id uuid NOT NULL,
          policy_version_id uuid NOT NULL, terms_snapshot_id uuid NOT NULL,
          status text NOT NULL CHECK (status IN (
            'draft','submitted','awaiting_applicant_confirmation','under_review','approved',
            'awaiting_borrower_final_confirmation','ready_for_disbursement','disbursed','repaying','closed',
            'rejected','withdrawn','expired','disbursement_failed','restructured','written_off','disputed')),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (cooperative_id,id), UNIQUE (cooperative_id,application_id),
          FOREIGN KEY (cooperative_id,application_id) REFERENCES loan_applications(cooperative_id,id),
          FOREIGN KEY (cooperative_id,member_id) REFERENCES members(cooperative_id,id),
          FOREIGN KEY (cooperative_id,product_id) REFERENCES loan_products(cooperative_id,id),
          FOREIGN KEY (cooperative_id,policy_version_id) REFERENCES cooperative_policy_versions(cooperative_id,id),
          FOREIGN KEY (cooperative_id,application_id,terms_snapshot_id)
            REFERENCES loan_terms_snapshots(cooperative_id,application_id,id)
        )"""
    )
    op.execute(
        """CREATE TABLE loan_schedule (
          cooperative_id uuid NOT NULL REFERENCES cooperatives(id), loan_id uuid NOT NULL,
          installment_no integer NOT NULL CHECK (installment_no > 0), due_at timestamptz NOT NULL,
          principal_kobo bigint NOT NULL CHECK (principal_kobo >= 0),
          interest_kobo bigint NOT NULL CHECK (interest_kobo >= 0),
          payment_kobo bigint NOT NULL CHECK (payment_kobo = principal_kobo + interest_kobo),
          balance_kobo bigint NOT NULL CHECK (balance_kobo >= 0),
          PRIMARY KEY (cooperative_id,loan_id,installment_no),
          FOREIGN KEY (cooperative_id,loan_id) REFERENCES loans(cooperative_id,id)
        )"""
    )
    op.execute(
        """CREATE TABLE guarantees (
          id uuid NOT NULL DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
          application_id uuid NOT NULL, guarantor_member_id uuid NOT NULL,
          guaranteed_amount_kobo bigint NOT NULL CHECK (guaranteed_amount_kobo > 0),
          status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','accepted','released')),
          created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (cooperative_id,id),
          FOREIGN KEY (cooperative_id,application_id) REFERENCES loan_applications(cooperative_id,id),
          FOREIGN KEY (cooperative_id,guarantor_member_id) REFERENCES members(cooperative_id,id),
          UNIQUE (cooperative_id,application_id,guarantor_member_id)
        )"""
    )
    op.execute(
        """CREATE TABLE approvals (
          id uuid NOT NULL DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
          application_id uuid NOT NULL, approver_id uuid NOT NULL,
          policy_version_id uuid NOT NULL, product_id uuid NOT NULL,
          decision text NOT NULL CHECK (decision IN ('approved','rejected')),
          reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (cooperative_id,id),
          FOREIGN KEY (cooperative_id,application_id) REFERENCES loan_applications(cooperative_id,id),
          FOREIGN KEY (cooperative_id,approver_id) REFERENCES users(cooperative_id,id),
          FOREIGN KEY (cooperative_id,policy_version_id) REFERENCES cooperative_policy_versions(cooperative_id,id),
          FOREIGN KEY (cooperative_id,product_id) REFERENCES loan_products(cooperative_id,id)
        )"""
    )
    op.execute(
        """CREATE TABLE fraud_alerts (
          id uuid NOT NULL DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
          rule_code text NOT NULL CHECK (rule_code IN ('parallel_loan','third_party_beneficiary','destination_changed','same_user_authorization')),
          entity_type text NOT NULL, entity_id uuid NOT NULL, summary jsonb NOT NULL DEFAULT '{}'::jsonb,
          status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','reviewed','dismissed')),
          created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (cooperative_id,id)
        )"""
    )
    for table in (
        "loan_applications",
        "loan_terms_snapshots",
        "loans",
        "loan_schedule",
        "guarantees",
        "approvals",
        "fraud_alerts",
    ):
        _rls(table)
    op.execute(
        """CREATE UNIQUE INDEX loan_applications_one_active_per_member
          ON loan_applications(cooperative_id,member_id)
          WHERE status IN ('submitted','approved','disbursed','repaying')"""
    )
    op.execute(
        """CREATE UNIQUE INDEX loans_one_active_per_member ON loans(cooperative_id,member_id)
          WHERE status IN ('approved','disbursed','repaying')"""
    )
    op.execute(
        """CREATE FUNCTION protect_loan_snapshot() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        BEGIN RAISE EXCEPTION 'loan terms snapshots are immutable' USING ERRCODE='55000'; END $$"""
    )
    op.execute(
        """CREATE TRIGGER loan_terms_snapshot_immutable BEFORE UPDATE OR DELETE ON loan_terms_snapshots
        FOR EACH STATEMENT EXECUTE FUNCTION protect_loan_snapshot()"""
    )
    op.execute(
        """CREATE TRIGGER loan_terms_snapshot_no_truncate BEFORE TRUNCATE ON loan_terms_snapshots
        FOR EACH STATEMENT EXECUTE FUNCTION protect_loan_snapshot()"""
    )
    op.execute(
        """DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_app') THEN
          GRANT SELECT,INSERT,UPDATE ON loan_applications TO ajo_app;
          GRANT SELECT,INSERT,UPDATE ON loans TO ajo_app;
          GRANT SELECT,INSERT ON loan_terms_snapshots,loan_schedule,guarantees,approvals,fraud_alerts TO ajo_app;
          REVOKE DELETE,TRUNCATE ON loan_applications,loan_terms_snapshots,loans,loan_schedule,guarantees,approvals,fraud_alerts FROM ajo_app;
          REVOKE UPDATE,DELETE,TRUNCATE ON loan_terms_snapshots,loan_schedule,guarantees,approvals,fraud_alerts FROM ajo_app;
        END IF; END $$"""
    )


def downgrade() -> None:
    op.execute("ALTER TABLE loan_applications DROP CONSTRAINT loan_app_current_snapshot_fk")
    for table in (
        "fraud_alerts",
        "approvals",
        "guarantees",
        "loan_schedule",
        "loans",
        "loan_terms_snapshots",
    ):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP TABLE loan_applications")
    op.execute("DROP FUNCTION protect_loan_snapshot()")
