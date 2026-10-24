"""Member confirmations, disputes and encrypted notification outbox.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant ON {table} USING "
        "(cooperative_id = app_current_cooperative_id()) WITH CHECK "
        "(cooperative_id = app_current_cooperative_id())"
    )


def upgrade() -> None:
    op.execute("ALTER TABLE loans ADD COLUMN deductions_paused boolean NOT NULL DEFAULT false")
    op.execute("ALTER TABLE fraud_alerts DROP CONSTRAINT fraud_alerts_rule_code_check")
    op.execute("ALTER TABLE fraud_alerts ADD CONSTRAINT fraud_alerts_rule_code_check CHECK (rule_code IN ('parallel_loan','third_party_beneficiary','destination_changed','same_user_authorization','consent_dispute'))")
    op.execute("""CREATE TABLE member_consents (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
      application_id uuid NOT NULL, member_id uuid NOT NULL,
      kind text NOT NULL CHECK (kind IN ('applicant_ack','final_terms','beneficiary')),
      terms_hash bytea CHECK (terms_hash IS NULL OR octet_length(terms_hash)=32),
      token_hash bytea NOT NULL CHECK (octet_length(token_hash)=32),
      otp_hash bytea, otp_attempts integer NOT NULL DEFAULT 0 CHECK (otp_attempts BETWEEN 0 AND 5),
      status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','rejected','expired','invalidated')),
      expires_at timestamptz NOT NULL, otp_expires_at timestamptz, decided_at timestamptz,
      channel text, ip inet, user_agent text, consent_text_version text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (cooperative_id,application_id) REFERENCES loan_applications(cooperative_id,id),
      FOREIGN KEY (cooperative_id,member_id) REFERENCES members(cooperative_id,id),
      UNIQUE (cooperative_id,token_hash)
    )""")
    op.execute("""CREATE TABLE consent_rate_limits (
      cooperative_id uuid NOT NULL REFERENCES cooperatives(id), dimension text NOT NULL,
      key_hash bytea NOT NULL, window_started_at timestamptz NOT NULL, attempts integer NOT NULL,
      PRIMARY KEY (cooperative_id,dimension,key_hash,window_started_at)
    )""")
    op.execute("""CREATE TABLE disputes (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
      application_id uuid, loan_id uuid, member_id uuid NOT NULL, opened_via text NOT NULL,
      reason text NOT NULL, status text NOT NULL DEFAULT 'open' CHECK (status IN
        ('open','under_investigation','resolved_invalid','resolved_valid')),
      resolved_by uuid, resolution_note text, created_at timestamptz NOT NULL DEFAULT now(),
      CHECK ((application_id IS NOT NULL)::int + (loan_id IS NOT NULL)::int = 1),
      FOREIGN KEY (cooperative_id,application_id) REFERENCES loan_applications(cooperative_id,id),
      FOREIGN KEY (cooperative_id,loan_id) REFERENCES loans(cooperative_id,id),
      FOREIGN KEY (cooperative_id,member_id) REFERENCES members(cooperative_id,id),
      FOREIGN KEY (cooperative_id,resolved_by) REFERENCES users(cooperative_id,id)
    )""")
    op.execute("""CREATE TABLE notifications (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), cooperative_id uuid NOT NULL REFERENCES cooperatives(id),
      member_id uuid NOT NULL, kind text NOT NULL, payload_encrypted text,
      status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','expired','failed')),
      attempts integer NOT NULL DEFAULT 0, next_attempt_at timestamptz NOT NULL DEFAULT now(), sent_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (cooperative_id,member_id) REFERENCES members(cooperative_id,id)
    )""")
    for table in ("member_consents", "consent_rate_limits", "disputes", "notifications"):
        _rls(table)
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_app') THEN
      GRANT SELECT,INSERT,UPDATE ON member_consents,consent_rate_limits,disputes,notifications TO ajo_app;
      REVOKE DELETE,TRUNCATE ON member_consents,consent_rate_limits,disputes,notifications FROM ajo_app;
    END IF; END $$""")


def downgrade() -> None:
    for table in ("notifications", "disputes", "consent_rate_limits", "member_consents"):
        op.execute(f"DROP TABLE {table}")
    op.execute("ALTER TABLE loans DROP COLUMN deductions_paused")
    op.execute("ALTER TABLE fraud_alerts DROP CONSTRAINT fraud_alerts_rule_code_check")
    op.execute("ALTER TABLE fraud_alerts ADD CONSTRAINT fraud_alerts_rule_code_check CHECK (rule_code IN ('parallel_loan','third_party_beneficiary','destination_changed','same_user_authorization'))")
