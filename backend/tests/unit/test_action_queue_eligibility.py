from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.core.config import Settings
from app.domains.notifications import eligibility, outbox
from app.worker.email_sender import build_action_email

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows


class _CandidatesSession:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.queries = 0

    async def execute(self, *_args: Any, **_kwargs: Any) -> _Result:
        self.queries += 1
        return _Result(self.rows)


def _settings() -> Settings:
    return Settings(
        app_env="development",
        public_base_url="http://ajo.example.test",
        app_name="Ajo",
    )


async def test_shared_action_items_match_role_and_exclude_requester(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    own_request = uuid.uuid4()
    other_request = uuid.uuid4()
    candidates = [
        {
            "event_type": eligibility.WITHDRAWAL_APPROVAL,
            "entity_id": own_request,
            "excluded_user_ids": [user_id],
        },
        {
            "event_type": eligibility.WITHDRAWAL_APPROVAL,
            "entity_id": other_request,
            "excluded_user_ids": [],
        },
    ]
    session = _CandidatesSession(candidates)

    async def all_candidates(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return candidates

    monkeypatch.setattr(eligibility, "get_all_action_candidates", all_candidates)
    items = await eligibility.get_action_items_for_user(
        session, uuid.uuid4(), user_id, {"treasurer", "officer"}
    )
    assert items["withdrawal_requests"] == (other_request,)
    assert session.queries == 0
    officer_items = await eligibility.get_action_items_for_user(
        session, uuid.uuid4(), user_id, {"officer"}
    )
    assert officer_items.get("withdrawal_requests", ()) == ()


def test_action_alerts_name_kind_and_direct_link_without_business_data() -> None:
    settings = _settings()
    entity_id = uuid.uuid4()
    payload = outbox.build_action_payload(
        eligibility.PAYROLL_SIGN_OFF, settings, entity_id=entity_id
    )
    email = build_action_email(eligibility.PAYROLL_SIGN_OFF, payload, settings)
    assert email.subject == "Action needed in Ajo: payroll sign-off"
    assert "A payroll run needs your sign-off" in email.text
    assert payload["url"].endswith(f"/payroll/runs/{entity_id}")
    for text in (email.subject, email.text, email.html):
        assert "Ada" not in text
        assert "₦" not in text
        assert "100" not in text


def test_withdrawal_alert_links_to_member_request_anchor() -> None:
    member_id = uuid.uuid4()
    request_id = uuid.uuid4()
    payload = outbox.build_action_payload(
        eligibility.WITHDRAWAL_APPROVAL,
        _settings(),
        entity_id=request_id,
        member_id=member_id,
    )
    assert payload["url"] == (
        f"http://ajo.example.test/members/{member_id}?withdrawal_request={request_id}"
    )
    assert payload["message"] == "A withdrawal request needs your approval."


def test_all_action_alerts_use_fixed_kind_sentences_and_entity_links() -> None:
    settings = _settings()
    for event_type, spec in eligibility.ACTION_SPECS.items():
        entity_id = uuid.uuid4()
        member_id = uuid.uuid4() if event_type == eligibility.WITHDRAWAL_APPROVAL else None
        payload = outbox.build_action_payload(
            event_type, settings, entity_id=entity_id, member_id=member_id
        )
        assert payload["action_kind"] == spec.action_kind
        assert payload["message"] == spec.sentence
        assert str(entity_id) in payload["url"]
        assert "Ada" not in payload["message"]
        assert "₦" not in payload["message"]


async def test_shared_candidate_collection_is_one_tenant_scoped_query() -> None:
    cooperative_id = uuid.uuid4()
    request_id = uuid.uuid4()
    requester_id = uuid.uuid4()
    candidates = [
        {
            "event_type": eligibility.WITHDRAWAL_APPROVAL,
            "entity_id": request_id,
            "excluded_user_ids": [requester_id],
        }
    ]

    class Session:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def execute(self, query: Any, params: dict[str, Any]) -> _Result:
            self.calls.append((str(query), params))
            return _Result(candidates)

    session = Session()
    items = await eligibility.get_action_items_for_user(
        session, cooperative_id, requester_id, {"treasurer", "officer"}
    )
    assert len(session.calls) == 1
    assert session.calls[0][1] == {"cooperative_id": cooperative_id}
    assert "UNION ALL" in session.calls[0][0]
    assert items.get("withdrawal_requests", ()) == ()
