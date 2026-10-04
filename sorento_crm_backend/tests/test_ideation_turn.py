"""Shared-service plumbing kept from the ``ideate`` draft flow, plus the turn endpoint.

The multi-turn draft turn (``handle_turn``) was removed by IDEATION-CAPTURE C2; the one-message
turn is covered by ``tests/test_ideation_capture_turn.py``. What stays here:

- ``call_create_idea`` wraps httpx errors into ``IdeationServiceError`` (AC-19 layer);
- ``_get_contact_row`` reads the submitter tier by ``sort_order`` then ``code`` (AC-1207);
- **AC-20** - the endpoint writes an ``integration_log`` on success AND failure;
- the response schema keeps ``offered_media``.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

import app.services.ideation_turn_service as svc
from app.models.access import ContactAccessType, RespondContact, respond_contact_access_types
from app.services.ideation_turn_service import IdeationServiceError
from tests._external_auth import external_permissions_granted
from tests._pg_fixture import blank_session




# --------------------------------------------------------------------------- #
# call_create_idea wraps httpx errors into IdeationServiceError (AC-19 layer)  #
# --------------------------------------------------------------------------- #
def test_call_create_idea_wraps_httpx_error(monkeypatch):
    def _boom(self, url, **kw):  # noqa: ANN001
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx.Client, "post", _boom)
    with pytest.raises(IdeationServiceError):
        svc.call_create_idea("https://shared.test", "k", {"product_id": "p"})


# --------------------------------------------------------------------------- #
# Reviewer Should fix 3 (round 2): the HTTP status -> `status_code` mapping    #
# itself has to be driven through an actual `httpx.HTTPStatusError`, not      #
# monkeypatched directly - otherwise deleting the mapping code leaves every   #
# sweep test green while production silently retries every 4xx forever.      #
# --------------------------------------------------------------------------- #
def test_call_create_idea_maps_http_status_error_status_code(monkeypatch):
    def _respond(self, url, **kw):  # noqa: ANN001
        return httpx.Response(404, json={"error": "not found"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", _respond)
    with pytest.raises(IdeationServiceError) as exc_info:
        svc.call_create_idea("https://shared.test", "k", {"product_id": "p"})
    assert exc_info.value.status_code == 404


# --------------------------------------------------------------------------- #
# AC-20 - endpoint writes an integration_log on success AND failure           #
# --------------------------------------------------------------------------- #
@pytest.fixture
def api_client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.dependencies import get_db, get_external_api_user

    logs: list = []

    def _capture_log(self, log_data, request_payload_dict=None):  # noqa: ANN001
        logs.append(log_data)
        return None

    monkeypatch.setattr(
        "app.api.v1.external.ideation.IntegrationLogService.create_integration_log",
        _capture_log,
    )
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_external_api_user] = lambda: {"id": "system"}
    try:
        # Authorization is out of scope here (get_db is stubbed to None);
        # enforcement is covered by test_external_permission_guard/_coverage.
        with external_permissions_granted():
            yield TestClient(app), logs, monkeypatch
    finally:
        app.dependency_overrides.clear()


_TURN_URL = "/api/v1/external/ideation/turn"
_TURN_BODY = {"respond_io_id": "rio-1", "message_text": "idea: bulk-tag complaints"}


def test_endpoint_logs_on_success(api_client):
    client, logs, mp = api_client
    mp.setattr(
        "app.api.v1.external.ideation.handle_capture_turn",
        lambda *a, **k: {"status": "collecting", "reply_text": "ok", "session_vars": {}},
    )
    resp = client.post(_TURN_URL, json=_TURN_BODY)
    assert resp.status_code == 200
    assert resp.json()["status"] == "collecting"
    assert len(logs) == 1
    assert logs[0].status == "success"
    assert logs[0].direction == "inbound"
    assert logs[0].external_reference == "rio-1"


@pytest.mark.parametrize(
    "body,expected",
    [
        ({**_TURN_BODY, "is_test": True}, True),
        (_TURN_BODY, False),
    ],
    ids=["is_test-true", "absent-defaults-false"],
)
def test_endpoint_forwards_is_test_to_the_service(api_client, body, expected):
    """AC-4 (#1179): the flag the MCP tool posts reaches `handle_capture_turn`."""
    client, _logs, mp = api_client
    seen: list = []

    def _capture(*a, **k):  # noqa: ANN001
        seen.append(k)
        return {"status": "collecting", "reply_text": "ok", "session_vars": {}}

    mp.setattr("app.api.v1.external.ideation.handle_capture_turn", _capture)
    resp = client.post(_TURN_URL, json=body)
    assert resp.status_code == 200
    assert seen[0]["is_test"] is expected


def test_endpoint_logs_on_failure(api_client):
    client, logs, mp = api_client

    def _boom(*a, **k):  # noqa: ANN001
        raise RuntimeError("kaboom")

    mp.setattr("app.api.v1.external.ideation.handle_capture_turn", _boom)
    resp = client.post(_TURN_URL, json=_TURN_BODY)
    assert resp.status_code == 500
    assert len(logs) == 1
    assert logs[0].status == "failed"
    assert logs[0].status_code == 500



# --------------------------------------------------------------------------- #
# Reviewer Should fix 7 (round 1, PR #1222 at 720bb8f5): the submitter_tier    #
# SQL (join + ORDER BY sort_order, code) must actually run against Postgres,  #
# not just get set on a monkeypatched contact row (AC-1207).                  #
# --------------------------------------------------------------------------- #
def test_submitter_tier_sql_orders_by_sort_order_then_code():
    with blank_session() as db:
        db.add_all(
            [
                ContactAccessType(code="dealer", name="Dealer", sort_order=2),
                ContactAccessType(code="end_user", name="End user", sort_order=1),
            ]
        )
        db.flush()
        contact = RespondContact(
            id=str(uuid.uuid4()),
            respond_io_id=str(uuid.uuid4()),
            phone_number=f"+601{uuid.uuid4().int % 10**8:08d}",
            session_vars={},
        )
        db.add(contact)
        db.flush()
        db.execute(
            respond_contact_access_types.insert().values(
                [
                    {"contact_id": contact.id, "access_type_code": "dealer"},
                    {"contact_id": contact.id, "access_type_code": "end_user"},
                ]
            )
        )
        db.commit()

        state = svc._get_contact_row(db, contact.respond_io_id)
        assert state.submitter_tier == "end_user"  # lower sort_order wins over dealer

        contact_no_tier = RespondContact(
            id=str(uuid.uuid4()),
            respond_io_id=str(uuid.uuid4()),
            phone_number=f"+601{uuid.uuid4().int % 10**8:08d}",
            session_vars={},
        )
        db.add(contact_no_tier)
        db.commit()

        state_none = svc._get_contact_row(db, contact_no_tier.respond_io_id)
        assert state_none.submitter_tier is None



def test_ideation_turn_response_schema_keeps_offered_media():
    """`response_model` silently drops undeclared fields (repo lesson) - the
    schema must declare `offered_media` or it never reaches n8n/the console."""
    from app.schemas.external.ideation import IdeationTurnResponse

    result = {
        "status": "collecting",
        "reply_text": "x",
        "session_vars": {},
        "offered_media": [{"position": 1, "kind": "image", "url": "u", "filename": "f"}],
    }
    dumped = IdeationTurnResponse(**result).model_dump()
    assert dumped.get("offered_media") == result["offered_media"]

