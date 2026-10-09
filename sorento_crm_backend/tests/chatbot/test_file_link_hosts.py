"""PHOTO-SEND-CAP hand-test fix (owner, 9 Oct 2026): promo PDFs on https://cdn-sorento.com
were sent as attachments in link mode, with nothing said and `chatbot_file_links` empty.

The link-able hosts are ONE explicit list (`file_link_service.link_hosts`): the configured
CloudFront and R2 CDN hosts plus `CHATBOT_LINK_CDN_HOSTS` (default `cdn-sorento.com`, the
production R2 custom domain every stored url names). Minting and the public route read the
same list. A file whose host is not on it goes as an attachment ON ITS OWN, says so under
its row, and the turn records why; the other files still go as links.
"""
from __future__ import annotations

import re

import pytest

import app.main  # noqa: F401  isort:skip
from app.config import settings as app_settings
from app.models.chatbot_file_link import ChatbotFileLink
from app.services import file_link_service
from app.services.chatbot import engine, file_links
from app.services.chatbot.turn.state import Profile
from tests._pg_fixture import blank_session

BASE_URL = "http://photo-send-cap.localhost:3111"
#: The test copy's own CDN hosts (owner's report): neither is the host the rows name.
COPY_ENV = {"CLOUDFRONT_DOMAIN": "d1k6example.cloudfront.net", "R2_CDN_DOMAIN": "pub-example.r2.dev"}

PROMO_TEXT = (
    "I have attached the file(s) below.\n\n"
    "1. *Promotion:* Vixx Loo Oct promo\n"
    "*File Name:* Vixx Loo A.pdf\n*Link:* https://cdn-sorento.com/promotion/7e5155f1/Vixx Loo A.pdf\n"
    "*File Name:* Vixx Loo B.pdf\n*Link:* https://cdn-sorento.com/promotion/7e5155f1/Vixx Loo B.pdf\n"
    "*File Name:* Vixx Loo C.pdf\n*Link:* https://cdn-sorento.com/promotion/7e5155f1/Vixx Loo C.pdf"
)
PROMO_FILES = [
    {"url": f"https://cdn-sorento.com/promotion/7e5155f1/Vixx Loo {x}.pdf", "filename": f"Vixx Loo {x}.pdf", "mimeType": "application/pdf"}
    for x in "ABC"
]


@pytest.fixture()
def copy_env(monkeypatch):
    for key, value in COPY_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(app_settings, "frontend_base_url", BASE_URL)
    monkeypatch.setattr(app_settings, "chatbot_link_cdn_hosts", "cdn-sorento.com")


@pytest.fixture()
def db(copy_env):
    with blank_session() as session:
        yield session


def _codes(text: str) -> list[str]:
    return re.findall(re.escape(BASE_URL) + r"/f/([0-9A-Z]+)", text)


def test_the_list_is_the_cdn_hosts_plus_the_setting(copy_env, monkeypatch):
    assert file_link_service.link_hosts() == {"d1k6example.cloudfront.net", "pub-example.r2.dev", "cdn-sorento.com"}
    monkeypatch.setattr(app_settings, "chatbot_link_cdn_hosts", " CDN-Sorento.com , files.example.com ,")
    assert file_link_service.link_hosts() == {
        "d1k6example.cloudfront.net",
        "pub-example.r2.dev",
        "cdn-sorento.com",
        "files.example.com",
    }


def test_the_default_setting_names_the_production_custom_domain():
    from app.config import Settings

    assert Settings.model_fields["chatbot_link_cdn_hosts"].default == "cdn-sorento.com"


def test_the_owners_promo_case_links_all_three_pdfs(db):
    """Turn 85e162ca (check_promotion, contact 477071884): 3 PDFs on cdn-sorento.com."""
    out, minted, skipped = file_links.links_for(db, PROMO_TEXT, PROMO_FILES, contact_respond_id="477071884", turn_id=None)
    assert minted == 3 and skipped == []
    assert len(set(_codes(out))) == 3
    assert "cdn-sorento.com" not in out
    assert db.query(ChatbotFileLink).count() == 3


def test_a_file_off_the_list_goes_alone_and_says_so(db):
    off = {"url": "https://files.elsewhere.test/x.pdf", "filename": "x.pdf", "mimeType": "application/pdf"}
    text = PROMO_TEXT + "\n*File Name:* x.pdf\n*Link:* https://files.elsewhere.test/x.pdf"
    out, minted, skipped = file_links.links_for(db, text, [*PROMO_FILES, off], contact_respond_id="C1", turn_id=None)
    assert minted == 3
    assert skipped == [off]
    assert "files.elsewhere.test" not in out
    assert "*File Name:* x.pdf\n*Link:* (sent as a file)" in out


def test_engine_mixed_reply_attaches_only_the_skipped_file_and_records_why(copy_env, monkeypatch):
    off = {"url": "https://files.elsewhere.test/x.pdf", "filename": "x.pdf", "mimeType": "application/pdf"}

    def _links_for(db, text, files, **kw):
        return text, 3, [off]

    monkeypatch.setattr(file_links, "links_for", _links_for)
    text, files, mode, fallback = engine._apply_file_mode(
        PROMO_TEXT, [*PROMO_FILES, off], profile=Profile(files_as_links=True),
        session_factory=lambda: _Db(), contact_respond_id="C1", turn_id="T1",
    )
    assert files == [off]
    assert mode == "mixed"
    assert fallback == {"reason": "host_not_linkable", "hosts": ["files.elsewhere.test"], "files": 1}
    # One file still goes as an attachment, so the attached sentence stays true.
    assert "I have attached the file(s) below." in text


def test_engine_all_skipped_is_attachments_with_the_reason(copy_env, monkeypatch):
    monkeypatch.setattr(file_links, "links_for", lambda db, text, files, **kw: (text, 0, list(files)))
    text, files, mode, fallback = engine._apply_file_mode(
        PROMO_TEXT, PROMO_FILES, profile=Profile(files_as_links=True),
        session_factory=lambda: _Db(), contact_respond_id="C1", turn_id="T1",
    )
    assert mode == "attachments" and files == PROMO_FILES
    assert fallback["reason"] == "host_not_linkable"
    assert fallback["hosts"] == ["cdn-sorento.com"]


def test_engine_an_error_falls_back_and_records_it(copy_env, monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(file_links, "links_for", _boom)
    _text, files, mode, fallback = engine._apply_file_mode(
        PROMO_TEXT, PROMO_FILES, profile=Profile(files_as_links=True),
        session_factory=lambda: _Db(), contact_respond_id="C1", turn_id="T1",
    )
    assert mode == "attachments" and files == PROMO_FILES
    assert fallback == {"reason": "error", "error": "RuntimeError", "files": 3}


def test_engine_full_link_mode_records_no_fallback(copy_env, monkeypatch):
    monkeypatch.setattr(file_links, "links_for", lambda db, text, files, **kw: (text, 3, []))
    _text, files, mode, fallback = engine._apply_file_mode(
        PROMO_TEXT, PROMO_FILES, profile=Profile(files_as_links=True),
        session_factory=lambda: _Db(), contact_respond_id="C1", turn_id="T1",
    )
    assert (files, mode, fallback) == ([], "links", None)


class _Db:
    def close(self) -> None:
        pass
