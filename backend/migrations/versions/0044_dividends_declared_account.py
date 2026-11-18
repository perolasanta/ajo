"""Add dividends_declared account to chart and seed existing cooperatives.

Revision ID: 0044
Revises: 0043
Create Date: 2026-01-16 12:05:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add dividends_declared equity account to all cooperatives."""
    op.execute(
        """INSERT INTO accounts (
            cooperative_id, code, name, account_type, created_at
        )
        SELECT
            c.id,
            'dividends_declared',
            'Dividends declared',
            'equity',
            CURRENT_TIMESTAMP
        FROM cooperatives c
        WHERE NOT EXISTS (
            SELECT 1 FROM accounts
            WHERE cooperative_id = c.id AND code = 'dividends_declared'
        );
    """
    )


def downgrade() -> None:
    """Remove dividends_declared account from all cooperatives."""
    op.execute(
        """DELETE FROM accounts
           WHERE code = 'dividends_declared';
    """
    )
