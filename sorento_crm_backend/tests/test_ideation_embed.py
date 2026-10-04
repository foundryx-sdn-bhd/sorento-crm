"""Slice D - Ideas iframe embed-session mint (SSO) endpoint + service.

Keys back to ``documentation/plans/ideation/ideation-embed-sso-acceptance-criteria.md``:

- **AC-E-2 [BE]** - a logged-in sorento user's request mints a signed assertion
  (resolved embed signing secret + connection id), POSTs it to
  ``{ideation_shared_service_url}/embed/session`` (backend base), and returns
  ``{ iframe_url, token, expires_at }``.
- **AC-E-3 [BE]** - ``iframe_url`` is built from the distinct FE base
  (``ideation_embed_fe_base_url``), NOT the backend base used for the POST.
- **AC-E-4 [BE]** - blank config keeps the feature DORMANT: a clean 4xx (not a 500)
  and no shared-service call.
- **AC-E-12 [BE]** - secrets (signing secret, the minted assertion) are never echoed
  in the response body.

The shared-service ``/embed/session`` handshake is STUBBED here (monkeypatched
``post_embed_session``). DB-driven config resolution is covered in
``test_ideation_embed_config.py``. Live-iframe verification is DEFERRED to the
shared-service embed dependency.
"""
from __future__ import annotations

import httpx
import pytest
from jose import jwt

import app.services.ideation_embed_service as svc
from app.config import settings
from app.services.ideation_embed_service import (
    IdeationEmbedNotConfigured,
    IdeationEmbedUpstreamError,
    create_embed_session,
    mint_embed_assertion,
)


_SIGNING_SECRET = "test-embed-signing-secret-value"
_CONNECTION_ID = "e5407a68-13ff-59f6-a337-408b46ca369b"
_SHARED_URL = "https://shared.test/be"       # backend base (POST /embed/session)
_FE_URL = "https://shared.test"              # FE root (iframe_url) - distinct (AC-E-3)

_USER = {"id": "user-uuid-1", "email": "staff@example.com", "name": "Staff Member"}


@pytest.fixture
def configured(monkeypatch):
    """Populate the ideation embed settings (.env fallback path) so the feature is
    live without a DB workspace row (``db=None`` in the calls below)."""
    monkeypatch.setattr(svc.settings, "ideation_shared_service_url", _SHARED_URL)
    monkeypatch.setattr(svc.settings, "ideation_embed_fe_base_url", _FE_URL)
    monkeypatch.setattr(svc.settings, "ideation_embed_signing_secret", _SIGNING_SECRET)
    monkeypatch.setattr(svc.settings, "ideation_embed_connection_id", _CONNECTION_ID)
    return monkeypatch


# --------------------------------------------------------------------------- #
# AC-E-2 - mint a signed assertion for the logged-in user                      #
# --------------------------------------------------------------------------- #
def test_mint_assertion_is_signed_and_carries_identity():
    token = mint_embed_assertion(_USER, secret=_SIGNING_SECRET, connection_id=_CONNECTION_ID)

    decoded = jwt.decode(
        token,
        _SIGNING_SECRET,
        algorithms=[settings.jwt_algorithm],
        audience="ideation-embed",
    )
    assert decoded["sub"] == "user-uuid-1"
    assert decoded["aud"] == "ideation-embed"
    assert decoded["connection_id"] == _CONNECTION_ID
    assert "exp" in decoded and "iat" in decoded
    # The signing secret is never carried inside the token payload.
    assert "signing_secret" not in decoded


def test_mint_assertion_rejected_by_wrong_secret():
    token = mint_embed_assertion(_USER, secret=_SIGNING_SECRET, connection_id=_CONNECTION_ID)
    with pytest.raises(Exception):
        jwt.decode(token, "wrong-secret", algorithms=[settings.jwt_algorithm], audience="ideation-embed")


def test_mint_assertion_dormant_when_secret_blank():
    with pytest.raises(IdeationEmbedNotConfigured):
        mint_embed_assertion(_USER, secret="", connection_id=_CONNECTION_ID)


# --------------------------------------------------------------------------- #
# AC-E-2/E-3 - create_embed_session posts to backend base, iframe uses fe base #
# --------------------------------------------------------------------------- #
def test_create_embed_session_board(configured, monkeypatch):
    captured: dict = {}

    def _fake_post(base_url, payload):  # noqa: ANN001
        captured["base_url"] = base_url
        captured["payload"] = payload
        return {"token": "embed-token-xyz", "expires_at": "2026-07-20T00:15:00+00:00"}

    monkeypatch.setattr(svc, "post_embed_session", _fake_post)

    result = create_embed_session(None, _USER, idea_id=None)

    # iframe_url is built from the FE base, NOT the backend base (AC-E-3)
    assert result["iframe_url"] == "https://shared.test/embed/ideas"
    assert result["token"] == "embed-token-xyz"
    assert result["expires_at"] == "2026-07-20T00:15:00+00:00"
    # The signed assertion was POSTed to the BACKEND base with the connection id.
    assert captured["base_url"] == _SHARED_URL
    assert captured["payload"]["connection_id"] == _CONNECTION_ID
    assert captured["payload"]["assertion"]  # a minted token, not blank
    assert "idea_id" not in captured["payload"]


def test_create_embed_session_detail(configured, monkeypatch):
    monkeypatch.setattr(
        svc, "post_embed_session",
        lambda base_url, payload: {"token": "t", "expires_at": "x"},
    )
    result = create_embed_session(None, _USER, idea_id="idea-123")
    assert result["iframe_url"] == "https://shared.test/embed/ideas/idea-123"


def test_backend_and_fe_bases_never_collapse(configured, monkeypatch):
    """AC-E-3 explicit: the POST target and the iframe origin path differ."""
    captured: dict = {}

    def _fake_post(base_url, payload):  # noqa: ANN001
        captured["base_url"] = base_url
        return {"token": "t", "expires_at": "x"}

    monkeypatch.setattr(svc, "post_embed_session", _fake_post)
    result = create_embed_session(None, _USER, idea_id=None)
    assert captured["base_url"].rstrip("/") == "https://shared.test/be"
    assert result["iframe_url"].startswith("https://shared.test/embed/ideas")
    assert "/be/embed/ideas" not in result["iframe_url"]


# --------------------------------------------------------------------------- #
# AC-E-4 - dormant when any required field blank                                #
# --------------------------------------------------------------------------- #
def _blank_all(monkeypatch):
    monkeypatch.setattr(svc.settings, "ideation_shared_service_url", None)
    monkeypatch.setattr(svc.settings, "ideation_embed_fe_base_url", None)
    monkeypatch.setattr(svc.settings, "ideation_embed_signing_secret", None)
    monkeypatch.setattr(svc.settings, "ideation_embed_connection_id", None)


def test_dormant_when_url_blank(monkeypatch):
    _blank_all(monkeypatch)
    monkeypatch.setattr(svc.settings, "ideation_embed_fe_base_url", _FE_URL)
    monkeypatch.setattr(svc.settings, "ideation_embed_signing_secret", _SIGNING_SECRET)
    monkeypatch.setattr(svc.settings, "ideation_embed_connection_id", _CONNECTION_ID)
    with pytest.raises(IdeationEmbedNotConfigured):
        create_embed_session(None, _USER, idea_id=None)


def test_dormant_when_fe_base_blank(monkeypatch):
    _blank_all(monkeypatch)
    monkeypatch.setattr(svc.settings, "ideation_shared_service_url", _SHARED_URL)
    monkeypatch.setattr(svc.settings, "ideation_embed_signing_secret", _SIGNING_SECRET)
    monkeypatch.setattr(svc.settings, "ideation_embed_connection_id", _CONNECTION_ID)
    with pytest.raises(IdeationEmbedNotConfigured):
        create_embed_session(None, _USER, idea_id=None)


def test_dormant_when_secret_blank(monkeypatch):
    _blank_all(monkeypatch)
    monkeypatch.setattr(svc.settings, "ideation_shared_service_url", _SHARED_URL)
    monkeypatch.setattr(svc.settings, "ideation_embed_fe_base_url", _FE_URL)
    monkeypatch.setattr(svc.settings, "ideation_embed_connection_id", _CONNECTION_ID)
    with pytest.raises(IdeationEmbedNotConfigured):
        create_embed_session(None, _USER, idea_id=None)


# --------------------------------------------------------------------------- #
# AC-E-4 (mirrored) - shared-service outage → clean upstream error, not a crash #
# --------------------------------------------------------------------------- #
def test_upstream_outage_raises_upstream_error(configured, monkeypatch):
    def _boom(base_url, payload):  # noqa: ANN001
        raise IdeationEmbedUpstreamError("connect timeout")

    monkeypatch.setattr(svc, "post_embed_session", _boom)
    with pytest.raises(IdeationEmbedUpstreamError):
        create_embed_session(None, _USER, idea_id=None)


def test_upstream_missing_token_raises(configured, monkeypatch):
    monkeypatch.setattr(svc, "post_embed_session", lambda base_url, payload: {"expires_at": "x"})
    with pytest.raises(IdeationEmbedUpstreamError):
        create_embed_session(None, _USER, idea_id=None)


def test_post_embed_session_wraps_httpx_error(configured, monkeypatch):
    def _boom(self, url, **kw):  # noqa: ANN001
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx.Client, "post", _boom)
    with pytest.raises(IdeationEmbedUpstreamError):
        svc.post_embed_session(_SHARED_URL, {"connection_id": _CONNECTION_ID, "assertion": "a"})


# --------------------------------------------------------------------------- #
# Endpoint - auth + status-code contract + secrets not echoed                  #
# --------------------------------------------------------------------------- #
@pytest.fixture
def api_client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.dependencies import get_current_user, get_db
    from app.main import app
    from app.services.user_service import UserPermissionService

    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: dict(_USER)
    # The route is gated on ideation.board.view via require_permission; grant it
    # so the existing service-level tests pass through the permission layer.
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: True,
    )
    try:
        yield TestClient(app), monkeypatch
    finally:
        app.dependency_overrides.clear()


_EMBED_URL = "/api/v1/integrations/ideation/embed-session"


def test_endpoint_returns_session(api_client):
    client, mp = api_client
    mp.setattr(
        "app.api.v1.integrations.ideation_embed.create_embed_session",
        lambda db, user, *, idea_id=None: {
            "iframe_url": "https://shared.test/embed/ideas",
            "token": "embed-token-xyz",
            "expires_at": "2026-07-20T00:15:00+00:00",
        },
    )
    resp = client.post(_EMBED_URL, json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["iframe_url"] == "https://shared.test/embed/ideas"
    assert body["token"] == "embed-token-xyz"
    assert body["expires_at"] == "2026-07-20T00:15:00+00:00"


def test_endpoint_detail_passes_idea_id(api_client):
    client, mp = api_client
    seen: dict = {}

    def _capture(db, user, *, idea_id=None):  # noqa: ANN001
        seen["idea_id"] = idea_id
        return {"iframe_url": f"https://shared.test/embed/ideas/{idea_id}", "token": "t", "expires_at": "x"}

    mp.setattr("app.api.v1.integrations.ideation_embed.create_embed_session", _capture)
    resp = client.post(_EMBED_URL, json={"idea_id": "idea-123"})
    assert resp.status_code == 200
    assert seen["idea_id"] == "idea-123"


def test_endpoint_dormant_is_clean_4xx_not_500(api_client):
    client, mp = api_client

    def _dormant(db, user, *, idea_id=None):  # noqa: ANN001
        raise IdeationEmbedNotConfigured("blank settings")

    mp.setattr("app.api.v1.integrations.ideation_embed.create_embed_session", _dormant)
    resp = client.post(_EMBED_URL, json={})
    assert 400 <= resp.status_code < 500
    assert resp.status_code != 500


def test_endpoint_upstream_failure_is_not_500(api_client):
    client, mp = api_client

    def _outage(db, user, *, idea_id=None):  # noqa: ANN001
        raise IdeationEmbedUpstreamError("embed session upstream failed")

    mp.setattr("app.api.v1.integrations.ideation_embed.create_embed_session", _outage)
    resp = client.post(_EMBED_URL, json={})
    assert resp.status_code == 502
    # No secret / internal URL leaks in the error body.
    text = resp.text.lower()
    assert "signing" not in text and "assertion" not in text


def test_endpoint_never_echoes_secrets(api_client, monkeypatch):
    """A real (service-level) round-trip with the shared-service POST stubbed - 
    the response must not contain the signing secret, the connection id, or the
    minted assertion."""
    client, mp = api_client
    monkeypatch.setattr(svc.settings, "ideation_shared_service_url", _SHARED_URL)
    monkeypatch.setattr(svc.settings, "ideation_embed_fe_base_url", _FE_URL)
    monkeypatch.setattr(svc.settings, "ideation_embed_signing_secret", _SIGNING_SECRET)
    monkeypatch.setattr(svc.settings, "ideation_embed_connection_id", _CONNECTION_ID)
    monkeypatch.setattr(
        svc, "post_embed_session",
        lambda base_url, payload: {"token": "embed-token-xyz", "expires_at": "x"},
    )
    resp = client.post(_EMBED_URL, json={})
    assert resp.status_code == 200
    text = resp.text
    assert _SIGNING_SECRET not in text
    assert _CONNECTION_ID not in text


# --------------------------------------------------------------------------- #
# The embed-session route's assertion carries ideas_manage and the verified phone #
# --------------------------------------------------------------------------- #
@pytest.fixture
def embed_route(configured, monkeypatch):
    import uuid
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient

    from app.dependencies import get_current_user, get_db
    from app.main import app
    from app.models.access import RespondContact
    from app.models.user import User
    from app.services.user_service import UserPermissionService
    from tests._pg_fixture import blank_session

    allow: set[str] = {"ideation.board.view"}
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in allow
    )
    posted: list[dict] = []
    monkeypatch.setattr(
        svc,
        "post_embed_session",
        lambda base_url, payload: posted.append(payload) or {"token": "t", "expires_at": "2099-01-01T00:00:00+00:00"},
    )

    def _decode(payload):
        return jwt.decode(
            payload["assertion"], _SIGNING_SECRET, algorithms=[settings.jwt_algorithm], audience="ideation-embed"
        )

    with blank_session() as db:

        def seed_user(*, verified: bool, phone: str = "+60100000001") -> dict:
            uid = str(uuid.uuid4())
            contact_id = str(uuid.uuid4())
            db.add(RespondContact(id=contact_id, phone_number=phone, name="ZZT Contact"))
            db.flush()
            db.add(
                User(
                    id=uid,
                    email=f"zzt-{uid[:8]}@example.test",
                    name="ZZT Embed",
                    status="ACTIVE",
                    respond_contact_id=contact_id,
                    contact_number=phone,
                    phone_verified_at=datetime.now(timezone.utc) if verified else None,
                )
            )
            db.commit()
            return {"id": uid, "email": f"zzt-{uid[:8]}@example.test", "name": "ZZT Embed"}

        def call(user: dict) -> dict:
            app.dependency_overrides[get_db] = lambda: db
            app.dependency_overrides[get_current_user] = lambda: dict(user)
            try:
                resp = TestClient(app).post(_EMBED_URL, json={})
            finally:
                app.dependency_overrides.clear()
            assert resp.status_code == 200, resp.text
            return _decode(posted[-1])

        yield seed_user, call, allow


def test_embed_route_ideas_manage_false_for_a_view_only_user(embed_route):
    seed_user, call, allow = embed_route
    claims = call(seed_user(verified=False))
    assert "ideas_manage" in claims
    assert claims["ideas_manage"] is False


def test_embed_route_ideas_manage_true_for_a_manage_holder(embed_route):
    seed_user, call, allow = embed_route
    allow.add("ideation.ideas.manage")
    assert call(seed_user(verified=False))["ideas_manage"] is True


def test_embed_route_carries_phone_for_a_verified_user(embed_route):
    seed_user, call, _ = embed_route
    assert call(seed_user(verified=True, phone="+60100000002"))["phone"] == "+60100000002"


def test_embed_route_omits_phone_for_an_unverified_user(embed_route):
    seed_user, call, _ = embed_route
    assert "phone" not in call(seed_user(verified=False, phone="+60100000003"))
