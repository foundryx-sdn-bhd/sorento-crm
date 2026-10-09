"""PHOTO-SEND-CAP round 2: the public short-link route, no login.

`GET /api/v1/public/file-links/{code}` counts a click and answers what the `/f/{code}`
page needs; `/content` streams a spreadsheet's bytes for the in-browser preview. An
unknown and an expired code answer the SAME 404, so a guess learns nothing.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.main  # noqa: F401  isort:skip
from app.dependencies import get_db
from app.main import app
from app.models.chatbot_file_link import ChatbotFileLink
from app.services import rate_limit
from tests._pg_fixture import blank_session

BASE = "/api/v1/public/file-links"
EXPIRED = "This link has expired."


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("CLOUDFRONT_DOMAIN", "cdn.test")
    monkeypatch.delenv("R2_CDN_DOMAIN", raising=False)
    with blank_session() as db:

        def _override_db():
            yield db

        app.dependency_overrides[get_db] = _override_db
        signed: list[str] = []

        def _sign(url, **kwargs):
            signed.append(url)
            return f"{url}?sig=1".replace("http://", "https://")

        monkeypatch.setattr("app.api.v1.public.file_links.resolve_signed_url", _sign)
        monkeypatch.setattr(
            "app.api.v1.public.file_links.download_bytes", lambda url: b"col1,col2\n1,2\n"
        )
        try:
            yield TestClient(app, raise_server_exceptions=False), db, signed
        finally:
            app.dependency_overrides.clear()


def _link(db, code, url, name, mime=None, **kw) -> ChatbotFileLink:
    row = ChatbotFileLink(code=code, target_url=url, filename=name, mime_type=mime, **kw)
    db.add(row)
    db.commit()
    return row


def test_a_file_link_answers_a_signed_url_and_counts_the_click(env):
    client, db, signed = env
    row = _link(db, "PHOTO00001", "https://cdn.test/a.jpg", "a.jpg", "image/jpeg")
    resp = client.get(f"{BASE}/PHOTO00001")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "filename": "a.jpg",
        "mime_type": "image/jpeg",
        "kind": "file",
        "url": "https://cdn.test/a.jpg?sig=1",
    }
    assert signed == ["https://cdn.test/a.jpg"]
    db.refresh(row)
    assert row.click_count == 1
    assert row.first_clicked_at is not None and row.last_clicked_at is not None
    # A second open within the dedupe window is the same tap (a page loaded twice).
    client.get(f"{BASE}/PHOTO00001")
    db.refresh(row)
    assert row.click_count == 1
    # Later opens count.
    from datetime import datetime as _dt, timedelta as _td

    row.last_clicked_at = _dt.utcnow() - _td(seconds=11)
    db.commit()
    client.get(f"{BASE}/PHOTO00001")
    db.refresh(row)
    assert row.click_count == 2


def test_a_sheet_link_answers_no_url_and_its_content_streams(env):
    client, db, signed = env
    _link(db, "SHEET00001", "https://cdn.test/r.xlsx", "Sales Analysis.xlsx")
    resp = client.get(f"{BASE}/SHEET00001")
    assert resp.status_code == 200, resp.text
    assert resp.json()["kind"] == "sheet"
    assert resp.json()["url"] is None
    assert signed == []
    content = client.get(f"{BASE}/SHEET00001/content")
    assert content.status_code == 200
    assert content.content == b"col1,col2\n1,2\n"
    assert "Sales Analysis.xlsx" in content.headers["content-disposition"]


def test_content_is_refused_for_a_file_that_is_not_a_sheet(env):
    client, db, _ = env
    _link(db, "PHOTO00002", "https://cdn.test/a.jpg", "a.jpg", "image/jpeg")
    resp = client.get(f"{BASE}/PHOTO00002/content")
    assert resp.status_code == 404
    assert resp.json()["detail"] == EXPIRED


@pytest.mark.parametrize("path", ["", "/content"])
def test_unknown_and_expired_codes_answer_the_same_404(env, path):
    client, db, _ = env
    _link(
        db,
        "OLD0000001",
        "https://cdn.test/old.xlsx",
        "old.xlsx",
        expires_at=datetime.utcnow() - timedelta(seconds=1),
    )
    for code in ("OLD0000001", "NOSUCHCODE"):
        resp = client.get(f"{BASE}/{code}{path}")
        assert resp.status_code == 404
        assert resp.json() == {"detail": EXPIRED}
        assert "cdn.test" not in resp.text


def test_an_expired_click_is_not_counted(env):
    client, db, _ = env
    row = _link(
        db, "OLD0000002", "https://cdn.test/a.jpg", "a.jpg", expires_at=datetime.utcnow() - timedelta(seconds=1)
    )
    client.get(f"{BASE}/OLD0000002")
    db.refresh(row)
    assert row.click_count == 0


def test_the_route_is_rate_limited_per_ip(env, monkeypatch):
    client, db, _ = env
    _link(db, "PHOTO00003", "https://cdn.test/a.jpg", "a.jpg")
    buckets: list[str] = []

    def _hit(bucket, ident, **kw):
        buckets.append(bucket)
        return rate_limit.RateResult(allowed=False, retry_after_seconds=30)

    monkeypatch.setattr(rate_limit, "hit", _hit)
    for path in ("", "/content"):
        resp = client.get(f"{BASE}/PHOTO00003{path}")
        assert resp.status_code == 429
        assert resp.headers["retry-after"] == "30"
    assert buckets == ["chatbot_file_link", "chatbot_file_link"]


def test_a_signing_failure_is_a_502_without_the_storage_url(env, monkeypatch):
    client, db, _ = env
    _link(db, "PHOTO00004", "https://cdn.test/a.jpg", "a.jpg")
    monkeypatch.setattr("app.api.v1.public.file_links.resolve_signed_url", lambda url, **kw: None)
    resp = client.get(f"{BASE}/PHOTO00004")
    assert resp.status_code == 502
    assert "cdn.test" not in resp.text


@pytest.mark.parametrize("signed_url", ["javascript:alert(1)//X-Amz-Signature=1", "http://cdn.test/a.jpg", "https://evil.test/a.jpg"])
def test_only_an_https_url_on_our_cdn_is_handed_out(env, monkeypatch, signed_url):
    """Security review L2: never a redirect to another scheme or host."""
    client, db, _ = env
    _link(db, "PHOTO00005", "https://cdn.test/a.jpg", "a.jpg")
    monkeypatch.setattr("app.api.v1.public.file_links.resolve_signed_url", lambda url, **kw: signed_url)
    resp = client.get(f"{BASE}/PHOTO00005")
    assert resp.status_code == 502
    assert signed_url not in resp.text


def test_content_type_comes_from_the_extension_not_the_upload(env):
    """Security review L3: a `.csv` uploaded as text/html is served as text/csv."""
    client, db, _ = env
    _link(db, "SHEET00002", "https://cdn.test/r.csv", "r.csv", "text/html")
    resp = client.get(f"{BASE}/SHEET00002/content")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_the_limit_is_keyed_per_client_behind_the_proxy(env, monkeypatch):
    """Crew audit item 6: behind nginx every socket peer is the proxy; the bucket key is
    the forwarded client, so two dealers never share one limit."""
    client, db, _ = env
    _link(db, "PHOTO00006", "https://cdn.test/a.jpg", "a.jpg")
    idents: list[str | None] = []

    def _hit(bucket, ident, **kw):
        idents.append(ident)
        return rate_limit.RateResult(allowed=True)

    monkeypatch.setattr(rate_limit, "hit", _hit)
    client.get(f"{BASE}/PHOTO00006", headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.2"})
    client.get(f"{BASE}/PHOTO00006", headers={"X-Forwarded-For": "198.51.100.9"})
    assert idents == ["203.0.113.7", "198.51.100.9"]


def test_a_forwarded_value_that_is_not_an_ip_never_becomes_a_key(env, monkeypatch):
    """Security re-review finding 1: arbitrary header text would mint a Redis key per request."""
    client, db, _ = env
    _link(db, "PHOTO00007", "https://cdn.test/a.jpg", "a.jpg")
    idents: list[str | None] = []

    def _hit(bucket, ident, **kw):
        idents.append(ident)
        return rate_limit.RateResult(allowed=True)

    monkeypatch.setattr(rate_limit, "hit", _hit)
    client.get(f"{BASE}/PHOTO00007", headers={"X-Forwarded-For": "x" * 4000})
    assert idents and idents[0] != "x" * 4000
    assert len(idents[0] or "") < 64


# --------------------------------------------------------------------------- #
# Hand-test fix: a file on an allowlisted public host (cdn-sorento.com, the R2 custom
# domain the stored rows name) that this deployment's signer does not own.
# --------------------------------------------------------------------------- #


def test_a_public_listed_host_hands_out_its_stored_url_unsigned(env, monkeypatch):
    """No signer owns cdn-sorento.com on the test copy (its env names other CDN hosts),
    so the stored url (the same one attachment mode sends) is the redirect target."""
    from app.config import settings as app_settings

    client, db, signed = env
    monkeypatch.setattr(app_settings, "chatbot_link_cdn_hosts", "cdn-sorento.com")
    _link(db, "PROMO00000000001", "https://cdn-sorento.com/promotion/7e5155f1/Vixx Loo A.pdf", "Vixx Loo A.pdf", "application/pdf")
    resp = client.get(f"{BASE}/PROMO00000000001")
    assert resp.status_code == 200, resp.text
    assert resp.json()["url"] == "https://cdn-sorento.com/promotion/7e5155f1/Vixx Loo A.pdf"
    assert signed == []


def test_a_public_listed_host_sheet_is_fetched_over_https(env, monkeypatch):
    from app.config import settings as app_settings
    from app.services import file_link_service

    client, db, _ = env
    monkeypatch.setattr(app_settings, "chatbot_link_cdn_hosts", "cdn-sorento.com")
    monkeypatch.setattr("app.api.v1.public.file_links.download_bytes", file_link_service.download_bytes)
    fetched: list[str] = []
    monkeypatch.setattr(
        file_link_service, "_fetch_public", lambda url: fetched.append(url) or b"a,b\n1,2\n"
    )
    _link(db, "SHEET00000000003", "https://cdn-sorento.com/reports/r.csv", "r.csv")
    resp = client.get(f"{BASE}/SHEET00000000003/content")
    assert resp.status_code == 200
    assert resp.content == b"a,b\n1,2\n"
    assert fetched == ["https://cdn-sorento.com/reports/r.csv"]


def test_a_stored_url_off_the_list_is_never_handed_out(env, monkeypatch):
    from app.config import settings as app_settings

    client, db, _ = env
    monkeypatch.setattr(app_settings, "chatbot_link_cdn_hosts", "")
    _link(db, "PROMO00000000002", "https://cdn-sorento.com/promotion/x.pdf", "x.pdf", "application/pdf")
    resp = client.get(f"{BASE}/PROMO00000000002")
    assert resp.status_code == 502
    assert "cdn-sorento.com" not in resp.text
