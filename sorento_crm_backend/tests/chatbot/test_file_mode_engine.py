"""PHOTO-SEND-CAP round 2: the engine's one seam between a composed reply and its send.

`engine._apply_file_mode` is called by both tails (`run_tail` and the composer's tail).
Attachment mode is today's behaviour; link mode sends the text alone with short links;
a link mode that cannot mint falls back to attachments (never stuck), and the raw
storage url never reaches the customer.
"""
from __future__ import annotations

import pytest

import app.main  # noqa: F401  isort:skip
from app.services.chatbot import engine, file_links
from app.services.chatbot.turn.state import Profile

URL = "https://cdn.test/a.jpg"
FILES = [{"url": URL, "filename": "a.jpg", "mimeType": "image/jpeg", "fileHash": "h1"}]
TEXT = f"I have attached the file(s) below.\n\n1. *Product Code:* X1\n*File Name:* a.jpg\n*Link:* {URL}"


class _Db:
    def close(self) -> None:
        pass


def _factory():
    return _Db()


def test_attachment_mode_is_untouched():
    text, files, mode = engine._apply_file_mode(
        "words", FILES, profile=Profile(), session_factory=_factory, contact_respond_id="C1", turn_id="T1"
    )
    assert (text, files, mode) == ("words", FILES, "attachments")


def test_no_files_is_no_mode():
    text, files, mode = engine._apply_file_mode(
        "words", [], profile=Profile(files_as_links=True), session_factory=_factory, contact_respond_id="C1", turn_id="T1"
    )
    assert (text, files, mode) == ("words", [], None)


def test_link_mode_sends_the_text_alone(monkeypatch):
    calls = []

    def _links_for(db, text, files, *, contact_respond_id, turn_id):
        calls.append((contact_respond_id, turn_id, len(files)))
        return text.replace(URL, "https://crm.test/f/ABCDEFGHJK"), 1

    monkeypatch.setattr(file_links, "links_for", _links_for)
    text, files, mode = engine._apply_file_mode(
        TEXT, FILES, profile=Profile(files_as_links=True), session_factory=_factory, contact_respond_id="C1", turn_id="T1"
    )
    assert files == []
    assert mode == "links"
    assert URL not in text and "https://crm.test/f/ABCDEFGHJK" in text
    assert calls == [("C1", "T1", 1)]


@pytest.mark.parametrize("error", [file_links.LinkModeUnavailable("no base url"), RuntimeError("db down")])
def test_a_link_mode_that_cannot_mint_falls_back_to_attachments(monkeypatch, error):
    def _boom(*a, **kw):
        raise error

    monkeypatch.setattr(file_links, "links_for", _boom)
    text, files, mode = engine._apply_file_mode(
        TEXT, FILES, profile=Profile(files_as_links=True), session_factory=_factory, contact_respond_id="C1", turn_id="T1"
    )
    assert files == FILES
    assert mode == "attachments"
    assert URL not in text
    assert "*File Name:* a.jpg" in text


def test_link_mode_drops_the_attached_sentence(monkeypatch):
    monkeypatch.setattr(file_links, "links_for", lambda db, text, files, **kw: (text, 1))
    text, _files, _mode = engine._apply_file_mode(
        TEXT, FILES, profile=Profile(files_as_links=True), session_factory=_factory, contact_respond_id="C1", turn_id="T1"
    )
    assert "attached" not in text
    assert text.startswith("1. *Product Code:* X1")
