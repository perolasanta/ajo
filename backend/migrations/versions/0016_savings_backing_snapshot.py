"""Persist disbursement savings backing on the approval decision record.

Revision ID: 0016
Revises: 0015
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE approvals ADD COLUMN savings_backing_bps integer")
    op.execute("ALTER TABLE approvals ADD COLUMN savings_backing_amount_kobo bigint")
    op.execute("""ALTER TABLE approvals ADD CONSTRAINT approvals_savings_backing_bps_range
      CHECK (savings_backing_bps IS NULL OR savings_backing_bps BETWEEN 0 AND 10000)""")
    op.execute("""ALTER TABLE approvals ADD CONSTRAINT approvals_savings_backing_amount_nonnegative
      CHECK (savings_backing_amount_kobo IS NULL OR savings_backing_amount_kobo >= 0)""")
    op.execute("""ALTER TABLE approvals ADD CONSTRAINT approvals_savings_backing_values_pair
      CHECK ((savings_backing_bps IS NULL) = (savings_backing_amount_kobo IS NULL))""")
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_app') THEN
      GRANT UPDATE (savings_backing_bps,savings_backing_amount_kobo) ON approvals TO ajo_app;
    END IF; END $$""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_app') THEN
      REVOKE UPDATE (savings_backing_bps,savings_backing_amount_kobo) ON approvals FROM ajo_app;
    END IF; END $$""")
    op.execute("ALTER TABLE approvals DROP CONSTRAINT approvals_savings_backing_values_pair")
    op.execute("ALTER TABLE approvals DROP CONSTRAINT approvals_savings_backing_amount_nonnegative")
    op.execute("ALTER TABLE approvals DROP CONSTRAINT approvals_savings_backing_bps_range")
    op.execute("ALTER TABLE approvals DROP COLUMN savings_backing_amount_kobo")
    op.execute("ALTER TABLE approvals DROP COLUMN savings_backing_bps")
