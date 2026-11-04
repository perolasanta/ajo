from __future__ import annotations

import uuid

import pytest

from app import cli
from app.domains.auth import staff

pytestmark = pytest.mark.unit


class RecordingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(self, statement: object, params: dict[str, object]) -> None:
        self.calls.append((str(statement), params))


async def test_staff_creation_persists_optional_full_name(monkeypatch: pytest.MonkeyPatch) -> None:
    session = RecordingSession()
    async def audit(*args: object, **kwargs: object) -> None:
        return None
    monkeypatch.setattr(staff, "hash_password", lambda password: "hashed")
    monkeypatch.setattr(staff, "append_audit", audit)
    user_id = await staff._create_staff_user_in_session(
        session,
        cooperative_id=uuid.uuid4(),
        email="named@example.test",
        password="throwaway",
        roles=("admin",),
        actor_user_id=uuid.uuid4(),
        actor_role="admin",
        channel="test",
        full_name="Staff Name",
    )
    insert, params = session.calls[0]
    assert params["id"] == user_id
    assert "full_name" in insert
    assert params["full_name"] == "Staff Name"


def test_create_cooperative_cli_accepts_optional_full_name() -> None:
    args = cli._parser().parse_args(
        [
            "create-cooperative",
            "--slug",
            "demo",
            "--name",
            "Demo",
            "--admin-email",
            "a@example.test",
            "--full-name",
            "Ada Admin",
        ]
    )
    assert args.full_name == "Ada Admin"
