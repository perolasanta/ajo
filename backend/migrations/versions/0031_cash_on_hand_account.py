"""Add the cash-on-hand asset account for all existing cooperatives.

Revision ID: 0031
Revises: 0030
"""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """INSERT INTO accounts (cooperative_id,code,name,account_type)
        SELECT id,'cash_on_hand','Cash held by officers','asset' FROM cooperatives
        ON CONFLICT (cooperative_id,code) DO NOTHING"""
    )


def downgrade() -> None:
    op.execute(
        """DELETE FROM accounts WHERE code='cash_on_hand' AND NOT EXISTS (
          SELECT 1 FROM journal_lines WHERE journal_lines.cooperative_id=accounts.cooperative_id
            AND journal_lines.account_id=accounts.id
        )"""
    )
