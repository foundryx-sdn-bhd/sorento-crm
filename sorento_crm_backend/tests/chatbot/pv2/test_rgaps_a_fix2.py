"""PV2 redesign R-GAPS-a fix round 2 red tests. Real `apply_turn` paths with the pure fakes, no model call.

G1 a declined did-you-mean after a pick still answers the held items (dealer)   G2 set + remove of a held target is no question
"""
from __future__ import annotations

import pytest

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, SmallTalk, new_state
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held as mk_held, mk_state, out
from tests.chatbot.pv2.test_rgaps_a import _exact, _keys, _pick
from tests.chatbot.pv2.test_rgaps_a_fix1 import OPTION_ITEMS, _Resolver


def _product(p):
    return entity("product", phrase=p)


def _turns(ents, picks, can_offer):
    r = _Resolver(OPTION_ITEMS)
    st, acts = apply_turn(new_state(NOW), out(ask(act="new_topic", domain="incoming", entities=ents)), resolver=r, now=NOW,
                          can_offer_handover=can_offer)
    for p in picks:
        st, acts = apply_turn(st, _pick(p), resolver=r, now=NOW, can_offer_handover=can_offer)
    return st, acts[0]


# ------------------------------------------------------------------ G1: a declined did-you-mean after a pick (dealer)


def test_rg7g_a_dealer_declining_the_did_you_mean_after_picking_mou_gets_the_held_item_looked_up():
    st, last = _turns([_product("mou"), _product("srtwc28")], ["1", "no"], can_offer=True)
    assert isinstance(last, Lookup), f"{type(last).__name__}: the declined item is dropped, the picked mou is answered"
    assert _keys(st) == ["SRTWC286"]


def test_rg7g_guard_a_dealer_declining_a_did_you_mean_asked_first_is_the_declined_lane():
    st, last = _turns([_product("srtwc28"), _product("mou")], ["no"], can_offer=True)
    assert isinstance(last, SmallTalk) and _keys(st) == []


def test_rg7g_guard_a_staff_no_to_the_did_you_mean_after_a_pick_re_asks():
    st, last = _turns([_product("mou"), _product("srtwc28")], ["1", "no"], can_offer=False)
    assert isinstance(last, ClarifyAct) and last.pending.kind == "clarify_did_you_mean"


# ------------------------------------------------------------------ G2: set + remove of a held target in one ask


R = FakeResolver({"entities": {f"customer|{c}": _exact("customer", c) for c in ("Y", "Z", "W")}})


def _refine(held_customers, ents):
    st = mk_state(mk_held("order", {"customer": held_customers, "product": ["A1"]}))
    return apply_turn(st, out(ask(act="refines_held", domain="order", entities=ents)), resolver=R, now=NOW)


@pytest.mark.parametrize("held_customers", [["Z"], ["Y", "Z"]], ids=["Z held", "Y and Z held"])
def test_rg7g_customer_y_and_remove_customer_z_in_one_ask_is_no_question(held_customers):
    st, acts = _refine(held_customers, [entity("customer", phrase="Y"), entity("customer", phrase="Z", op="remove")])
    assert isinstance(acts[0], Lookup), f"{type(acts[0]).__name__}: set Y then remove Z of a held Z must not ask"
    assert _keys(st, "customer") == ["Y"]


def test_rg7g_guard_a_remove_of_a_customer_never_held_still_asks_which_held():
    st, acts = _refine(["Z"], [entity("customer", phrase="Y"), entity("customer", phrase="W", op="remove")])
    assert isinstance(acts[0], ClarifyAct) and acts[0].pending.kind == "clarify_which_held"
