"""Support provider-neutral staff action notifications in the existing outbox.

Revision ID: 0022
Revises: 0021
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE notifications ALTER COLUMN member_id DROP NOT NULL")
    op.execute("ALTER TABLE notifications ALTER COLUMN channel DROP NOT NULL")
    op.execute("ALTER TABLE notifications ALTER COLUMN template_version DROP NOT NULL")
    op.execute(
        """ALTER TABLE notifications
          ADD COLUMN recipient_user_id uuid,
          ADD COLUMN entity_id uuid,
          ADD CONSTRAINT notifications_recipient_check CHECK (
            (member_id IS NULL) <> (recipient_user_id IS NULL)
          ),
          ADD CONSTRAINT notifications_action_fields_check CHECK (
            (recipient_user_id IS NULL AND entity_id IS NULL)
            OR (recipient_user_id IS NOT NULL AND entity_id IS NOT NULL
              AND channel IS NULL AND template_version IS NULL
              AND kind IN ('payroll_sign_off','disbursement_authorization',
                'role_change_approval','loan_disputed'))
          ),
          ADD CONSTRAINT notifications_recipient_user_fk
            FOREIGN KEY (cooperative_id,recipient_user_id)
            REFERENCES users(cooperative_id,id)"""
    )
    op.execute(
        """CREATE UNIQUE INDEX notifications_action_dedupe_idx
          ON notifications(cooperative_id,recipient_user_id,kind,entity_id)
          WHERE recipient_user_id IS NOT NULL"""
    )


def downgrade() -> None:
    op.execute("DELETE FROM notifications WHERE recipient_user_id IS NOT NULL")
    op.execute("DROP INDEX notifications_action_dedupe_idx")
    op.execute(
        """ALTER TABLE notifications
          DROP CONSTRAINT notifications_recipient_user_fk,
          DROP CONSTRAINT notifications_action_fields_check,
          DROP CONSTRAINT notifications_recipient_check,
          DROP COLUMN entity_id,
          DROP COLUMN recipient_user_id"""
    )
    op.execute("ALTER TABLE notifications ALTER COLUMN template_version SET NOT NULL")
    op.execute("ALTER TABLE notifications ALTER COLUMN channel SET NOT NULL")
    op.execute("ALTER TABLE notifications ALTER COLUMN member_id SET NOT NULL")
