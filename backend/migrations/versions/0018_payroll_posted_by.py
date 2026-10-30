"""Track the user who posts a payroll run for independent sign-off.

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payroll_runs",
        sa.Column("posted_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_payroll_runs_posted_by_users",
        "payroll_runs",
        "users",
        ["cooperative_id", "posted_by"],
        ["cooperative_id", "id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_payroll_runs_posted_by_users", "payroll_runs", type_="foreignkey")
    op.drop_column("payroll_runs", "posted_by")
