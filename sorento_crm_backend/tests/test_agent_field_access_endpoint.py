"""GET/PUT /api/v1/user-management/access-agents/{id}/field-access.

The admin surface for "which fields may this agent reveal". Two things carry it:

* **The GET is a complete checklist**, built from the code registry rather than
  from whatever rows exist. A field that has no row must still appear, unticked -
  a field an admin cannot see is a field they cannot grant, which looks exactly
  like the feature being broken.
* **The PUT touches only what was sent.** Not replace-all: the screen may be
  showing one resource while a second admin edits another, and a blanket delete
  would silently revoke fields nobody looked at.

Plus the guard that makes the ticks honest - a tick on a field the gate does not
consult would be a switch wired to nothing, so it is rejected rather than stored.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_current_user, get_db
from app.main import app
from app.models.access import AccessAgent, AgentFieldAccess
from app.services.field_access import GATED_FIELDS
from tests._pg_fixture import blank_session

OWNER = "incoming_stock_enquiries"


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


@pytest.fixture
def client(db, monkeypatch):
    """A caller authenticated against the same session the assertions read.

    GET .../field-access requires user_management.access_agents.view
    (PLAN-user-management-read-gates.md row 10) and PUT .../field-access requires
    user_management.access_agents.edit (PLAN-access-agents-write-gates.md row 5).
    Grant both here so this file keeps testing the field-access feature rather
    than re-deriving the permission gates on every test - the gates have their
    own dedicated tests in tests/test_user_management_read_gates.py and
    tests/test_access_agents_write_gates.py.
    """
    from app.services.user_service import UserPermissionService

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: {
        "id": str(uuid.uuid4()),
        "email": "zzt-admin@example.com",
    }
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug
        in {"user_management.access_agents.view", "user_management.access_agents.edit"},
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def agent(db) -> AccessAgent:
    row = AccessAgent(
        id=str(uuid.uuid4()), code=OWNER, name="Incoming stock enquiries", is_active=True
    )
    db.add(row)
    db.flush()
    return row


def _url(agent) -> str:
    return f"/api/v1/user-management/access-agents/{agent.id}/field-access"


def test_the_get_lists_every_gated_field_even_with_no_rows(client, agent):
    """A field with no row is unticked, not missing - you cannot grant what the
    screen never shows you."""
    body = client.get(_url(agent)).json()

    assert body["agent_code"] == OWNER
    assert len(body["fields"]) == len(GATED_FIELDS["incoming_stock"])
    assert all(f["is_allowed"] is False for f in body["fields"])


def test_the_get_labels_fields_in_words_an_admin_recognises(client, agent):
    """`loc`, `etc_date` and `coa_permit_no` are unguessable as column names, and
    the decision being made is a business one."""
    labels = {f["field_key"]: f["label"] for f in client.get(_url(agent)).json()["fields"]}

    assert labels["eta_delay_date"] == "ETA delay"
    assert labels["inspection_date"] == "CIDB inspection"
    assert labels["loc"] == "Location"


def test_a_tick_is_persisted_and_reflected_back(client, agent, db):
    response = client.put(
        _url(agent),
        json={
            "fields": [
                {
                    "resource": "incoming_stock",
                    "field_key": "eta_delay_date",
                    "is_allowed": True,
                }
            ]
        },
    )

    assert response.status_code == 200
    allowed = {f["field_key"] for f in response.json()["fields"] if f["is_allowed"]}
    assert allowed == {"eta_delay_date"}

    row = (
        db.query(AgentFieldAccess)
        .filter(
            AgentFieldAccess.agent_code == OWNER,
            AgentFieldAccess.field_key == "eta_delay_date",
            AgentFieldAccess.contact_id.is_(None),
        )
        .one()
    )
    assert row.is_allowed is True
    assert row.created_by == "zzt-admin@example.com", "attribute the grant to someone"
    assert row.updated_by == "zzt-admin@example.com"


def test_a_grant_on_a_pre_seeded_row_is_still_attributed(client, agent, db):
    """Every row is pre-seeded denied by the bootstrap, so a real grant always takes
    the UPDATE branch. Attributing only on insert left every grant anonymous - the
    live save through the UI wrote a NULL."""
    db.add(
        AgentFieldAccess(
            id=str(uuid.uuid4()),
            agent_code=OWNER,
            resource="incoming_stock",
            field_key="eta_delay_date",
            is_allowed=False,
        )
    )
    db.flush()

    client.put(
        _url(agent),
        json={"fields": [{"resource": "incoming_stock", "field_key": "eta_delay_date", "is_allowed": True}]},
    )

    row = (
        db.query(AgentFieldAccess)
        .filter(AgentFieldAccess.field_key == "eta_delay_date")
        .one()
    )
    assert row.is_allowed is True
    assert row.updated_by == "zzt-admin@example.com"


def test_a_second_put_leaves_untouched_fields_alone(client, agent):
    """Not replace-all: two admins editing different rows must not revoke each
    other's work."""
    client.put(
        _url(agent),
        json={"fields": [{"resource": "incoming_stock", "field_key": "eta_delay_date", "is_allowed": True}]},
    )
    body = client.put(
        _url(agent),
        json={"fields": [{"resource": "incoming_stock", "field_key": "gatepass_date", "is_allowed": True}]},
    ).json()

    allowed = {f["field_key"] for f in body["fields"] if f["is_allowed"]}
    assert allowed == {"eta_delay_date", "gatepass_date"}


def test_a_tick_can_be_taken_back(client, agent):
    client.put(
        _url(agent),
        json={"fields": [{"resource": "incoming_stock", "field_key": "eta_delay_date", "is_allowed": True}]},
    )
    body = client.put(
        _url(agent),
        json={"fields": [{"resource": "incoming_stock", "field_key": "eta_delay_date", "is_allowed": False}]},
    ).json()

    assert not [f for f in body["fields"] if f["is_allowed"]]


def test_an_ungated_field_is_rejected_not_stored(client, agent):
    """A tick on a field the gate never consults is a switch wired to nothing."""
    response = client.put(
        _url(agent),
        json={"fields": [{"resource": "incoming_stock", "field_key": "shipment_number", "is_allowed": True}]},
    )

    assert response.status_code == 422
    assert "not a gated field" in response.text


def test_a_field_owned_by_another_agent_is_rejected(client, db):
    """Otherwise any agent could be handed any field, and the AGENT_NOT_ASSIGNED
    denial would start naming the wrong agent to grant."""
    stranger = AccessAgent(
        id=str(uuid.uuid4()), code="zzt_other_agent", name="Other", is_active=True
    )
    db.add(stranger)
    db.flush()

    response = client.put(
        _url(stranger),
        json={"fields": [{"resource": "incoming_stock", "field_key": "eta_delay_date", "is_allowed": True}]},
    )

    assert response.status_code == 422
    assert "belongs to agent" in response.text


def test_an_agent_that_owns_no_gated_fields_gets_an_empty_list(client, db):
    stranger = AccessAgent(
        id=str(uuid.uuid4()), code="zzt_other_agent", name="Other", is_active=True
    )
    db.add(stranger)
    db.flush()

    body = client.get(_url(stranger)).json()
    assert body["fields"] == []


def test_a_missing_agent_is_a_404_not_a_500(client):
    response = client.get(
        f"/api/v1/user-management/access-agents/{uuid.uuid4()}/field-access"
    )
    assert response.status_code == 404


def test_a_per_contact_override_is_reported_separately(client, agent, db):
    """The ticks alone would not explain why one dealer sees something different."""
    from app.models.access import RespondContact

    contact = RespondContact(
        id="zzt-contact-1", phone_number="+60123456789", name="Ah Seng Hardware"
    )
    db.add(contact)
    db.flush()

    client.put(
        _url(agent),
        json={
            "fields": [
                {"resource": "incoming_stock", "field_key": "eta_delay_date", "is_allowed": True},
                {
                    "resource": "incoming_stock",
                    "field_key": "eta_delay_date",
                    "is_allowed": False,
                    "contact_id": contact.id,
                },
            ]
        },
    )

    body = client.get(_url(agent)).json()

    # The agent-wide tick stays on; the exception is listed apart from it.
    assert [f for f in body["fields"] if f["is_allowed"]][0]["field_key"] == "eta_delay_date"
    assert body["overrides"] == [
        {
            "resource": "incoming_stock",
            "field_key": "eta_delay_date",
            "label": "ETA delay",
            "contact_id": contact.id,
            "contact_name": "Ah Seng Hardware",
            "is_allowed": False,
        }
    ]


# ------------------------------------------------- per-contact exceptions


def _contact(db, name="Ah Seng Hardware", cid="zzt-contact-1"):
    from app.models.access import RespondContact

    row = RespondContact(id=cid, phone_number=f"+6012{abs(hash(cid)) % 10**7:07d}", name=name)
    db.add(row)
    db.flush()
    return row


def test_scoping_to_a_contact_shows_the_inherited_value_alongside_the_override(client, agent, db):
    """An admin deciding "should this dealer see the gatepass" cannot tell an
    explicit deny from an untouched default unless both are on the row."""
    contact = _contact(db)
    client.put(
        _url(agent),
        json={"fields": [{"resource": "incoming_stock", "field_key": "estimated_arrival_date", "is_allowed": True}]},
    )

    body = client.get(f"{_url(agent)}?contact_id={contact.id}").json()
    by_key = {f["field_key"]: f for f in body["fields"]}

    assert by_key["estimated_arrival_date"]["is_allowed"] is True, "the agent-wide default"
    assert by_key["estimated_arrival_date"]["override"] is None, "this contact has no exception"
    assert by_key["estimated_arrival_date"]["effective"] is True, "so it inherits the default"


def test_an_override_can_grant_a_contact_more_than_the_agent(client, agent, db):
    """The question this whole surface answers: allow ONE contact a field the other
    52 do not get."""
    contact = _contact(db)

    body = client.put(
        f"{_url(agent)}?contact_id={contact.id}",
        json={
            "fields": [
                {
                    "resource": "incoming_stock",
                    "field_key": "gatepass_date",
                    "is_allowed": True,
                    "contact_id": contact.id,
                }
            ]
        },
    ).json()

    row = {f["field_key"]: f for f in body["fields"]}["gatepass_date"]
    assert row["is_allowed"] is False, "the agent still denies it to everyone else"
    assert row["override"] is True
    assert row["effective"] is True


def test_clearing_an_override_returns_the_contact_to_following_the_agent(client, agent, db):
    """"Explicitly denied" and "inherits a denial" are different intentions, so
    undoing the first must not leave the second pretending to be explicit."""
    contact = _contact(db)
    client.put(
        _url(agent),
        json={"fields": [{"resource": "incoming_stock", "field_key": "estimated_arrival_date", "is_allowed": True}]},
    )
    client.put(
        f"{_url(agent)}?contact_id={contact.id}",
        json={
            "fields": [
                {
                    "resource": "incoming_stock",
                    "field_key": "estimated_arrival_date",
                    "is_allowed": False,
                    "contact_id": contact.id,
                }
            ]
        },
    )

    body = client.put(
        f"{_url(agent)}?contact_id={contact.id}",
        json={
            "fields": [
                {
                    "resource": "incoming_stock",
                    "field_key": "estimated_arrival_date",
                    "is_allowed": None,
                    "contact_id": contact.id,
                }
            ]
        },
    ).json()

    row = {f["field_key"]: f for f in body["fields"]}["estimated_arrival_date"]
    assert row["override"] is None, "the exception is gone, not flipped"
    assert row["effective"] is True, "so it follows the agent again"
    assert body["overrides"] == []


def test_clearing_without_a_contact_is_rejected(client, agent):
    """A null agent-wide would mean "deny by absence", which is_allowed=False
    already says - accepting it would give two spellings of one state."""
    response = client.put(
        _url(agent),
        json={"fields": [{"resource": "incoming_stock", "field_key": "estimated_arrival_date", "is_allowed": None}]},
    )
    assert response.status_code == 422
    assert "per-contact override" in response.text


def test_the_unscoped_view_does_not_leak_override_keys(client, agent, db):
    """The agent-level screen shows agent-level truth; adding `effective` there
    would silently mean "effective for nobody in particular"."""
    contact = _contact(db)
    client.put(
        f"{_url(agent)}?contact_id={contact.id}",
        json={
            "fields": [
                {
                    "resource": "incoming_stock",
                    "field_key": "estimated_arrival_date",
                    "is_allowed": True,
                    "contact_id": contact.id,
                }
            ]
        },
    )

    body = client.get(_url(agent)).json()
    assert all("override" not in f and "effective" not in f for f in body["fields"])
    # ...but the exception is still visible on that screen, as an override.
    assert len(body["overrides"]) == 1
