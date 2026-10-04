"""The viewer-scoped media proxy (UAC AC-N4).

An .xlsx in a chat bubble could not be previewed inline: the bytes live on the
R2 CDN / CloudFront / Respond's media hosts, none of which send CORS headers, so
the browser's fetch is blocked and the preview surface reports "No source
available to load this file". The fix is a backend proxy - and a backend that
fetches an arbitrary URL on a caller's behalf is an SSRF gadget, so the host
ALLOWLIST is the load-bearing part of this file, not an afterthought.

Pinned here:

- an allowlisted host is proxied with the upstream content type and an
  ``inline`` disposition carrying the real filename;
- any other host is refused with 400, including the interesting ones (an
  internal address, a look-alike suffix like ``evil-cdn.example.com`` where the
  allowlisted domain is a SUFFIX of the attacker's, and a non-http scheme);
- a redirect to a non-allowlisted host is not followed;
- a response above the size cap is refused with 413;
- the ticket-keyed route keeps the ticket scope (an outsider gets 404) while
  the contact-keyed route uses the AC-N2 permission.

Run:
    venv/bin/pytest tests/test_conversation_media_proxy.py -q
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.sla import SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate
from app.services import media_proxy_service
from app.services.sla_service import ConversationSLATrackingService
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session

PHONE = "+60120000541"
RESPOND_IO_ID = "zzt-media-1"
TICKET_BASE = "/api/v1/sla-management/conversation-sla-tracking"
INBOX_BASE = "/api/v1/sla-management/conversations"
VIEW = "sla_management.conversations.view"

ALLOWED_HOST = "cdn.chatapi.net"
ALLOWED_URL = f"https://{ALLOWED_HOST}/whatsapp_business/1/2/quotation.xlsx"

_GRANTS: set[str] = set()
_ADMINS: set[str] = set()
_ACTOR: dict = {"id": None, "name": "Test Actor"}


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    _GRANTS.clear()
    _GRANTS.add(VIEW)
    _ADMINS.clear()
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in _GRANTS,
    )
    monkeypatch.setattr(
        UserPermissionService,
        "get_user_role_slugs",
        lambda self, uid: {"admin"} if str(uid) in _ADMINS else set(),
    )
    yield
    _GRANTS.clear()
    _ADMINS.clear()


@pytest.fixture
def client(db):
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(_ACTOR)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(_ACTOR)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _act_as(user_id: str) -> None:
    _ACTOR["id"] = user_id


def _upstream(handler):
    """Point the proxy at an httpx MockTransport instead of the internet."""

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    return factory


def _ok(content: bytes = b"PK\x03\x04 spreadsheet bytes", content_type: str = "application/vnd.ms-excel"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, headers={"content-type": content_type})

    return handler


@pytest.fixture
def seed(db):
    policy_id = str(uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code=f"ZZT-{uuid.uuid4().hex[:6]}", name="ZZT Policy"))
    db.add(
        SLAPolicyTier(
            id=str(uuid.uuid4()),
            policy_id=policy_id,
            tier_level=1,
            tier_name="Tier 1",
            response_hours=4,
            resolution_hours=24,
        )
    )
    contact_id = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=contact_id,
            phone_number=PHONE,
            name="ZZT Media Contact",
            respond_io_id=RESPOND_IO_ID,
            session_vars={},
        )
    )
    assignee_id = str(uuid.uuid4())
    outsider_id = str(uuid.uuid4())
    db.add(User(id=assignee_id, email=f"zzt-a-{assignee_id[:8]}@test.com", name="Agent One"))
    db.add(User(id=outsider_id, email=f"zzt-o-{outsider_id[:8]}@test.com", name="Outsider"))
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="ZZT_MEDIA_AGENT", name="ZZT Media Agent"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="ZZT Media Team - Tier 1"))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="zzt_media_set",
            team_id=team_id,
            tier=1,
            policy_id=policy_id,
        )
    )
    db.commit()
    tracking = ConversationSLATrackingService(db).create_tracking(
        ConversationSLATrackingCreate(
            agent_code="ZZT_MEDIA_AGENT",
            team_set_code="zzt_media_set",
            policy_id=policy_id,
            assigned_to_id=assignee_id,
            contact_phone_number=PHONE,
            source_message_id="wamid.media-1",
            source_message_text="Here is the file",
        )
    )
    _act_as(assignee_id)
    return {
        "tracking_id": str(tracking.id),
        "assignee_id": assignee_id,
        "outsider_id": outsider_id,
        "contact_id": contact_id,
    }


# --------------------------------------------------------------------------- #
# The allowlist                                                                #
# --------------------------------------------------------------------------- #


def test_the_respond_media_hosts_are_allowlisted():
    hosts = media_proxy_service.allowed_hosts()
    assert "cdn.chatapi.net" in hosts
    assert "production--bucket.s3-accelerate.amazonaws.com" in hosts


def test_the_configured_storage_hosts_are_allowlisted(monkeypatch):
    monkeypatch.setenv("R2_CDN_DOMAIN", "cdn-example.test")
    monkeypatch.setenv("CLOUDFRONT_DOMAIN", "abc123.cloudfront.test")
    media_proxy_service.allowed_hosts.cache_clear()
    try:
        hosts = media_proxy_service.allowed_hosts()
        assert "cdn-example.test" in hosts
        assert "abc123.cloudfront.test" in hosts
    finally:
        media_proxy_service.allowed_hosts.cache_clear()


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/x.xlsx",
        # A look-alike whose SUFFIX is an allowlisted host: the check must be
        # host equality, never `endswith`.
        "https://evil-cdn.chatapi.net.attacker.test/x.xlsx",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "not a url at all",
        "",
    ],
)
def test_a_non_allowlisted_url_is_refused(url):
    with pytest.raises(Exception) as excinfo:
        media_proxy_service.assert_allowed(url)
    assert getattr(excinfo.value, "status_code", None) == 400


# --------------------------------------------------------------------------- #
# Streaming                                                                    #
# --------------------------------------------------------------------------- #


def test_an_allowlisted_url_is_proxied_inline_with_its_filename(client, seed, monkeypatch):
    monkeypatch.setattr(media_proxy_service, "_client_factory", _upstream(_ok()))
    got = client.get(
        f"{INBOX_BASE}/{RESPOND_IO_ID}/media", params={"url": ALLOWED_URL}
    )
    assert got.status_code == 200, got.text
    assert got.content == b"PK\x03\x04 spreadsheet bytes"
    assert got.headers["content-type"].startswith("application/vnd.ms-excel")
    assert got.headers["content-disposition"] == (
        "inline; filename=\"quotation.xlsx\"; filename*=UTF-8''quotation.xlsx"
    )


def test_a_non_allowlisted_url_is_400_on_the_route(client, seed):
    got = client.get(
        f"{INBOX_BASE}/{RESPOND_IO_ID}/media",
        params={"url": "https://evil.example.com/x.xlsx"},
    )
    assert got.status_code == 400, got.text


def test_a_redirect_off_the_allowlist_is_not_followed(client, seed, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example.com/x.xlsx"})

    monkeypatch.setattr(media_proxy_service, "_client_factory", _upstream(handler))
    got = client.get(f"{INBOX_BASE}/{RESPOND_IO_ID}/media", params={"url": ALLOWED_URL})
    assert got.status_code == 400, got.text


def test_a_redirect_within_the_allowlist_is_followed(client, seed, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.startswith("/final/"):
            return httpx.Response(
                302, headers={"location": f"https://{ALLOWED_HOST}/final/quotation.xlsx"}
            )
        return httpx.Response(200, content=b"final bytes", headers={"content-type": "text/csv"})

    monkeypatch.setattr(media_proxy_service, "_client_factory", _upstream(handler))
    got = client.get(f"{INBOX_BASE}/{RESPOND_IO_ID}/media", params={"url": ALLOWED_URL})
    assert got.status_code == 200, got.text
    assert got.content == b"final bytes"


def test_an_oversize_declared_body_is_413(client, seed, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x",
            headers={
                "content-type": "application/pdf",
                "content-length": str(media_proxy_service.MAX_BYTES + 1),
            },
        )

    monkeypatch.setattr(media_proxy_service, "_client_factory", _upstream(handler))
    got = client.get(f"{INBOX_BASE}/{RESPOND_IO_ID}/media", params={"url": ALLOWED_URL})
    assert got.status_code == 413, got.text


def test_an_upstream_404_is_reported_not_swallowed(client, seed, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"gone")

    monkeypatch.setattr(media_proxy_service, "_client_factory", _upstream(handler))
    got = client.get(f"{INBOX_BASE}/{RESPOND_IO_ID}/media", params={"url": ALLOWED_URL})
    assert got.status_code == 404, got.text


# --------------------------------------------------------------------------- #
# Who may proxy                                                                #
# --------------------------------------------------------------------------- #


def test_the_ticket_keyed_proxy_keeps_the_ticket_scope(client, seed, monkeypatch):
    monkeypatch.setattr(media_proxy_service, "_client_factory", _upstream(_ok()))
    tracking_id = seed["tracking_id"]

    _act_as(seed["assignee_id"])
    assert (
        client.get(f"{TICKET_BASE}/{tracking_id}/media", params={"url": ALLOWED_URL}).status_code
        == 200
    )

    _act_as(seed["outsider_id"])
    assert (
        client.get(f"{TICKET_BASE}/{tracking_id}/media", params={"url": ALLOWED_URL}).status_code
        == 404
    )


def test_the_contact_keyed_proxy_needs_the_view_permission(client, seed, monkeypatch):
    monkeypatch.setattr(media_proxy_service, "_client_factory", _upstream(_ok()))
    _GRANTS.clear()
    assert (
        client.get(
            f"{INBOX_BASE}/{RESPOND_IO_ID}/media", params={"url": ALLOWED_URL}
        ).status_code
        == 403
    )


def test_an_unknown_contact_is_404(client, seed, monkeypatch):
    monkeypatch.setattr(media_proxy_service, "_client_factory", _upstream(_ok()))
    got = client.get(f"{INBOX_BASE}/zzt-nobody/media", params={"url": ALLOWED_URL})
    assert got.status_code == 404, got.text
