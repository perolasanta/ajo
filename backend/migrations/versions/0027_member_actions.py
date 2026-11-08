"""Persist member contribution metadata and withdrawal rejection reasons.

Revision ID: 0027
Revises: 0026
"""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE withdrawal_requests ADD COLUMN reason text NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE withdrawal_requests ADD COLUMN rejection_reason text")
    op.execute("ALTER TABLE notifications DROP CONSTRAINT notifications_action_fields_check")
    op.execute(
        """ALTER TABLE notifications ADD CONSTRAINT notifications_action_fields_check CHECK (
          (recipient_user_id IS NULL AND entity_id IS NULL)
          OR (recipient_user_id IS NOT NULL AND entity_id IS NOT NULL
            AND channel IN ('email','telegram') AND template_version='v1'
            AND kind IN ('payroll_sign_off','disbursement_authorization',
              'role_change_approval','loan_disputed','withdrawal_approval'))
        )"""
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM notifications WHERE recipient_user_id IS NOT NULL AND kind='withdrawal_approval'"
    )
    op.execute("ALTER TABLE notifications DROP CONSTRAINT notifications_action_fields_check")
    op.execute(
        """ALTER TABLE notifications ADD CONSTRAINT notifications_action_fields_check CHECK (
          (recipient_user_id IS NULL AND entity_id IS NULL)
          OR (recipient_user_id IS NOT NULL AND entity_id IS NOT NULL
            AND channel IN ('email','telegram') AND template_version='v1'
            AND kind IN ('payroll_sign_off','disbursement_authorization',
              'role_change_approval','loan_disputed'))
        )"""
    )
    op.execute("ALTER TABLE withdrawal_requests DROP COLUMN rejection_reason")
    op.execute("ALTER TABLE withdrawal_requests DROP COLUMN reason")
