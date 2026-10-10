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


# --------------------------------------------------------------------------- E2 rebuilt from the live trace


def _seed_live_vocabulary(console) -> dict[str, list[str]]:
    """Categories whose class labels / synonyms are the live words, and a brand row Sorento."""
    import uuid

    from app.models.product import Brand, ProductCategory
    from app.services.company_scope import DEFAULT_COMPANY_ID

    tag = uuid.uuid4().hex[:6].upper()
    db = console.session_factory()
    try:
        cats: dict[str, list[str]] = {"water closet": [], "basin": []}
        for word, label, syn in (("water closet", "Water Closet", ["water closet", "wc", "toilet"]),
                                 ("basin", "Wash Basin", ["basin", "wash basin"])):
            for pre in ("SRT", "BRT"):
                row = ProductCategory(id=str(uuid.uuid4()), category_code=f"{pre}-{word[:2].upper()}-{tag}",
                                      category_name=f"{pre}-{word[:2].upper()}-{tag}", class_label=label,
                                      search_synonyms=syn, is_searchable=True, company_id=DEFAULT_COMPANY_ID)
                db.add(row)
                cats[word].append(row.id)
        brand = Brand(id=str(uuid.uuid4()), brand_code="SORENTO", brand_name="Sorento", company_id=DEFAULT_COMPANY_ID)
        db.add(brand)
        db.commit()
        return {**cats, "brand": [brand.id]}
    finally:
        db.close()


LIVE_FIRST = "what is the top discussed water closet and basin for sorento in 2026"


def _live_entities():
    return [_e("water closet", "category"), _e("basin", "category"),
            {**_e("Sorento", "brand"), "canonical_code": "SORENTO"}]


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(lambda: _reply(chat_report={"group_by": None, "measure": None}, top_n=5), id="a_null_axis"),
        pytest.param(lambda: _reply(intent_hint="check_order", chat_report={"group_by": "topic", "measure": "asks"},
                                    top_n=5), id="b_check_order_intent"),
        pytest.param(lambda: _reply(intent_hint="chat_report", domain_hint="chat_analytics",
                                    chat_report={"group_by": "topic", "measure": "asks"}, top_n=5),
                     id="c_chat_report_topic"),
    ],
)
def test_e2_live_trace_count_reply_runs_with_categories_and_brand(console, monkeypatch, reply) -> None:
    seeded = _seed_live_vocabulary(console)
    held = {**READING, "group_by": "topic", "askers": [], "top_n": None,
            "category_words": ["water closet", "basin"], "brand_words": ["sorento"]}
    _fake_extractor(monkeypatch, lambda t, o: held if t == LIVE_FIRST else {**held, "top_n": 5})
    first = _chat(*_live_entities(), group_by="topic", top_n=None)
    text, calls = console.say(first, LIVE_FIRST)
    assert text.strip() == COUNT_Q and calls == [], (text, calls)
    text, calls = console.say(reply(), "top 5")
    assert "couldn't find" not in text.lower(), text
    (args,) = _chat_calls(calls)
    assert set(seeded["water closet"] + seeded["basin"]) <= set(args["category_ids"]), args
    assert seeded["brand"][0] in args["brand_ids"] and "product_ids" not in args, args
    assert args["top_n"] == 5 and args["date_from"] == "2026-01-01", args


def test_e2_a_category_word_that_prefixes_a_product_code_never_becomes_a_product_filter(console, monkeypatch) -> None:
    """The live difference: the owner's catalogue has product codes that START with the typed
    words (`_resolve_products` matches by code prefix), so the parser's category / brand tags were
    resolved as PRODUCTS and the route answered 404 'Product not found' ("I couldn't find that
    product."). The category and brand words are the extractor's; no product filter is built."""
    import uuid

    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._mc_lookup_seed import product

    db = console.session_factory()
    try:
        tag = uuid.uuid4().hex[:5].upper()
        for prefix in ("BASIN", "WATER CLOSET", "SORENTO"):
            product(db, company_id=DEFAULT_COMPANY_ID, code=f"{prefix} {tag}")
        db.commit()
    finally:
        db.close()
    seeded = _seed_live_vocabulary(console)
    held = {**READING, "group_by": "topic", "askers": [], "top_n": None,
            "category_words": ["water closet", "basin"], "brand_words": ["sorento"]}
    _fake_extractor(monkeypatch, lambda t, o: held if t == LIVE_FIRST else {**held, "top_n": 5})
    text, calls = console.say(_chat(*_live_entities(), group_by="topic", top_n=None), LIVE_FIRST)
    assert text.strip() == COUNT_Q, text
    _text, calls = console.say(_reply(chat_report={"group_by": None, "measure": None}, top_n=5), "top 5")
    (args,) = _chat_calls(calls)
    assert "product_ids" not in args, args
    assert seeded["brand"][0] in args["brand_ids"], args
