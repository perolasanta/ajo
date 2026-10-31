"""Member consent workflow integration tests against local PostgreSQL."""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import asyncpg
import pytest
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.db import asyncpg_dsn, dispose_engine, transaction
from app.core.errors import AppError, Conflict, Forbidden, NotFound
from app.domains.consent import service as consent_service
from app.domains.consent.service import decide as decide_consent
from app.domains.consent.service import send_otp as enqueue_otp
from app.domains.disbursements.service import prepare
from app.domains.loans import service as loan_service
from app.domains.loans.service import decide_application, submit_application
from app.domains.notifications.outbox import decrypt_payload
from app.worker.outbox import process_next_notification
from tests.integration.test_loans import LoanBase, _create_loan_base, session, setup_loan_member

pytestmark = pytest.mark.db


class FakeOtpSender:
    """In-memory OTP sink for consent tests; never sends a real message."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, destination: str, code: str) -> None:
        self.sent.append((destination, code))


async def send_otp(slug: str, token: str, ip: str, sender: FakeOtpSender) -> None:
    await enqueue_otp(
        slug,
        token,
        ip,
        delivery_observer=lambda destination, code: sender.sent.append((destination, code)),
    )


class FakeSmsGateway:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, destination: str, message: str) -> None:
        self.sent.append((destination, message))


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
    phone_pair: tuple[str, str] | None = None,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, list[str]]:
    cid = base.cooperative_id
    borrower = await setup_loan_member(db, base)
    beneficiary = borrower if same_borrower_and_beneficiary else await setup_loan_member(db, base)
    borrower_phone, beneficiary_phone = phone_pair or (
        "+2348030000001",
        "+2348030000002",
    )
    if beneficiary == borrower:
        beneficiary_phone = borrower_phone
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
            borrower_phone,
        )
        await owner.execute(
            "UPDATE members SET phone_e164=$3 WHERE cooperative_id=$1 AND id=$2",
            cid,
            beneficiary,
            beneficiary_phone,
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
    details = await consent_service.verify_otp(slug, token, code, "127.0.0.1")
    assert details is not None
    return await decide_consent(slug, token, "confirm", "127.0.0.1", "test-agent")


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


async def final_terms_token(
    db: asyncpg.Connection,
    cid: uuid.UUID,
    application_id: uuid.UUID,
    tokens: list[str],
) -> str:
    rows = await consent_rows_for_tokens(db, cid, application_id, tokens)
    return next(token for token, row in rows.items() if row["kind"] == "final_terms")


async def test_otp_send_limit_is_three_per_consent_per_hour(
    db: asyncpg.Connection, loan_base: LoanBase, test_database
) -> None:
    application, _, _, _, tokens = await approved_loan(
        db,
        loan_base,
        test_database,
        phone_pair=("+2348030100001", "+2348030100002"),
    )
    cid = loan_base.cooperative_id
    token = await final_terms_token(db, cid, application, tokens)
    slug = await db.fetchval("SELECT slug FROM cooperatives WHERE id=$1", cid)
    sender = FakeOtpSender()
    for _ in range(3):
        await send_otp(slug, token, "198.51.100.11", sender)
    with pytest.raises(AppError) as error:
        await send_otp(slug, token, "198.51.100.11", sender)
    assert error.value.status_code == 429
    assert len(sender.sent) == 3
    assert (
        await db.fetchval(
            "SELECT max(attempts) FROM consent_rate_limits WHERE cooperative_id=$1 AND dimension='consent'",
            cid,
        )
        == 3
    )


async def test_otp_send_limit_is_three_per_destination_per_hour(
    db: asyncpg.Connection, loan_base: LoanBase, test_database
) -> None:
    cid = loan_base.cooperative_id
    await db.execute("SELECT set_config('app.cooperative_id',$1,false)", str(cid))
    slug = await db.fetchval("SELECT slug FROM cooperatives WHERE id=$1", cid)
    sender = FakeOtpSender()
    tokens: list[str] = []
    for _ in range(4):
        application, _, _, _, created_tokens = await approved_loan(
            db,
            loan_base,
            test_database,
            phone_pair=("+2348030200001", "+2348030200002"),
        )
        tokens.append(await final_terms_token(db, cid, application, created_tokens))
    for token in tokens[:3]:
        await send_otp(slug, token, "198.51.100.21", sender)
    with pytest.raises(AppError) as error:
        await send_otp(slug, tokens[3], "198.51.100.21", sender)
    assert error.value.status_code == 429
    assert len(sender.sent) == 3
    assert (
        await db.fetchval(
            "SELECT max(attempts) FROM consent_rate_limits WHERE cooperative_id=$1 AND dimension='destination'",
            cid,
        )
        == 3
    )


async def test_otp_send_limit_is_ten_per_ip_per_hour(
    db: asyncpg.Connection, loan_base: LoanBase, test_database
) -> None:
    cid = loan_base.cooperative_id
    await db.execute("SELECT set_config('app.cooperative_id',$1,false)", str(cid))
    slug = await db.fetchval("SELECT slug FROM cooperatives WHERE id=$1", cid)
    sender = FakeOtpSender()
    tokens: list[str] = []
    for index in range(11):
        application, _, _, _, created_tokens = await approved_loan(
            db,
            loan_base,
            test_database,
            phone_pair=(f"+23480303{index:05d}", f"+23480304{index:05d}"),
        )
        tokens.append(await final_terms_token(db, cid, application, created_tokens))
    for token in tokens[:10]:
        await send_otp(slug, token, "198.51.100.31", sender)
    with pytest.raises(AppError) as error:
        await send_otp(slug, tokens[10], "198.51.100.31", sender)
    assert error.value.status_code == 429
    assert len(sender.sent) == 10
    assert (
        await db.fetchval(
            "SELECT max(attempts) FROM consent_rate_limits WHERE cooperative_id=$1 AND dimension='ip'",
            cid,
        )
        == 10
    )


async def test_otp_attempts_accumulate_across_resends_and_lock_after_five(
    db: asyncpg.Connection, loan_base: LoanBase, test_database
) -> None:
    application, _, _, _, tokens = await approved_loan(
        db,
        loan_base,
        test_database,
        phone_pair=("+2348030400001", "+2348030400002"),
    )
    cid = loan_base.cooperative_id
    token = await final_terms_token(db, cid, application, tokens)
    token_hash = hashlib.sha256(bytes.fromhex(token)).digest()
    slug = await db.fetchval("SELECT slug FROM cooperatives WHERE id=$1", cid)
    ip = "198.51.100.41"
    sender = FakeOtpSender()
    await send_otp(slug, token, ip, sender)
    for _ in range(2):
        code = sender.sent[-1][1]
        wrong_code = "000000" if code != "000000" else "000001"
        assert await consent_service.verify_otp(slug, token, wrong_code, ip) is None
    assert (
        await db.fetchval(
            "SELECT otp_attempts FROM member_consents WHERE cooperative_id=$1 AND token_hash=$2",
            cid,
            token_hash,
        )
        == 2
    )

    await send_otp(slug, token, ip, sender)
    assert (
        await db.fetchval(
            "SELECT otp_attempts FROM member_consents WHERE cooperative_id=$1 AND token_hash=$2",
            cid,
            token_hash,
        )
        == 2
    )
    for _ in range(3):
        code = sender.sent[-1][1]
        wrong_code = "000000" if code != "000000" else "000001"
        assert await consent_service.verify_otp(slug, token, wrong_code, ip) is None
    locked = await db.fetchrow(
        "SELECT status,otp_attempts FROM member_consents WHERE cooperative_id=$1 AND token_hash=$2",
        cid,
        token_hash,
    )
    assert (locked["status"], locked["otp_attempts"]) == ("invalidated", 5)
    with pytest.raises(NotFound):
        await send_otp(slug, token, ip, sender)


async def test_locked_consent_can_be_reissued_by_an_officer(
    db: asyncpg.Connection, loan_base: LoanBase, test_database
) -> None:
    application, _, borrower, _, tokens = await approved_loan(
        db,
        loan_base,
        test_database,
        phone_pair=("+2348030500001", "+2348030500002"),
    )
    cid = loan_base.cooperative_id
    token = await final_terms_token(db, cid, application, tokens)
    token_hash = hashlib.sha256(bytes.fromhex(token)).digest()
    old_id = await db.fetchval(
        "SELECT id FROM member_consents WHERE cooperative_id=$1 AND token_hash=$2",
        cid,
        token_hash,
    )
    slug = await db.fetchval("SELECT slug FROM cooperatives WHERE id=$1", cid)
    ip = "198.51.100.51"
    sender = FakeOtpSender()
    await send_otp(slug, token, ip, sender)
    for _ in range(5):
        code = sender.sent[-1][1]
        wrong_code = "000000" if code != "000000" else "000001"
        await consent_service.verify_otp(slug, token, wrong_code, ip)
    assert (
        await db.fetchval(
            "SELECT status FROM member_consents WHERE cooperative_id=$1 AND id=$2", cid, old_id
        )
        == "invalidated"
    )

    reissue = getattr(loan_service, "reissue_consent", None)
    assert callable(reissue), "officer reissue service is required"
    with pytest.raises(Forbidden):
        await reissue(session(cid, loan_base.user_ids[1], "committee"), application, old_id)
    new_tokens: list[str] = []
    new_id = await reissue(
        session(cid, loan_base.user_ids[0], "officer"),
        application,
        old_id,
        delivery_observer=new_tokens.append,
    )
    assert new_id != old_id
    assert len(new_tokens) == 1
    new_hash = hashlib.sha256(bytes.fromhex(new_tokens[0])).digest()
    new_consent = await db.fetchrow(
        """SELECT id,kind,member_id,status,otp_attempts,token_hash FROM member_consents
          WHERE cooperative_id=$1 AND token_hash=$2""",
        cid,
        new_hash,
    )
    assert (
        new_consent["id"],
        new_consent["kind"],
        new_consent["member_id"],
        new_consent["status"],
        new_consent["otp_attempts"],
    ) == (new_id, "final_terms", borrower, "pending", 0)
    assert new_consent["token_hash"] != token_hash
    await send_otp(slug, new_tokens[0], ip, sender)


async def test_confirmation_metadata_is_minimal_until_otp_verification(
    db: asyncpg.Connection, loan_base: LoanBase, test_database
) -> None:
    application, _, _, _, tokens = await approved_loan(
        db,
        loan_base,
        test_database,
        phone_pair=("+2348030600001", "+2348030600002"),
    )
    cid = loan_base.cooperative_id
    token = await final_terms_token(db, cid, application, tokens)
    slug = await db.fetchval("SELECT slug FROM cooperatives WHERE id=$1", cid)
    metadata = await consent_service.view(slug, token)
    assert set(metadata) == {"cooperative_name", "kind", "expires_at"}
    assert metadata["cooperative_name"] == await db.fetchval(
        "SELECT name FROM cooperatives WHERE id=$1", cid
    )

    sender = FakeOtpSender()
    ip = "198.51.100.61"
    await send_otp(slug, token, ip, sender)
    verify = getattr(consent_service, "verify_otp", None)
    assert callable(verify), "a separate OTP verification step is required"
    details = await verify(slug, token, sender.sent[-1][1], ip)
    assert details is not None
    assert details["applicant"]
    assert details["terms"]
    assert await decide_consent(slug, token, "confirm", ip, "test-agent") == "recorded"


async def test_outbox_worker_delivers_encrypted_confirmation_otp(
    db: asyncpg.Connection, loan_base: LoanBase, test_database
) -> None:
    application, _, _, _, tokens = await approved_loan(
        db,
        loan_base,
        test_database,
        phone_pair=("+2348030700001", "+2348030700002"),
    )
    cid = loan_base.cooperative_id
    slug = await db.fetchval("SELECT slug FROM tenant_directory WHERE cooperative_id=$1", cid)
    token = await final_terms_token(db, cid, application, tokens)
    generated: list[tuple[str, str]] = []
    await enqueue_otp(
        slug,
        token,
        "198.51.100.71",
        delivery_observer=lambda destination, code: generated.append((destination, code)),
    )
    assert generated

    async with transaction(cid) as session_db:
        queued = (
            (
                await session_db.execute(
                    text("""SELECT payload_encrypted,kind,status FROM notifications
                  WHERE cooperative_id=:cid AND member_id=(
                    SELECT member_id FROM member_consents
                    WHERE cooperative_id=:cid AND token_hash=:token)
                    AND kind='confirmation_otp' ORDER BY created_at DESC LIMIT 1"""),
                    {"cid": cid, "token": hashlib.sha256(bytes.fromhex(token)).digest()},
                )
            )
            .mappings()
            .one()
        )
    assert queued["status"] == "pending"
    assert queued["kind"] == "confirmation_otp"
    payload = decrypt_payload(queued["payload_encrypted"])
    assert payload["code"] == generated[0][1]
    assert payload["destination"] == generated[0][0]

    runtime = get_settings()
    worker_settings = Settings(
        app_env=runtime.app_env,
        secret_key=runtime.secret_key,
        encryption_key=runtime.encryption_key,
        public_base_url="https://ajo.example.test",
    )
    gateway = FakeSmsGateway()
    expected_destination, expected_code = generated[0]
    for _ in range(100):
        processed = await process_next_notification(cid, gateway, worker_settings)
        if any(
            destination == expected_destination and expected_code in message
            for destination, message in gateway.sent
        ):
            break
        if not processed:
            break
    assert any(
        destination == expected_destination and expected_code in message
        for destination, message in gateway.sent
    )
