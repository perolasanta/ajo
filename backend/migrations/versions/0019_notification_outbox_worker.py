"""Add claimable notification delivery and versioned SMS templates.

Revision ID: 0019
Revises: 0018
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE notifications DROP CONSTRAINT notifications_status_check")
    op.execute(
        """ALTER TABLE notifications ADD CONSTRAINT notifications_status_check
          CHECK (status IN ('pending','processing','sent','expired','failed'))"""
    )
    op.execute(
        """ALTER TABLE notifications
          ADD COLUMN channel text NOT NULL DEFAULT 'sms'
            CHECK (channel IN ('sms','email','whatsapp','telegram')),
          ADD COLUMN template_version text NOT NULL DEFAULT 'v1',
          ADD COLUMN claimed_at timestamptz,
          ADD COLUMN claim_token uuid,
          ADD COLUMN last_error_code text,
          ADD CONSTRAINT notifications_claim_fields_check CHECK (
            (status='processing' AND claimed_at IS NOT NULL AND claim_token IS NOT NULL)
            OR (status<>'processing' AND claimed_at IS NULL AND claim_token IS NULL)
          )"""
    )
    op.execute(
        "CREATE INDEX notifications_pending_due_idx ON notifications(cooperative_id,next_attempt_at,created_at) WHERE status='pending'"
    )
    op.execute(
        "CREATE INDEX notifications_processing_lease_idx ON notifications(cooperative_id,claimed_at) WHERE status='processing'"
    )
    op.execute(
        """CREATE TABLE notification_templates (
          kind text NOT NULL,
          channel text NOT NULL CHECK (channel IN ('sms','email','whatsapp','telegram')),
          version text NOT NULL,
          body text NOT NULL CHECK (length(btrim(body)) BETWEEN 1 AND 2000),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY(kind,channel,version)
        )"""
    )
    op.execute(
        """INSERT INTO notification_templates(kind,channel,version,body) VALUES
          ('confirmation_request','sms','v1',
            'Ajo: Please review your cooperative loan confirmation: {link}'),
          ('confirmation_otp','sms','v1',
            'Ajo verification code: {code}. It expires in {minutes} minutes. Do not share it.')"""
    )
    op.execute(
        """DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_app') THEN
            GRANT SELECT ON notification_templates TO ajo_app;
          END IF;
        END $$"""
    )


def downgrade() -> None:
    op.execute(
        """UPDATE notifications SET status='pending',claimed_at=NULL,claim_token=NULL
          WHERE status='processing'"""
    )
    op.execute("DROP TABLE notification_templates")
    op.execute("DROP INDEX notifications_processing_lease_idx")
    op.execute("DROP INDEX notifications_pending_due_idx")
    op.execute("ALTER TABLE notifications DROP CONSTRAINT notifications_claim_fields_check")
    op.execute("ALTER TABLE notifications DROP CONSTRAINT notifications_status_check")
    op.execute(
        """ALTER TABLE notifications ADD CONSTRAINT notifications_status_check
          CHECK (status IN ('pending','sent','expired','failed'))"""
    )
    op.execute(
        """ALTER TABLE notifications
          DROP COLUMN last_error_code,
          DROP COLUMN claim_token,
          DROP COLUMN claimed_at,
          DROP COLUMN template_version,
          DROP COLUMN channel"""
    )
