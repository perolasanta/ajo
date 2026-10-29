"""Keep one nonterminal loan application per member across all workflow states.

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


_TERMINAL_STATUSES = "'rejected','withdrawn','expired','disbursement_failed','closed','written_off'"


def upgrade() -> None:
    op.execute("DROP INDEX loan_applications_one_active_per_member")
    op.execute(
        """CREATE UNIQUE INDEX loan_applications_one_active_per_member
        ON loan_applications(cooperative_id,member_id)
        WHERE status NOT IN ("""
        + _TERMINAL_STATUSES
        + ")"
    )


def downgrade() -> None:
    op.execute("DROP INDEX loan_applications_one_active_per_member")
    op.execute(
        """CREATE UNIQUE INDEX loan_applications_one_active_per_member
        ON loan_applications(cooperative_id,member_id)
        WHERE status IN ('submitted','approved','disbursed','repaying')"""
    )
