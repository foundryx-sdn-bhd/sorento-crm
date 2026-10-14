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


@pytest.fixture(autouse=True)
def _trust_this_deploys_proxies(monkeypatch):
    """Trust is opt-in; these tests run with the value prod sets once the edge nginx appends."""
    from app.services.client_ip import RECOMMENDED_TRUSTED_PROXIES

    monkeypatch.setattr(settings, "trusted_proxies", RECOMMENDED_TRUSTED_PROXIES)


@pytest.mark.parametrize("peer", [PROXY, "127.0.0.1", "::1"])
def test_unset_trusted_proxies_ignores_xff_from_every_peer(monkeypatch, peer):
    # The safe default: if the edge nginx passes a typed header through untouched, nothing a
    # client sends picks its key, because no proxy is believed until one is configured.
    monkeypatch.setattr(settings, "trusted_proxies", "")
    assert client_ip(_req(peer, "6.6.6.6, 203.0.113.7")) == peer


def _req(peer: str | None, xff: str | list[str] | None = None) -> Request:
    headers = []
    for value in ([xff] if isinstance(xff, str) else (xff or [])):
        headers.append((b"x-forwarded-for", value.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "query_string": b"",
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


@pytest.mark.parametrize("peer", ["192.168.1.1", "fd00::5", "fe80::1"])
def test_private_and_ula_peers_are_trusted_in_the_recommended_list(peer):
    assert client_ip(_req(peer, "6.6.6.6, 203.0.113.7")) == "203.0.113.7"


def test_ipv6_loopback_peer_is_trusted_and_ipv6_client_returned():
    assert client_ip(_req("::1", "2001:db8::1")) == "2001:db8::1"


def test_an_oversized_header_cannot_produce_an_oversized_ip():
    assert client_ip(_req(PROXY, "x" * 4000)) == PROXY


def test_trusted_proxies_setting_is_the_whole_trust_list(monkeypatch):
    # A CDN in front: its edge address is public, so it has to be named to be trusted.
    monkeypatch.setattr(settings, "trusted_proxies", "198.51.100.0/24, 127.0.0.1/32")
    assert client_ip(_req("198.51.100.4", "6.6.6.6, 203.0.113.7")) == "203.0.113.7"
    # ...and a private range left off the list is not trusted.
    assert client_ip(_req(PROXY, "203.0.113.7")) == PROXY


# --- duplicates gone -----------------------------------------------------------------------


def test_quotation_documents_uses_the_shared_resolver_not_its_own_copy():
    from app.api.v1.projects import quotation_documents

    assert not hasattr(quotation_documents, "_client_ip")
    assert "x-forwarded-for" not in inspect.getsource(quotation_documents).lower()


def test_services_logging_has_no_hand_rolled_xff_parse():
    from app.services import logging as svc_logging

    assert "x-forwarded-for" not in inspect.getsource(svc_logging).lower()


class _Stop(Exception):
    status_code = 418


class _Db:
    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)

    def commit(self):
        pass

    def rollback(self):
        pass


def test_staff_signature_records_the_proxied_client_ip(monkeypatch):
    import asyncio

    from app.api.v1.projects import quotation_documents as route

    seen = {}
    monkeypatch.setattr(route, "validate_uuid_path", lambda *a, **k: None)
    monkeypatch.setattr(route, "_editable_project", lambda *a, **k: None)
    monkeypatch.setattr(route.svc, "get_document_or_404", lambda *a, **k: object())

    def _sign(db, *, document, actor_user_id, payload):
        seen.update(payload)
        raise _Stop()

    monkeypatch.setattr(route.svc, "sign_as_sorento", _sign)
    payload = type("P", (), {"model_dump": lambda self, **k: {}})()
    with pytest.raises(_Stop):
        asyncio.run(route.sign_quotation_document(
            "p", "d", payload, _req(PROXY, "6.6.6.6, 203.0.113.7"), {"id": "u"}, _Db(),
        ))
    assert seen["ip_address"] == "203.0.113.7"


def test_customer_acceptance_records_the_proxied_client_ip(monkeypatch):
    import asyncio

    from app.api.v1.public import quotation_sign as route

    seen = {}
    monkeypatch.setattr(route.svc, "get_issue_by_sign_token", lambda *a, **k: object())

    def _accept(db, **kw):
        seen.update(kw)
        raise _Stop()

    monkeypatch.setattr(route.svc, "accept_issue", _accept)
    payload = type("P", (), {"signer_name": "S", "mode": "typed", "image_data_uri": None, "gps_lat": None, "gps_lng": None})()
    with pytest.raises(_Stop):
        asyncio.run(route.accept_quotation("t", payload, _req(PROXY, "6.6.6.6, 203.0.113.7"), _Db()))
    assert seen["ip_address"] == "203.0.113.7"


def test_api_request_log_records_the_proxied_client_ip():
    import asyncio

    from app.services.logging import log_api_request

    db = _Db()
    asyncio.run(log_api_request(_req(PROXY, "6.6.6.6, 203.0.113.7"), None, db))
    assert db.rows and db.rows[0].ip_address == "203.0.113.7"


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


# --- security review round: a floor that does not depend on the IP -------------------------


def test_bad_trusted_proxies_value_fails_at_startup_not_per_request():
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(trusted_proxies="10.0.0.0/8, not-a-cidr")


def test_a_per_email_ceiling_locks_whatever_ip_the_attempts_claim():
    from app.services import login_throttle

    email = f"floor-{uuid.uuid4().hex[:8]}@example.com"
    for i in range(login_throttle.EMAIL_MAX_ATTEMPTS):
        login_throttle.record_failure(email, f"203.0.{i // 250}.{i % 250 + 1}")
    gate = login_throttle.check(email, "198.51.100.200")  # a never-seen IP
    assert not gate.allowed
    assert gate.retry_after_seconds and gate.retry_after_seconds > 0
    # Another account is untouched.
    assert login_throttle.check(f"other-{email}", "198.51.100.200").allowed


def test_ipv6_addresses_share_one_bucket_per_64():
    from app.services import login_throttle

    email = f"v6-{uuid.uuid4().hex[:8]}@example.com"
    for i in range(login_throttle.MAX_ATTEMPTS):
        login_throttle.record_failure(email, f"2001:db8:1:2::{i + 1:x}")
    assert not login_throttle.check(email, "2001:db8:1:2:ffff::9").allowed
    assert login_throttle.check(email, "2001:db8:1:3::1").allowed


# --- the two prod paths, as the owner's host nginx (`nginx -T`, 10 Oct) builds them ---------
#
# Host nginx is the edge (no CDN) and sets `X-Forwarded-For $proxy_add_x_forwarded_for` on every
# location. Containers publish on 127.0.0.1, so the container sees the docker gateway as peer.
#   A: browser -> host nginx -> backend: peer = gateway, XFF = "<typed>, <client>".
#   B: browser -> host nginx -> frontend [-> image nginx, which appends the gateway] -> Next
#      server -> backend (sign-in, signup, reset; Next forwards XFF unchanged, lib/forwarded-for.ts):
#      peer = the frontend container, XFF = "<typed>, <client>[, <gateway>]".
CLIENT = "203.0.113.7"
GATEWAY = "172.18.0.1"
FRONTEND_CONTAINER = "172.18.0.3"

PROD_PATHS = pytest.mark.parametrize(
    "peer, xff",
    [
        (GATEWAY, f"6.6.6.6, {CLIENT}"),
        (GATEWAY, CLIENT),
        (FRONTEND_CONTAINER, f"6.6.6.6, {CLIENT}"),
        (FRONTEND_CONTAINER, f"6.6.6.6, {CLIENT}, {GATEWAY}"),
    ],
    ids=["A_nginx_backend_forged", "A_nginx_backend", "B_via_next", "B_via_image_nginx_and_next"],
)


def _login_ip_seen(monkeypatch, peer: str, xff: str) -> str | None:
    from app.services import login_throttle

    seen: list = []

    def _check(email, ip):
        seen.append(ip)
        return login_throttle.ThrottleResult(allowed=False, retry_after_seconds=60)

    monkeypatch.setattr(login_throttle, "check", _check)
    with TestClient(app, client=(peer, 40000)) as c:
        res = c.post(
            "/api/v1/auth/login",
            json={"email": "path@example.com", "password": "x"},
            headers={"X-Forwarded-For": xff},
        )
    assert res.status_code == 429, res.text
    return seen[0]


@PROD_PATHS
def test_prod_paths_resolve_to_the_real_client_with_trusted_proxies_set(monkeypatch, peer, xff):
    assert _login_ip_seen(monkeypatch, peer, xff) == CLIENT


@PROD_PATHS
def test_prod_paths_fall_back_to_the_peer_with_trusted_proxies_unset(monkeypatch, peer, xff):
    monkeypatch.setattr(settings, "trusted_proxies", "")
    assert _login_ip_seen(monkeypatch, peer, xff) == peer


# --- behind Cloudflare (HA-SEC-EDGE, 14 Oct) -----------------------------------------------
#
# Cloudflare appends the address it saw to any typed header, and the host nginx appends the
# Cloudflare edge it saw, so the backend gets peer = gateway, XFF = "<typed>, <client>, <edge>".
# TRUSTED_PROXIES is then the private ranges PLUS Cloudflare's published ranges
# (documentation/runbooks/prod-edge-lockdown.md builds the value from cloudflare.com/ips).
# Two sample ranges stand in for the full list here.
CF_RANGES = "173.245.48.0/20,2606:4700::/32"
CF_EDGE = "173.245.48.10"
CF_EDGE_V6 = "2606:4700::6810:1"


def _behind_cloudflare(monkeypatch):
    from app.services.client_ip import RECOMMENDED_TRUSTED_PROXIES

    monkeypatch.setattr(settings, "trusted_proxies", f"{RECOMMENDED_TRUSTED_PROXIES},{CF_RANGES}")


@pytest.mark.parametrize("edge", [CF_EDGE, CF_EDGE_V6])
def test_cloudflare_path_resolves_to_the_client(monkeypatch, edge):
    _behind_cloudflare(monkeypatch)
    assert client_ip(_req(GATEWAY, f"6.6.6.6, {CLIENT}, {edge}")) == CLIENT


def test_cloudflare_path_without_its_ranges_trusted_stops_at_the_edge(monkeypatch):
    # The state before the owner adds the ranges: one bucket per Cloudflare edge, not forgeable.
    assert client_ip(_req(GATEWAY, f"6.6.6.6, {CLIENT}, {CF_EDGE}")) == CF_EDGE


def test_a_direct_hit_on_the_origin_cannot_borrow_a_cloudflare_address(monkeypatch):
    # Someone going round Cloudflare straight to the origin types a Cloudflare edge into the
    # header; nginx appends their real address, which is not trusted, so that is the answer.
    _behind_cloudflare(monkeypatch)
    attacker = "198.51.100.66"
    assert client_ip(_req(GATEWAY, f"{CLIENT}, {CF_EDGE}, {attacker}")) == attacker
