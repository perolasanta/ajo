"""Add period_key to dividend_runs, partial unique index, and journal_entry_id.

- period_key TEXT (nullable): set to the financial year as text ('2025') for new runs;
  legacy rows keep NULL and do not conflict.
- Partial unique index on (cooperative_id, period_key) WHERE status IN
  ('unprocessed','approved','posted') AND period_key IS NOT NULL; prevents
  duplicate active runs for the same period.
- journal_entry_id UUID (nullable FK to journal_entries): records which entry
  was posted so the repair command can detect missing entries.

Existing 'approved' rows on staging have no entry and are repaired by the
repair command; this migration does not break them.

Revision ID: 0046
Revises: 0045
"""
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """ALTER TABLE dividend_runs
           ADD COLUMN IF NOT EXISTS period_key TEXT"""
    )
    op.execute(
        """ALTER TABLE dividend_runs
           ADD COLUMN IF NOT EXISTS journal_entry_id UUID"""
    )
    op.execute(
        """CREATE UNIQUE INDEX uq_dividend_runs_coop_period_active
           ON dividend_runs (cooperative_id, period_key)
           WHERE status IN ('unprocessed','approved','posted')
             AND period_key IS NOT NULL"""
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS uq_dividend_runs_coop_period_active"
    )
    op.execute(
        "ALTER TABLE dividend_runs DROP COLUMN IF EXISTS journal_entry_id"
    )
    op.execute(
        "ALTER TABLE dividend_runs DROP COLUMN IF EXISTS period_key"
    )
