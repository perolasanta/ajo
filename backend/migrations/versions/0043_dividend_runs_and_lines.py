"""Add dividend_runs and dividend_lines tables.

Revision ID: 0043
Revises: 0042
Create Date: 2026-01-16 12:00:00.000000
"""

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create dividend_runs and dividend_lines tables."""
    op.execute(
        """CREATE TABLE dividend_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            cooperative_id UUID NOT NULL REFERENCES cooperatives(id) ON DELETE CASCADE,
            record_date DATE NOT NULL,
            declared_by_meeting_id UUID NOT NULL,
            pool_kobo BIGINT NOT NULL CHECK (pool_kobo > 0),
            prepared_by UUID NOT NULL,
            prepared_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            approved_by UUID,
            approved_at TIMESTAMPTZ,
            rejected_by UUID,
            rejected_at TIMESTAMPTZ,
            rejection_reason TEXT,
            posted_by UUID,
            posted_at TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'unprocessed' CHECK (status IN (
                'unprocessed', 'approved', 'rejected', 'cancelled', 'posted'
            )),
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cooperative_id, prepared_by) REFERENCES users(cooperative_id, id),
            FOREIGN KEY (cooperative_id, approved_by) REFERENCES users(cooperative_id, id),
            FOREIGN KEY (cooperative_id, rejected_by) REFERENCES users(cooperative_id, id),
            FOREIGN KEY (cooperative_id, posted_by) REFERENCES users(cooperative_id, id)
        )"""
    )
    op.execute(
        """CREATE INDEX dividend_runs_cooperative_id_idx ON dividend_runs(cooperative_id)"""
    )
    op.execute(
        """CREATE INDEX dividend_runs_status_idx ON dividend_runs(status)"""
    )
    op.execute(
        """CREATE INDEX dividend_runs_record_date_idx ON dividend_runs(record_date)"""
    )
    op.execute(
        """ALTER TABLE dividend_runs ENABLE ROW LEVEL SECURITY"""
    )
    op.execute(
        """CREATE POLICY dividend_runs_cooperative_isolation ON dividend_runs
            USING (cooperative_id = app_current_cooperative_id())
            WITH CHECK (cooperative_id = app_current_cooperative_id())"""
    )
    op.execute(
        """CREATE TABLE dividend_lines (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            cooperative_id UUID NOT NULL REFERENCES cooperatives(id) ON DELETE CASCADE,
            run_id UUID NOT NULL REFERENCES dividend_runs(id) ON DELETE CASCADE,
            member_id UUID NOT NULL,
            shares_held BIGINT NOT NULL CHECK (shares_held > 0),
            amount_kobo BIGINT NOT NULL CHECK (amount_kobo > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cooperative_id, member_id) REFERENCES members(cooperative_id, id)
        )"""
    )
    op.execute(
        """CREATE INDEX dividend_lines_run_id_idx ON dividend_lines(run_id)"""
    )
    op.execute(
        """CREATE INDEX dividend_lines_member_id_idx ON dividend_lines(member_id)"""
    )
    op.execute(
        """CREATE INDEX dividend_lines_cooperative_id_idx ON dividend_lines(cooperative_id)"""
    )
    op.execute(
        """ALTER TABLE dividend_lines ENABLE ROW LEVEL SECURITY"""
    )
    op.execute(
        """CREATE POLICY dividend_lines_cooperative_isolation ON dividend_lines
            USING (cooperative_id = app_current_cooperative_id())
            WITH CHECK (cooperative_id = app_current_cooperative_id())"""
    )
    op.execute(
        """GRANT SELECT, INSERT, UPDATE ON dividend_runs TO ajo_app"""
    )
    op.execute(
        """GRANT SELECT, INSERT ON dividend_lines TO ajo_app"""
    )


def downgrade() -> None:
    """Drop dividend_runs and dividend_lines tables."""
    op.execute("""DROP TABLE IF EXISTS dividend_lines""")
    op.execute("""DROP TABLE IF EXISTS dividend_runs""")
