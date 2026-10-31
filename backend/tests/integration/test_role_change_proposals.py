"""Maker-checker role change tests against disposable local PostgreSQL."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import asyncpg
import pytest
from cryptography.fernet import Fernet

from app.core import security
from app.core.config import get_settings
from app.core.db import asyncpg_dsn, dispose_engine
from app.core.errors import Conflict, Forbidden
from app.domains.auth import service as auth_service
from tests.conftest import create_tenant

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
async def isolated_role_change_engine(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    get_settings.cache_clear()
    security._fernet.cache_clear()
    yield
    await dispose_engine()
    security._fernet.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
async def tenant(test_database) -> dict[str, Any]:
    app = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    seed = await create_tenant(
        app,
        name="Role change integration test",
        slug_prefix="role-change",
        roles=("admin", "admin", "officer", "auditor"),
    )
    slug = f"role-change-{seed.cooperative_id.hex}"
    await app.execute(
        "INSERT INTO tenant_directory (slug, cooperative_id) VALUES ($1, $2)",
        slug,
        seed.cooperative_id,
    )
    await app.close()
    return {
        "cooperative_id": seed.cooperative_id,
        "user_ids": seed.user_ids,
        "slug": slug,
        "app_url": test_database.app_url,
    }


def current(tenant: dict[str, Any], user_id: uuid.UUID, *roles: str) -> auth_service.CurrentSession:
    return auth_service.CurrentSession(
        cooperative_id=tenant["cooperative_id"],
        slug=tenant["slug"],
        session_id=uuid.uuid4(),
        user_id=user_id,
        email=f"{user_id.hex}@example.test",
        roles=tuple(roles),
        csrf_hash=bytes(32),
    )


async def set_role(tenant: dict[str, Any], user_id: uuid.UUID, role: str, *, present: bool) -> None:
    app = await asyncpg.connect(asyncpg_dsn(tenant["app_url"]))
    try:
        await app.execute(
            "SELECT set_config('app.cooperative_id', $1, false)",
            str(tenant["cooperative_id"]),
        )
        if present:
            await app.execute(
                "INSERT INTO user_roles (cooperative_id, user_id, role) VALUES ($1, $2, $3) "
                "ON CONFLICT DO NOTHING",
                tenant["cooperative_id"],
                user_id,
                role,
            )
        else:
            await app.execute(
                "DELETE FROM user_roles WHERE cooperative_id=$1 AND user_id=$2 AND role=$3",
                tenant["cooperative_id"],
                user_id,
                role,
            )
    finally:
        await app.close()


async def admin_count(tenant: dict[str, Any]) -> int:
    app = await asyncpg.connect(asyncpg_dsn(tenant["app_url"]))
    try:
        await app.execute(
            "SELECT set_config('app.cooperative_id', $1, false)",
            str(tenant["cooperative_id"]),
        )
        return await app.fetchval(
            "SELECT count(DISTINCT ur.user_id) FROM user_roles ur "
            "JOIN users u ON u.cooperative_id=ur.cooperative_id AND u.id=ur.user_id "
            "WHERE ur.cooperative_id=$1 AND ur.role='admin' AND u.status='active'",
            tenant["cooperative_id"],
        )
    finally:
        await app.close()


async def make_proposal(
    tenant: dict[str, Any],
    *,
    proposer: int = 0,
    target: int = 2,
    role: str = "auditor",
    action: str = "add",
    reason: str = "Approved staffing update",
) -> uuid.UUID:
    return await auth_service.propose_role_change(
        current(tenant, tenant["user_ids"][proposer], "admin"),
        tenant["user_ids"][target],
        role,
        action,
        reason,
        "127.0.0.1",
    )


async def approve(tenant: dict[str, Any], proposal_id: uuid.UUID, *, actor: int = 1) -> None:
    await auth_service.approve_role_change(
        current(tenant, tenant["user_ids"][actor], "admin"), proposal_id, "127.0.0.1"
    )


async def test_http_role_change_endpoints_enforce_maker_checker(tenant: dict[str, Any]) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.api import auth as auth_api
    from app.main import create_app

    await set_role(tenant, tenant["user_ids"][1], "admin", present=False)
    app = create_app()
    app.dependency_overrides[auth_api.require_csrf] = lambda: None
    app.dependency_overrides[auth_api.current_session] = lambda: current(
        tenant, tenant["user_ids"][0], "admin"
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        one_admin = await client.post(
            "/api/v1/auth/role-change-proposals",
            json={
                "target_user_id": str(tenant["user_ids"][2]),
                "role": "auditor",
                "action": "add",
                "reason": "Grant audit access",
            },
        )
        legacy = await client.post(
            f"/api/v1/auth/users/{tenant['user_ids'][2]}/roles",
            json={"role": "auditor", "enabled": True},
        )
    assert one_admin.status_code == 409
    assert "At least two active admins" in one_admin.json()["message"]
    assert legacy.status_code == 409
    assert "Immediate role changes are disabled" in legacy.json()["message"]


async def test_single_admin_cannot_propose_role_change(test_database) -> None:
    app = await asyncpg.connect(asyncpg_dsn(test_database.app_url))
    seed = await create_tenant(app, slug_prefix="single-admin", roles=("admin", "officer"))
    slug = f"single-admin-{seed.cooperative_id.hex}"
    await app.execute(
        "INSERT INTO tenant_directory (slug, cooperative_id) VALUES ($1, $2)",
        slug,
        seed.cooperative_id,
    )
    await app.close()
    tenant_data = {
        "cooperative_id": seed.cooperative_id,
        "user_ids": seed.user_ids,
        "slug": slug,
    }
    with pytest.raises(Conflict, match="At least two active admins"):
        await auth_service.propose_role_change(
            current(tenant_data, seed.user_ids[0], "admin"),
            seed.user_ids[1],
            "auditor",
            "add",
            "Add auditor access",
            "127.0.0.1",
        )


async def test_non_admin_cannot_propose_or_approve(tenant: dict[str, Any]) -> None:
    with pytest.raises(Forbidden, match="Admin role required"):
        await auth_service.propose_role_change(
            current(tenant, tenant["user_ids"][2], "officer"),
            tenant["user_ids"][3],
            "committee",
            "add",
            "Add committee access",
            "127.0.0.1",
        )

    proposal_id = await make_proposal(tenant)
    with pytest.raises(Forbidden, match="Admin role required"):
        await auth_service.approve_role_change(
            current(tenant, tenant["user_ids"][2], "officer"), proposal_id, "127.0.0.1"
        )


async def test_approver_must_still_be_admin_at_approval_time(tenant: dict[str, Any]) -> None:
    proposal_id = await make_proposal(tenant)
    await set_role(tenant, tenant["user_ids"][1], "admin", present=False)
    with pytest.raises(Forbidden, match="Admin role required"):
        await auth_service.approve_role_change(
            current(tenant, tenant["user_ids"][1], "admin"), proposal_id, "127.0.0.1"
        )


async def test_proposer_cannot_approve_own_proposal(tenant: dict[str, Any]) -> None:
    proposal_id = await make_proposal(tenant)
    with pytest.raises(Forbidden, match="proposer cannot approve"):
        await auth_service.approve_role_change(
            current(tenant, tenant["user_ids"][0], "admin"), proposal_id, "127.0.0.1"
        )


async def test_target_cannot_approve_own_role_add(tenant: dict[str, Any]) -> None:
    proposal_id = await make_proposal(tenant, target=1, role="auditor", action="add")
    with pytest.raises(Forbidden, match="target cannot approve a role addition"):
        await auth_service.approve_role_change(
            current(tenant, tenant["user_ids"][1], "admin"), proposal_id, "127.0.0.1"
        )


async def test_stale_add_is_rejected(tenant: dict[str, Any]) -> None:
    proposal_id = await make_proposal(tenant, role="committee", action="add")
    await set_role(tenant, tenant["user_ids"][2], "committee", present=True)
    with pytest.raises(Conflict, match="stale"):
        await approve(tenant, proposal_id)


async def test_stale_remove_is_rejected(tenant: dict[str, Any]) -> None:
    proposal_id = await make_proposal(tenant, role="officer", action="remove")
    await set_role(tenant, tenant["user_ids"][2], "officer", present=False)
    with pytest.raises(Conflict, match="stale"):
        await approve(tenant, proposal_id)


async def test_missing_target_is_rejected_as_stale(tenant: dict[str, Any]) -> None:
    proposal_id = await make_proposal(tenant, role="auditor", action="add")
    app = await asyncpg.connect(asyncpg_dsn(tenant["app_url"]))
    try:
        await app.execute(
            "SELECT set_config('app.cooperative_id', $1, false)",
            str(tenant["cooperative_id"]),
        )
        await app.execute(
            "UPDATE role_change_proposals SET target_user_id=$3 WHERE cooperative_id=$1 AND id=$2",
            tenant["cooperative_id"],
            proposal_id,
            uuid.uuid4(),
        )
    finally:
        await app.close()
    with pytest.raises(Conflict, match="stale"):
        await approve(tenant, proposal_id)


async def test_last_admin_role_cannot_be_removed(tenant: dict[str, Any]) -> None:
    # Three admins allow the original proposal; intervening role changes leave its target last.
    app = await asyncpg.connect(asyncpg_dsn(tenant["app_url"]))
    try:
        await app.execute(
            "SELECT set_config('app.cooperative_id', $1, false)",
            str(tenant["cooperative_id"]),
        )
        for user_id in tenant["user_ids"][:3]:
            await app.execute(
                "INSERT INTO user_roles (cooperative_id, user_id, role) VALUES ($1, $2, 'admin') "
                "ON CONFLICT DO NOTHING",
                tenant["cooperative_id"],
                user_id,
            )
    finally:
        await app.close()
    proposal_id = await make_proposal(tenant, proposer=0, target=1, role="admin", action="remove")
    await set_role(tenant, tenant["user_ids"][0], "admin", present=False)
    await set_role(tenant, tenant["user_ids"][2], "admin", present=False)
    with pytest.raises(Conflict, match="last active admin"):
        await approve(tenant, proposal_id, actor=1)
    assert await admin_count(tenant) == 1


async def test_concurrent_admin_removals_cannot_leave_zero_admins(tenant: dict[str, Any]) -> None:
    admin_a, admin_b = tenant["user_ids"][:2]
    proposal_a = await make_proposal(tenant, proposer=0, target=1, role="admin", action="remove")
    proposal_b = await make_proposal(tenant, proposer=1, target=0, role="admin", action="remove")
    outcomes = await asyncio.gather(
        auth_service.approve_role_change(
            current(tenant, admin_b, "admin"), proposal_a, "127.0.0.1"
        ),
        auth_service.approve_role_change(
            current(tenant, admin_a, "admin"), proposal_b, "127.0.0.1"
        ),
        return_exceptions=True,
    )
    assert sum(outcome is None for outcome in outcomes) == 1
    assert sum(isinstance(outcome, (Conflict, Forbidden)) for outcome in outcomes) == 1
    assert await admin_count(tenant) == 1


async def test_sessions_are_revoked_after_effective_role_change(tenant: dict[str, Any]) -> None:
    app = await asyncpg.connect(asyncpg_dsn(tenant["app_url"]))
    session_id = uuid.uuid4()
    try:
        await app.execute(
            "SELECT set_config('app.cooperative_id', $1, false)",
            str(tenant["cooperative_id"]),
        )
        await app.execute(
            "INSERT INTO user_sessions "
            "(id, cooperative_id, user_id, token_hash, csrf_token_hash, expires_at) "
            "VALUES ($1, $2, $3, $4, $5, now() + interval '1 day')",
            session_id,
            tenant["cooperative_id"],
            tenant["user_ids"][2],
            uuid.uuid4().bytes * 2,
            uuid.uuid4().bytes * 2,
        )
    finally:
        await app.close()

    proposal_id = await make_proposal(tenant, target=2, role="auditor", action="add")
    await approve(tenant, proposal_id)
    app = await asyncpg.connect(asyncpg_dsn(tenant["app_url"]))
    try:
        await app.execute(
            "SELECT set_config('app.cooperative_id', $1, false)",
            str(tenant["cooperative_id"]),
        )
        revoked_at = await app.fetchval(
            "SELECT revoked_at FROM user_sessions WHERE cooperative_id=$1 AND id=$2",
            tenant["cooperative_id"],
            session_id,
        )
        assert revoked_at is not None
        roles = await app.fetch(
            "SELECT role FROM user_roles WHERE cooperative_id=$1 AND user_id=$2 ORDER BY role",
            tenant["cooperative_id"],
            tenant["user_ids"][2],
        )
        assert {row["role"] for row in roles} == {"auditor", "officer"}
    finally:
        await app.close()


async def test_removing_one_role_preserves_unrelated_roles(tenant: dict[str, Any]) -> None:
    add_auditor = await make_proposal(tenant, role="auditor", action="add")
    await approve(tenant, add_auditor)
    remove_officer = await make_proposal(tenant, role="officer", action="remove")
    await approve(tenant, remove_officer)

    app = await asyncpg.connect(asyncpg_dsn(tenant["app_url"]))
    try:
        await app.execute(
            "SELECT set_config('app.cooperative_id', $1, false)",
            str(tenant["cooperative_id"]),
        )
        roles = await app.fetch(
            "SELECT role FROM user_roles WHERE cooperative_id=$1 AND user_id=$2",
            tenant["cooperative_id"],
            tenant["user_ids"][2],
        )
    finally:
        await app.close()
    assert [row["role"] for row in roles] == ["auditor"]


async def test_proposal_lifecycle_audits_actor_target_old_and_new_role(
    tenant: dict[str, Any],
) -> None:
    approved = await make_proposal(tenant, role="auditor", action="add")
    await approve(tenant, approved)

    rejected = await make_proposal(tenant, role="committee", action="add")
    await auth_service.reject_role_change(
        current(tenant, tenant["user_ids"][1], "admin"), rejected, "127.0.0.1"
    )

    cancelled = await make_proposal(tenant, role="auditor", action="remove")
    await auth_service.cancel_role_change(
        current(tenant, tenant["user_ids"][0], "admin"), cancelled, "127.0.0.1"
    )

    expired = await make_proposal(tenant, role="treasurer", action="add")
    app = await asyncpg.connect(asyncpg_dsn(tenant["app_url"]))
    try:
        await app.execute(
            "SELECT set_config('app.cooperative_id', $1, false)",
            str(tenant["cooperative_id"]),
        )
        lifetime = await app.fetchval(
            "SELECT expires_at - created_at FROM role_change_proposals "
            "WHERE cooperative_id=$1 AND id=$2",
            tenant["cooperative_id"],
            expired,
        )
        assert lifetime == timedelta(days=7)
        await app.execute(
            "UPDATE role_change_proposals SET expires_at=now()-interval '1 second' "
            "WHERE cooperative_id=$1 AND id=$2",
            tenant["cooperative_id"],
            expired,
        )
    finally:
        await app.close()
    with pytest.raises(Conflict, match="expired"):
        await approve(tenant, expired)

    app = await asyncpg.connect(asyncpg_dsn(tenant["app_url"]))
    try:
        await app.execute(
            "SELECT set_config('app.cooperative_id', $1, false)",
            str(tenant["cooperative_id"]),
        )
        rows = await app.fetch(
            "SELECT action, actor_user_id, summary->>'target_user_id' AS target_user_id, "
            "summary->>'old_role' AS old_role, summary->>'new_role' AS new_role "
            "FROM audit_logs WHERE cooperative_id=$1 AND action LIKE 'role_change_%' "
            "ORDER BY created_at, id",
            tenant["cooperative_id"],
        )
    finally:
        await app.close()
    actions = {row["action"] for row in rows}
    assert {
        "role_change_proposed",
        "role_change_approved",
        "role_change_rejected",
        "role_change_cancelled",
        "role_change_expired",
    } <= actions
    assert all(row["target_user_id"] for row in rows)
    assert all(
        row["actor_user_id"] is not None for row in rows if row["action"] != "role_change_expired"
    )
    assert all(row["old_role"] is not None or row["new_role"] is not None for row in rows)


async def test_duplicate_pending_target_role_action_is_rejected(tenant: dict[str, Any]) -> None:
    await make_proposal(tenant, role="committee", action="add")
    with pytest.raises(Conflict, match="pending proposal already exists"):
        await make_proposal(tenant, role="committee", action="add")
