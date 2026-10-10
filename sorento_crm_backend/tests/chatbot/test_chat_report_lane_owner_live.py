"""CA-3 owner live run (PR #1553, crew note at 514753927), RED: four lane defects.

Full console turns like `test_chat_report_lane.py`; the parser and the extractor are fakes (NO
provider call), the MCP transport is a recorder. Every extractor here is installed by the test
itself (the module's autouse stub does not apply to this file).

D1  a STAFF contact (active CRM user, role admin) that also has customer links is not a dealer:
    the lane must not read `customer_scope["enforced"]` as "dealer", and the engine's generic
    customer-scope gate must not answer "isn't under your account" on a chat_analytics turn.
    A real dealer (links, no user) keeps the dealer refusal for a non-dealer axis.
D2  the answering turn: the extractor is called ONCE more with the reply text and the open ask
    (`extract_chat_report(db, text, today=..., open_ask=<dict>)`); the held axis, measure and
    period are merged with what the reply fills; the parser's re-guess never overrides them.
    Pinned: `open_ask` carries the extractor's own key names (`group_by`, `measure`, `date_from`,
    `date_to`). This supersedes the older pin that the reply is not re-read by the extractor.
D3  category words resolve through the owner vocabulary, `business_services.resolve_category_class`
    (-> `product_class_signal.resolve_classes_for_term`: `product_categories.class_label` and
    `search_synonyms`, exact and case-insensitive), not only code / name.
D4  the parser's product entity "salesperson" never yields a miss line on a chat report reply.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text as sql

from tests.chatbot.test_chat_report_lane import (  # noqa: F401  (fixtures and helpers by name)
    COUNT_Q,
    PERIOD_Q,
    SEP,
    _chat,
    _chat_calls,
    _e,
    _reply,
    console,
)
from tests.chatbot.test_engine import CONTACT_ID
from tests.chatbot.test_report_ask_lane import _link_dealer, _other_customer

FULL = {"group_by": "topic", "measure": "asks", "subject": None, "top_n": 5,
        "date_from": "2026-01-01", "date_to": "2026-12-31",
        "askers": [], "category_words": [], "brand_words": []}
REFUSALS = ("not available for your account", "isn't under your account", "couldn't find", "could not find")


def _fake_extractor(monkeypatch, reading_for):
    """`reading_for(text, open_ask)` -> dict | None. Records `(text, open_ask)` per call."""
    from app.services.chatbot.analytics import chat_report_extractor

    seen: list[tuple[str, object]] = []

    def fake(db, text, *args, open_ask=None, **kwargs):
        seen.append((text, open_ask))
        got = reading_for(text, open_ask)
        return dict(got) if got is not None else None

    monkeypatch.setattr(chat_report_extractor, "extract_chat_report", fake)
    return seen


def _make_staff(console) -> None:
    """Link the harness contact to an ACTIVE CRM user holding the role `admin`."""
    from app.models.user import User, UserRole, UserRoleAssignment

    db = console.session_factory()
    try:
        contact_id = db.execute(
            sql("SELECT id FROM respond_contacts WHERE respond_io_id = :c"), {"c": str(CONTACT_ID)}
        ).scalar_one()
        role = db.query(UserRole).filter_by(slug="admin").first()
        if role is None:
            role = UserRole(id=str(uuid.uuid4()), slug="admin", name=f"ZZT Admin {uuid.uuid4().hex[:6]}")
            db.add(role)
            db.flush()
        user_id = str(uuid.uuid4())
        db.add(User(id=user_id, email=f"zzt-{user_id[:8]}@zzt.test", name="ZZT Staff Admin",
                    status="ACTIVE", respond_contact_id=contact_id))
        db.flush()
        db.add(UserRoleAssignment(id=str(uuid.uuid4()), user_id=user_id, role_id=role.id))
        db.commit()
    finally:
        db.close()


# --------------------------------------------------------------------------- D1


@pytest.mark.parametrize("group_by", ["customer", "salesperson", "category"])
def test_a_staff_contact_with_customer_links_is_not_a_dealer(console, monkeypatch, group_by) -> None:
    _link_dealer(console)
    _make_staff(console)
    _fake_extractor(monkeypatch, lambda t, o: {**FULL, "group_by": group_by})
    text, calls = console.say(_chat(group_by=group_by), f"top 5 {group_by} who asked the most in 2026")
    assert not any(r in text.lower() for r in REFUSALS), text
    (args,) = _chat_calls(calls)
    assert args["group_by"] == group_by, args


def test_a_staff_contact_with_links_naming_another_customer_is_not_refused_by_the_scope_gate(
    console, monkeypatch
) -> None:
    _link_dealer(console)
    _make_staff(console)
    other = _other_customer(console, "ZZT STAFF LOOKUP SDN BHD")
    _fake_extractor(monkeypatch, lambda t, o: FULL)
    text, calls = console.say(
        _chat(_e("ZZT STAFF LOOKUP SDN BHD", "customer")), "top 5 questions from ZZT STAFF LOOKUP SDN BHD in 2026"
    )
    assert "isn't under your account" not in text, text
    (args,) = _chat_calls(calls)
    assert args["customer_ids"] == [other], args


def test_a_real_dealer_still_gets_the_dealer_refusal_for_a_non_dealer_axis(console, monkeypatch) -> None:
    _link_dealer(console)
    _fake_extractor(monkeypatch, lambda t, o: {**FULL, "group_by": "salesperson"})
    text, calls = console.say(_chat(group_by="salesperson"), "top 5 salesperson who asked the most in 2026")
    assert _chat_calls(calls) == [], calls
    assert "That breakdown is not available for your account." in text, text


# --------------------------------------------------------------------------- D2

REPLY_SHAPES = {
    "fills_only": lambda top_n, start, end: {
        "group_by": None, "measure": None, "subject": None, "top_n": top_n,
        "date_from": start, "date_to": end, "askers": [], "category_words": [], "brand_words": []},
    "merged": None,  # filled per case with the held axis and measure
}


def _answering_turn(console, monkeypatch, *, first, held, first_verdict, reply_verdict, reply_text, reply_reading):
    """First message opens the question; the reply goes through the lane. Returns
    `(seen, text, calls)` of the reply turn."""
    readings = {first: held}

    def reading_for(text, open_ask):
        return readings.get(text) if text == first else reply_reading

    seen = _fake_extractor(monkeypatch, reading_for)
    text, calls = console.say(first_verdict, first)
    assert calls == [], calls
    first_text = text
    text, calls = console.say(reply_verdict, reply_text)
    return seen, first_text, text, _chat_calls(calls)


@pytest.mark.parametrize("shape", ["fills_only", "merged"])
@pytest.mark.parametrize(
    "reply_verdict",
    [
        pytest.param(lambda: _chat(group_by="month", top_n=5, date_filter_start="2026-10-07",
                                   date_filter_end="2026-10-07"), id="parser_guesses_month_and_one_day"),
        pytest.param(lambda: _chat(group_by=None, measure=None, top_n=5, date_filter_start="2026-10-07",
                                   date_filter_end="2026-10-07"), id="parser_guesses_null_axis_and_one_day"),
    ],
)
def test_a_period_and_count_reply_keeps_the_held_axis_and_calls_the_extractor_with_the_open_ask(
    console, monkeypatch, shape, reply_verdict
) -> None:
    first = "top questions customers ask"
    held = {**FULL, "top_n": None, "date_from": None, "date_to": None}
    reading = (
        REPLY_SHAPES["fills_only"](5, "2026-01-01", "2026-12-31") if shape == "fills_only"
        else {**FULL, "top_n": 5}
    )
    seen, first_text, text, calls = _answering_turn(
        console, monkeypatch, first=first, held=held,
        first_verdict=_chat(top_n=None, date_filter_start=None, date_filter_end=None),
        reply_verdict=reply_verdict(), reply_text="this year, top 5", reply_reading=reading,
    )
    assert PERIOD_Q in first_text and COUNT_Q in first_text, first_text
    assert [t for t, _ in seen] == [first, "this year, top 5"], seen
    open_ask = seen[1][1]
    assert isinstance(open_ask, dict), "the reply is read WITH the open ask"
    assert open_ask.get("group_by") == "topic" and open_ask.get("measure") == "asks", open_ask
    (args,) = calls
    assert args["group_by"] == "topic" and args["measure"] == "asks" and args["top_n"] == 5, args
    assert args["date_from"] == "2026-01-01" and args["date_to"] == "2026-12-31", args


@pytest.mark.parametrize("shape", ["fills_only", "merged"])
@pytest.mark.parametrize(
    "reply_verdict",
    [
        pytest.param(lambda: _chat(group_by="month", top_n=5, date_filter_start="2026-10-07",
                                   date_filter_end="2026-10-07"), id="parser_guesses_month_and_one_day"),
        pytest.param(lambda: _chat(group_by=None, measure=None, top_n=5, date_filter_start=None,
                                   date_filter_end=None), id="parser_guesses_null_axis"),
    ],
)
def test_a_count_reply_keeps_the_held_period_and_never_asks_which_period(
    console, monkeypatch, shape, reply_verdict
) -> None:
    first = "top products customers ask about from 7 to 10 Oct"
    held = {**FULL, "group_by": "product", "top_n": None, "date_from": "2026-10-07", "date_to": "2026-10-10"}
    reading = (
        REPLY_SHAPES["fills_only"](5, None, None) if shape == "fills_only"
        else {**held, "top_n": 5}
    )
    seen, first_text, text, calls = _answering_turn(
        console, monkeypatch, first=first, held=held,
        first_verdict=_chat(group_by="product", top_n=None,
                            date_filter_start="2026-10-07", date_filter_end="2026-10-10"),
        reply_verdict=reply_verdict(), reply_text="top 5", reply_reading=reading,
    )
    assert first_text.strip() == COUNT_Q, first_text
    assert [t for t, _ in seen] == [first, "top 5"], seen
    open_ask = seen[1][1]
    assert isinstance(open_ask, dict) and open_ask.get("group_by") == "product", open_ask
    assert open_ask.get("date_from") == "2026-10-07" and open_ask.get("date_to") == "2026-10-10", open_ask
    assert PERIOD_Q not in text, text
    (args,) = calls
    assert args["group_by"] == "product" and args["top_n"] == 5, args
    assert args["date_from"] == "2026-10-07" and args["date_to"] == "2026-10-10", args


# --------------------------------------------------------------------------- D3


def _seed_categories(console) -> dict[str, str]:
    """Four real categories under unique class labels. Category names are copies of the code
    (as on live rows), so only the class vocabulary can reach them."""
    from app.models.product import ProductCategory
    from app.services.company_scope import DEFAULT_COMPANY_ID

    tag = uuid.uuid4().hex[:6].upper()
    rows = {
        "closet_a": (f"ZZT-SRT-WC-{tag}", "ZZT Closet", ["zzt closet", "zzt wc", "zzt toilet"]),
        "closet_b": (f"ZZT-BRT-WC-{tag}", "ZZT Closet", ["zzt closet", "zzt wc", "zzt toilet"]),
        "basin_a": (f"ZZT-SRT-BA-{tag}", "ZZT Wash Basin", ["zzt basin", "zzt wash basin"]),
        "basin_b": (f"ZZT-BRT-BA-{tag}", "ZZT Wash Basin", ["zzt basin", "zzt wash basin"]),
    }
    db = console.session_factory()
    try:
        ids = {}
        for key, (code, label, synonyms) in rows.items():
            row = ProductCategory(
                id=str(uuid.uuid4()), category_code=code, category_name=code, class_label=label,
                search_synonyms=synonyms, is_searchable=True, company_id=DEFAULT_COMPANY_ID,
            )
            db.add(row)
            ids[key] = row.id
        db.commit()
        return ids
    finally:
        db.close()


@pytest.mark.parametrize(
    "word,keys",
    [
        ("zzt closet", ("closet_a", "closet_b")),
        ("ZZT Closet", ("closet_a", "closet_b")),
        ("zzt toilet", ("closet_a", "closet_b")),
        ("zzt basin", ("basin_a", "basin_b")),
    ],
)
def test_category_words_resolve_through_class_label_and_synonyms(console, monkeypatch, word, keys) -> None:
    ids = _seed_categories(console)
    _fake_extractor(monkeypatch, lambda t, o: {**FULL, "group_by": "product", "category_words": [word]})
    text, calls = console.say(_chat(group_by="product"), f"top discussed {word} in 2026")
    assert "I couldn't find" not in text, text
    (args,) = _chat_calls(calls)
    assert sorted(args["category_ids"]) == sorted(ids[k] for k in keys), (args, text)


def test_a_partial_category_word_is_never_fuzzy(console, monkeypatch) -> None:
    _seed_categories(console)
    _fake_extractor(monkeypatch, lambda t, o: {**FULL, "group_by": "product", "category_words": ["zzt clos"]})
    text, calls = console.say(_chat(group_by="product"), "top discussed zzt clos in 2026")
    assert _chat_calls(calls) == [] and "I couldn't find" in text, (text, calls)


# --------------------------------------------------------------------------- D4


@pytest.mark.parametrize("word", ["salesperson", "sales person"])
def test_the_word_salesperson_as_a_product_entity_never_yields_a_miss_line_on_the_reply(
    console, monkeypatch, word
) -> None:
    first = "top questions customers ask"
    held = {**FULL, "top_n": None}
    seen = _fake_extractor(
        monkeypatch,
        lambda t, o: held if t == first else {**held, "top_n": 5},
    )
    text, calls = console.say(_chat(top_n=None, **SEP), first)
    assert text.strip() == COUNT_Q and calls == [], (text, calls)
    text, calls = console.say(_reply(top_n=5, entities=[_e(word, "product")], **SEP), "top 5")
    lowered = text.lower()
    assert not any(m in lowered for m in ("couldn't find", "could not find", "did you mean", "i don't know")), text
    assert len(_chat_calls(calls)) == 1, (text, calls)
