"""Allow the application role to activate repayment schedules at disbursement.

Revision ID: 0015
Revises: 0014
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_app') THEN
      GRANT UPDATE (activated_at) ON TABLE loan_schedule TO ajo_app;
      GRANT UPDATE (staff_no) ON TABLE members TO ajo_app;
    END IF; END $$""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_app') THEN
      REVOKE UPDATE (activated_at) ON TABLE loan_schedule FROM ajo_app;
      REVOKE UPDATE (staff_no) ON TABLE members FROM ajo_app;
    END IF; END $$""")
