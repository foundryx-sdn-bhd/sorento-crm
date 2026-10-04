"""HTTP-level tests for the two requestor-picker endpoints
(PLAN-requested-by-contact-routing.md C1/C4/C5):

  GET /api/v1/master-data/respond-contacts/requestor-select   (internal, JWT/API key)
  GET /api/v1/public/portal/requestor-options                 (portal, token)

Auth denial (401) + happy path, including the portal endpoint's D3 guarantee
that the submitting contact is always an option even with no flagged segment.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves circular-import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.models.access import MarketSegment, RespondContact, respond_contact_market_segments
from app.models.portal import PortalToken
from tests._pg_fixture import blank_session


@pytest.fixture
def client():
    from app.database import get_db

    with blank_session() as db:
        def _override_get_db():
            yield db

        app.dependency_overrides[get_db] = _override_get_db
        try:
            with TestClient(app) as c:
                yield c, db
        finally:
            app.dependency_overrides.clear()


def _segment(db, code="PROJECT") -> None:
    db.add(MarketSegment(code=code, name=code, is_active=True, is_requestor_selectable=True))
    db.commit()


def _contact(db, *, name, segments=()) -> str:
    c = RespondContact(id=str(uuid.uuid4()), phone_number=f"+6011{uuid.uuid4().hex[:8]}", name=name)
    db.add(c)
    db.flush()
    for code in segments:
        db.execute(
            respond_contact_market_segments.insert().values(contact_id=c.id, segment_code=code)
        )
    db.commit()
    return c.id


# --------------------------------------------------------------------------
# Internal (JWT/API key) endpoint
# --------------------------------------------------------------------------


def test_internal_requestor_select_requires_auth(client):
    c, db = client
    res = c.get("/api/v1/master-data/respond-contacts/requestor-select")
    assert res.status_code == 401


def test_internal_requestor_select_happy_path(client):
    from app.dependencies import get_current_user_or_api_key

    c, db = client
    _segment(db)
    eric = _contact(db, name="CONTACT Y", segments=["PROJECT"])

    app.dependency_overrides[get_current_user_or_api_key] = lambda: {
        "id": str(uuid.uuid4()),
        "email": "staff@test.com",
    }
    try:
        res = c.get("/api/v1/master-data/respond-contacts/requestor-select")
        assert res.status_code == 200, res.text
        body = res.json()
        assert [i["id"] for i in body["items"]] == [eric]
        assert set(body["items"][0].keys()) == {"id", "name"}
    finally:
        app.dependency_overrides.pop(get_current_user_or_api_key, None)


def test_internal_requestor_select_include_ids(client):
    """`include_ids` keeps a form's own submitter / saved requestor selectable
    even when they sit outside a flagged segment - so an edit never silently
    blanks the field."""
    from app.dependencies import get_current_user_or_api_key
    from app.models.procurement import StockInquiry

    c, db = client
    contact_x = _contact(db, name="CONTACT X Submitter")  # no segments
    db.add(
        StockInquiry(
            id=str(uuid.uuid4()),
            inquiry_number="SEL-SI-1",
            status="new",
            contact_id=contact_x,
        )
    )
    db.commit()

    app.dependency_overrides[get_current_user_or_api_key] = lambda: {
        "id": str(uuid.uuid4()),
        "email": "staff@test.com",
    }
    try:
        res = c.get(
            "/api/v1/master-data/respond-contacts/requestor-select",
            params={"include_ids": contact_x},
        )
        assert res.status_code == 200, res.text
        assert [i["id"] for i in res.json()["items"]] == [contact_x]
    finally:
        app.dependency_overrides.pop(get_current_user_or_api_key, None)


def test_internal_requestor_select_include_ids_cannot_resolve_arbitrary_contact(client):
    """An id not referenced by ANY form row is dropped: otherwise the endpoint is
    an id -> contact-name lookup for every contact in the CRM (code-review S7)."""
    from app.dependencies import get_current_user_or_api_key

    c, db = client
    stranger = _contact(db, name="Unrelated Person")  # no segments, no form row

    app.dependency_overrides[get_current_user_or_api_key] = lambda: {
        "id": str(uuid.uuid4()),
        "email": "staff@test.com",
    }
    try:
        res = c.get(
            "/api/v1/master-data/respond-contacts/requestor-select",
            params={"include_ids": stranger},
        )
        assert res.status_code == 200, res.text
        assert res.json()["items"] == []
    finally:
        app.dependency_overrides.pop(get_current_user_or_api_key, None)


# --------------------------------------------------------------------------
# Portal (token) endpoint
# --------------------------------------------------------------------------


def _token(db, contact_id: str, space_id="space-1") -> str:
    t = PortalToken(
        id=str(uuid.uuid4()),
        token=f"tok-{uuid.uuid4().hex}",
        contact_id=contact_id,
        space_id=space_id,
        expires_at=datetime.utcnow() + timedelta(days=30),
        verified_at=datetime.utcnow() - timedelta(minutes=5),
    )
    db.add(t)
    db.commit()
    return t.token


def test_portal_requestor_options_requires_a_token(client):
    c, _ = client
    res = c.get("/api/v1/public/portal/requestor-options")
    assert res.status_code == 401


def test_portal_requestor_options_always_includes_the_submitter(client):
    """D3: the submitting contact is ALWAYS an option, even belonging to no
    flagged segment -- self-service can never be blocked."""
    c, db = client
    contact_x = _contact(db, name="CONTACT X Submitter")  # no segments
    token = _token(db, contact_x)

    res = c.get(
        "/api/v1/public/portal/requestor-options", headers={"X-Portal-Token": token}
    )
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert contact_x in [i["id"] for i in items]


def test_portal_requestor_options_names_only(client):
    c, db = client
    _segment(db)
    eric = _contact(db, name="CONTACT Y", segments=["PROJECT"])
    contact_x = _contact(db, name="CONTACT X Submitter")
    token = _token(db, contact_x)

    res = c.get(
        "/api/v1/public/portal/requestor-options", headers={"X-Portal-Token": token}
    )
    assert res.status_code == 200, res.text
    for item in res.json()["items"]:
        assert set(item.keys()) == {"id", "name"}
    ids = {i["id"] for i in res.json()["items"]}
    assert {eric, contact_x} <= ids
