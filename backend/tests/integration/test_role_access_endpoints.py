"""HTTP role-access regressions for screens that read tenant data."""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.auth import current_session
from app.core.db import asyncpg_dsn, dispose_engine
from app.domains.auth.service import CurrentSession
from app.main import create_app
from tests.conftest import TenantSeed, create_tenant

pytestmark = pytest.mark.db


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


@pytest.fixture
async def role_api():
    selected: dict[str, CurrentSession] = {}
    app = create_app()
    app.dependency_overrides[current_session] = lambda: selected["current"]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://role-access.test"
    ) as client:
        yield client, selected
    app.dependency_overrides.clear()


def _session(seed: TenantSeed, role: str) -> CurrentSession:
    role_index = ("officer", "admin", "treasurer", "auditor").index(role)
    user_id = seed.user_ids[role_index]
    return CurrentSession(
        seed.cooperative_id,
        "role-access-test",
        uuid.uuid4(),
        user_id,
        f"{role}@example.test",
        (role,),
        bytes(32),
    )


@pytest.mark.parametrize(
    ("screen", "role", "path"),
    [
        ("payroll profiles", "officer", "/api/v1/payroll/profiles"),
        ("payroll profiles", "admin", "/api/v1/payroll/profiles"),
        ("payroll runs", "admin", "/api/v1/payroll/runs"),
        ("payroll runs", "treasurer", "/api/v1/payroll/runs"),
        ("loan dashboard cards", "officer", "/api/v1/loan-applications?status=under_review"),
        ("loan dashboard cards", "admin", "/api/v1/loan-applications?status=under_review"),
        ("loan dashboard cards", "treasurer", "/api/v1/loan-applications?status=under_review"),
        ("disbursement dashboard cards", "admin", "/api/v1/disbursements?status=pending_authorization"),
        ("disbursement dashboard cards", "treasurer", "/api/v1/disbursements?status=authorized"),
        ("disbursement list", "admin", "/api/v1/disbursements"),
        ("disbursement list", "treasurer", "/api/v1/disbursements"),
        ("disbursement ready-loan queue", "admin", "/api/v1/disbursements/ready-loans"),
        ("disbursement ready-loan queue", "treasurer", "/api/v1/disbursements/ready-loans"),
        ("audit events", "admin", "/api/v1/audit-events"),
        ("staff selector", "admin", "/api/v1/staff"),
        ("new-loan member picker", "officer", "/api/v1/members"),
        ("new-loan product picker", "officer", "/api/v1/loan-products"),
    ],
)
async def test_screen_read_endpoint_is_available_to_matrix_role_without_csrf(
    screen: str,
    role: str,
    path: str,
    db: asyncpg.Connection,
    role_api: tuple[AsyncClient, dict[str, CurrentSession]],
) -> None:
    seed = await create_tenant(
        db,
        name="Role access API test",
        slug_prefix="role-access",
        roles=("officer", "admin", "treasurer", "auditor"),
        include_member=True,
    )
    client, selected = role_api
    selected["current"] = _session(seed, role)

    response = await client.get(path)

    assert response.status_code == 200, f"{screen} for {role}: {response.status_code} {response.text}"
    if screen == "staff selector":
        assert any(
            item["user_id"] == str(selected["current"].user_id)
            for item in response.json()["items"]
        )
    if screen == "new-loan member picker":
        assert any(item["id"] == str(seed.member_id) for item in response.json())


async def test_member_detail_endpoint_is_available_to_officer_for_new_loan_form(
    db: asyncpg.Connection,
    role_api: tuple[AsyncClient, dict[str, CurrentSession]],
) -> None:
    seed = await create_tenant(
        db,
        name="Member picker API test",
        slug_prefix="member-picker",
        roles=("officer", "admin", "treasurer", "auditor"),
        include_member=True,
    )
    assert seed.member_id is not None
    client, selected = role_api
    selected["current"] = _session(seed, "officer")

    response = await client.get(f"/api/v1/members/{seed.member_id}")

    assert response.status_code == 200, f"new-loan member detail: {response.status_code} {response.text}"


async def test_officer_loan_detail_endpoint_returns_snapshot_for_detail_screen(
    db: asyncpg.Connection,
    test_database,
    role_api: tuple[AsyncClient, dict[str, CurrentSession]],
) -> None:
    from app.domains.loans.service import decide_application, submit_application
    from tests.integration.test_loans import _create_loan_base, setup_loan_member

    base = await _create_loan_base(test_database)
    member_id = await setup_loan_member(db, base)
    bank_account_id = uuid.uuid4()
    await db.execute(
        """INSERT INTO bank_accounts(cooperative_id,id,member_id,bank_code,account_number_encrypted,
          last4,account_name,status,verified_at)
          VALUES($1,$2,$3,'001','test-ciphertext','0010','Snapshot Beneficiary','verified',now())""",
        base.cooperative_id,
        bank_account_id,
        member_id,
    )
    officer = CurrentSession(
        base.cooperative_id,
        "loan-detail-test",
        uuid.uuid4(),
        base.user_ids[0],
        "officer@example.test",
        ("officer",),
        bytes(32),
    )
    application_id = await submit_application(
        officer,
        member_id=member_id,
        product_id=base.product_id,
        principal_kobo=500_000,
        tenor_months=6,
        destination_bank_account_id=bank_account_id,
    )
    await db.execute(
        "UPDATE loan_applications SET status='under_review' WHERE cooperative_id=$1 AND id=$2",
        base.cooperative_id,
        application_id,
    )
    committee = CurrentSession(
        base.cooperative_id,
        "loan-detail-test",
        uuid.uuid4(),
        base.user_ids[1],
        "committee@example.test",
        ("committee",),
        bytes(32),
    )
    await decide_application(committee, application_id, "approved", "test approval")
    client, selected = role_api
    selected["current"] = officer

    response = await client.get(f"/api/v1/loan-applications/{application_id}")

    assert response.status_code == 200, f"loan detail: {response.status_code} {response.text}"
    detail = response.json()
    assert detail["terms_snapshot"]["destination"]["bank_code"] == "001"
    assert detail["terms_snapshot"]["destination"]["last4"] == "0010"
    assert detail["terms_snapshot"]["destination"]["account_name"] == "Snapshot Beneficiary"
