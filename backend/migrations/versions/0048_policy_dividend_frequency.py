"""Add dividend_frequency typed column to cooperative_policy_versions.

Revision ID: 0048
Revises: 0047
Create Date: 2026-11-18

Adds a CHECK-constrained text column so the cooperative can configure how
often it pays dividends (annual, half_yearly, quarterly, monthly).  Existing
approved rows are left untouched by the DDL (ADD COLUMN with a DEFAULT does
not fire the immutable-row guard).  The default 'annual' ensures old rows
behave exactly as before.
"""
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

_VALID = "CHECK (dividend_frequency IN ('annual', 'half_yearly', 'quarterly', 'monthly'))"


def upgrade() -> None:
    op.execute(
        f"""ALTER TABLE cooperative_policy_versions
        ADD COLUMN dividend_frequency TEXT NOT NULL DEFAULT 'annual'
        {_VALID}"""
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE cooperative_policy_versions DROP COLUMN dividend_frequency"
    )
