"""Allow settings approval action notifications.

Revision ID: 0035
Revises: 0034
"""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

_ACTION_KINDS = (
    "payroll_sign_off",
    "disbursement_authorization",
    "role_change_approval",
    "loan_disputed",
    "withdrawal_approval",
    "payroll_posting",
    "disbursement_record",
    "disbursement_confirmation",
    "loan_decision",
    "payment_confirmation",
    "cash_deposit",
    "policy_approval",
    "product_approval",
)


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS notifications_action_dedupe_idx")
    op.execute(
        """CREATE UNIQUE INDEX notifications_action_dedupe_idx
          ON notifications(cooperative_id, recipient_user_id, channel, kind, entity_id)
          WHERE recipient_user_id IS NOT NULL"""
    )
    values = ",".join(f"'{kind}'" for kind in _ACTION_KINDS)
    op.execute("ALTER TABLE notifications DROP CONSTRAINT notifications_action_fields_check")
    op.execute(
        f"""ALTER TABLE notifications ADD CONSTRAINT notifications_action_fields_check CHECK (
          (recipient_user_id IS NULL AND entity_id IS NULL)
          OR (recipient_user_id IS NOT NULL AND entity_id IS NOT NULL
            AND channel IN ('email','telegram') AND template_version='v1'
            AND kind IN ({values}))
        )"""
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM notifications WHERE recipient_user_id IS NOT NULL AND kind IN "
        "('policy_approval','product_approval')"
    )
    values = ",".join(f"'{kind}'" for kind in _ACTION_KINDS[:-2])
    op.execute("ALTER TABLE notifications DROP CONSTRAINT notifications_action_fields_check")
    op.execute(
        f"""ALTER TABLE notifications ADD CONSTRAINT notifications_action_fields_check CHECK (
          (recipient_user_id IS NULL AND entity_id IS NULL)
          OR (recipient_user_id IS NOT NULL AND entity_id IS NOT NULL
            AND channel IN ('email','telegram') AND template_version='v1'
            AND kind IN ({values}))
        )"""
    )
