"""Route staff action notifications through the email delivery channel.

Revision ID: 0023
Revises: 0022
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE notifications DROP CONSTRAINT notifications_action_fields_check")
    op.execute(
        """UPDATE notifications SET channel='email',template_version='v1'
          WHERE recipient_user_id IS NOT NULL AND channel IS NULL"""
    )
    op.execute(
        """ALTER TABLE notifications ADD CONSTRAINT notifications_action_fields_check CHECK (
          (recipient_user_id IS NULL AND entity_id IS NULL)
          OR (recipient_user_id IS NOT NULL AND entity_id IS NOT NULL
            AND channel='email' AND template_version='v1'
            AND kind IN ('payroll_sign_off','disbursement_authorization',
              'role_change_approval','loan_disputed'))
        )"""
    )


def downgrade() -> None:
    op.execute("ALTER TABLE notifications DROP CONSTRAINT notifications_action_fields_check")
    op.execute(
        """UPDATE notifications SET channel=NULL,template_version=NULL
          WHERE recipient_user_id IS NOT NULL"""
    )
    op.execute(
        """ALTER TABLE notifications ADD CONSTRAINT notifications_action_fields_check CHECK (
          (recipient_user_id IS NULL AND entity_id IS NULL)
          OR (recipient_user_id IS NOT NULL AND entity_id IS NOT NULL
            AND channel IS NULL AND template_version IS NULL
            AND kind IN ('payroll_sign_off','disbursement_authorization',
              'role_change_approval','loan_disputed'))
        )"""
    )
