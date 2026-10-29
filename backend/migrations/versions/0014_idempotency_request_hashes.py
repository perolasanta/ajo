"""Bind idempotency keys to canonical payload hashes.

Revision ID: 0014
Revises: 0013
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


_LEDGER_FUNCTION_SIGNATURE = "uuid, date, text, uuid, text, uuid, text, jsonb, jsonb, uuid"


_OLD_POST_FUNCTION = """
CREATE OR REPLACE FUNCTION post_journal_entry(
    p_cooperative_id uuid,
    p_effective_date date,
    p_source_type text,
    p_source_id uuid,
    p_description text,
    p_created_by uuid,
    p_idempotency_key text,
    p_lines jsonb,
    p_reverses_entry_id uuid DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    entry_id_value uuid;
BEGIN
    IF app_current_cooperative_id() IS DISTINCT FROM p_cooperative_id THEN
        RAISE EXCEPTION 'cooperative context does not match journal entry tenant'
            USING ERRCODE = '42501';
    END IF;
    IF jsonb_typeof(p_lines) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'journal lines must be a JSON array'
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO journal_entries (
        cooperative_id, effective_date, source_type, source_id,
        description, created_by, idempotency_key, reverses_entry_id
    ) VALUES (
        p_cooperative_id, p_effective_date, p_source_type, p_source_id,
        p_description, p_created_by, p_idempotency_key, p_reverses_entry_id
    )
    ON CONFLICT (cooperative_id, idempotency_key) DO NOTHING
    RETURNING id INTO entry_id_value;

    IF entry_id_value IS NULL THEN
        SELECT id INTO entry_id_value
          FROM journal_entries
         WHERE cooperative_id = p_cooperative_id
           AND idempotency_key = p_idempotency_key;
        RETURN entry_id_value;
    END IF;

    INSERT INTO journal_lines (
        cooperative_id, entry_id, line_number, account_id,
        member_id, loan_id, debit_kobo, credit_kobo
    )
    SELECT p_cooperative_id,
           entry_id_value,
           line.ordinality::integer,
           (line.value ->> 'account_id')::uuid,
           nullif(line.value ->> 'member_id', '')::uuid,
           nullif(line.value ->> 'loan_id', '')::uuid,
           coalesce((line.value ->> 'debit_kobo')::bigint, 0),
           coalesce((line.value ->> 'credit_kobo')::bigint, 0)
      FROM jsonb_array_elements(p_lines) WITH ORDINALITY AS line(value, ordinality);

    RETURN entry_id_value;
END
$$
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("""CREATE FUNCTION canonical_request_hash(p_payload jsonb) RETURNS bytea
      LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE SET search_path=pg_catalog,public
      AS $$ SELECT digest(convert_to(p_payload::text,'UTF8'),'sha256') $$""")
    op.execute("ALTER TABLE journal_entries ADD COLUMN request_hash bytea")
    op.execute("ALTER TABLE repayments ADD COLUMN request_hash bytea")
    op.execute("ALTER TABLE withdrawal_requests ADD COLUMN request_hash bytea")
    op.execute("DROP TRIGGER journal_entries_no_update_delete ON journal_entries")

    op.execute("""UPDATE repayments SET request_hash=canonical_request_hash(jsonb_build_object(
      'operation','loan_repayment','loan_id',loan_id,'member_id',member_id,
      'amount_kobo',amount_kobo,'source',source,
      'received_on',received_on,'reference',reference,'reason',reason))""")
    op.execute("""UPDATE withdrawal_requests SET request_hash=canonical_request_hash(jsonb_build_object(
      'operation','withdrawal_request','member_id',member_id,'amount_kobo',amount_kobo))""")
    op.execute("""UPDATE journal_entries e SET request_hash=canonical_request_hash(jsonb_build_object(
      'operation','member_contribution',
      'kind',(SELECT CASE a.code WHEN 'member_savings' THEN 'savings'
                    WHEN 'member_shares' THEN 'shares' WHEN 'dues_income' THEN 'dues' END
                FROM journal_lines l JOIN accounts a
                  ON a.cooperative_id=l.cooperative_id AND a.id=l.account_id
                WHERE l.cooperative_id=e.cooperative_id AND l.entry_id=e.id AND l.credit_kobo>0
                ORDER BY l.line_number LIMIT 1),
      'member_id',e.source_id,
      'amount_kobo',(SELECT coalesce(sum(l.debit_kobo),0)::bigint FROM journal_lines l
                WHERE l.cooperative_id=e.cooperative_id AND l.entry_id=e.id)))
      WHERE e.source_type='member_contribution'""")
    op.execute("""UPDATE journal_entries e SET request_hash=r.request_hash
      FROM repayments r WHERE r.cooperative_id=e.cooperative_id AND r.journal_entry_id=e.id""")
    op.execute("""UPDATE journal_entries e SET request_hash=canonical_request_hash(jsonb_build_object(
      'effective_date',e.effective_date,'source_type',e.source_type,'source_id',e.source_id,
      'description',e.description,'lines',(SELECT coalesce(jsonb_agg(jsonb_build_object(
        'account_id',l.account_id,'member_id',l.member_id,'loan_id',l.loan_id,
        'debit_kobo',l.debit_kobo,'credit_kobo',l.credit_kobo) ORDER BY l.line_number),'[]'::jsonb)
        FROM journal_lines l WHERE l.cooperative_id=e.cooperative_id AND l.entry_id=e.id),
      'reverses_entry_id',e.reverses_entry_id)) WHERE e.request_hash IS NULL""")
    op.execute("""CREATE TRIGGER journal_entries_no_update_delete
      BEFORE UPDATE OR DELETE ON journal_entries FOR EACH STATEMENT
      EXECUTE FUNCTION reject_ledger_mutation()""")

    op.execute("ALTER TABLE journal_entries ALTER COLUMN request_hash SET NOT NULL")
    op.execute("ALTER TABLE repayments ALTER COLUMN request_hash SET NOT NULL")
    op.execute("ALTER TABLE withdrawal_requests ALTER COLUMN request_hash SET NOT NULL")
    op.execute(
        "ALTER TABLE journal_entries ADD CONSTRAINT journal_entries_request_hash_length CHECK (octet_length(request_hash)=32)"
    )
    op.execute(
        "ALTER TABLE repayments ADD CONSTRAINT repayments_request_hash_length CHECK (octet_length(request_hash)=32)"
    )
    op.execute(
        "ALTER TABLE withdrawal_requests ADD CONSTRAINT withdrawal_request_hash_length CHECK (octet_length(request_hash)=32)"
    )

    op.execute("""CREATE FUNCTION post_journal_entry_with_payload(
      p_cooperative_id uuid,p_effective_date date,p_source_type text,p_source_id uuid,
      p_description text,p_created_by uuid,p_idempotency_key text,p_lines jsonb,
      p_request_payload jsonb,p_reverses_entry_id uuid DEFAULT NULL
    ) RETURNS uuid LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
    DECLARE entry_id_value uuid; stored_hash bytea; payload_hash bytea;
    BEGIN
      IF app_current_cooperative_id() IS DISTINCT FROM p_cooperative_id THEN
        RAISE EXCEPTION 'cooperative context does not match journal entry tenant' USING ERRCODE='42501';
      END IF;
      IF jsonb_typeof(p_lines) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'journal lines must be a JSON array' USING ERRCODE='22023';
      END IF;
      IF jsonb_typeof(p_request_payload) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'idempotency request payload must be a JSON object' USING ERRCODE='22023';
      END IF;
      payload_hash := canonical_request_hash(p_request_payload);
      INSERT INTO journal_entries(cooperative_id,effective_date,source_type,source_id,description,
        created_by,idempotency_key,request_hash,reverses_entry_id)
      VALUES(p_cooperative_id,p_effective_date,p_source_type,p_source_id,p_description,
        p_created_by,p_idempotency_key,payload_hash,p_reverses_entry_id)
      ON CONFLICT(cooperative_id,idempotency_key) DO NOTHING RETURNING id INTO entry_id_value;

      IF entry_id_value IS NULL THEN
        SELECT id,request_hash INTO entry_id_value,stored_hash FROM journal_entries
          WHERE cooperative_id=p_cooperative_id AND idempotency_key=p_idempotency_key;
        IF stored_hash IS DISTINCT FROM payload_hash THEN
          RAISE EXCEPTION 'idempotency key reused with different payload' USING ERRCODE='23505';
        END IF;
        RETURN entry_id_value;
      END IF;

      INSERT INTO journal_lines(cooperative_id,entry_id,line_number,account_id,member_id,loan_id,
        debit_kobo,credit_kobo)
      SELECT p_cooperative_id,entry_id_value,line.ordinality::integer,
        (line.value->>'account_id')::uuid,nullif(line.value->>'member_id','')::uuid,
        nullif(line.value->>'loan_id','')::uuid,
        coalesce((line.value->>'debit_kobo')::bigint,0),
        coalesce((line.value->>'credit_kobo')::bigint,0)
      FROM jsonb_array_elements(p_lines) WITH ORDINALITY AS line(value,ordinality);
      RETURN entry_id_value;
    END $$""")

    op.execute("""CREATE OR REPLACE FUNCTION post_journal_entry(
      p_cooperative_id uuid,p_effective_date date,p_source_type text,p_source_id uuid,
      p_description text,p_created_by uuid,p_idempotency_key text,p_lines jsonb,
      p_reverses_entry_id uuid DEFAULT NULL
    ) RETURNS uuid LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
    DECLARE request_payload jsonb;
    BEGIN
      IF jsonb_typeof(p_lines) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'journal lines must be a JSON array' USING ERRCODE='22023';
      END IF;
      request_payload := jsonb_build_object(
        'effective_date',p_effective_date,'source_type',p_source_type,'source_id',p_source_id,
        'description',p_description,'lines',(SELECT coalesce(jsonb_agg(jsonb_build_object(
          'account_id',(line.value->>'account_id')::uuid,
          'member_id',nullif(line.value->>'member_id','')::uuid,
          'loan_id',nullif(line.value->>'loan_id','')::uuid,
          'debit_kobo',coalesce((line.value->>'debit_kobo')::bigint,0),
          'credit_kobo',coalesce((line.value->>'credit_kobo')::bigint,0)
        ) ORDER BY line.ordinality),'[]'::jsonb)
          FROM jsonb_array_elements(p_lines) WITH ORDINALITY AS line(value,ordinality)),
        'reverses_entry_id',p_reverses_entry_id);
      RETURN post_journal_entry_with_payload(p_cooperative_id,p_effective_date,p_source_type,
        p_source_id,p_description,p_created_by,p_idempotency_key,p_lines,request_payload,
        p_reverses_entry_id);
    END $$""")

    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ajo_app') THEN
      GRANT EXECUTE ON FUNCTION canonical_request_hash(jsonb) TO ajo_app;
      GRANT EXECUTE ON FUNCTION post_journal_entry_with_payload(uuid,date,text,uuid,text,uuid,text,jsonb,jsonb,uuid) TO ajo_app;
    END IF; END $$""")


def downgrade() -> None:
    op.execute(_OLD_POST_FUNCTION)
    op.execute(f"DROP FUNCTION post_journal_entry_with_payload({_LEDGER_FUNCTION_SIGNATURE})")
    op.execute("DROP FUNCTION canonical_request_hash(jsonb)")
    op.execute("ALTER TABLE withdrawal_requests DROP CONSTRAINT withdrawal_request_hash_length")
    op.execute("ALTER TABLE repayments DROP CONSTRAINT repayments_request_hash_length")
    op.execute("ALTER TABLE journal_entries DROP CONSTRAINT journal_entries_request_hash_length")
    op.execute("ALTER TABLE withdrawal_requests DROP COLUMN request_hash")
    op.execute("ALTER TABLE repayments DROP COLUMN request_hash")
    op.execute("ALTER TABLE journal_entries DROP COLUMN request_hash")
