"""Add optional staff display names.

Revision ID: 0021
Revises: 0020
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN full_name text")
    op.execute(
        """DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_app') THEN
          GRANT UPDATE (full_name) ON TABLE users TO ajo_app;
        END IF; END $$"""
    )


def downgrade() -> None:
    op.execute(
        """DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_app') THEN
          REVOKE UPDATE (full_name) ON TABLE users FROM ajo_app;
        END IF; END $$"""
    )
    op.execute("ALTER TABLE users DROP COLUMN full_name")
