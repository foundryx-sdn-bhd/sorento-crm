"""CA-3 owner live run at 58e50c3f4, RED (E1 = Q11, E2 = Q12). Parser and extractor are fakes.

E1  on a chat report the parser's customer / product ENTITIES never filter the report and never
    make a miss line; the customer and product filter words come only from the extractor's reading
    (`customer_words`, `product_words`), resolved by the lane. A word the extractor names that
    resolves to nothing keeps the UAC-5 miss line.
E2  "top discussed water closet and basin for sorento in 2026": after the "top 5" reply the route
    runs with category_ids + brand_ids and no product filter, and no "I couldn't find that product."
"""
from __future__ import annotations

import pytest

from tests.chatbot.test_chat_report_lane import (  # noqa: F401  (fixtures and helpers by name)
    COUNT_Q,
    CUSTOMER_NAME,
    PRODUCT_CODE,
    _chat,
    _chat_calls,
    _e,
    _reply,
    console,
)
from tests.chatbot.test_chat_report_lane_owner_live import _fake_extractor, _seed_categories

READING = {"group_by": "category", "measure": "asks", "subject": None, "top_n": 5,
           "date_from": "2026-01-01", "date_to": "2026-12-31",
           "askers": ["salesperson"], "category_words": [], "brand_words": [],
           "customer_words": [], "product_words": []}


def _brand(monkeypatch):
    from app.services.chatbot.lanes.business import services

    monkeypatch.setattr(services, "resolve_brand_token",
                        lambda db, token, *a, **k: [("brand-s", "Sorento")] if token.lower() == "sorento" else [])


def test_e1_parser_customer_and_product_tags_on_a_word_do_not_filter_or_miss(console, monkeypatch) -> None:
    _brand(monkeypatch)
    _fake_extractor(monkeypatch, lambda t, o: {**READING, "brand_words": ["sorento"]})
    verdict = _chat(_e("salesperson", "customer"), _e("salesperson", "product"), group_by="category")
    text, calls = console.say(verdict, "top category sorento salespeople asked about in 2026")
    (args,) = _chat_calls(calls)
    assert "customer_ids" not in args and "product_ids" not in args, args
    assert args["askers"] == ["salesperson"] and args["group_by"] == "category" and args["brand_ids"] == ["brand-s"], args
    assert "couldn't find" not in text.lower(), text


def test_e1_a_real_parser_customer_entity_is_not_a_filter_unless_the_extractor_names_it(console, monkeypatch) -> None:
    _fake_extractor(monkeypatch, lambda t, o: {**READING, "group_by": "topic", "askers": []})
    _text, calls = console.say(_chat(_e(CUSTOMER_NAME, "customer")), "top topics in 2026")
    (args,) = _chat_calls(calls)
    assert "customer_ids" not in args, args


def test_e1_an_extractor_customer_word_still_resolves(console, monkeypatch) -> None:
    _fake_extractor(monkeypatch, lambda t, o: {**READING, "group_by": "topic", "askers": [],
                                              "customer_words": [CUSTOMER_NAME]})
    _text, calls = console.say(_chat(), f"top topics from {CUSTOMER_NAME} in 2026")
    (args,) = _chat_calls(calls)
    assert args["customer_ids"] == [console.ids["customer"]], args


def test_e1_an_extractor_product_word_still_resolves(console, monkeypatch) -> None:
    _fake_extractor(monkeypatch, lambda t, o: {**READING, "group_by": "topic", "askers": [],
                                              "product_words": [PRODUCT_CODE]})
    _text, calls = console.say(_chat(), f"top topics about {PRODUCT_CODE} in 2026")
    (args,) = _chat_calls(calls)
    assert args["product_ids"] == [console.ids["product"]], args


@pytest.mark.parametrize("key,word", [("customer_words", "Nonexistent Trading Sdn Bhd"),
                                      ("product_words", "ZZTNOSUCHCODE9")])
def test_e1_an_extractor_word_that_resolves_to_nothing_keeps_the_miss_line(console, monkeypatch, key, word) -> None:
    _fake_extractor(monkeypatch, lambda t, o: {**READING, "group_by": "topic", "askers": [], key: [word]})
    text, calls = console.say(_chat(), f"top topics about {word} in 2026")
    assert _chat_calls(calls) == [] and "I couldn't find" in text and word in text, (text, calls)


@pytest.mark.parametrize(
    "entities",
    [
        pytest.param([("water closet", "category"), ("basin", "category")], id="categories"),
        pytest.param([("water closet", "category"), ("basin", "product")], id="category_and_product"),
        pytest.param([("water closet", "product"), ("basin", "product")], id="products_only"),
        pytest.param([("water closet", "category"), ("basin", "category"), ("sorento", "product")],
                     id="brand_tagged_as_product"),
    ],
)
@pytest.mark.parametrize(
    "reply_verdict",
    [
        pytest.param(lambda ents: _reply(top_n=5, entities=list(ents)), id="reply_repeats_the_words"),
        pytest.param(lambda ents: _reply(top_n=5, entities=[_e("top 5", "product")]), id="reply_tags_the_count_as_product"),
        pytest.param(lambda ents: _chat(*ents, group_by="product", top_n=5, date_filter_start=None,
                                        date_filter_end=None), id="reply_is_read_as_a_chat_report"),
    ],
)
def test_e2_count_reply_runs_with_categories_and_brand_and_no_product_miss(
    console, monkeypatch, entities, reply_verdict
) -> None:
    ids = _seed_categories(console)
    _brand(monkeypatch)
    first = "what is the top discussed zzt closet and zzt basin for sorento in 2026"
    held = {**READING, "group_by": "product", "askers": [], "top_n": None,
            "category_words": ["zzt closet", "zzt basin"], "brand_words": ["sorento"]}
    _fake_extractor(monkeypatch, lambda t, o: held if t == first else {**held, "top_n": 5})
    verdict_entities = [_e(w.replace("water closet", "zzt closet").replace("basin", "zzt basin"), h)
                        for w, h in entities]
    text, calls = console.say(_chat(*verdict_entities, group_by="product", top_n=None), first)
    assert text.strip() == "How many? For example top 5.", text
    text, calls = console.say(reply_verdict(verdict_entities), "top 5")
    assert "couldn't find" not in text.lower(), text
    (args,) = _chat_calls(calls)
    assert sorted(args["category_ids"]) == sorted([ids["closet_a"], ids["closet_b"], ids["basin_a"], ids["basin_b"]]), args
    assert args["brand_ids"] == ["brand-s"] and "product_ids" not in args and args["group_by"] == "product", args
