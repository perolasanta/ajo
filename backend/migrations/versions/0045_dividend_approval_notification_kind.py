"""Allow dividend approval action notifications.

Revision ID: 0045
Revises: 0044
"""

from alembic import op

revision = "0045"
down_revision = "0044"
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
    "share_purchase_needs_approval",
    "cash_deposit_overdue",
    "dividend_approval",
)

_PREVIOUS_KINDS = _ACTION_KINDS[:-1]


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
        "DELETE FROM notifications WHERE recipient_user_id IS NOT NULL "
        "AND kind='dividend_approval'"
    )
    _set_constraint(_PREVIOUS_KINDS)
