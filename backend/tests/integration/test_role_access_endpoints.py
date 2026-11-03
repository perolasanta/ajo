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
