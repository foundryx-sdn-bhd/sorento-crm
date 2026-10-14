"""PV2 redesign R2, fix round 6: a pick on a which_held question never re-misses, whatever entity raised the question.
Real `apply_turn` paths from a held subject, phrase-only product parses, staff (no hand-over offer) and non-staff.
Pre-R2 (941fb867a) MEMORY after the pick is hard-coded (exported with git archive, never a checkout).

Raise sites of a which_held question in pv2/apply.py (file:line on d7f643913):
  :316  switch_back naming a topic that was never printed          -> raise _Miss("which_held")          [switch]
  :343  refines_held on an EMPTY held with no domain              -> _Miss(nothing_held); no options, no pick possible
  :368  an attachment ask with no product held and none saved     -> _Miss(nothing_held)                 [attach]
  :842  a NEW ask about "this one" with no held ref of that kind  -> _Miss(nothing_held)                 [newtopic_this]
  :849  a refine about "this one" with no held ref of that kind   -> _Miss(nothing_held)   (test_redesign_r2_fix5 B1)
  :863  remove of a kind the held subject does not carry          -> _Miss(nothing_held)                 [c2]
  :965  remove of a key the held subject does not carry           -> _Miss("which_held")                 [c1, c3_*]
  :977  a single-subject `add` beside a held ref (clarify built, not raised) -> test_redesign_r2_fix2/fix4 (A3)
  :163-171 the flagged clarify about which_held (full origin)    -> [c3_flagged] and fix5 B1
"""
from __future__ import annotations

import pytest

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile, render_memory
from tests.chatbot.pv2._r4a_memory import without_last_reply
from app.services.chatbot.pv2.schema import Clarify, Period, ReportSpec
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held as mk_held, mk_state, out

# BEGIN SCENARIOS6
def _ex(kind, key):
    return {"status": "exact", "refs": [{"kind": kind, "key": key, "label": key}]}


def _s_c1():  # held product A1 + customer C1; remove customer Z (exact, not held) -> which_held; pick product
    r = FakeResolver({"entities": {"customer|Z": _ex("customer", "Z")}})
    st = mk_state(mk_held("order", {"product": ["A1"], "customer": ["C1"]}))
    return st, r, [out(ask(act="refines_held", domain="order", entities=[entity("customer", phrase="Z", op="remove")]))]


def _s_c2():  # held customer ABC only; remove product SRTWC286 -> which_held
    r = FakeResolver({"entities": {"product|SRTWC286": _ex("product", "SRTWC286")}})
    st = mk_state(mk_held("order", {"customer": ["ABC SDN BHD"]}))
    return st, r, [out(ask(act="refines_held", domain="order", entities=[entity("product", phrase="SRTWC286", op="remove")]))]


def _s_c3_flagged():  # flagged which_held whose origin carries the cross-kind remove
    r = FakeResolver({"entities": {"customer|Z": _ex("customer", "Z")}})
    st = mk_state(mk_held("order", {"product": ["A1"], "customer": ["C1"]}))
    return st, r, [out(ask(act="refines_held", domain="order", entities=[entity("customer", phrase="Z", op="remove")]),
                       clarify=Clarify(needed=True, phrase="that", about="which_held"))]


def _s_c3_report():  # held sales report + customer; remove a product not held
    h = mk_held("sales", {"customer": ["C1"]})
    h.report = ReportSpec(dataset="sales", measures=["revenue"], group_by=["customer"], top_n_per_group=None, filters=[],
                          period=Period(preset="last_month", n=None, date_from=None, date_to=None), compare_to=None,
                          compare_period=None, grain=None, threshold=None, sort=None, top_n=5, basis=None)
    r = FakeResolver({"entities": {"product|X": _ex("product", "X")}})
    return mk_state(h), r, [out(ask(act="refines_held", domain="sales", entities=[entity("product", phrase="X", op="remove")]))]


def _s_switch():  # raise site :316 switch_back naming a topic that was never printed
    st = mk_state(mk_held("order", {"product": ["A1"], "customer": ["C1"]}))
    return st, FakeResolver({}), [out(ask(act="switch_back", switch_back_to="T9"))]


def _s_attach():  # raise site :368 an attachment ask with no product held and none saved
    st = mk_state(mk_held("resource", {"customer": ["C1"]}))
    r = FakeResolver({"attachment_types": {"datasheet": {"status": "exact", "refs": [
        {"kind": "attachment_type", "key": "datasheet", "label": "datasheet"}]}}})
    return st, r, [out(ask(act="refines_held", domain="resource", attachment_type="datasheet"))]


def _s_newtopic_this():  # raise site :842 a NEW ask about "this one" with no held ref of that kind
    st = mk_state(mk_held("order", {"customer": ["C1"]}))
    return st, FakeResolver({}), [out(ask(act="new_topic", domain="incoming", entities=[entity("product", refers_to_held=True)]))]


def _play6(scenario, can_offer):
    st, r, parses = scenario()
    trail = []
    for parsed in parses + [out(ask(act="answers_bot_question", answer={"option_key": "1", "free_value": None}))]:
        st, acts = apply_turn(st, parsed, resolver=r, now=NOW, can_offer_handover=can_offer)
        mem = render_memory(st, MemoryProfile(audience="staff"), NOW)
        trail.append((type(acts[0]).__name__, getattr(getattr(acts[0], "pending", None), "kind", None), without_last_reply(mem[mem.index("HELD"):mem.index("LAST ANSWER")])))  # R4a: LAST ANSWER ROWS / HELD codes / LAST REPLY
    return trail
# END SCENARIOS6


PRE_R2_AFTER_PICK = {
    "c1": 'HELD: domain=order\n  customer: C1\n  product: A1\nPENDING QUESTION: none\nTOPICS (switch_back keys, newest first): none\n',
    "c2": 'HELD: domain=order\n  customer: ABC SDN BHD\nPENDING QUESTION: none\nTOPICS (switch_back keys, newest first): none\n',
    "c3_flagged": 'HELD: domain=order\n  customer: C1\n  product: A1\nPENDING QUESTION: none\nTOPICS (switch_back keys, newest first): none\n',
    "c3_report": 'HELD: domain=sales\n  customer: C1\n  report: dataset=sales; measures=revenue; basis=none; group_by=customer; period=last_month; top_n=5\nPENDING QUESTION: none\nTOPICS (switch_back keys, newest first): none\n',
    "switch": 'HELD: domain=order\n  customer: C1\n  product: A1\nPENDING QUESTION: none\nTOPICS (switch_back keys, newest first): none\n',
    "attach": 'HELD: domain=resource\n  customer: C1\n  attachment_type: datasheet\nPENDING QUESTION: none\nTOPICS (switch_back keys, newest first): none\n',
    "newtopic_this": 'HELD: domain=incoming\n  customer: C1\nPENDING QUESTION: none\nTOPICS (switch_back keys, newest first): none\n',
}


CASES = ["c1", "c2", "c3_flagged", "c3_report", "switch", "attach", "newtopic_this"]


@pytest.mark.parametrize("can_offer", [False, True], ids=["staff", "non-staff"])
@pytest.mark.parametrize("case", CASES)
def test_a_valid_pick_on_a_which_held_question_reaches_a_lookup_on_the_first_pick(case, can_offer):
    trail = _play6(globals()["_s_" + case], can_offer)
    assert trail[0][1] == "clarify_which_held", trail[0]
    kind, pending_kind, memory = trail[-1]
    assert kind == "Lookup", f"pre-R2 answered on the first pick; got {kind} ({pending_kind}), never the same question or an offer"
    assert memory == PRE_R2_AFTER_PICK[case]
