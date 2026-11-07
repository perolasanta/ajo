"""No-database contract tests for staff action notifications."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import pytest

from app.core.config import Settings
from app.domains.notifications import eligibility
from app.domains.notifications import outbox as notification_outbox

pytestmark = pytest.mark.unit

EVENT_ROLES = {
    "payroll_sign_off": "admin",
    "disbursement_authorization": "admin",
    "role_change_approval": "admin",
    "loan_disputed": "treasurer",
}


@pytest.mark.parametrize(("event_type", "allowed_role"), EVENT_ROLES.items())
def test_each_event_uses_role_union_and_excludes_requester(
    event_type: str, allowed_role: str
) -> None:
    requester, single_role, multiple_roles, unrelated = (uuid.uuid4() for _ in range(4))
    roles = {
        requester: {allowed_role},
        single_role: {allowed_role},
        multiple_roles: {"admin", "treasurer"},
        unrelated: {"officer"},
    }

    recipients = eligibility.eligible_recipients_for_action(
        event_type, excluded_user_ids={requester}, roles_by_user=roles
    )

    expected = (
        {single_role, multiple_roles} if allowed_role in roles[multiple_roles] else {single_role}
    )
    assert set(recipients) == expected
    assert len(recipients) == len(expected)
    assert requester not in recipients


def test_role_add_target_is_excluded_from_approval_recipients() -> None:
    proposer, target, independent_admin = (uuid.uuid4() for _ in range(3))
    roles = {proposer: {"admin"}, target: {"admin"}, independent_admin: {"admin"}}

    recipients = eligibility.eligible_recipients_for_action(
        "role_change_approval",
        excluded_user_ids={proposer, target},
        roles_by_user=roles,
    )

    assert recipients == (independent_admin,)


@pytest.mark.parametrize("event_type", EVENT_ROLES)
def test_payload_has_only_section_and_public_section_link(event_type: str) -> None:
    settings = Settings(
        app_env="development",
        app_name="Ajo",
        public_base_url="https://ajo.example.test/base/",
    )

    payload = notification_outbox.build_action_payload(event_type, settings)

    assert set(payload) == {"section", "url"}
    assert payload["url"].startswith("https://ajo.example.test/")
    serialized = json.dumps(payload)
    for forbidden in (
        "Ada Lovelace",
        "₦1,234",
        "member_no",
        "loan amount",
        "Audit Admin",
        "entity_id",
        "proposer",
        "recipient",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize("event_type", EVENT_ROLES)
async def test_enqueue_is_deduplicated_per_recipient_event_entity(
    event_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cooperative_id, entity_id = uuid.uuid4(), uuid.uuid4()
    requester, admin_one, admin_two, treasurer = (uuid.uuid4() for _ in range(4))
    recipient_ids = [admin_one, admin_two] if EVENT_ROLES[event_type] == "admin" else [treasurer]
    recipients = [
        eligibility.ActionRecipient(event_type, entity_id, user_id) for user_id in recipient_ids
    ]
    monkeypatch.setattr(
        notification_outbox,
        "get_eligible_action_recipients",
        lambda *args, **kwargs: _resolved(recipients),
    )
    monkeypatch.setattr(
        notification_outbox,
        "get_settings",
        lambda: Settings(app_env="development", public_base_url="https://ajo.example.test"),
    )
    payloads: list[dict[str, Any]] = []

    def capture_payload(payload: dict[str, Any]) -> str:
        payloads.append(payload)
        return "encrypted"

    monkeypatch.setattr(notification_outbox, "encrypt_payload", capture_payload)

    class FakeSession:
        def __init__(self) -> None:
            self.rows: dict[tuple[object, ...], dict[str, Any]] = {}

        async def execute(self, statement: Any, params: dict[str, Any]) -> None:
            assert "ON CONFLICT" in str(statement).upper()
            assert "CHANNEL" in str(statement).upper()
            key = (
                params["cid"],
                params["recipient_user_id"],
                params["kind"],
                params["entity_id"],
            )
            self.rows.setdefault(key, params)

    session = FakeSession()
    await notification_outbox.enqueue_action_notifications(
        session, cooperative_id, event_type, entity_id
    )
    await notification_outbox.enqueue_action_notifications(
        session, cooperative_id, event_type, entity_id
    )

    assert {key[1] for key in session.rows} == set(recipient_ids)
    assert len(session.rows) == len(recipient_ids)
    assert all(
        key[0] == cooperative_id and key[2:] == (event_type, entity_id) for key in session.rows
    )
    assert requester not in {key[1] for key in session.rows}
    assert all(
        row["member_id"] is None and row["channel"] == "email" and row["template_version"] == "v1"
        for row in session.rows.values()
    )
    assert all(set(payload) == {"section", "url"} for payload in payloads)


async def test_enqueue_failure_is_swallowed_inside_savepoint(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class FakeNestedTransaction:
        rolled_back = False

        async def __aenter__(self) -> FakeNestedTransaction:
            return self

        async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool:
            self.rolled_back = exc_type is not None
            return False

    class FakeSession:
        def __init__(self) -> None:
            self.nested = FakeNestedTransaction()

        def begin_nested(self) -> FakeNestedTransaction:
            return self.nested

    async def fail_enqueue(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError(
            "db rejected 'Ada Lovelace' user@example.test "
            "id 550e8400-e29b-41d4-a716-446655440000 amount ₦123,000"
        )

    monkeypatch.setattr(notification_outbox, "enqueue_action_notifications", fail_enqueue)
    session = FakeSession()
    cooperative_id, entity_id = uuid.uuid4(), uuid.uuid4()
    caplog.set_level(logging.ERROR, logger=notification_outbox.__name__)

    await notification_outbox.enqueue_action_notifications_safely(
        session, cooperative_id, "payroll_sign_off", entity_id
    )

    assert session.nested.rolled_back
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.ERROR
    assert record.getMessage() == (
        f"action_notification_enqueue_failed event_type=payroll_sign_off entity_id={entity_id} "
        "exception_class=RuntimeError exception_message="
        "db rejected [redacted-value] [redacted-address] id [redacted-id] "
        "amount [redacted-amount]"
    )
    assert record.event_type == "payroll_sign_off"
    assert record.entity_id == str(entity_id)
    assert record.exception_class == "RuntimeError"
    assert record.exception_message == (
        "db rejected [redacted-value] [redacted-address] id [redacted-id] amount [redacted-amount]"
    )
    assert "error_code" not in record.__dict__
    assert "Ada Lovelace" not in caplog.text
    assert "user@example.test" not in caplog.text
    assert "550e8400-e29b-41d4-a716-446655440000" not in caplog.text
    assert "₦123,000" not in caplog.text
    assert len(notification_outbox._safe_exception_message(RuntimeError("x" * 250))) == 200
    assert notification_outbox._safe_exception_message(RuntimeError()) == ""


async def _resolved(
    recipients: list[eligibility.ActionRecipient],
) -> list[eligibility.ActionRecipient]:
    return recipients
