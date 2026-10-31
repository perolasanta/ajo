"""Persist reasons for manual repayments on disputed or paused loans.

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE repayments ADD COLUMN reason text")


def downgrade() -> None:
    op.execute("ALTER TABLE repayments DROP COLUMN reason")
