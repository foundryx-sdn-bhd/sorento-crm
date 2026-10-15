"""PV2 R5 slice D (UAC R5-2): "this product" after a one-item document. Red tests, no LLM, no DB.

The parse is hand-written: a product entity with refers_to_held=true and no key / phrase (what the parser emits for
"what is the stock of this product?", "is that item available?", "barang ni ada stok?", "这个有货吗"). The bot's
last answer printed one document; its product lines are known to the register whether or not the answer printed
them (the `delivery_orders.products` reveal). One line -> that line's product; several -> a clarify listing the
real codes.

UAC R5-2 security re-scope (lead ruling 15 Oct): only lines the user may see count. A contact without the
`delivery_orders.products` reveal (lanes/business/fetch.py drops the lines) never gets a line code from "this product",
neither in the reply nor as the resolved or held product; PV2 asks which product instead.
"""
from __future__ import annotations

import re

import pytest

from app.services.chatbot.pv2 import clarify_copy
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, RegisterEntry
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.pv2 import register
from tests.chatbot.pv2 import _register_envelopes as E
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held as mk_held, mk_state, out

NO_PRODUCTS_REVEAL = [a for a in E.ALL_REVEALS if a != "delivery_orders.products"]
ONE_LINE_DO = E.ORDERS_RAW["data"][1]  # 202609-2013, one line SRTW5000
TWO_LINE_DO = E.ORDERS_RAW["data"][0]  # 202609-2012, SRTWB1096 + SRTWT7203
UUID_RE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


def _exact(key):
    return {"status": "exact", "refs": [{"kind": "product", "key": key, "label": key}]}


def _resolver():
    return FakeResolver({"entities": {f"product|{c}": _exact(c) for c in ("SRTW5000", "SRTWB1096", "SRTWT7203")}})


def _state(doc, revealed):
    st = mk_state(mk_held("order", {"delivery_order": [doc["order_number"]]}), turn_no=3)
    env = E.envelope(E.ORDERS_TOOL, {"data": [doc]}, access=None if revealed else NO_PRODUCTS_REVEAL)
    engine_mod._pv2_write_register({"pv2_state": st}, [env])
    return st


def _this_product(domain):
    return out(ask(act="refines_held", domain=domain, entities=[entity("product", refers_to_held=True)]))


def _held_products(st):
    return [x.key for x in st.held.refs.get("product", [])]


@pytest.mark.parametrize("revealed", [True], ids=["lines printed"])
@pytest.mark.parametrize("domain", ["inventory", "master_products"])
def test_d1_this_product_after_a_one_line_delivery_order_is_that_lines_product(revealed, domain):
    st = _state(ONE_LINE_DO, revealed)
    s2, acts = apply_turn(st, _this_product(domain), resolver=_resolver(), now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], Lookup), f"{type(acts[0]).__name__}: {getattr(acts[0], 'about', None)}"
    assert _held_products(s2) == ["SRTW5000"], _held_products(s2)


def test_d2_this_product_after_a_one_line_sales_order_is_that_lines_product():
    st = mk_state(mk_held("order", {"sales_order": ["SO419122"]}), turn_no=3)
    st.register = [
        RegisterEntry(id="R1", kind="sales_order", key="SO419122", label="SO419122", parent=None, subject=None, turn=3),
        RegisterEntry(id="R2", kind="product", key="SRTW5000", label="SRTW5000", parent="R1", subject=None, turn=3),
    ]
    st.last_rows = register.shown_rows(st.register, 3)
    s2, acts = apply_turn(st, _this_product("inventory"), resolver=_resolver(), now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], Lookup), f"{type(acts[0]).__name__}: {getattr(acts[0], 'about', None)}"
    assert _held_products(s2) == ["SRTW5000"], _held_products(s2)


@pytest.mark.parametrize("revealed", [True], ids=["lines printed"])
def test_d3_several_lines_ask_which_listing_the_real_codes_never_the_nothing_selected_miss(revealed):
    st = _state(TWO_LINE_DO, revealed)
    s2, acts = apply_turn(st, _this_product("inventory"), resolver=_resolver(), now=NOW, can_offer_handover=False)
    act = acts[0]
    assert isinstance(act, ClarifyAct), type(act).__name__
    labels = [o.label for o in act.pending.options]
    assert sorted(labels) == ["SRTWB1096", "SRTWT7203"], labels
    shown = act.pending.asked + " ".join(labels)
    assert "202609-2012" not in labels and "DELUXE HOME CENTRE SDN BHD" not in labels
    assert clarify_copy.text("nothing_held", "en") not in shown, shown
    assert not re.search(UUID_RE, shown + " ".join(o.key for o in act.pending.options)), shown


def test_d3_picking_one_of_the_listed_lines_holds_that_product():
    st = _state(TWO_LINE_DO, True)
    r = _resolver()
    st, acts = apply_turn(st, _this_product("inventory"), resolver=r, now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__
    st, acts = apply_turn(st, out(ask(act="answers_bot_question", answer={"option_key": "2", "free_value": None})),
                          resolver=r, now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert len(_held_products(st)) == 1 and _held_products(st)[0] in ("SRTWB1096", "SRTWT7203"), _held_products(st)


LINE_CODES = ("SRTW5000", "SRTWB1096", "SRTWT7203")


@pytest.mark.parametrize("doc,domain", [(ONE_LINE_DO, "inventory"), (ONE_LINE_DO, "master_products"),
                                        (TWO_LINE_DO, "inventory")],
                         ids=["one line-inventory", "one line-master_products", "two lines-inventory"])
def test_d5_a_contact_who_may_not_see_do_lines_gets_no_line_code_and_is_asked_which_product(doc, domain):
    """UAC R5-2 security re-scope: the lines were dropped for this contact, so none of them is the answer, the
    held product or a word of the reply; the ask is the which-product question (kind asserted, not wording)."""
    st = _state(doc, revealed=False)
    r = _resolver()
    s2, acts = apply_turn(st, _this_product(domain), resolver=r, now=NOW, can_offer_handover=False)
    act = acts[0]
    assert isinstance(act, ClarifyAct), type(act).__name__
    assert act.about == "entity" and act.pending.kind == "clarify_entity", (act.about, act.pending.kind)
    reply = clarify_copy.render(act.pending, "en")
    assert not any(code in reply for code in LINE_CODES), reply
    assert not any(isinstance(a, Lookup) for a in acts)
    assert not set(_held_products(s2)) & set(LINE_CODES), _held_products(s2)
    assert not any(f"product|{code}" in r.entity_lookups for code in LINE_CODES), r.entity_lookups


def test_d4_guard_nothing_held_still_asks_which():  # guard: passes today
    st = mk_state(mk_held("order", {}), turn_no=3)
    _, acts = apply_turn(st, _this_product("inventory"), resolver=_resolver(), now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], ClarifyAct)
