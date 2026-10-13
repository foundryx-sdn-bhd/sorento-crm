"""SPEC-PROMO-PARAMS conversation goldens (crew rule, owner, 13 Oct): for every answer type this lane
touches, (1) a follow-up that narrows the last answer (brand, measure), (2) a short reply to every
question the bot asks ("more" / "yes" to "Reply to see more.", a quantity to "How many units for
each?", a number to the promotion level menu), (3) one topic switch and back.

Answer types: a size stock list (class + size), a typed code plus a size, the size miss, the
dealer's quantity ask, the promotion level pick (`test_promo_spec_parity`). The parser is mocked per
turn (its entities are what it emits, carried ones `current_message: False`); the resolver, the
stock route and the MCP presenter are real. Made-up rows only (ZZT*).
"""
from __future__ import annotations

import re
import uuid
from typing import Any

import pytest

from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _run_turn
from tests.chatbot.test_spec_on_category_entity import (  # noqa: F401
    STOCK_TOOL,
    _category,
    _clear_overrides,
    _route_backed_stock,
    _spec_entity,
)
from tests.chatbot import test_outstanding_lane as harness

SEE_MORE = "Reply to see more"


def _seed(session_factory) -> dict[str, Any]:
    """Wash basins of two brands: brand A holds 600 on length (3) and width (1) and one 500; brand
    B holds 600 on length (2). One stock row each (5 on hand)."""
    from app.models.product_spec import ProductSpecifications
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._mc_lookup_seed import product, stock, warehouse
    from tests.chatbot.test_lane_require import _seed_registry
    from tests.chatbot.test_outstanding_lane import _seed_contact
    from tests.chatbot.test_promo_brand_category import _brand
    from tests.chatbot.test_promo_subject_filter import _class

    _seed_contact(session_factory, variables={})
    _class(session_factory, "Wash Basin", ("basin",))
    db = session_factory()
    _seed_registry(db)
    tag = uuid.uuid4().hex[:4].upper()
    brand_a, brand_b = _brand(db, f"Zztalpha{tag}"), _brand(db, f"Zztbeta{tag}")
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    rows = [
        ("A-L1", brand_a, {"dim_length": 600}), ("A-L2", brand_a, {"dim_length": 600}),
        ("A-L3", brand_a, {"dim_length": 600}), ("A-W1", brand_a, {"dim_width": 600}),
        ("A-L5", brand_a, {"dim_length": 500}),
        ("B-L1", brand_b, {"dim_length": 600}), ("B-L2", brand_b, {"dim_length": 600}),
    ]
    codes: dict[str, str] = {}
    for key, brand, mm in rows:
        p = product(db, company_id=DEFAULT_COMPANY_ID, code=f"ZZT-CG{tag}-{key}")
        p.brand_id = brand.id
        values = {"class": {"value": "Wash Basin"}, **{k: {"value": v, "unit": "mm"} for k, v in mm.items()}}
        db.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=p.id, values=values))
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=5)
        codes[key] = p.product_code
    names = {"brand_a": brand_a.brand_name, "brand_b": brand_b.brand_name}
    db.commit()
    db.close()
    return {"codes": codes, **names}


def _carried(entity: dict[str, Any]) -> dict[str, Any]:
    return {**entity, "current_message": False}


def _brand_entity(name: str) -> dict[str, Any]:
    return {"raw": name.lower(), "hint": "brand", "canonical_code": name, "current_message": True,
            "confident": True, "hint_confident": True, "quantity": None}


class _Chat:
    """One contact's turns against the real engine, the stock and product tools answered by the
    real routes."""

    def __init__(self, session_factory, monkeypatch):
        self.sf, self.mp = session_factory, monkeypatch
        call, self.captured = _route_backed_stock(monkeypatch, session_factory)
        monkeypatch.setattr(harness, "_capturing_mcp", lambda response=None: (call, self.captured))

    def say(self, text: str, **parse: Any) -> str:
        self.captured.clear()
        qf = _parser_output(**{"domain_hint": "inventory", "intent_hint": "check_stock", **parse})
        result, _ = _run_turn(self.sf, self.mp, qf=qf, text_body=text, msg_id=f"zzt-cg-{uuid.uuid4().hex[:6]}", real_resolver=True)
        return (result.reply or {}).get("text") or ""

    def stock_calls(self) -> list[dict[str, Any]]:
        return [a for n, a in self.captured if n == STOCK_TOOL]


def _listed(reply: str, codes: dict[str, str], keys) -> set[str]:
    return {k for k in keys if re.search(rf"(?<![\w-]){re.escape(codes[k])}(?![\w-])", reply)}


SIX_600 = {"A-L1", "A-L2", "A-L3", "A-W1", "B-L1", "B-L2"}
SIZE_ASK = [_category("basin", "basin"), _spec_entity("600mm", "size", 600)]


# ---------------------------------------------------------------- (1) a follow-up that narrows


def test_g1_a_brand_narrows_the_size_list(session_factory, monkeypatch, _clear_overrides):
    """"600mm basin got stock?" then "only <brand A>": brand A's 600 mm basins only, the size kept."""
    w = _seed(session_factory)
    chat = _Chat(session_factory, monkeypatch)
    chat.say("600mm basin got stock?", entities=SIZE_ASK)
    reply = chat.say(
        f"only {w['brand_a'].lower()}",
        entities=[*map(_carried, SIZE_ASK), _brand_entity(w["brand_a"])],
    )
    assert _listed(reply, w["codes"], w["codes"]) == {"A-L1", "A-L2", "A-L3", "A-W1"}, reply


def test_g1b_a_brand_not_on_file_never_loses_the_size(session_factory, monkeypatch, _clear_overrides):
    """Reviewer N2 (018fe813): "only sorrento" (no such brand) dropped every constraint with it and
    listed the 500 mm basin. A brand name no brand carries narrows nothing; the class and the
    size still filter."""
    w = _seed(session_factory)
    chat = _Chat(session_factory, monkeypatch)
    chat.say("600mm basin got stock?", entities=SIZE_ASK)
    reply = chat.say("only zztnosuchbrand", entities=[*map(_carried, SIZE_ASK), _brand_entity("Zztnosuchbrand")])
    shown = _listed(reply, w["codes"], w["codes"])
    assert "A-L5" not in shown and shown and shown <= SIX_600, reply
    calls = chat.stock_calls()
    assert calls and {"key": "size", "op": "eq", "value": 600} in (calls[-1].get("spec_constraints") or []), calls


def test_g2_an_axis_narrows_the_size_list(session_factory, monkeypatch, _clear_overrides):
    """"600mm basin got stock?" then "length only": the basins holding 600 on length only."""
    w = _seed(session_factory)
    chat = _Chat(session_factory, monkeypatch)
    chat.say("600mm basin got stock?", entities=SIZE_ASK)
    reply = chat.say(
        "length only",
        entities=[_carried(SIZE_ASK[0]), _spec_entity("600mm length", "dim_length", 600)],
    )
    shown = _listed(reply, w["codes"], w["codes"])
    assert "A-W1" not in shown and "A-L5" not in shown and shown <= SIX_600, reply
    assert len(shown) == 5 and SEE_MORE not in reply or len(shown) == 5, reply


def test_g3_a_size_narrows_the_size_miss(session_factory, monkeypatch, _clear_overrides):
    """"700mm basin got stock?" (none: the sizes on file are said) then "600 then": the 600s."""
    w = _seed(session_factory)
    chat = _Chat(session_factory, monkeypatch)
    miss = chat.say("700mm basin got stock?", entities=[_category("basin", "basin"), _spec_entity("700mm", "size", 700)])
    assert "No 700 mm." in miss and "600 mm" in miss, miss
    reply = chat.say("600 then", entities=[_carried(SIZE_ASK[0]), _spec_entity("600", "size", 600)])
    assert _listed(reply, w["codes"], w["codes"]) <= SIX_600 and len(_listed(reply, w["codes"], w["codes"])) == 5, reply


# ---------------------------------------------------------------- (2) a short reply to every question


@pytest.mark.parametrize("short", ["yes", "more"])
def test_g4_a_short_reply_to_reply_to_see_more_pages(session_factory, monkeypatch, _clear_overrides, short):
    """The bot asks "Reply to see more."; "yes" / "more" (the parser's `continuation`) is page 2."""
    w = _seed(session_factory)
    chat = _Chat(session_factory, monkeypatch)
    first = chat.say("600mm basin got stock?", entities=SIZE_ASK)
    assert SEE_MORE in first, first
    second = chat.say(short, entities=[], continuation=True)
    page1, page2 = _listed(first, w["codes"], SIX_600), _listed(second, w["codes"], SIX_600)
    assert len(page2) == 1 and page1 | page2 == SIX_600, (first, second)
    assert SEE_MORE not in second, second


# ---------------------------------------------------------------- (3) a topic switch and back


def test_g5_a_topic_switch_and_a_short_way_back_keeps_the_size(session_factory, monkeypatch, _clear_overrides):
    """"600mm basin got stock?", then "any promo?" (a different topic), then back with a short
    "and their stock again?": the parser carries the basin and the 600 mm from the conversation
    (`current_message: False`), so the answer is the 600 mm basin list again, never every basin.
    (A class word typed again starts a new set, owner ruling W3; a "more" after the switch is a new
    ask, owner rulings 26 Sep and 12 Oct.)"""
    w = _seed(session_factory)
    chat = _Chat(session_factory, monkeypatch)
    first = chat.say("600mm basin got stock?", entities=SIZE_ASK)
    chat.say("any promo?", entities=[], domain_hint="promotion", intent_hint="check_promotion")
    back = chat.say("and their stock again?", entities=[*map(_carried, SIZE_ASK)])
    assert "A-L5" not in _listed(back, w["codes"], w["codes"]), back
    assert _listed(back, w["codes"], w["codes"]) == _listed(first, w["codes"], w["codes"]), (first, back)


def test_g6_a_topic_switch_and_a_fresh_ask_is_the_first_page_again(session_factory, monkeypatch, _clear_overrides):
    """The same switch, then the size ask again in full: the first page again, the same filters."""
    w = _seed(session_factory)
    chat = _Chat(session_factory, monkeypatch)
    first = chat.say("600mm basin got stock?", entities=SIZE_ASK)
    chat.say("any promo?", entities=[], domain_hint="promotion", intent_hint="check_promotion")
    again = chat.say("600mm basin got stock?", entities=SIZE_ASK)
    assert _listed(again, w["codes"], w["codes"]) == _listed(first, w["codes"], w["codes"]), (first, again)
    calls = chat.stock_calls()
    assert calls and {"key": "size", "op": "eq", "value": 600} in (calls[-1].get("spec_constraints") or []), calls


# ---------------------------------------------------------------- the dealer's quantity ask


def _make_dealer(session_factory) -> None:
    """The chatbot's contact on stock visibility `availability`, its categories capped at 50."""
    from sqlalchemy import text

    from app.models.access import StockVisibilityPolicy
    from app.models.product import Product, ProductCategory
    from tests.chatbot.test_engine import CONTACT_ID

    db = session_factory()
    contact_id = db.execute(
        text("SELECT id FROM respond_contacts WHERE respond_io_id = :cid ORDER BY id LIMIT 1"), {"cid": str(CONTACT_ID)}
    ).scalar()
    db.add(StockVisibilityPolicy(id=str(uuid.uuid4()), contact_id=contact_id, mode="availability", warehouse_ids=None))
    category_ids = {pid for (pid,) in db.query(Product.category_id).filter(Product.product_code.like("ZZT-CG%")).all() if pid}
    for row in db.query(ProductCategory).filter(ProductCategory.id.in_(category_ids)).all():
        row.chatbot_max_qty = 50
    db.commit()
    db.close()


def test_g7_a_dealer_answers_the_quantity_ask_for_the_size_list_only(session_factory, monkeypatch, _clear_overrides):
    """The bot asks a dealer "How many units for each?" on a size list; the short reply "5" (the
    parser's `demand_qty`) answers for the 600 mm basins only, never the 500 mm one."""
    w = _seed(session_factory)
    _make_dealer(session_factory)
    chat = _Chat(session_factory, monkeypatch)
    ask = chat.say("600mm basin got stock?", entities=SIZE_ASK)
    assert "How many units" in ask, ask
    answer = chat.say("5", entities=[], demand_qty=5)
    shown = _listed(answer, w["codes"], w["codes"])
    assert shown and shown <= SIX_600 and "A-L5" not in shown, (ask, answer)
    assert "x 5" in answer, answer


# ---------------------------------------------------------------- a typed code plus a size


def test_g8_a_size_narrows_a_family_answer(session_factory, monkeypatch, _clear_overrides):
    """"<family> got stock?" lists every variant; "200mm only" (the code carried) keeps the 200s."""
    from tests.chatbot.test_spec_size_on_a_code import _code, _seed_wcs

    w = _seed_wcs(session_factory)
    f = w["family"]
    chat = _Chat(session_factory, monkeypatch)
    every = chat.say(f"{f.lower()} got stock?", entities=[_code(f)])
    assert f"{f}-SH-150" in every, every
    reply = chat.say("200mm only", entities=[_carried(_code(f)), _spec_entity("200mm", "size", 200)])
    listed = {c for c in w["codes"] if re.search(rf"(?<![\w-]){re.escape(c)}(?![\w-])", reply)}
    assert listed == {f"{f}-SH-200", f"{f}-SH-NEW-200"}, reply


def test_g9_a_family_size_miss_then_a_size_on_file(session_factory, monkeypatch, _clear_overrides):
    """"250mm <family> got stock?" says the sizes on file; the short reply "200 then" lists the 200s."""
    from tests.chatbot.test_spec_size_on_a_code import _code, _seed_wcs

    w = _seed_wcs(session_factory)
    f = w["family"]
    chat = _Chat(session_factory, monkeypatch)
    miss = chat.say(f"250mm {f.lower()} got stock?", entities=[_spec_entity("250mm", "size", 250), _code(f)])
    assert f"No 250 mm for {f}." in miss, miss
    reply = chat.say("200 then", entities=[_carried(_code(f)), _spec_entity("200", "size", 200)])
    listed = {c for c in w["codes"] if re.search(rf"(?<![\w-]){re.escape(c)}(?![\w-])", reply)}
    assert listed == {f"{f}-SH-200", f"{f}-SH-NEW-200"}, reply


# ---------------------------------------------------------------- the LIVE parser's follow-up shapes
#
# Crew live check on 1e3250479 (v103): 8 of 12 follow-ups lost the size or the family. The goldens
# above handed the engine the earlier class and size as carried entities; the live parser does not
# (probe on the published line, 13 Oct): a follow-up names only what is NEW and says how it relates
# through `entity_op` ("reuse": the same thing as before; "replace_combine": a new value replaces
# the axis it names and keeps every other carried axis) and `continuation`. These are those shapes.


def _live(chat: _Chat, text: str, entities, entity_op: str, **more: Any) -> str:
    return chat.say(text, entities=entities, entity_op=entity_op, **more)


def test_f1_yes_with_reuse_and_nothing_new_pages_the_list(session_factory, monkeypatch, _clear_overrides):
    """4a on dev: "yes" to "Reply to see more." parsed `entity_op: reuse`, no entities and no
    `continuation`; it must page the same filtered list, never list other products."""
    w = _seed(session_factory)
    chat = _Chat(session_factory, monkeypatch)
    first = chat.say("600mm basin got stock?", entities=SIZE_ASK)
    second = _live(chat, "yes", [], "reuse")
    page1, page2 = _listed(first, w["codes"], SIX_600), _listed(second, w["codes"], w["codes"])
    assert page2 and page2 <= SIX_600 and not page1 & page2, (first, second)


@pytest.mark.parametrize("op", ["replace_combine", "replace"])
def test_f2_a_brand_alone_narrows_the_size_list(session_factory, monkeypatch, _clear_overrides, op):
    """4b: "only <brand>" parsed as the brand alone (`replace_combine`, or `replace` for "only X");
    it narrows the last list, the class and the size kept. Never "I need at least one filter"."""
    w = _seed(session_factory)
    chat = _Chat(session_factory, monkeypatch)
    chat.say("600mm basin got stock?", entities=SIZE_ASK)
    reply = _live(chat, f"only {w['brand_a'].lower()}", [_brand_entity(w["brand_a"])], op)
    assert "at least one filter" not in reply, reply
    assert _listed(reply, w["codes"], w["codes"]) == {"A-L1", "A-L2", "A-L3", "A-W1"}, reply


def test_f3_an_axis_alone_narrows_the_size_list(session_factory, monkeypatch, _clear_overrides):
    """4c: "length only" as the published rule asks the parser to emit it: the axis with the size
    the last ask stated, nothing else. Only the basins holding 600 on length."""
    w = _seed(session_factory)
    chat = _Chat(session_factory, monkeypatch)
    chat.say("600mm basin got stock?", entities=SIZE_ASK)
    reply = _live(chat, "length only", [_spec_entity("length", "dim_length", 600)], "replace_combine")
    shown = _listed(reply, w["codes"], w["codes"])
    assert shown and shown <= {"A-L1", "A-L2", "A-L3", "B-L1", "B-L2"}, reply


def test_f4_the_dealers_quantity_reply_answers_the_size_list(session_factory, monkeypatch, _clear_overrides):
    """6a: "5" parsed `entity_op: reuse`, `demand_qty: 5`, no entities. It answers the quantity ask
    for the size list (works on main for codes); never only one product, never the ask again."""
    w = _seed(session_factory)
    _make_dealer(session_factory)
    chat = _Chat(session_factory, monkeypatch)
    ask = chat.say("600mm basin got stock?", entities=SIZE_ASK)
    assert "How many units" in ask, ask
    answer = _live(chat, "5", [], "reuse", demand_qty=5)
    shown = _listed(answer, w["codes"], w["codes"])
    assert "How many units" not in answer, answer
    assert len(shown) >= 2 and shown <= SIX_600, answer
    assert answer.count("x 5") >= 2, answer


def test_f5_a_size_alone_narrows_the_family_answer(session_factory, monkeypatch, _clear_overrides):
    """7a: "200mm only" after "<family> got stock?" parsed `reuse` with the size alone; the family
    is kept and only its 200 variants list."""
    from tests.chatbot.test_spec_size_on_a_code import _code, _seed_wcs

    w = _seed_wcs(session_factory, more_200=1)
    f = w["family"]
    chat = _Chat(session_factory, monkeypatch)
    chat.say(f"{f.lower()} got stock?", entities=[_code(f)])
    reply = _live(chat, "200mm only", [_spec_entity("200mm", "size", 200)], "reuse")
    listed = {c for c in w["codes"] if re.search(rf"(?<![\w-]){re.escape(c)}(?![\w-])", reply)}
    assert listed == {f"{f}-SH-200", f"{f}-SH-NEW-200", f"{f}-SH-200-0"}, reply


def test_f6_a_size_alone_after_a_family_miss_keeps_the_family_and_the_trap(session_factory, monkeypatch, _clear_overrides):
    """1a: "180 then" after "p trap 250mm <family>" parsed `replace_combine`, `continuation`, the
    size alone and the product domain; the family and the trap are kept, the size replaced."""
    from tests.chatbot.test_spec_size_on_a_code import _code, _seed_wcs

    w = _seed_wcs(session_factory, other_180=True)
    f = w["family"]
    chat = _Chat(session_factory, monkeypatch)
    chat.say(f"p trap 250mm {f.lower()} got stock?",
             entities=[_spec_entity("p trap", "trap_type", "p_trap"), _spec_entity("250mm", "size", 250), _code(f)])
    reply = _live(chat, "180 then", [_spec_entity("180", "size", 180)], "replace_combine",
                  continuation=True, domain_hint="master_products", intent_hint="check_product")
    listed = {c for c in w["codes"] if re.search(rf"(?<![\w-]){re.escape(c)}(?![\w-])", reply)}
    assert listed == {f"{f}-P", f"{f}-NEW-P"}, reply


def test_f7_back_after_a_topic_switch_with_reuse(session_factory, monkeypatch, _clear_overrides):
    """5b: "and their stock again?" after "any promo?" parsed `reuse`, no entities: the 600 mm
    basin list again, never other products."""
    w = _seed(session_factory)
    chat = _Chat(session_factory, monkeypatch)
    first = chat.say("600mm basin got stock?", entities=SIZE_ASK)
    chat.say("any promo?", entities=[], domain_hint="promotion", intent_hint="check_promotion")
    back = _live(chat, "and their stock again?", [], "reuse")
    shown = _listed(back, w["codes"], w["codes"])
    assert shown and shown <= SIX_600, (first, back)
