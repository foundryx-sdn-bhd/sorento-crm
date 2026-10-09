"""PHOTO-SEND-CAP round 2 (owner, 9 Oct 2026): files sent as short links, per contact.

`app.services.chatbot.file_links` mints one `chatbot_file_links` row per distinct file
(`fileHash`, else url), rewrites the reply so each file row carries its short link, and
appends a block for a file no row names. Card: documentation/plans/chatbot/
CARD-photo-send-links-09oct.md.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta

import pytest

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.config import settings as app_settings
from app.models.chatbot_file_link import ChatbotFileLink
from app.models.user import SystemSetting
from app.services.chatbot import file_links
from tests._pg_fixture import blank_session

BASE_URL = "https://crm.test"


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(app_settings, "frontend_base_url", BASE_URL)
    with blank_session() as session:
        yield session


def _file(url: str, name: str, file_hash: str | None = None, mime: str = "image/jpeg") -> dict:
    entry = {"url": url, "filename": name, "mimeType": mime, "attachmentType": "Product Photo"}
    if file_hash:
        entry["fileHash"] = file_hash
    return entry


def _codes(text: str) -> list[str]:
    return re.findall(re.escape(BASE_URL) + r"/f/([0-9A-Z]+)", text)


def test_a_code_is_sixteen_crockford_characters_and_never_repeats():
    """80 bits: links never expire by default, so a code must stay unguessable (review L1)."""
    codes = {file_links.new_code() for _ in range(2000)}
    assert len(codes) == 2000
    assert all(re.fullmatch(r"[0-9ABCDEFGHJKMNPQRSTVWXYZ]{16}", c) for c in codes)


def test_a_row_link_is_rewritten_to_its_short_url(db):
    url = "https://cdn.test/photos/a.jpg"
    text = f"1. *Product Code:* SRTSC1012\n*File Name:* a.jpg\n*Link:* {url}"
    out, minted = file_links.links_for(
        db, text, [_file(url, "a.jpg", "h1")], contact_respond_id="C1", turn_id=str(uuid.uuid4())
    )
    assert url not in out
    (code,) = _codes(out)
    assert out.endswith(f"*Link:* {BASE_URL}/f/{code}")
    assert minted == 1
    row = db.query(ChatbotFileLink).filter(ChatbotFileLink.code == code).one()
    assert row.target_url == url
    assert row.filename == "a.jpg"
    assert row.contact_respond_id == "C1"
    assert row.click_count == 0


def test_one_image_under_two_urls_is_one_code_repeated(db):
    """Q5 (a): the same link repeats under each product that uses it."""
    a, b = "https://cdn.test/a.jpg", "https://cdn.test/b.jpg"
    text = f"1. *Product Code:* X1\n*Link:* {a}\n\n2. *Product Code:* X2\n*Link:* {b}"
    out, minted = file_links.links_for(
        db, text, [_file(a, "a.jpg", "h1"), _file(b, "b.jpg", "h1")], contact_respond_id="C1", turn_id=None
    )
    codes = _codes(out)
    assert len(codes) == 2 and codes[0] == codes[1]
    assert minted == 1


def test_a_file_no_row_names_gets_its_own_block(db):
    url = "https://cdn.test/reports/sales.xlsx"
    out, minted = file_links.links_for(
        db,
        "Here is your sales analysis.",
        [_file(url, "Sales Analysis Sep 2026.xlsx", mime="application/vnd.ms-excel")],
        contact_respond_id="C1",
        turn_id=None,
    )
    (code,) = _codes(out)
    assert out == (
        "Here is your sales analysis.\n\n"
        "*File:* Sales Analysis Sep 2026.xlsx\n"
        f"*Link:* {BASE_URL}/f/{code}"
    )
    assert minted == 1


def test_no_expiry_setting_means_the_link_never_expires(db):
    db.add(SystemSetting(id=str(uuid.uuid4()), name="ZZT co", chatbot_file_link_expiry_days=None))
    db.commit()
    out, _ = file_links.links_for(
        db, "x", [_file("https://cdn.test/a.jpg", "a.jpg")], contact_respond_id="C1", turn_id=None
    )
    row = db.query(ChatbotFileLink).filter(ChatbotFileLink.code == _codes(out)[0]).one()
    assert row.expires_at is None


def test_an_expiry_setting_stamps_the_link(db):
    db.add(SystemSetting(id=str(uuid.uuid4()), name="ZZT co", chatbot_file_link_expiry_days=7))
    db.commit()
    out, _ = file_links.links_for(
        db, "x", [_file("https://cdn.test/a.jpg", "a.jpg")], contact_respond_id="C1", turn_id=None
    )
    row = db.query(ChatbotFileLink).filter(ChatbotFileLink.code == _codes(out)[0]).one()
    assert row.expires_at is not None
    assert timedelta(days=6, hours=23) < row.expires_at - row.created_at <= timedelta(days=7, minutes=1)


def test_resolve_refuses_an_unknown_or_expired_code(db):
    db.add(
        ChatbotFileLink(
            code="EXPIRED001",
            target_url="https://cdn.test/a.jpg",
            filename="a.jpg",
            expires_at=datetime.utcnow() - timedelta(minutes=1),
        )
    )
    db.add(ChatbotFileLink(code="LIVE000001", target_url="https://cdn.test/b.jpg", filename="b.jpg"))
    db.commit()
    assert file_links.resolve(db, "EXPIRED001") is None
    assert file_links.resolve(db, "NOPE000000") is None
    assert file_links.resolve(db, "LIVE000001").filename == "b.jpg"


def test_without_a_public_base_url_minting_fails_loudly(db, monkeypatch):
    """The engine falls back to attachment mode on this error; a link to nowhere is never sent."""
    monkeypatch.setattr(app_settings, "frontend_base_url", None)
    with pytest.raises(file_links.LinkModeUnavailable):
        file_links.links_for(
            db, "x", [_file("https://cdn.test/a.jpg", "a.jpg")], contact_respond_id="C1", turn_id=None
        )


def test_strip_raw_links_removes_only_the_link_lines():
    url = "https://cdn.test/a.jpg"
    text = f"1. *Product Code:* X1\n*File Name:* a.jpg\n*Link:* {url}\n\n_Data last updated: 1/1_"
    assert file_links.strip_raw_links(text, [_file(url, "a.jpg")]) == (
        "1. *Product Code:* X1\n*File Name:* a.jpg\n\n_Data last updated: 1/1_"
    )


@pytest.mark.parametrize(
    ("name", "mime", "sheet"),
    [
        ("a.xlsx", None, True),
        ("a.XLS", None, True),
        ("a.xlsm", None, True),
        ("a.csv", "text/csv", True),
        ("a.pdf", "application/pdf", False),
        ("a.jpg", "image/jpeg", False),
        (None, None, False),
    ],
)
def test_is_sheet(name, mime, sheet):
    assert file_links.is_sheet(name, mime) is sheet


def test_a_second_url_of_one_image_from_another_section_is_rewritten_too(db):
    """Security review M1: compose keeps one entry per image and carries the other
    section's url as an alias, so no row is left naming a stored url."""
    a, b = "https://cdn.test/a.jpg", "https://cdn.test/b.jpg"
    entry = {**_file(a, "a.jpg", "h1"), "aliasUrls": [b]}
    out, minted = file_links.links_for(
        db, f"*Link:* {a}\n\n*Link:* {b}", [entry], contact_respond_id="C1", turn_id=None
    )
    assert "cdn.test" not in out
    assert len(set(_codes(out))) == 1 and minted == 1


def test_any_line_still_naming_our_cdn_is_dropped(monkeypatch):
    monkeypatch.setenv("R2_CDN_DOMAIN", "cdn.test")
    monkeypatch.delenv("CLOUDFRONT_DOMAIN", raising=False)
    text = "1. *Product Code:* X1\n*Link:* https://cdn.test/x.jpg\n*Link:* https://crm.test/f/ABC"
    assert file_links.strip_storage_urls(text) == "1. *Product Code:* X1\n*Link:* https://crm.test/f/ABC"


def test_turning_the_expiry_on_later_closes_links_already_sent(db):
    """Security review M3: the setting as it stands now applies to every link."""
    db.add(
        ChatbotFileLink(
            code="OLDLINK000000001",
            target_url="https://cdn.test/a.jpg",
            filename="a.jpg",
            created_at=datetime.utcnow() - timedelta(days=10),
        )
    )
    db.commit()
    assert file_links.resolve(db, "OLDLINK000000001") is not None
    db.add(SystemSetting(id=str(uuid.uuid4()), name="ZZT co", chatbot_file_link_expiry_days=7))
    db.commit()
    assert file_links.resolve(db, "OLDLINK000000001") is None
