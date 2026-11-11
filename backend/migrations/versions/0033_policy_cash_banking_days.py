"""Add a typed cash-banking window to cooperative policy versions.

Revision ID: 0033
Revises: 0032

The immutable approved-policy guard is a row trigger for UPDATE/DELETE. Adding a
constant DEFAULT column is DDL, not an UPDATE, and therefore does not fire that
guard or alter the stored values of existing approved rows.
"""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """ALTER TABLE cooperative_policy_versions
        ADD COLUMN cash_banking_days integer NOT NULL DEFAULT 2
        CHECK (cash_banking_days BETWEEN 1 AND 30)"""
    )


def downgrade() -> None:
    op.execute("ALTER TABLE cooperative_policy_versions DROP COLUMN cash_banking_days")
