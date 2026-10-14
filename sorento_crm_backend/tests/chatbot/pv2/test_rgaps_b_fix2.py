"""PV2 redesign R-GAPS-b fix round 2 red tests: H1 (an "all" answer still owes the carry question), H2 (a stored set
is re-checked active). No model call."""
from __future__ import annotations

import pytest

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.resolve import DbResolver
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, new_state
from tests.chatbot.pv2.carry_fakes import NOW, ask, entity, out
from tests.chatbot.pv2.test_rgaps_b_fix1 import (
    PINNED, SEVENTH, WHO, _c, _inv, _inv_code, _is_carry, _keys, _p, _pick, _play, _prods, _set, _t, _turn, db,
)

# ------------------------------------------------------------------ H1: "all" to the entity question, then the carry question


def _to_all(can_offer):
    st, acts = _play(_t(_c("hanlim"), period=SEVENTH), _t(_p("mou")), can_offer=can_offer)
    assert isinstance(acts[0], ClarifyAct) and acts[0].pending.kind == "clarify_entity", type(acts[0]).__name__
    return _play(_pick("all"), can_offer=can_offer, state=st)


@WHO
def test_h1_an_all_answer_to_the_entity_question_asks_the_carry_question_and_fetches_nothing(can_offer):
    st, acts = _to_all(can_offer)
    assert _is_carry(acts[0]), f"{type(acts[0]).__name__}: straight to a Lookup on HANLIM + MOU-1 + MOU-2"


@pytest.mark.parametrize("pick,customers", [("1", ["c-hanlim"]), ("2", [])], ids=["only HANLIM", "all customers"])
@WHO
def test_h1_the_carry_pick_after_all_keeps_both_items_and_the_customer_only_on_pick_1(can_offer, pick, customers):
    st, acts = _to_all(can_offer)
    assert _is_carry(acts[0]), type(acts[0]).__name__
    st, acts = _play(_pick(pick), can_offer=can_offer, state=st)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _keys(st) == ["MOU-1", "MOU-2"] and _keys(st, "customer") == customers


# ------------------------------------------------------------------ H2: a stored set ref is re-checked active


def _stored_set_then_refine(db, *, deactivate):
    a, b = _prods(db, "SRTSETH-A", "SRTSETH-B")
    row = _set(db, "SRTSETH", [(a, 1), (b, 2)])
    # Resolved in a domain that does not expand sets, so the set ref is what stays held between turns.
    st, acts = _turn(db, out(ask(act="new_topic", domain="order", entities=[entity("product", phrase="SRTSETH")])))
    if deactivate:
        row.is_active = False
        db.flush()
    return _turn(db, _inv(act="refines_held"), state=st)


def test_h2_guard_a_stored_active_set_expands_into_its_members_when_the_domain_becomes_inventory(db):
    st, acts = _stored_set_then_refine(db, deactivate=False)
    assert isinstance(acts[0], Lookup) and _keys(st) == ["SRTSETH-A", "SRTSETH-B"], (type(acts[0]).__name__, _keys(st))


def test_h2_a_set_deactivated_after_it_was_resolved_no_longer_expands(db):
    st, acts = _stored_set_then_refine(db, deactivate=True)
    assert "SRTSETH-A" not in _keys(st) and "SRTSETH-B" not in _keys(st), f"stale members held: {_keys(st)}"
