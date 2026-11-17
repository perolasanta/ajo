"""Allow statement evidence rows without a bank-provided narration.

Revision ID: 0042
Revises: 0041
"""

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """ALTER TABLE bank_statement_lines
           DROP CONSTRAINT bank_statement_lines_narration_check,
           ADD CONSTRAINT bank_statement_lines_narration_length_check
             CHECK (char_length(btrim(narration)) <= 2000)"""
    )


def downgrade() -> None:
    op.execute(
        """ALTER TABLE bank_statement_lines
           DROP CONSTRAINT bank_statement_lines_narration_length_check,
           ADD CONSTRAINT bank_statement_lines_narration_check
             CHECK (char_length(btrim(narration)) BETWEEN 1 AND 2000) NOT VALID"""
    )
