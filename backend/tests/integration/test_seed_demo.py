"""End-to-end demo seeding invariants against local PostgreSQL."""

from __future__ import annotations

import re

import asyncpg
import pyotp
import pytest

from app.cli import create_cooperative
from app.core.config import get_settings
from app.core.db import asyncpg_dsn, dispose_engine
from app.domains.auth import service as auth_service
from app.seed_demo import seed_demo

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
async def reset_engine() -> None:
    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture
async def db(test_database) -> asyncpg.Connection:
    conn = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    assert await conn.fetchval("SELECT current_user") == "ajo_app"
    yield conn
    await conn.close()


async def test_seed_demo_end_to_end(db: asyncpg.Connection, capsys) -> None:
    slug = "seedtest"
    admin_email = "admin@seed.test"
    admin_password = await create_cooperative(slug, "Seed test", admin_email)

    summary = await seed_demo(slug, yes_fake_data=True)
    assert summary == {
        "members": 25,
        "loan_applications": 14,
        "loans": 9,
        "staff_users": 5,
        "disputed_loan_reachable": True,
    }

    output = capsys.readouterr().out
    passwords = dict(
        re.findall(rf"^\s+([a-z]+)@{re.escape(slug)}\.demo: (\S+)$", output, re.MULTILINE)
    )
    assert set(passwords) == {"officer", "committee", "treasurer", "auditor"}

    cooperative_id = await db.fetchval(
        "SELECT cooperative_id FROM tenant_directory WHERE slug = $1", slug
    )
    async with db.transaction():
        await db.execute("SELECT set_config('app.cooperative_id', $1, true)", str(cooperative_id))
        assert (
            await db.fetchval(
                "SELECT count(*) FROM members WHERE cooperative_id = $1", cooperative_id
            )
            == 25
        )
        assert (
            await db.fetchval(
                "SELECT count(*) FROM loan_applications WHERE cooperative_id = $1",
                cooperative_id,
            )
            == 14
        )
        assert (
            await db.fetchval(
                "SELECT count(*) FROM loans WHERE cooperative_id = $1", cooperative_id
            )
            == 9
        )
        role_rows = await db.fetch(
            "SELECT role FROM user_roles WHERE cooperative_id = $1", cooperative_id
        )
        assert {row["role"] for row in role_rows} == {
            "admin",
            "officer",
            "committee",
            "treasurer",
            "auditor",
        }

        rows = await db.fetch(
            """SELECT m.member_no, a.status AS application_status, l.status AS loan_status
                 FROM loan_applications a
                 JOIN members m ON m.cooperative_id=a.cooperative_id AND m.id=a.member_id
                 LEFT JOIN loans l ON l.cooperative_id=a.cooperative_id AND l.application_id=a.id
                WHERE a.cooperative_id=$1 ORDER BY m.member_no""",
            cooperative_id,
        )
        expected_application_states = [
            "awaiting_applicant_confirmation",
            "awaiting_applicant_confirmation",
            "awaiting_applicant_confirmation",
            "under_review",
            "under_review",
            "awaiting_borrower_final_confirmation",
            "awaiting_borrower_final_confirmation",
            "ready_for_disbursement",
            "repaying",
            "repaying",
            "repaying",
            "repaying",
            "closed",
            "disputed",
        ]
        expected_loan_states = [
            None,
            None,
            None,
            None,
            None,
            "approved",
            "approved",
            "ready_for_disbursement",
            "repaying",
            "repaying",
            "repaying",
            "repaying",
            "closed",
            "disputed",
        ]
        assert len(rows) == 14
        assert [row["application_status"] for row in rows] == expected_application_states
        assert [row["loan_status"] for row in rows] == expected_loan_states

        unbalanced = await db.fetchval(
            "SELECT count(*) FROM (SELECT entry_id, sum(debit_kobo) AS debits, "
            "sum(credit_kobo) AS credits FROM journal_lines WHERE cooperative_id = $1 "
            "GROUP BY entry_id) t WHERE debits <> credits",
            cooperative_id,
        )
        assert unbalanced == 0
        totals = await db.fetchrow(
            "SELECT coalesce(sum(debit_kobo), 0) AS debits, "
            "coalesce(sum(credit_kobo), 0) AS credits FROM journal_lines WHERE cooperative_id = $1",
            cooperative_id,
        )
        assert totals["debits"] == totals["credits"]

    credentials = {
        "admin": (admin_email, admin_password),
        **{role: (f"{role}@{slug}.demo", password) for role, password in passwords.items()},
    }
    for index, (role, (email, password)) in enumerate(credentials.items(), start=1):
        ip = f"127.0.2.{index}"
        login = await auth_service.login(
            slug=slug,
            email=email,
            password=password,
            totp_code=None,
            recovery_code=None,
            ip=ip,
            user_agent="seed-demo-integration-test",
        )
        assert login.data["mfa_enrollment_required"] is True
        assert login.session_cookie is not None
        secret = pyotp.parse_uri(login.data["provisioning_uri"]).secret
        _, current = await auth_service.complete_mfa_enrollment(
            login.session_cookie, pyotp.TOTP(secret).now(), ip
        )
        assert current is not None
        assert current.roles == (role,)

    with pytest.raises(RuntimeError, match="use a new slug"):
        await seed_demo(slug, yes_fake_data=True)


async def test_seed_demo_refuses_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="production"):
            await seed_demo("any-slug", yes_fake_data=True)
    finally:
        get_settings.cache_clear()
