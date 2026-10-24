"""Members, member financial records, and withdrawal requests.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
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
        """CREATE TABLE members (
          id uuid NOT NULL DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
          member_no text NOT NULL, full_name text NOT NULL, phone_e164 text NOT NULL,
          email text, staff_no text, employer text,
          status text NOT NULL DEFAULT 'applicant' CHECK (status IN ('applicant','active','suspended','exited')),
          joined_on date, created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (cooperative_id,id), UNIQUE (cooperative_id,member_no)
        )"""
    )
    op.execute(
        """CREATE TABLE salary_records (
          id uuid NOT NULL DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
          member_id uuid NOT NULL, effective_from date NOT NULL,
          basis text NOT NULL CHECK (basis IN ('net','gross')), amount_kobo bigint NOT NULL CHECK (amount_kobo >= 0),
          source text NOT NULL CHECK (source IN ('declared','payroll','hr')), recorded_by uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (cooperative_id,id),
          FOREIGN KEY (cooperative_id,member_id) REFERENCES members(cooperative_id,id),
          FOREIGN KEY (cooperative_id,recorded_by) REFERENCES users(cooperative_id,id)
        )"""
    )
    op.execute(
        """CREATE TABLE bank_accounts (
          id uuid NOT NULL DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
          member_id uuid NOT NULL, bank_code text NOT NULL, account_number_encrypted text NOT NULL,
          last4 text NOT NULL CHECK (last4 ~ '^[0-9]{4}$'), account_name text NOT NULL,
          verified_at timestamptz, status text NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','verified','disabled')), created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (cooperative_id,id),
          FOREIGN KEY (cooperative_id,member_id) REFERENCES members(cooperative_id,id)
        )"""
    )
    op.execute(
        """CREATE TABLE savings_locks (
          id uuid NOT NULL DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
          member_id uuid NOT NULL, loan_id uuid, amount_kobo bigint NOT NULL CHECK (amount_kobo > 0),
          released_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (cooperative_id,id),
          FOREIGN KEY (cooperative_id,member_id) REFERENCES members(cooperative_id,id)
        )"""
    )
    op.execute(
        """CREATE TABLE withdrawal_requests (
          id uuid NOT NULL DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
          member_id uuid NOT NULL, amount_kobo bigint NOT NULL CHECK (amount_kobo > 0),
          idempotency_key text NOT NULL, status text NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','approved','rejected')),
          requested_by uuid NOT NULL, approved_by uuid, journal_entry_id uuid,
          created_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz,
          PRIMARY KEY (cooperative_id,id), UNIQUE (cooperative_id,idempotency_key),
          FOREIGN KEY (cooperative_id,member_id) REFERENCES members(cooperative_id,id),
          FOREIGN KEY (cooperative_id,requested_by) REFERENCES users(cooperative_id,id),
          FOREIGN KEY (cooperative_id,approved_by) REFERENCES users(cooperative_id,id),
          FOREIGN KEY (cooperative_id,journal_entry_id) REFERENCES journal_entries(cooperative_id,id)
        )"""
    )
    for table in (
        "members",
        "salary_records",
        "bank_accounts",
        "savings_locks",
        "withdrawal_requests",
    ):
        _rls(table)
    op.execute(
        "CREATE INDEX salary_records_member_date_idx ON salary_records(cooperative_id,member_id,effective_from DESC)"
    )
    op.execute(
        "CREATE INDEX savings_locks_active_idx ON savings_locks(cooperative_id,member_id) WHERE released_at IS NULL"
    )
    op.execute(
        """CREATE TRIGGER salary_records_append_only BEFORE UPDATE OR DELETE ON salary_records
        FOR EACH STATEMENT EXECUTE FUNCTION reject_ledger_mutation()"""
    )
    op.execute(
        """CREATE TRIGGER salary_records_no_truncate BEFORE TRUNCATE ON salary_records
        FOR EACH STATEMENT EXECUTE FUNCTION reject_ledger_mutation()"""
    )
    op.execute(
        """ALTER TABLE journal_lines ADD CONSTRAINT journal_lines_member_fk
        FOREIGN KEY (cooperative_id,member_id) REFERENCES members(cooperative_id,id)"""
    )
    op.execute(
        """DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_app') THEN
          GRANT SELECT,INSERT ON members,salary_records,bank_accounts TO ajo_app;
          GRANT SELECT,INSERT,UPDATE ON savings_locks,withdrawal_requests TO ajo_app;
          REVOKE UPDATE,DELETE,TRUNCATE ON members,salary_records,bank_accounts FROM ajo_app;
          REVOKE DELETE,TRUNCATE ON savings_locks,withdrawal_requests FROM ajo_app;
        END IF; END $$"""
    )


def downgrade() -> None:
    op.execute("ALTER TABLE journal_lines DROP CONSTRAINT journal_lines_member_fk")
    for table in (
        "withdrawal_requests",
        "savings_locks",
        "bank_accounts",
        "salary_records",
        "members",
    ):
        op.execute(f"DROP TABLE {table}")
