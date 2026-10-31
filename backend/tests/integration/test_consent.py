"""Member consent workflow integration tests against local PostgreSQL."""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import asyncpg
import pytest

from app.core.db import asyncpg_dsn, dispose_engine
from app.core.errors import Conflict
from app.domains.consent.service import decide as decide_consent
from app.domains.consent.service import send_otp
from app.domains.disbursements.service import prepare
from app.domains.loans.service import decide_application, submit_application
from tests.integration.test_loans import LoanBase, _create_loan_base, session, setup_loan_member

pytestmark = pytest.mark.db


class FakeOtpSender:
    """In-memory OTP sink for consent tests; never sends a real message."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, destination: str, code: str) -> None:
        self.sent.append((destination, code))


@pytest.fixture(scope="module")
def loan_base(test_database) -> LoanBase:
    return asyncio.run(_create_loan_base(test_database, fee_bps=100))


@pytest.fixture(autouse=True)
async def reset_engine():
    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture
async def db(test_database) -> asyncpg.Connection:
    connection = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    assert await connection.fetchval("SELECT current_user") == "ajo_app"
    yield connection
    await connection.close()


async def approved_loan(
    db: asyncpg.Connection,
    base: LoanBase,
    test_database,
    *,
    same_borrower_and_beneficiary: bool = False,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, list[str]]:
    cid = base.cooperative_id
    borrower = await setup_loan_member(db, base)
    beneficiary = borrower if same_borrower_and_beneficiary else await setup_loan_member(db, base)
    owner = await asyncpg.connect(asyncpg_dsn(test_database.owner_url))
    try:
        await owner.execute("SELECT set_config('app.cooperative_id',$1,false)", str(cid))
        slug = await owner.fetchval("SELECT slug FROM cooperatives WHERE id=$1", cid)
        await owner.execute(
            """INSERT INTO tenant_directory(slug,cooperative_id)
              VALUES($1,$2) ON CONFLICT (slug) DO NOTHING""",
            slug,
            cid,
        )
        await owner.execute(
            "UPDATE members SET phone_e164=$3 WHERE cooperative_id=$1 AND id=$2",
            cid,
            borrower,
            "+2348030000001",
        )
        await owner.execute(
            "UPDATE members SET phone_e164=$3 WHERE cooperative_id=$1 AND id=$2",
            cid,
            beneficiary,
            "+2348030000002" if beneficiary != borrower else "+2348030000001",
        )
    finally:
        await owner.close()
    bank_account = uuid.uuid4()
    await db.execute(
        """INSERT INTO bank_accounts(cooperative_id,id,member_id,bank_code,
          account_number_encrypted,last4,account_name,status,verified_at)
          VALUES($1,$2,$3,'001','ciphertext','4321','Member','verified',now())""",
        cid,
        bank_account,
        borrower,
    )
    application = await submit_application(
        session(cid, base.user_ids[0], "officer"),
        member_id=borrower,
        beneficiary_member_id=beneficiary,
        product_id=base.product_id,
        principal_kobo=500_000,
        tenor_months=6,
        destination_bank_account_id=bank_account,
    )
    await db.execute(
        "UPDATE loan_applications SET status='under_review' WHERE cooperative_id=$1 AND id=$2",
        cid,
        application,
    )
    tokens: list[str] = []
    await decide_application(
        session(cid, base.user_ids[1], "committee"),
        application,
        "approved",
        "review",
        consent_delivery_observer=tokens.append,
    )
    loan_id = await db.fetchval(
        "SELECT id FROM loans WHERE cooperative_id=$1 AND application_id=$2", cid, application
    )
    assert loan_id is not None
    return application, loan_id, borrower, beneficiary, tokens


async def consent_rows_for_tokens(
    db: asyncpg.Connection, cid: uuid.UUID, application_id: uuid.UUID, tokens: list[str]
) -> dict[str, asyncpg.Record]:
    rows: dict[str, asyncpg.Record] = {}
    for token in tokens:
        token_hash = hashlib.sha256(bytes.fromhex(token)).digest()
        row = await db.fetchrow(
            """SELECT id,kind,member_id,status FROM member_consents
              WHERE cooperative_id=$1 AND application_id=$2 AND token_hash=$3""",
            cid,
            application_id,
            token_hash,
        )
        assert row is not None
        rows[token] = row
    return rows


async def confirm_token(
    db: asyncpg.Connection,
    cid: uuid.UUID,
    slug: str,
    token: str,
    sender: FakeOtpSender,
) -> str:
    await send_otp(slug, token, "127.0.0.1", sender)
    destination, code = sender.sent[-1]
    row = await db.fetchrow(
        """SELECT m.phone_e164 FROM member_consents c
          JOIN members m ON m.cooperative_id=c.cooperative_id AND m.id=c.member_id
          WHERE c.cooperative_id=$1 AND c.token_hash=$2""",
        cid,
        hashlib.sha256(bytes.fromhex(token)).digest(),
    )
    assert row is not None
    assert destination == row["phone_e164"]
    return await decide_consent(slug, token, code, "confirm", "127.0.0.1", "test-agent")


async def test_third_party_requires_both_member_confirmations_and_uses_each_phone(
    db: asyncpg.Connection, loan_base: LoanBase, test_database
) -> None:
    application, loan_id, borrower, beneficiary, tokens = await approved_loan(
        db, loan_base, test_database
    )
    cid = loan_base.cooperative_id
    assert len(tokens) == 2
    assert len(set(tokens)) == 2

    rows = await consent_rows_for_tokens(db, cid, application, tokens)
    assert {(row["kind"], row["member_id"]) for row in rows.values()} == {
        ("final_terms", borrower),
        ("beneficiary", beneficiary),
    }
    slug = await db.fetchval("SELECT slug FROM cooperatives WHERE id=$1", cid)
    sender = FakeOtpSender()
    for token in tokens:
        assert await confirm_token(db, cid, slug, token, sender) == "recorded"

    assert {destination for destination, _ in sender.sent} == {
        "+2348030000001",
        "+2348030000002",
    }
    assert (
        await db.fetchval(
            """SELECT count(*) FROM member_consents WHERE cooperative_id=$1 AND application_id=$2
          AND kind IN ('final_terms','beneficiary') AND status='confirmed'""",
            cid,
            application,
        )
        == 2
    )
    identifier = await prepare(session(cid, loan_base.user_ids[0], "treasurer"), loan_id, None)
    assert identifier is not None


@pytest.mark.parametrize(
    ("confirmed_kind", "missing_kind"),
    [("beneficiary", "final_terms"), ("final_terms", "beneficiary")],
)
async def test_third_party_disbursement_is_blocked_when_either_confirmation_is_missing(
    db: asyncpg.Connection,
    loan_base: LoanBase,
    test_database,
    confirmed_kind: str,
    missing_kind: str,
) -> None:
    application, loan_id, _, _, _ = await approved_loan(db, loan_base, test_database)
    cid = loan_base.cooperative_id
    await db.execute(
        """UPDATE member_consents SET status='confirmed',decided_at=now()
          WHERE cooperative_id=$1 AND application_id=$2 AND kind=$3""",
        cid,
        application,
        confirmed_kind,
    )
    await db.execute(
        "UPDATE loan_applications SET status='ready_for_disbursement' WHERE cooperative_id=$1 AND id=$2",
        cid,
        application,
    )
    await db.execute(
        "UPDATE loans SET status='ready_for_disbursement' WHERE cooperative_id=$1 AND id=$2",
        cid,
        loan_id,
    )
    with pytest.raises(Conflict):
        await prepare(session(cid, loan_base.user_ids[0], "treasurer"), loan_id, None)

    assert (
        await db.fetchval(
            """SELECT status FROM member_consents WHERE cooperative_id=$1 AND application_id=$2
          AND kind=$3""",
            cid,
            application,
            missing_kind,
        )
        == "pending"
    )


async def test_same_borrower_and_beneficiary_uses_one_confirmation(
    db: asyncpg.Connection, loan_base: LoanBase, test_database
) -> None:
    application, loan_id, borrower, beneficiary, tokens = await approved_loan(
        db, loan_base, test_database, same_borrower_and_beneficiary=True
    )
    assert borrower == beneficiary
    assert len(tokens) == 1
    rows = await consent_rows_for_tokens(db, loan_base.cooperative_id, application, tokens)
    row = next(iter(rows.values()))
    assert row["kind"] == "final_terms"
    assert row["member_id"] == borrower

    slug = await db.fetchval("SELECT slug FROM cooperatives WHERE id=$1", loan_base.cooperative_id)
    sender = FakeOtpSender()
    assert await confirm_token(db, loan_base.cooperative_id, slug, tokens[0], sender) == "recorded"
    identifier = await prepare(
        session(loan_base.cooperative_id, loan_base.user_ids[0], "treasurer"), loan_id, None
    )
    assert identifier is not None
