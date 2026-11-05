from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.db import transaction
from app.domains.audit.reads import audit_projection, list_audit_events
from app.domains.audit.service import append_audit
from app.domains.auth.service import CurrentSession
from tests.conftest import create_tenant

pytestmark = pytest.mark.db


@pytest.fixture
async def audit_reader_tenant(test_database) -> CurrentSession:
    import asyncpg

    conn = await asyncpg.connect(test_database.app_url)
    seed = await create_tenant(conn, slug_prefix="audit-read", roles=("admin",))
    await conn.close()
    return CurrentSession(
        cooperative_id=seed.cooperative_id,
        slug=f"audit-read-{seed.cooperative_id.hex}",
        session_id=seed.user_ids[0],
        user_id=seed.user_ids[0],
        email="admin@example.test",
        roles=("admin",),
        csrf_hash=b"test",
    )


async def test_audit_reader_accepts_no_filter_and_each_filter_alone(
    audit_reader_tenant: CurrentSession,
) -> None:
    current = audit_reader_tenant
    created_at = datetime.now(UTC)
    async with transaction(current.cooperative_id) as session:
        await append_audit(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            role="admin",
            action="audit_filter_test",
            entity_type="test_entity",
            entity_id="entity-1",
            summary={"status": "active"},
            channel="pytest",
            ip=None,
        )

    cases = [
        {},
        {"from_timestamp": created_at - timedelta(minutes=1)},
        {"to_timestamp": created_at + timedelta(minutes=1)},
        {"actor_id": current.user_id},
        {"action": "audit_filter_test"},
        {"entity_type": "test_entity"},
    ]
    for filters in cases:
        page = await list_audit_events(current, **filters)
        assert page["total"] == 1
        assert len(page["items"]) == 1


async def test_audit_actor_display_name_is_joined_at_read_time(
    audit_reader_tenant: CurrentSession,
) -> None:
    current = audit_reader_tenant
    async with transaction(current.cooperative_id) as session:
        await append_audit(
            session,
            cooperative_id=current.cooperative_id,
            actor_user_id=current.user_id,
            role="admin",
            action="audit_name_test",
            entity_type="test_entity",
            entity_id="entity-2",
            summary={"status": "active"},
            channel="pytest",
            ip=None,
        )
    async with transaction(current.cooperative_id) as session:
        await session.execute(
            text("UPDATE users SET full_name = :name WHERE cooperative_id = :cid AND id = :uid"),
            {"name": "Audit Admin", "cid": current.cooperative_id, "uid": current.user_id},
        )
        actor_email = await session.scalar(
            text("SELECT email FROM users WHERE cooperative_id = :cid AND id = :uid"),
            {"cid": current.cooperative_id, "uid": current.user_id},
        )
    page = await list_audit_events(current, action="audit_name_test")
    items = page["items"]
    assert isinstance(items, list)
    item = items[0]
    assert isinstance(item, dict)
    assert item["actor_user_id"] == current.user_id
    assert item["actor_full_name"] == "Audit Admin"
    projected = audit_projection(item)
    assert projected["actor_id"] == str(current.user_id)
    assert projected["actor_display_name"] == f"Audit Admin ({actor_email})"
