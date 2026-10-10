"""SORENTO-CLIENT-IP: one trusted-proxy client-IP resolver.

`app.services.client_ip.client_ip` used to return the LEFT-most `X-Forwarded-For` entry, which is
whatever the caller typed, so a rotating fake header escaped the public file-link bucket and
planted any signer IP in the e-sign evidence. The auth throttles keyed on the socket peer, which
behind nginx / the Next server is the proxy, so every user shared one bucket.

The rule now (mirrors foundryx-shared-service #120): trust `X-Forwarded-For` only when the socket
peer is a trusted proxy, walk it right to left, and the first untrusted hop is the client.
Plan: documentation/plans/security/PLAN-trusted-proxy-client-ip-10oct.md
"""
from __future__ import annotations

import inspect
import uuid

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.user import User
from app.services.client_ip import client_ip
from tests._pg_fixture import blank_session

PROXY = "172.18.0.5"  # a docker-network peer: the frontend container or the host nginx gateway


def _req(peer: str | None, xff: str | list[str] | None = None) -> Request:
    headers = []
    for value in ([xff] if isinstance(xff, str) else (xff or [])):
        headers.append((b"x-forwarded-for", value.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (peer, 1234) if peer else None,
    }
    return Request(scope)


# --- resolver ------------------------------------------------------------------------------


def test_forged_xff_from_an_untrusted_peer_is_ignored():
    assert client_ip(_req("203.0.113.50", "1.2.3.4")) == "203.0.113.50"


def test_no_xff_returns_the_peer():
    assert client_ip(_req(PROXY)) == PROXY
    assert client_ip(_req("198.51.100.1")) == "198.51.100.1"


def test_no_client_at_all_returns_none():
    assert client_ip(_req(None, "1.2.3.4")) is None


def test_proxied_client_is_the_rightmost_untrusted_hop_not_the_forged_leftmost():
    # Browser typed "6.6.6.6"; nginx appended the real address it saw.
    assert client_ip(_req(PROXY, "6.6.6.6, 203.0.113.7")) == "203.0.113.7"


def test_a_chain_of_trusted_proxies_is_skipped():
    # host nginx -> container nginx -> Next server -> backend
    assert client_ip(_req("127.0.0.1", "6.6.6.6, 203.0.113.7, 172.17.0.1, 10.0.0.9")) == "203.0.113.7"


def test_multiple_xff_headers_are_read_as_one_list():
    assert client_ip(_req(PROXY, ["6.6.6.6", "203.0.113.8"])) == "203.0.113.8"


def test_a_malformed_hop_stops_the_walk_at_the_last_trusted_address():
    assert client_ip(_req(PROXY, "203.0.113.7, not-an-ip")) == PROXY
    assert client_ip(_req(PROXY, "203.0.113.7, garbage, 10.0.0.2")) == "10.0.0.2"


def test_every_hop_trusted_returns_the_leftmost():
    assert client_ip(_req(PROXY, "192.168.1.20, 10.0.0.2")) == "192.168.1.20"


def test_ipv6_loopback_peer_is_trusted_and_ipv6_client_returned():
    assert client_ip(_req("::1", "2001:db8::1")) == "2001:db8::1"


def test_an_oversized_header_cannot_produce_an_oversized_ip():
    assert client_ip(_req(PROXY, "x" * 4000)) == PROXY


def test_trusted_proxies_setting_overrides_the_default(monkeypatch):
    # A CDN in front: its edge address is public, so it has to be named to be trusted.
    monkeypatch.setattr(settings, "trusted_proxies", "198.51.100.0/24, 127.0.0.1/32")
    assert client_ip(_req("198.51.100.4", "6.6.6.6, 203.0.113.7")) == "203.0.113.7"
    # ...and the default private ranges are no longer trusted once the list is explicit.
    assert client_ip(_req(PROXY, "203.0.113.7")) == PROXY


# --- duplicates gone -----------------------------------------------------------------------


def test_quotation_documents_uses_the_shared_resolver_not_its_own_copy():
    from app.api.v1.projects import quotation_documents

    assert not hasattr(quotation_documents, "_client_ip")
    assert "x-forwarded-for" not in inspect.getsource(quotation_documents).lower()


def test_services_logging_has_no_hand_rolled_xff_parse():
    from app.services import logging as svc_logging

    assert "x-forwarded-for" not in inspect.getsource(svc_logging).lower()


# --- auth throttles per real client ---------------------------------------------------------


@pytest.fixture()
def proxied_client():
    """A TestClient whose socket peer is a proxy, the way every prod request arrives."""
    with blank_session() as db:
        bind = db.get_bind()

        def _override():
            yield db

        app.dependency_overrides[get_db] = _override
        with TestClient(app, client=(PROXY, 50000)) as c:
            c._session_factory = lambda: Session(bind=bind, join_transaction_mode="create_savepoint")  # type: ignore[attr-defined]
            yield c
        app.dependency_overrides.pop(get_db, None)


def test_two_clients_behind_the_proxy_get_separate_login_throttle_buckets(proxied_client):
    from app.services import login_throttle

    email = f"bucket-{uuid.uuid4().hex[:8]}@example.com"
    db = proxied_client._session_factory()  # type: ignore[attr-defined]
    try:
        db.add(User(
            id=str(uuid.uuid4()), email=email, name="Bucket Test", status="ACTIVE",
            password=bcrypt.hashpw(b"right-password", bcrypt.gensalt()).decode(),
        ))
        db.commit()
    finally:
        db.close()

    attacker = {"X-Forwarded-For": "203.0.113.66"}
    for _ in range(login_throttle.MAX_ATTEMPTS):
        proxied_client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"}, headers=attacker)
    locked = proxied_client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"}, headers=attacker)
    assert locked.status_code == 429, locked.text

    # The real user, behind the same proxy, is not locked out by the attacker's failures.
    real = proxied_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "right-password"},
        headers={"X-Forwarded-For": "198.51.100.23"},
    )
    assert real.status_code == 200, real.text


@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/v1/auth/signup", {"email": "x@example.com", "password": "Passw0rd!long", "name": "X"}),
        ("/api/v1/auth/reset-password", {"email": "x@example.com"}),
        ("/api/v1/auth/phone/request-code", {"phone": "+60123456789"}),
    ],
    ids=["signup", "reset", "phone_request_code"],
)
def test_auth_rate_limits_key_on_the_real_client_behind_the_proxy(proxied_client, monkeypatch, path, body):
    from app.services import rate_limit

    idents: list = []

    def _hit(name, ident, **_kw):
        idents.append(ident)
        return rate_limit.RateResult(allowed=False, retry_after_seconds=60)

    monkeypatch.setattr(rate_limit, "hit", _hit)
    res = proxied_client.post(path, json=body, headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.9"})
    assert res.status_code == 429, res.text
    assert idents[0] == "203.0.113.9"
