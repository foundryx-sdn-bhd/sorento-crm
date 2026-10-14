"""PV2 redesign R-GAPS-b fix round 3 red tests: S1 (the carry chain keeps every picked item when a second ambiguous item
is waiting), S2 (a stale held set code is a miss like a fresh ask, UAC RG-5b). No model call."""
from __future__ import annotations

import pytest

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.resolve import DbResolver
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, new_state
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, entity, out
from tests.chatbot.pv2.test_rgaps_b_fix1 import (
    PINNED, SEVENTH, WHO, _amb, _c, _ex, _inv, _is_carry, _p, _pick, _prods, _set, _t, _turn, db,
)

# ------------------------------------------------------------------ S1: the carry chain with a second ambiguous item

R = FakeResolver({"entities": {
    "customer|hanlim": _ex("customer", "c-hanlim", "HANLIM"),
    "product|mou": _amb("product", ["MOU-1", "MOU-2"]),
    "product|ber": _amb("product", ["BER-1", "BER-2"]),
}})


def _play(*parsed, can_offer, state=None):
    st, acts = state or new_state(NOW), None
    for p in parsed:
        st, acts = apply_turn(st, p, resolver=R, now=NOW, can_offer_handover=can_offer)
    return st, acts


def _lookup_keys(acts, kind):
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    return sorted(r.key for r in acts[0].held.refs.get(kind, []))


def _to_ber_question(can_offer, carry_pick):
    st, acts = _play(_t(_c("hanlim"), period=SEVENTH), _t(_p("mou"), _p("ber")), _pick("all"), can_offer=can_offer)
    assert _is_carry(acts[0]), f"{type(acts[0]).__name__}: the carry question comes after the mou answer"
    st, acts = _play(_pick(carry_pick), can_offer=can_offer, state=st)
    assert isinstance(acts[0], ClarifyAct) and "BER-1" in [o.label for o in acts[0].pending.options], \
        f"{type(acts[0]).__name__}: the BER question is next"
    return st


@WHO
def test_s1_carry_pick_1_then_a_ber_pick_keeps_hanlim_and_both_mou_and_the_ber(can_offer):
    st = _to_ber_question(can_offer, "1")
    st, acts = _play(_pick("1"), can_offer=can_offer, state=st)
    assert _lookup_keys(acts, "product") == ["BER-1", "MOU-1", "MOU-2"], "today only BER-1 is left"
    assert _lookup_keys(acts, "customer") == ["c-hanlim"]


@WHO
def test_s1_carry_pick_2_then_a_ber_pick_keeps_both_mou_and_the_ber_and_no_customer(can_offer):
    st = _to_ber_question(can_offer, "2")
    st, acts = _play(_pick("1"), can_offer=can_offer, state=st)
    assert _lookup_keys(acts, "product") == ["BER-1", "MOU-1", "MOU-2"], "today only BER-1 is left"
    assert _lookup_keys(acts, "customer") == []


@WHO
def test_s1_guard_all_on_the_ber_question_keeps_all_four(can_offer):
    st = _to_ber_question(can_offer, "1")
    st, acts = _play(_pick("all"), can_offer=can_offer, state=st)
    assert _lookup_keys(acts, "product") == ["BER-1", "BER-2", "MOU-1", "MOU-2"]


# ------------------------------------------------------------------ S2: a stale held set code (RG-5b)


def _held_then_deactivated(db):
    a, b = _prods(db, "SRTSETS2-A", "SRTSETS2-B")
    row = _set(db, "SRTSETS2", [(a, 1), (b, 2)])
    st, _ = _turn(db, out(_order_ask("SRTSETS2")))
    row.is_active = False
    db.flush()
    return st


def _order_ask(code):
    from tests.chatbot.pv2.carry_fakes import ask

    return ask(act="new_topic", domain="order", entities=[entity("product", phrase=code)])


def _shape(act):
    return (type(act).__name__, getattr(getattr(act, "pending", None), "kind", None))


def test_s2_a_stale_held_set_code_is_not_sent_to_v1_as_a_product_code_and_misses_like_a_fresh_ask(db):
    st = _held_then_deactivated(db)
    st, acts = _turn(db, _inv(act="refines_held"), state=st)
    fresh_st, fresh_acts = _turn(db, _inv(entity("product", phrase="SRTSETS2")))
    held = [r.key for a in acts if isinstance(a, Lookup) for r in a.held.refs.get("product", [])]
    assert "SRTSETS2" not in held, f"the stale set code went out as a product code: {held}"
    assert _shape(acts[0]) == _shape(fresh_acts[0]), (_shape(acts[0]), "vs fresh", _shape(fresh_acts[0]))
