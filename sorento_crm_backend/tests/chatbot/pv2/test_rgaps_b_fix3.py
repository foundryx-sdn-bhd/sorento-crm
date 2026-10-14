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
    SEVENTH, WHO, _amb, _c, _ex, _is_carry, _p, _pick, _t,
)

# ------------------------------------------------------------------ S1: the carry chain with a second ambiguous item

R = FakeResolver({"entities": {
    "customer|hanlim": _ex("customer", "c-hanlim", "HANLIM"),
    "product|SRT391-GM": _ex("product", "SRT391-GM"),
    "customer|cmou": _amb("customer", ["CMOU-1", "CMOU-2"]),
    "customer|cber": _amb("customer", ["CBER-1", "CBER-2"]),
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
    # # owner rule 15 Oct: a named customer carries; only product then customer asks
    st, acts = _play(_t(_p("SRT391-GM"), period=SEVENTH), _t(_c("cmou"), _c("cber")), _pick("all"), can_offer=can_offer)
    assert _is_carry(acts[0]), f"{type(acts[0]).__name__}: the carry question comes after the cmou answer"
    st, acts = _play(_pick(carry_pick), can_offer=can_offer, state=st)
    assert isinstance(acts[0], ClarifyAct) and "CBER-1" in [o.label for o in acts[0].pending.options], \
        f"{type(acts[0]).__name__}: the BER question is next"
    return st


@WHO
def test_s1_carry_pick_1_then_a_ber_pick_keeps_the_product_and_both_cmou_and_the_cber(can_offer):
    st = _to_ber_question(can_offer, "1")
    st, acts = _play(_pick("1"), can_offer=can_offer, state=st)
    assert _lookup_keys(acts, "customer") == ["CBER-1", "CMOU-1", "CMOU-2"], "today only CBER-1 is left"
    assert _lookup_keys(acts, "product") == ["SRT391-GM"]


@WHO
def test_s1_carry_pick_2_then_a_ber_pick_keeps_both_cmou_and_the_cber_and_no_product(can_offer):
    st = _to_ber_question(can_offer, "2")
    st, acts = _play(_pick("1"), can_offer=can_offer, state=st)
    assert _lookup_keys(acts, "customer") == ["CBER-1", "CMOU-1", "CMOU-2"], "today only CBER-1 is left"
    assert _lookup_keys(acts, "product") == []


@WHO
def test_s1_guard_all_on_the_ber_question_keeps_all_four(can_offer):
    st = _to_ber_question(can_offer, "1")
    st, acts = _play(_pick("all"), can_offer=can_offer, state=st)
    assert _lookup_keys(acts, "customer") == ["CBER-1", "CBER-2", "CMOU-1", "CMOU-2"]


# S2 (a stale held set code) is pinned by test_rgaps_b_fix4.py::test_k3a / test_k3b: it resolves like a fresh ask.
