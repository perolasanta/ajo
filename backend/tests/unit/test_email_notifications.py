"""No-database contract tests for staff action emails."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.core.config import Settings
from app.domains.notifications import eligibility
from app.domains.notifications import outbox as notification_outbox
from app.worker.email_sender import build_action_email

pytestmark = pytest.mark.unit


EVENT_ROLES = {
    "payroll_sign_off": "admin",
    "disbursement_authorization": "admin",
    "role_change_approval": "admin",
    "loan_disputed": "treasurer",
}


@pytest.mark.parametrize(("event_type", "allowed_role"), EVENT_ROLES.items())
def test_each_event_unions_roles_but_excludes_its_requester(
    event_type: str, allowed_role: str
) -> None:
    requester = uuid.uuid4()
    single_role = uuid.uuid4()
    multiple_roles = uuid.uuid4()
    unrelated = uuid.uuid4()
    roles = {
        requester: {allowed_role},
        single_role: {allowed_role},
        multiple_roles: {"admin", "treasurer"},
        unrelated: {"officer"},
    }

    recipients = eligibility.eligible_recipients_for_action(
        event_type, excluded_user_ids={requester}, roles_by_user=roles
    )

    expected = {user_id for user_id in (single_role, multiple_roles) if allowed_role in roles[user_id]}
    assert set(recipients) == expected
    assert len(recipients) == len(expected)
    assert requester not in recipients


def test_role_add_target_is_never_an_email_recipient() -> None:
    proposer = uuid.uuid4()
    target = uuid.uuid4()
    independent_admin = uuid.uuid4()
    roles = {proposer: {"admin"}, target: {"admin"}, independent_admin: {"admin"}}

    recipients = eligibility.eligible_recipients_for_action(
        "role_change_approval",
        excluded_user_ids={proposer, target},
        roles_by_user=roles,
    )

    assert recipients == (independent_admin,)


@pytest.mark.parametrize("event_type", EVENT_ROLES)
def test_email_content_is_one_generic_sentence_with_public_link(event_type: str) -> None:
    settings = Settings(
        app_env="development",
        app_name="Ajo",
        public_base_url="https://ajo.example.test",
    )

    message = build_action_email(event_type, settings)

    assert message.subject.startswith("Action needed in Ajo: ")
    assert message.text.count(".") == 1
    assert "https://ajo.example.test/" in message.text
    assert message.html.count("<a ") == 1
    for forbidden in ("Ada Lovelace", "₦1,234", "member_no", "loan amount", "Audit Admin"):
        assert forbidden not in message.subject
        assert forbidden not in message.text
        assert forbidden not in message.html


@pytest.mark.parametrize("event_type", EVENT_ROLES)
async def test_email_enqueue_is_idempotent_and_uses_only_eligible_users(
    event_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cooperative_id, entity_id = uuid.uuid4(), uuid.uuid4()
    requester, admin_one, admin_two, treasurer = (uuid.uuid4() for _ in range(4))
    role = EVENT_ROLES[event_type]
    recipients = [
        eligibility.ActionRecipient(event_type, entity_id, admin_one),
        eligibility.ActionRecipient(event_type, entity_id, admin_two),
    ]
    if role == "treasurer":
        recipients.append(eligibility.ActionRecipient(event_type, entity_id, treasurer))
    monkeypatch.setattr(
        notification_outbox,
        "get_eligible_action_recipients",
        lambda *args, **kwargs: _resolved(recipients),
    )
    monkeypatch.setattr(notification_outbox, "encrypt_payload", lambda payload: "encrypted")

    class FakeSession:
        def __init__(self) -> None:
            self.rows: dict[tuple[object, ...], dict[str, Any]] = {}

        async def execute(self, statement: Any, params: dict[str, Any]) -> None:
            assert "ON CONFLICT" in str(statement).upper()
            key = (
                params["cid"],
                params["recipient_user_id"],
                params["kind"],
                params["entity_id"],
            )
            self.rows.setdefault(key, params)

    session = FakeSession()
    await notification_outbox.enqueue_action_email_notifications(
        session, cooperative_id, event_type, entity_id
    )
    await notification_outbox.enqueue_action_email_notifications(
        session, cooperative_id, event_type, entity_id
    )

    assert {key[1] for key in session.rows} == {item.user_id for item in recipients}
    assert len(session.rows) == len(recipients)
    assert all(key[0] == cooperative_id and key[2:] == (event_type, entity_id) for key in session.rows)
    assert requester not in {key[1] for key in session.rows}


async def _resolved(items: list[eligibility.ActionRecipient]) -> list[eligibility.ActionRecipient]:
    return items
