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
