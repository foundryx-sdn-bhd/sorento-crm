"""CA-3 owner hand test round, RED: the lane resolves category / brand words and passes the asker
axis through (UAC-27, UAC-30). Full console turns like `test_chat_report_lane.py`; the parser and
the extractor are fakes (NO provider call), the MCP transport is a recorder.

Pinned where the plan is silent (the coder follows, or asks the captain to change the test):
* the extractor result gains `askers`, `category_words`, `brand_words` (see
  `test_chat_report_extractor_askers.py`); the lane reads them from the same dict;
* route args: `askers` (the list as read, omitted when empty), `category_ids`, `brand_ids` (omitted
  when no word), `group_by` passed through for `asker`, `salesperson`, `category`, `brand`;
* the lane calls `business_services.resolve_category_token(db, word)` ->
  `[(id, code, name)]` and `resolve_brand_token(db, word)` -> `[(id, name)]` through the module
  attribute (so a monkeypatch of `lanes.business.services` takes effect), once per word;
* a word that resolves to nothing: one reply containing "I couldn't find", no route call, and the
  word is quoted. A word with several matches keeps them all (the resolvers return the ask).
"""
from __future__ import annotations

import pytest

from tests.chatbot.test_chat_report_lane import (  # noqa: F401  (fixtures and helpers by name)
    SEP,
    _chat,
    _chat_calls,
    console,
)

BASE = {"group_by": "topic", "measure": "asks", "subject": None, "top_n": 5,
        "date_from": "2026-01-01", "date_to": "2026-12-31",
        "askers": [], "category_words": [], "brand_words": []}


def _extractor(monkeypatch, **over):
    from app.services.chatbot.analytics import chat_report_extractor

    reading = {**BASE, **over}
    monkeypatch.setattr(chat_report_extractor, "extract_chat_report", lambda db, text, *a, **k: dict(reading))


def _resolvers(monkeypatch, categories=None, brands=None):
    from app.services.chatbot.lanes.business import services

    seen = {"category": [], "brand": []}

    def category(db, token, *a, **k):
        seen["category"].append(token)
        return list((categories or {}).get(token.lower(), []))

    def brand(db, token, *a, **k):
        seen["brand"].append(token)
        return list((brands or {}).get(token.lower(), []))

    monkeypatch.setattr(services, "resolve_category_token", category)
    monkeypatch.setattr(services, "resolve_brand_token", brand)
    return seen


@pytest.mark.parametrize("group_by", ["asker", "salesperson", "category", "brand"])
def test_the_new_axes_go_through_as_group_by(console, monkeypatch, group_by):
    _extractor(monkeypatch, group_by=group_by)
    _text, calls = console.say(_chat(), "top things asked in 2026")
    (args,) = _chat_calls(calls)
    assert args["group_by"] == group_by and "askers" not in args, args


def test_askers_go_as_a_list_and_empty_askers_are_left_out(console, monkeypatch):
    _extractor(monkeypatch, group_by="topic", askers=["dealer", "unclassified"])
    _text, calls = console.say(_chat(), "top topics dealers asked in 2026")
    (args,) = _chat_calls(calls)
    assert args["askers"] == ["dealer", "unclassified"], args


def test_category_and_brand_words_resolve_to_ids(console, monkeypatch):
    _extractor(monkeypatch, group_by="product", category_words=["water closet", "basin"], brand_words=["sorento"])
    seen = _resolvers(
        monkeypatch,
        categories={"water closet": [("cat-wc", "WC", "WATER CLOSET")], "basin": [("cat-ba", "BA", "BASIN")]},
        brands={"sorento": [("brand-s", "Sorento")]},
    )
    _text, calls = console.say(_chat(), "top discussed water closet and basin for sorento in 2026")
    (args,) = _chat_calls(calls)
    assert args["category_ids"] == ["cat-wc", "cat-ba"] and args["brand_ids"] == ["brand-s"], args
    assert seen == {"category": ["water closet", "basin"], "brand": ["sorento"]}, seen


def test_a_category_alone_leaves_no_brand_arg(console, monkeypatch):
    _extractor(monkeypatch, category_words=["basin"])
    _resolvers(monkeypatch, categories={"basin": [("cat-ba", "BA", "BASIN")]})
    _text, calls = console.say(_chat(), "top basin questions in 2026")
    (args,) = _chat_calls(calls)
    assert args["category_ids"] == ["cat-ba"] and "brand_ids" not in args, args


def test_an_unresolvable_category_word_is_one_line_and_no_call(console, monkeypatch):
    _extractor(monkeypatch, category_words=["flying carpets"])
    _resolvers(monkeypatch)
    text, calls = console.say(_chat(), "top flying carpets asked in 2026")
    assert calls == [], "never an empty ranking that looks like data"
    assert "I couldn't find" in text and "flying carpets" in text and "\n" not in text.strip(), text


def test_an_unresolvable_brand_word_is_one_line_and_no_call(console, monkeypatch):
    _extractor(monkeypatch, brand_words=["nosuchbrand"])
    _resolvers(monkeypatch)
    text, calls = console.say(_chat(), "top products for nosuchbrand in 2026")
    assert calls == [] and "I couldn't find" in text and "nosuchbrand" in text, (text, calls)


def test_one_good_word_does_not_hide_a_bad_one(console, monkeypatch):
    _extractor(monkeypatch, category_words=["basin", "flying carpets"])
    _resolvers(monkeypatch, categories={"basin": [("cat-ba", "BA", "BASIN")]})
    text, calls = console.say(_chat(), "top basin and flying carpets asked in 2026")
    assert calls == [] and "I couldn't find" in text and "flying carpets" in text, (text, calls)


def test_a_period_reply_keeps_the_resolved_ids(console, monkeypatch):
    """No period on the first message: ask, then the reply runs the ask WITH the ids resolved."""
    _extractor(monkeypatch, date_from=None, date_to=None, category_words=["basin"], brand_words=["sorento"])
    _resolvers(monkeypatch, categories={"basin": [("cat-ba", "BA", "BASIN")]},
               brands={"sorento": [("brand-s", "Sorento")]})
    text, calls = console.say(_chat(date_filter_start=None, date_filter_end=None), "top basin for sorento")
    assert calls == [] and "period" in text.lower(), text
    from tests.chatbot.test_chat_report_lane import _reply

    _text, calls = console.say(_reply(**SEP), "September")
    (args,) = _chat_calls(calls)
    assert args["category_ids"] == ["cat-ba"] and args["brand_ids"] == ["brand-s"], args
