from datetime import UTC, datetime, timedelta

import pytest

from app.core.db import transaction
from app.domains.audit.reads import list_audit_events
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
