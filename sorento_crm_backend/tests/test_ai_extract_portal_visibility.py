"""AI extract gates a `portal.<kind>` form_key the same way the generic
portal submission routes do (SEC4, r4/AC-R7): a hidden kind 403s
FORM_TYPE_NOT_VISIBLE on both POST /ai-extract and GET /ai-extract/schema.
`master.*` keys have no portal kind to check against and are issue #964,
left alone.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from tests._pg_fixture import blank_session
from tests._portal_grant import grant_portal_forms, link_contact_company

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def api():
    from app.database import get_db

    with blank_session() as db:

        def _override_get_db():
            yield db

        app.dependency_overrides[get_db] = _override_get_db
        try:
            yield db
        finally:
            app.dependency_overrides.clear()


def _contact(db):
    from app.models.access import RespondContact

    contact = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"+6011{uuid.uuid4().hex[:8]}",
        name="ZZT-ai-extract-visibility",
    )
    db.add(contact)
    db.flush()
    link_contact_company(db, contact.id)
    return contact


def _token(db, contact):
    from app.models.portal import PortalToken

    token = PortalToken(
        id=str(uuid.uuid4()),
        token=f"ZZT-tok-{uuid.uuid4().hex}",
        contact_id=contact.id,
        space_id="ZZT-ai-extract-space",
        expires_at=datetime.utcnow() + timedelta(days=30),
        verified_at=datetime.utcnow() - timedelta(minutes=5),
    )
    db.add(token)
    db.commit()
    return token


def _hide(db, contact_id: str, kind: str) -> None:
    from app.models.price_tag import ContactPortalFormOverride

    db.add(
        ContactPortalFormOverride(
            id=str(uuid.uuid4()),
            contact_id=contact_id,
            form_type=kind,
            is_enabled=False,
        )
    )
    db.flush()


def _assert_gated(response) -> None:
    assert response.status_code == 403, response.text
    assert response.json().get("code") == "FORM_TYPE_NOT_VISIBLE", response.text


def test_post_extract_hidden_kind_is_403(api):
    db = api
    contact = _contact(db)
    token = _token(db, contact)
    _hide(db, contact.id, "complaint")

    with TestClient(app) as c:
        response = c.post(
            "/api/v1/public/portal/ai-extract",
            data={"form_key": "portal.complaint"},
            files=[("files", ("zzt.png", _TINY_PNG, "image/png"))],
            headers={"X-Portal-Token": token.token},
        )

    _assert_gated(response)


def test_get_schema_hidden_kind_is_403(api):
    db = api
    contact = _contact(db)
    token = _token(db, contact)
    _hide(db, contact.id, "complaint")

    with TestClient(app) as c:
        response = c.get(
            "/api/v1/public/portal/ai-extract/schema",
            params={"form_key": "portal.complaint"},
            headers={"X-Portal-Token": token.token},
        )

    _assert_gated(response)


def test_visible_kind_is_unaffected(api, monkeypatch):
    """A regression guard: granting the kind (rather than hiding it) still
    reaches the service, same as before this gate existed."""
    from app.api.v1.public import ai_extract as route_module
    from app.services.ai_extract.extract_service import ExtractResult

    db = api
    contact = _contact(db)
    token = _token(db, contact)
    grant_portal_forms(db, contact.id, ["price_tag_request"])

    monkeypatch.setattr(
        route_module.AIExtractService, "extract", lambda *a, **k: ExtractResult(values={})
    )

    with TestClient(app) as c:
        response = c.post(
            "/api/v1/public/portal/ai-extract",
            data={"form_key": "portal.price_tag_request"},
            files=[("files", ("zzt.png", _TINY_PNG, "image/png"))],
            headers={"X-Portal-Token": token.token},
        )

    assert response.status_code == 200, response.text
