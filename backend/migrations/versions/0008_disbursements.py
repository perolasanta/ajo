"""Manual disbursement preparation and independent authorization.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE disbursements (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
      loan_id uuid NOT NULL, amount_kobo bigint NOT NULL CHECK (amount_kobo > 0),
      fee_kobo bigint NOT NULL CHECK (fee_kobo >= 0), net_kobo bigint NOT NULL CHECK (net_kobo > 0),
      bank_code text NOT NULL, last4 text NOT NULL CHECK (last4 ~ '^[0-9]{4}$'),
      account_name text NOT NULL, bank_account_id uuid NOT NULL,
      status text NOT NULL DEFAULT 'pending_authorization' CHECK (status IN
        ('pending_authorization','authorized','transfer_recorded','paid','failed','cancelled')),
      prepared_by uuid NOT NULL, authorized_by uuid, transfer_reference text,
      transfer_recorded_by uuid, transfer_date date, paid_confirmed_by uuid,
      created_at timestamptz NOT NULL DEFAULT now(), authorized_at timestamptz,
      transfer_recorded_at timestamptz, paid_at timestamptz, failed_at timestamptz,
      journal_entry_id uuid, reversed_by uuid, reversed_at timestamptz, reversal_reason text,
      reversal_entry_id uuid,
      FOREIGN KEY (cooperative_id,loan_id) REFERENCES loans(cooperative_id,id),
      FOREIGN KEY (cooperative_id,bank_account_id) REFERENCES bank_accounts(cooperative_id,id),
      FOREIGN KEY (cooperative_id,prepared_by) REFERENCES users(cooperative_id,id),
      FOREIGN KEY (cooperative_id,authorized_by) REFERENCES users(cooperative_id,id),
      FOREIGN KEY (cooperative_id,transfer_recorded_by) REFERENCES users(cooperative_id,id),
      FOREIGN KEY (cooperative_id,paid_confirmed_by) REFERENCES users(cooperative_id,id),
      FOREIGN KEY (cooperative_id,journal_entry_id) REFERENCES journal_entries(cooperative_id,id),
      FOREIGN KEY (cooperative_id,reversed_by) REFERENCES users(cooperative_id,id),
      FOREIGN KEY (cooperative_id,reversal_entry_id) REFERENCES journal_entries(cooperative_id,id),
      UNIQUE (cooperative_id,id)
    )""")
    op.execute("ALTER TABLE disbursements ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE disbursements FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY disbursements_tenant ON disbursements USING
      (cooperative_id=app_current_cooperative_id()) WITH CHECK
      (cooperative_id=app_current_cooperative_id())""")
    op.execute("""CREATE UNIQUE INDEX disbursements_one_open_per_loan ON disbursements(cooperative_id,loan_id)
      WHERE status NOT IN ('failed','cancelled')""")
    op.execute("""CREATE UNIQUE INDEX savings_locks_one_active_per_loan
      ON savings_locks(cooperative_id,loan_id) WHERE loan_id IS NOT NULL AND released_at IS NULL""")
    op.execute("ALTER TABLE loan_schedule ADD COLUMN activated_at timestamptz")
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_app') THEN
      GRANT SELECT,INSERT,UPDATE ON disbursements TO ajo_app;
      REVOKE DELETE,TRUNCATE ON disbursements FROM ajo_app;
    END IF; END $$""")


def downgrade() -> None:
    op.execute("DROP INDEX disbursements_one_open_per_loan")
    op.execute("DROP INDEX savings_locks_one_active_per_loan")
    op.execute("ALTER TABLE loan_schedule DROP COLUMN activated_at")
    op.execute("DROP TABLE disbursements")
