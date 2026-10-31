"""Persist maker-checker role-change proposals.

Revision ID: 0020
Revises: 0019
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE role_change_proposals (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
            target_user_id uuid NOT NULL,
            proposer_user_id uuid NOT NULL,
            role text NOT NULL CHECK (
                role IN ('officer', 'committee', 'treasurer', 'admin', 'auditor')
            ),
            action text NOT NULL CHECK (action IN ('add', 'remove')),
            reason text NOT NULL CHECK (char_length(btrim(reason)) BETWEEN 1 AND 500),
            status text NOT NULL DEFAULT 'pending' CHECK (
                status IN ('pending', 'approved', 'rejected', 'cancelled', 'expired')
            ),
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL DEFAULT (now() + interval '7 days'),
            decided_at timestamptz,
            decided_by uuid,
            PRIMARY KEY (cooperative_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX role_change_proposals_pending_idx "
        "ON role_change_proposals (cooperative_id, created_at DESC) "
        "WHERE status = 'pending'"
    )
    op.execute(
        "CREATE INDEX role_change_proposals_target_idx "
        "ON role_change_proposals (cooperative_id, target_user_id, role, action, status)"
    )
    op.execute("ALTER TABLE role_change_proposals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE role_change_proposals FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY role_change_proposals_cooperative_isolation
            ON role_change_proposals
            USING (cooperative_id = app_current_cooperative_id())
            WITH CHECK (cooperative_id = app_current_cooperative_id())
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ajo_app') THEN
            GRANT SELECT, INSERT, UPDATE ON role_change_proposals TO ajo_app;
            REVOKE DELETE, TRUNCATE ON role_change_proposals FROM ajo_app;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE role_change_proposals")
