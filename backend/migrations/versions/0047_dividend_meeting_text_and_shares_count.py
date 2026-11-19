"""Convert dividend_runs.declared_by_meeting_id to meeting_or_resolution text.

Also renames the column so the UI no longer exposes a UUID field.
Existing UUID values are preserved as their text representation so staging
data is not lost.  The column becomes nullable so rows without a meeting
reference are valid (the field is optional).

Revision ID: 0047
Revises: 0046
Create Date: 2026-11-18
"""
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Rename and retype declared_by_meeting_id UUID → meeting_or_resolution text."""
    op.execute(
        """ALTER TABLE dividend_runs
           ADD COLUMN meeting_or_resolution TEXT"""
    )
    op.execute(
        """UPDATE dividend_runs
           SET meeting_or_resolution = declared_by_meeting_id::text
           WHERE declared_by_meeting_id IS NOT NULL"""
    )
    op.execute(
        """ALTER TABLE dividend_runs
           DROP COLUMN declared_by_meeting_id"""
    )


def downgrade() -> None:
    """Restore declared_by_meeting_id UUID column (data loss: text that is not a UUID is dropped)."""
    op.execute(
        """ALTER TABLE dividend_runs
           ADD COLUMN declared_by_meeting_id UUID"""
    )
    op.execute(
        """UPDATE dividend_runs
           SET declared_by_meeting_id = meeting_or_resolution::uuid
           WHERE meeting_or_resolution ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'"""
    )
    op.execute(
        """ALTER TABLE dividend_runs
           DROP COLUMN meeting_or_resolution"""
    )
