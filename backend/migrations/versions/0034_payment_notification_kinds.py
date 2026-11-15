"""Allow payment action kinds in the shared notification queue.

Revision ID: 0034
Revises: 0033
"""

from alembic import op

revision = "0034"
down_revision = "0033"
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
)


_PREVIOUS_KINDS = (
    "payroll_sign_off",
    "disbursement_authorization",
    "role_change_approval",
    "loan_disputed",
    "withdrawal_approval",
    "payroll_posting",
    "disbursement_record",
    "disbursement_confirmation",
    "loan_decision",
)


def _set_constraint(kinds: tuple[str, ...]) -> None:
    values = ",".join(f"'{kind}'" for kind in kinds)
    op.execute("ALTER TABLE notifications DROP CONSTRAINT notifications_action_fields_check")
    op.execute(
        f"""ALTER TABLE notifications ADD CONSTRAINT notifications_action_fields_check CHECK (
          (recipient_user_id IS NULL AND entity_id IS NULL)
          OR (recipient_user_id IS NOT NULL AND entity_id IS NOT NULL
            AND channel IN ('email','telegram') AND template_version='v1'
            AND kind IN ({values}))
        )"""
    )


def upgrade() -> None:
    _set_constraint(_ACTION_KINDS)


def downgrade() -> None:
    op.execute(
        "DELETE FROM notifications WHERE recipient_user_id IS NOT NULL AND kind IN "
        "('payment_confirmation','cash_deposit')"
    )
    _set_constraint(_PREVIOUS_KINDS)
