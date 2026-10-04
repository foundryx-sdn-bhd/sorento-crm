"""Owner hand test round 2 on PR #833 (26 Sep 2026, contact 900000039, a dealer spanning
Mocha + Sorento). W1 to W6 of the fix brief, as whole turns.

Each case runs `engine.run_turn` with the v3 verdict shape, the real resolver, the real
class vocabulary and the real brands table, with only the MCP tool call stubbed (same
substrate as `test_reverse_asks_owner_phrasings.py`).

The owner's turns this pins (verbatim):
  5. "whici sorento wash basin has stock" -> "2,306 products have stock" (brand AND class lost)
  9. "which sorento wall hung basin has stock?" -> "144 products have stock" (noun lost)
 10. "10" -> "334 products have stock. Here are the first 10" (the page dropped the brand)
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

import pytest

from tests.chatbot.set_reply import legacy_lines, one_line_header, row_blocks, row_codes, snake_tokens  # noqa: F401
from tests.chatbot.test_counted_set_no_paging import _link_to_default_company
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name
from tests.chatbot.test_lane_require import (
    _s4_contact_id,
    _s4_envelope,
    _s4_real_resolve_entity,
    _s4_seed_contact,
    _s4_wire_engine,
    _seed_category_and_uom,
    _seed_registry,
    _stock_for,
    _warehouse,
)
from tests.chatbot.test_reverse_asks_owner_phrasings import _class_category


# --------------------------------------------------------------------------- #
# World                                                                         #
# --------------------------------------------------------------------------- #


def _brand(db, name: str, *, default: bool = False):
    from app.models.product import Brand

    row = db.query(Brand).filter(Brand.brand_name == name).first()
    if row is None:
        row = Brand(id=str(uuid.uuid4()), brand_code=name.upper()[:50], brand_name=name, is_active=True)
        db.add(row)
        db.flush()
    # Round 4 R1: the round 2 "default" brand is the one weighted above the rest.
    row.chatbot_weight = 1.5 if default else 0
    db.flush()
    return row


def _product(db, *, brand_id: str, category_id: str, uom_id: str, noun: str, prefix: str, name: str | None = None):
    """A product whose description's TRAILING noun is its class (`derive_for_code` reads
    the tail), with its real brand row so the derived spec carries `brand`."""
    from app.models.product import Product
    from app.services.product_spec_derivation import derive_for_code
    from tests._pg_fixture import unique_code

    # alpha: the rounds built on this world send digit-only code tokens ("65502 eta") and
    # assert an exact result set; a hex suffix can spell those digits and the substring
    # match that then lists this product is correct app behaviour, not a defect.
    # The "Z" stops the same thing across the join: the resolver folds the separators
    # away, so a bathtub "ZZT-ZZR3T-apdcadcp" read "zzr3tapdcadcp" and matched "tap"
    # whenever its random suffix began "ap" (CI on PR #1435). The suffix's letters are
    # a to p, so with a "Z" in front no word a round searches can form across it.
    code = unique_code(f"{prefix}-Z", alpha=True)[:50]
    row = Product(
        id=str(uuid.uuid4()),
        product_code=code,
        product_name=name or f"{noun.title()} {code[-4:]}",
        description=f"{code} WHITE {noun}",
        category_id=category_id,
        base_uom_id=uom_id,
        brand_id=brand_id,
        list_price=10,
        is_active=True,
    )
    db.add(row)
    db.flush()
    derive_for_code(db, code)
    return row


@pytest.fixture()
def world(session_factory):
    """Sorento: 3 counter basins with stock and 1 without, 2 wall hung basins with stock,
    1 wall hung water closet with stock. (No taps: other suites count every tap with stock
    in the shared database.)
    Mocha: 2 counter basins and 1 wall hung basin, all with stock."""
    from app.models.product import Brand

    db = session_factory()
    # Clear any default a previous test left, so each test states its own.
    db.query(Brand).update({Brand.chatbot_weight: 0})
    _category_id, uom_id = _seed_category_and_uom(db)
    basin_category = _class_category(db, "WB")
    wc_category = _class_category(db, "WC")
    sorento = _brand(db, f"Sorento{uuid.uuid4().hex[:4]}")
    mocha = _brand(db, f"Mocha{uuid.uuid4().hex[:4]}")
    _seed_registry(db)
    wh = _warehouse(db)

    def make(brand, category, noun, prefix, name=None):
        return _product(db, brand_id=brand.id, category_id=category, uom_id=uom_id, noun=noun, prefix=prefix, name=name)

    srt_basins = [make(sorento, basin_category, "WASH BASIN", "ZZSB", f"Sorento Counter Basin {i}") for i in range(4)]
    srt_wall = [make(sorento, basin_category, "WALL HUNG WASH BASIN", "ZZSW", f"Sorento Wall Basin {i}") for i in range(2)]
    srt_wc = [make(sorento, wc_category, "WALL HUNG WATER CLOSET", "ZZSC")]
    mch_basins = [make(mocha, basin_category, "WASH BASIN", "ZZMB") for _ in range(2)]
    mch_wall = [make(mocha, basin_category, "WALL HUNG WASH BASIN", "ZZMW")]
    for p in srt_basins[:3] + srt_wall + srt_wc + mch_basins + mch_wall:
        _stock_for(db, product_id=p.id, warehouse_id=wh.id)
    db.commit()
    return {
        "db": db,
        "sorento": sorento,
        "mocha": mocha,
        "srt_basins": srt_basins,
        "srt_wall": srt_wall,
        "srt_wc": srt_wc,
        "mch_basins": mch_basins,
        "mch_wall": mch_wall,
        "every": srt_basins + srt_wall + srt_wc + mch_basins + mch_wall,
    }


def _stock_summary_tool(db, calls: list[dict[str, Any]]):
    """The stock tool's summary mode, as the owner saw it: one row per product,
    "Product Code" and "Total", titled by the code."""
    from app.models.inventory import Stock
    from app.models.product import Product

    def fake_call_tool(name: str, args: dict[str, Any]) -> str:
        calls.append({"name": name, "args": dict(args)})
        ids = list(args.get("product_ids") or [])
        rows = db.query(Product).filter(Product.id.in_(ids)).order_by(Product.product_code).all() if ids else []
        items = []
        for p in rows:
            total = sum(int(s.quantity_on_hand or 0) for s in db.query(Stock).filter(Stock.product_id == p.id))
            items.append(
                {
                    "title": p.product_code,
                    "fields": [
                        {"key": "product_code", "label": "Product Code", "value": p.product_code},
                        {"key": "total", "label": "Total", "value": total},
                    ],
                    "flags": {},
                }
            )
        return json.dumps(
            {
                "result_type": "stock",
                "intro": "Stock summary for the requested products.",
                "items": items,
                "has_result": bool(items),
            }
        )

    return fake_call_tool


def _stock_verdict(entities: list[dict[str, Any]], goal: str, **overrides: Any) -> dict[str, Any]:
    from tests.chatbot.test_engine import _parser_output

    base = dict(
        intent_hint="check_stock",
        domain_hint="inventory",
        match_mode="or",
        user_goal=goal,
        requested_attributes=["stock"],
        entities=[
            {"canonical_code": None, "current_message": True, "confident": True, **e} for e in entities
        ],
    )
    base.update(overrides)
    return _parser_output(**base)


class _Chat:
    def __init__(self, session_factory, monkeypatch, stub_parser, stub_access, world):
        self.session_factory = session_factory
        self.stub_parser = stub_parser
        self.stub_access = stub_access
        self.calls: list[dict[str, Any]] = []
        self.contact_id = _s4_contact_id(f"r2{uuid.uuid4().hex[:6]}")
        _s4_seed_contact(session_factory, contact_id=self.contact_id, session_vars={"variables": {}})
        _link_to_default_company(session_factory, self.contact_id)
        self.engine = _s4_wire_engine(
            session_factory,
            monkeypatch,
            resolve_entity=_s4_real_resolve_entity(world["db"]),
            fetch_mcp_call=_stock_summary_tool(world["db"], self.calls),
        )
        self.n = 0

    def say(self, text: str, verdict: dict[str, Any]) -> str:
        self.n += 1
        self.stub_parser(verdict)
        self.stub_access()
        turn = self.engine.run_turn(
            _s4_envelope(contact_id=self.contact_id, message_id=f"ZZT-{self.contact_id}-{self.n}", text=text),
            session_factory=self.session_factory,
        )
        assert turn.status == "done", turn.error
        reply = (turn.reply or {}).get("text") or ""
        # Round 4 R7 on PR #833: no reply carries a snake_case token.
        assert not snake_tokens(reply), (text, snake_tokens(reply), reply)
        return reply


@pytest.fixture()
def chat(session_factory, monkeypatch, stub_parser, stub_access, world):
    return _Chat(session_factory, monkeypatch, stub_parser, stub_access, world)


def _codes(products) -> set[str]:
    return {p.product_code for p in products}


def _codes_in(text: str, world) -> set[str]:
    return {p.product_code for p in world["every"] if p.product_code in text}


# The two shapes the live parser gives a brand-plus-class ask: the brand as its own
# entity, or folded into the class word's raw.
def _brand_entity_shapes(brand: str, class_word: str) -> list[list[dict[str, Any]]]:
    return [
        [{"raw": brand, "hint": "brand"}, {"raw": class_word, "hint": "product_type"}],
        [{"raw": f"{brand} {class_word}", "hint": "product_type"}],
    ]


# --------------------------------------------------------------------------- #
# W1: the brand word is honoured                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("shape", [0, 1], ids=["brand_entity", "brand_in_class_raw"])
def test_w1_sorento_wash_basin_is_the_sorento_basin_set_never_wider(chat, world, shape):
    """Owner turn 5: "whici sorento wash basin has stock" answered "2,306 products".

    Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set and its count.
    """
    brand = world["sorento"].brand_name.lower()
    text = chat.say(
        f"whici {brand} wash basin has stock",
        _stock_verdict(_brand_entity_shapes(brand, "wash basin")[shape], f"which {brand} wash basin has stock"),
    )
    assert f"Here's what you want: {_display(world['sorento'].brand_name)} wash basins with stock (5)" in text, text
    assert _codes_in(text, world) == _codes(world["srt_basins"][:3] + world["srt_wall"]), text
    # The brand word scoped the set, so it was found.
    assert "could not find" not in text, text


@pytest.mark.parametrize("shape", [0, 1], ids=["brand_entity", "brand_in_class_raw"])
def test_w1_sorento_wall_hung_basin_keeps_brand_class_and_mounting(chat, world, shape):
    """Owner turn 9: "which sorento wall hung basin has stock?" answered "144 products".

    Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set and its count.
    """
    brand = world["sorento"].brand_name.lower()
    text = chat.say(
        f"which {brand} wall hung basin has stock?",
        _stock_verdict(_brand_entity_shapes(brand, "wall hung basin")[shape], f"which {brand} wall hung basin has stock"),
    )
    assert f"Here's what you want: {_display(world['sorento'].brand_name)} wall hung wash basins with stock (2)" in text, text
    assert _codes_in(text, world) == _codes(world["srt_wall"]), text


def test_w1_wall_hung_basin_keeps_the_basin_class(chat, world):
    """Owner turn 7: "which wall hung basin has stock" answered "334 products": the
    class word at the tail of a longer term was lost, so every wall hung product
    (water closets included) qualified.

    Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set and its count.
    """
    text = chat.say(
        "which wall hung basin has stock",
        _stock_verdict([{"raw": "wall hung basin", "hint": "product_type"}], "which wall hung basin has stock"),
    )
    assert "Here's what you want: wall hung wash basins with stock (3)" in text, text
    assert _codes_in(text, world) == _codes(world["srt_wall"] + world["mch_wall"]), text


def test_w1_a_brand_word_in_the_class_term_is_a_brand_binding_off_the_brands_table(world):
    """The brand is read off `Product.brand_id` through the brands table, never off the
    derived spec value: a product re-branded since its spec row was derived still
    answers under its real brand."""
    from app.models.product_spec import ProductSpecifications
    from app.services.product_predicate_service import resolve_product_set

    db = world["db"]
    stale = world["srt_wall"][0]
    row = db.query(ProductSpecifications).filter(ProductSpecifications.product_id == stale.id).one()
    values = dict(row.values)
    values["brand"] = {**(values.get("brand") or {}), "value": world["mocha"].brand_name}
    row.values = values
    db.flush()

    brand = world["sorento"].brand_name
    outcome = resolve_product_set(db, require={"stock": True}, scope_terms=[f"{brand.lower()} wall hung basin"])

    assert outcome["brand"] == brand
    assert outcome["qualifying_total"] == 2, outcome
    assert {c["product_code"] for c in outcome["candidates"]} == _codes(world["srt_wall"])


# --------------------------------------------------------------------------- #
# W2: say what was identified, in plain words                                   #
# --------------------------------------------------------------------------- #


def _display(brand_name: str) -> str:
    return brand_name.title() if brand_name.isupper() else brand_name


@pytest.mark.parametrize("shape", [0, 1], ids=["brand_entity", "brand_in_class_raw"])
def test_w2_the_header_names_brand_type_and_mounting_in_plain_words(chat, world, shape):
    """Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set - brand,
    mounting and product type all still named in plain words, inside that one line."""
    brand = world["sorento"].brand_name
    text = chat.say(
        f"which {brand.lower()} wall hung basin has stock?",
        _stock_verdict(_brand_entity_shapes(brand.lower(), "wall hung basin")[shape], "which wall hung basin has stock"),
    )
    first = one_line_header(text)
    assert first == f"Here's what you want: {_display(brand)} wall hung wash basins with stock (2)", text
    assert "wall_hung" not in text and "_" not in first, text


def test_w2_without_a_brand_the_header_still_names_the_spec(chat, world):
    """Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set."""
    text = chat.say(
        "which wall hung basin has stock",
        _stock_verdict([{"raw": "wall hung basin", "hint": "product_type"}], "which wall hung basin has stock"),
    )
    assert one_line_header(text) == "Here's what you want: wall hung wash basins with stock (3)", text


def test_w2_a_word_that_was_not_understood_is_said_never_silently_dropped(chat, world):
    """Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set."""
    text = chat.say(
        "which zzqx wash basin has stock",
        _stock_verdict(
            [{"raw": "wash basin", "hint": "product_type"}, {"raw": "zzqx", "hint": "product_type"}],
            "which zzqx wash basin has stock",
        ),
    )
    first = one_line_header(text)
    assert "Here's what you want: wash basins with stock" in first, text
    # Amended to fix round 9 on PR #833 (owner: "no explanations"): the word is the "did
    # not match" line of the reply, never how the search was run.
    assert 'Couldn\'t find: "zzqx".' in text and "did not understand" not in text, text


# --------------------------------------------------------------------------- #
# W3: rows a dealer can read                                                    #
# --------------------------------------------------------------------------- #


def test_w3_each_row_is_the_product_code_row_with_its_stock(chat, world):
    """Superseded in layout by round 3 W1 (owner, 26 Sep 13:07Z: "vertical, don't use |"),
    round 4 R3 (owner, 27 Sep 00:03 MYT: "one product is at most 2 lines"), and again by
    the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the row is the
    product-code answer's own row ("N. *Product Code:* X" then its facts), never the
    "N. <name> (<code>)" block."""
    brand = world["sorento"].brand_name.lower()
    text = chat.say(
        f"which {brand} wash basin has stock",
        _stock_verdict([{"raw": brand, "hint": "brand"}, {"raw": "wash basin", "hint": "product_type"}],
                       f"which {brand} wash basin has stock"),
    )
    assert "|" not in text, text
    codes = row_codes(text)
    expected = _codes(world["srt_wall"] + world["srt_basins"][:3])
    assert set(codes) == expected and len(codes) == len(expected), text
    for block in row_blocks(text):
        assert block[1:] == ["*Total:* 10"], text


# --------------------------------------------------------------------------- #
# W4: page answers keep the set                                                  #
# --------------------------------------------------------------------------- #


def _bare(**overrides: Any) -> dict[str, Any]:
    from tests.chatbot.test_engine import _parser_output

    base = dict(intent_hint=None, domain_hint=None, entities=[], user_goal=None)
    base.update(overrides)
    return _parser_output(**base)


def _listed(text: str, world) -> list[str]:
    """The product codes in the order the rows list them (round 4 R3: "N. <name> (<code>)")."""
    codes = {p.product_code for p in world["every"]}
    return [c for c in row_codes(text) if c in codes]


@pytest.fixture()
def small_list(monkeypatch):
    from app.services.chatbot.lanes.business import answer as answer_mod

    monkeypatch.setattr(answer_mod, "SET_LIST_MAX", 3)


@pytest.mark.parametrize("shape", [0, 1], ids=["brand_entity", "brand_in_class_raw"])
def test_w4_a_bare_count_after_the_count_question_keeps_the_brand_set(chat, world, small_list, shape):
    """Owner turns 9 then 10: "144 products have stock ..." then "10" answered from the
    earlier 334 set, the brand dropped.

    Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set and its count;
    a named count says which ones inside the intro's parenthetical, never "Here are the
    first N.".
    """
    brand = world["sorento"].brand_name
    ask = chat.say(
        f"which {brand.lower()} wash basin has stock",
        _stock_verdict(_brand_entity_shapes(brand.lower(), "wash basin")[shape], "which wash basin has stock"),
    )
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026: never a paging question):
    # one brand named and nothing else splits the 5, so the first 3 are listed.
    assert one_line_header(ask) == f"Here's what you want: {_display(brand)} wash basins with stock (5, showing 1 to 3)", ask
    assert "How many" not in ask, ask

    page = chat.say("2", _bare())

    # The last 2 of the 5: every product now shown, so the count is bare.
    first = one_line_header(page)
    assert first == f"Here's what you want: {_display(brand)} wash basins with stock (5)", page
    listed = _listed(page, world)
    assert len(listed) == 2 and set(listed) <= _codes(world["srt_basins"][:3] + world["srt_wall"]), page
    assert not set(listed) & set(_listed(ask, world)), (ask, page)


@pytest.mark.parametrize("parser_top_n", [None, 2], ids=["parser_null", "parser_reads_the_count"])
def test_w4_another_n_continues_from_where_the_list_stopped(chat, world, small_list, parser_top_n):
    """Owner turns 3 then 4: "10" listed the first 10, then "can give another 40?"
    listed the SAME first 10 again with no header.

    Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set and its count;
    a named count says which ones inside the intro's parenthetical, never "Here are N to
    M.".
    """
    brand = world["sorento"].brand_name
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026: never a paging question): one
    # brand named and nothing else splits the 5, so the ask itself lists the first 3.
    page1 = chat.say(
        f"which {brand.lower()} wash basin has stock",
        _stock_verdict(_brand_entity_shapes(brand.lower(), "wash basin")[0], "which wash basin has stock"),
    )
    assert one_line_header(page1) == f"Here's what you want: {_display(brand)} wash basins with stock (5, showing 1 to 3)", page1
    page2 = chat.say("can give another 2?", _bare(top_n=parser_top_n))

    first = one_line_header(page2)
    assert first == f"Here's what you want: {_display(brand)} wash basins with stock (5)", page2
    # The rows are numbered where the list stands, 4 and 5, not 1 and 2 again.
    assert [ln.split(".")[0] for ln in page2.splitlines()[1:] if re.match(r"^\d+\. ", ln)] == ["4", "5"], page2
    one, two = _listed(page1, world), _listed(page2, world)
    assert len(two) == 2 and not set(one) & set(two), (page1, page2)
    every = sorted(_codes(world["srt_basins"][:3] + world["srt_wall"]))
    assert one + two == every, (page1, page2)

    # And again: every product is shown already.
    page3 = chat.say("another 2", _bare())
    assert one_line_header(page3) == f"Here's what you want: {_display(brand)} wash basins with stock (5). That is all 5.", page3


def test_w4_another_n_after_a_listed_page_never_offers_more_itself(chat, world, small_list):
    brand = world["sorento"].brand_name
    chat.say(
        f"which {brand.lower()} wash basin has stock",
        _stock_verdict(_brand_entity_shapes(brand.lower(), "wash basin")[0], "which wash basin has stock"),
    )
    page = chat.say("2", _bare())
    lowered = page.lower()
    assert "more" not in lowered and "next" not in lowered, page


def test_w4_a_count_that_moved_between_the_ask_and_the_page_is_said(chat, world, small_list):
    """Owner turns 5 then 6: 2,306 became 2,322 with no word about it.

    Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set and its count.
    """
    brand = world["sorento"].brand_name
    ask = chat.say(
        f"which {brand.lower()} wash basin has stock",
        _stock_verdict(_brand_entity_shapes(brand.lower(), "wash basin")[0], "which wash basin has stock"),
    )
    # Amended to fix round 9 on PR #833: the ask lists what fits.
    assert f"Here's what you want: {_display(brand)} wash basins with stock (5, showing 1 to 3)" in ask, ask
    db = world["db"]
    wh = _warehouse(db)
    _stock_for(db, product_id=world["srt_basins"][3].id, warehouse_id=wh.id)
    db.commit()

    page = chat.say("2", _bare())

    first = one_line_header(page)
    assert f"Here's what you want: {_display(brand)} wash basins with stock (6" in first, page
    assert "It was 5 when you asked." in first, page


def test_w4_a_count_named_in_the_ask_itself_continues_on_another_n(chat, world, small_list):
    """Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set and its count;
    a named count says which ones inside the intro's parenthetical, never "Here are the
    first N.". """
    brand = world["sorento"].brand_name
    first = chat.say(
        f"show 2 {brand.lower()} wash basins with stock",
        _stock_verdict(_brand_entity_shapes(brand.lower(), "wash basin")[0], "show 2 wash basins with stock", top_n=2),
    )
    assert f"Here's what you want: {_display(brand)} wash basins with stock (5, showing 1 to 2)" in one_line_header(first), first
    more = chat.say("another 2", _bare())
    assert f"Here's what you want: {_display(brand)} wash basins with stock (5, showing 3 to 4)" in one_line_header(more), more
    assert not set(_listed(first, world)) & set(_listed(more, world)), (first, more)


# `test_w4_a_bare_number_after_a_listed_page_is_not_read_as_another_page` is retired: the
# owner ruled the opposite in round 3 (26 Sep 13:07Z, "why when i say 10, it gives some
# other answer"). A bare count after a listed page continues the same set; see
# `test_attribute_asks_round3.py::test_w2_a_count_after_a_listed_page_continues_the_same_set`.


# --------------------------------------------------------------------------- #
# W5: the brand preference knob                                                 #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def sorento_default(world):
    from app.models.product import Brand

    db = world["db"]
    db.query(Brand).update({Brand.chatbot_weight: 0})
    world["sorento"].chatbot_weight = 1.5
    db.commit()
    return world


def test_w5_no_brand_named_answers_the_default_brand_first_and_names_the_others(chat, sorento_default):
    """Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set and its count."""
    world = sorento_default
    text = chat.say(
        "which wash basin has stock",
        _stock_verdict([{"raw": "wash basin", "hint": "product_type"}], "which wash basin has stock"),
    )
    first = one_line_header(text)
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026): no brand named means every
    # brand, whatever the weights, and no "Other brands" line.
    assert first == "Here's what you want: wash basins with stock (8)", text
    assert "Other brands" not in text, text
    assert _codes(world["srt_basins"][:3] + world["srt_wall"] + world["mch_basins"] + world["mch_wall"]) <= set(row_codes(text)), text


def test_w5_naming_a_brand_answers_that_brand_only(chat, sorento_default):
    """Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set and its count."""
    world = sorento_default
    mocha = world["mocha"].brand_name
    text = chat.say(
        f"which {mocha.lower()} wash basin has stock",
        _stock_verdict(_brand_entity_shapes(mocha.lower(), "wash basin")[0], "which wash basin has stock"),
    )
    first = one_line_header(text)
    assert one_line_header(first).startswith(f"Here's what you want: {_display(mocha)} wash basins with stock (3)"), text
    assert "(default)" not in text and "Other brands" not in text, text
    assert _codes_in(text, world) == _codes(world["mch_basins"] + world["mch_wall"]), text


def test_w5_a_default_brand_with_nothing_in_the_set_leaves_the_set_whole(chat, sorento_default):
    """Sorento has no water closet with stock except its one wall hung WC; Mocha has none.
    A set the default brand does not reach at all is answered across brands."""
    world = sorento_default
    db = world["db"]
    world["sorento"].chatbot_weight = 0
    world["mocha"].chatbot_weight = 1.5
    db.commit()
    text = chat.say(
        "which water closet has stock",
        _stock_verdict([{"raw": "water closet", "hint": "product_type"}], "which water closet has stock"),
    )
    assert "(default)" not in text, text
    assert _codes_in(text, world) == _codes(world["srt_wc"]), text


def test_w5_a_page_of_the_default_brand_set_keeps_the_default(chat, sorento_default, small_list):
    """Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set and its count."""
    world = sorento_default
    chat.say(
        "which wash basin has stock",
        _stock_verdict([{"raw": "wash basin", "hint": "product_type"}], "which wash basin has stock"),
    )
    page = chat.say("2", _bare())
    first = one_line_header(page)
    # Amended to fix round 9 on PR #833: no default brand; the count typed after the
    # breakdown lists that many of the whole set.
    assert first.startswith("Here's what you want: wash basins with stock (8, showing 1 to 2)"), page
    assert len(row_codes(page)) == 2, page


# --------------------------------------------------------------------------- #
# W6: "water basin" is a wash basin; codes are never dumped in one line          #
# --------------------------------------------------------------------------- #


def test_w6_water_basin_is_a_wash_basin_even_where_the_category_lacks_the_word(chat, world):
    """Owner turn 1. The owner's local database converges through `create_all`, so
    migration 511's appended "water basin" synonym may never have reached its categories;
    the class word must still read as Wash Basin."""
    from sqlalchemy import text as sa_text

    db = world["db"]
    db.execute(
        sa_text(
            "UPDATE product_categories SET search_synonyms = search_synonyms - 'water basin' "
            "WHERE class_label = 'Wash Basin'"
        )
    )
    db.commit()

    text = chat.say(
        "which water basin has stock",
        _stock_verdict([{"raw": "water basin", "hint": "product_type"}], "which water basin has stock"),
    )
    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    # header is the product-code answer's own intro naming the described set and its
    # count.
    assert one_line_header(text) == "Here's what you want: wash basins with stock (8)", text
    assert _codes_in(text, world) == _codes(world["srt_basins"][:3] + world["srt_wall"] + world["mch_basins"] + world["mch_wall"]), text


def test_w6_a_long_subject_list_is_counted_never_dumped_as_one_line_of_codes():
    """Owner turn 1 answered "*stock* for BRBC22102W, BRBC22108W-1A, ..." - one giant line."""
    from tests.chatbot.test_rearch_s3_compose_data import _compose, _domain_row, _envelope, _policy

    row = _domain_row("inventory", narrowing={"product": "list_all"})
    row["label"] = "stock"
    codes = [f"BRBC2210{i}W" for i in range(12)]

    answer = _compose([_envelope("inventory", entities=codes)], policy=_policy(row))

    first = one_line_header(answer.text)
    assert first == "*stock* for 12 products:", answer.text
    assert codes[5] not in first, answer.text


def test_w6_a_short_subject_list_still_names_its_codes():
    from tests.chatbot.test_rearch_s3_compose_data import _compose, _domain_row, _envelope, _policy

    row = _domain_row("inventory", narrowing={"product": "list_all"})
    row["label"] = "stock"
    answer = _compose([_envelope("inventory", entities=["MSK11C", "MSK11C-BL-DIY"])], policy=_policy(row))
    assert answer.text.startswith("*stock* for MSK11C, MSK11C-BL-DIY:"), answer.text
