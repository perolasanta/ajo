"""Allow action notifications through Telegram and dedupe by channel.

Revision ID: 0025
Revises: 0024
"""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    op.execute("DROP INDEX notifications_action_dedupe_idx")
    op.execute(
        """CREATE UNIQUE INDEX notifications_action_dedupe_idx
          ON notifications(cooperative_id,recipient_user_id,channel,kind,entity_id)
          WHERE recipient_user_id IS NOT NULL"""
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM notifications WHERE recipient_user_id IS NOT NULL AND channel='telegram'"
    )
    op.execute("DROP INDEX notifications_action_dedupe_idx")
    op.execute(
        """CREATE UNIQUE INDEX notifications_action_dedupe_idx
          ON notifications(cooperative_id,recipient_user_id,kind,entity_id)
          WHERE recipient_user_id IS NOT NULL"""
    )
    op.execute("ALTER TABLE notifications DROP CONSTRAINT notifications_action_fields_check")
    op.execute(
        """ALTER TABLE notifications ADD CONSTRAINT notifications_action_fields_check CHECK (
          (recipient_user_id IS NULL AND entity_id IS NULL)
          OR (recipient_user_id IS NOT NULL AND entity_id IS NOT NULL
            AND channel='email' AND template_version='v1'
            AND kind IN ('payroll_sign_off','disbursement_authorization',
              'role_change_approval','loan_disputed'))
        )"""
    )
