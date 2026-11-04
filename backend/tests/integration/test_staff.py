from __future__ import annotations
import uuid
import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from app.api import staff as staff_api
from app.core.db import asyncpg_dsn, dispose_engine
from app.domains.auth.service import CurrentSession
from app.main import create_app
from tests.conftest import create_tenant
pytestmark = pytest.mark.db
@pytest.fixture(autouse=True)
async def reset_engine():
    await dispose_engine()
    yield
    await dispose_engine()
async def test_admin_lists_seeded_staff_with_roles(test_database) -> None:
    conn = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    try:
        seed = await create_tenant(
            conn,
            name="Staff list integration test",
            slug_prefix="staff-list",
            roles=("admin", "officer"),
        )
        await conn.execute(
            "INSERT INTO user_roles (cooperative_id, user_id, role) VALUES ($1, $2, $3)",
            seed.cooperative_id,
            seed.user_ids[1],
            "treasurer",
        )
    finally:
        await conn.close()
    current = CurrentSession(
        cooperative_id=seed.cooperative_id,
        slug=f"staff-list-{seed.cooperative_id.hex}",
        session_id=uuid.uuid4(),
        user_id=seed.user_ids[0],
        email="admin@example.test",
        roles=("admin",),
        csrf_hash=bytes(32),
    )
    app = create_app()
    app.dependency_overrides[staff_api.current_session] = lambda: current
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        response = await client.get("/api/v1/staff")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    rows_by_id = {row["user_id"]: row for row in body["items"]}
    assert rows_by_id[str(seed.user_ids[0])]["roles"] == ["admin"]
    assert rows_by_id[str(seed.user_ids[1])]["roles"] == ["officer", "treasurer"]
