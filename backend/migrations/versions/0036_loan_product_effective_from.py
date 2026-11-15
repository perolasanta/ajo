"""Add effective dates to loan products.

Revision ID: 0036
Revises: 0035
"""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE loan_products ADD COLUMN effective_from date NOT NULL DEFAULT CURRENT_DATE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE loan_products DROP COLUMN effective_from")
