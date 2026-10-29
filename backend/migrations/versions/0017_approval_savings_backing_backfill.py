"""Backfill savings-backing snapshots for existing approved loan decisions.

Revision ID: 0017
Revises: 0016
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE approvals AS approval
           SET savings_backing_bps = COALESCE(
                   (policy.settings ->> 'savings_backing_bps')::integer, 0
               ),
               savings_backing_amount_kobo = GREATEST(
                   policy.min_savings_kobo,
                   CEIL(
                       application.principal_kobo::numeric
                       * COALESCE((policy.settings ->> 'savings_backing_bps')::integer, 0)
                       / 10000
                   )::bigint
               )
          FROM loan_applications AS application
          JOIN cooperative_policy_versions AS policy
            ON policy.cooperative_id = application.cooperative_id
         WHERE approval.cooperative_id = application.cooperative_id
           AND approval.application_id = application.id
           AND approval.policy_version_id = policy.id
           AND approval.decision = 'approved'
           AND (
               approval.savings_backing_bps IS NULL
               OR approval.savings_backing_amount_kobo IS NULL
           )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE approvals
           SET savings_backing_bps = NULL,
               savings_backing_amount_kobo = NULL
         WHERE decision = 'approved'
        """
    )
