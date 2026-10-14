"""PV2-FIX r7f (crew failure analysis): the v1 question text mirrored, the pending cleared after a pick,
the topic saved on a domain switch, the held product kept after a failed turn."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.chatbot.pv2 import turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.schema import Ask
from app.services.chatbot.pv2.state import ClarifyAct, Lookup
from app.services.chatbot.turn import pending as v1p
from tests.chatbot.pv2 import replay
from tests.chatbot.pv2.carry_fakes import ask as _base_ask, held as mk_held, mk_state, option, out, pending
from tests.chatbot.pv2.test_fix_round2 import _Fake, _e

OWNER = {json.loads(l)["case"]: json.loads(l) for l in
         (Path(__file__).parent / "fixtures" / "livecheck_owner_r7.jsonl").read_text(encoding="utf-8").splitlines()
         if l.strip()}


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


def _run_row(case):
    (result,) = replay.run_case([dict(OWNER[case], _where=case)])
    return result


# ------------------------------------------------------------------ J the mirrored question carries v1's text


PICKER_REPLY = ("Which product do you mean? Please choose:\n1. SRTWC286-SH - no incoming\n"
                "2. SRTWC286-SH-150 - no incoming\n3. SRTWC286-SH-200 - no incoming")
STOCK_REPLY = ("SRTWC286 matches 10 products. Which one?\n1. SRTWC286-SH\n2. SRTWC286-SH-150\n3. SRTWC286-SH-200")
OFFER_REPLY = "Stock summary for the requested products.\n\n1. *Product Code:* SRTWC286-SH\n\nWould you like me to escalate to warehouse team?"


def _v1_pending(kind, expects=None, payload=None, n=3, team="purchasing"):
    opts = [{"code": f"C{i}", "label": f"SRTWC286-V{i}", "position": i, "entity_type": "product"} for i in range(1, n + 1)]
    return v1p.ask(kind, [] if expects == "yes_no" else opts, team=team, expects=expects,
                   payload=payload or {}, asked_at_turn=3)


@pytest.mark.parametrize("reply,asked", [
    (PICKER_REPLY, "Which product do you mean? Please choose:"),
    (STOCK_REPLY, "SRTWC286 matches 10 products. Which one?"),
], ids=["picker", "stock-menu"])
def test_the_mirrored_pick_question_carries_the_text_v1_printed(reply, asked):
    state = turn.mirror_v1_pending(mk_state(), _v1_pending("product_pick"), last_reply=reply)
    assert state.pending.asked == asked, state.pending.asked


def test_the_mirrored_offer_carries_the_question_v1_printed():
    # crew 14 Oct r8a item 4: the offer is worded from v1's own pending team (what v1 printed: warehouse)
    state = turn.mirror_v1_pending(mk_state(), _v1_pending("team_pick", expects="yes_no", team="warehouse"),
                                   last_reply=OFFER_REPLY)
    assert state.pending.asked == "Would you like me to escalate to warehouse team?", state.pending.asked


# ------------------------------------------------------------------ K the pending is cleared after a pick v1 answers


def test_a_pick_that_v1_answers_without_a_new_question_clears_the_pending():
    h = mk_held("incoming", {"product": ["SRTWC286"]})
    state = turn.mirror_v1_pending(mk_state(h), _v1_pending("product_pick", payload={"domain": "incoming"}, n=10),
                                   last_reply=PICKER_REPLY)
    state, actions = apply_turn(state, out(_ask(act="answers_bot_question",
                                                answer={"option_key": "7", "free_value": None})), resolver=_Fake({}))
    assert isinstance(actions[0], Lookup)
    assert state.pending is None


def test_a_v1_picker_already_answered_is_not_mirrored_again_next_turn():
    answered = _v1_pending("product_pick", payload={"domain": "incoming", "answered_positions": [7]}, n=10)
    state = turn.mirror_v1_pending(mk_state(mk_held("incoming", {"product": ["SRTWC286"]})), answered, last_reply="")
    assert state.pending is None, state.pending


# ------------------------------------------------------------------ L the topic is saved on a domain switch


def test_a_refine_that_changes_the_domain_saves_the_previous_held_as_a_topic():
    result = _run_row("owner-A-eta-fbbe484e")
    state = result.state_out
    assert state.held.domain == "incoming"
    assert [r.key for r in state.held.refs["product"]] == ["SRTWC286"]
    assert [t.domain for t in state.topics][:1] == ["inventory"], [t.domain for t in state.topics]


# ------------------------------------------------------------------ M the held product survives a failed turn

SRT = {"product|SRTWC286": ("exact", [("product", "SRTWC286")], False)}


def _held_state():
    return mk_state(mk_held("inventory", {"product": ["SRTWC286"]}))


def _keys(state):
    return [r.key for r in state.held.refs.get("product", [])]


def test_a_new_topic_with_an_ambiguous_product_keeps_the_held_product():
    table = {**SRT, "product|WF12C": ("ambiguous", [("product", "SRTWF12C"), ("product", "SRTWF12C-BL")], False)}
    state, actions = apply_turn(_held_state(), out(_ask(act="new_topic", domain="incoming", entities=[_e(phrase="WF12C")])),
                                resolver=_Fake(table))
    assert isinstance(actions[0], ClarifyAct)
    assert "SRTWC286" in _keys(state), state.held


def test_a_new_topic_with_a_not_found_document_keeps_the_held_product():
    ent = {"kind": "order", "key": None, "phrase": None, "op": "set", "refers_to_held": False,
           "quantity": None, "doc_number": "202699-9999"}
    state, actions = apply_turn(_held_state(), out(_ask(act="new_topic", domain="order", entities=[ent])),
                                resolver=_Fake({}))
    assert isinstance(actions[0], ClarifyAct)
    assert "SRTWC286" in _keys(state), state.held


def test_a_new_topic_with_a_did_you_mean_product_keeps_the_held_product():
    table = {"product|SRTXYZ999": ("ambiguous", [("product", "SRTWB999")], True)}
    state, actions = apply_turn(_held_state(), out(_ask(act="new_topic", domain="inventory",
                                                        entities=[_e(phrase="SRTXYZ999")])), resolver=_Fake(table))
    assert isinstance(actions[0], ClarifyAct)
    assert "SRTWC286" in _keys(state), state.held


def test_a_refine_naming_an_unknown_product_keeps_the_held_product():
    state, actions = apply_turn(_held_state(), out(_ask(act="refines_held", domain="inventory",
                                                        entities=[_e(phrase="NOPE123")])), resolver=_Fake({}))
    assert "SRTWC286" in _keys(state), state.held


def test_a_missed_answer_keeps_the_held_product():
    state = mk_state(mk_held("inventory", {"product": ["SRTWC286"]}),
                     pending_=pending("clarify_entity", [option("1", "A"), option("2", "B")]))
    state, actions = apply_turn(state, out(_ask(act="answers_bot_question",
                                                answer={"option_key": "9", "free_value": None})), resolver=_Fake({}))
    assert "SRTWC286" in _keys(state), state.held


def test_an_unclear_turn_keeps_the_held_product():
    state, actions = apply_turn(_held_state(), out(_ask(act="unclear"), clarify={"needed": True, "phrase": "x", "about": "other"}),
                                resolver=_Fake({}))
    assert "SRTWC286" in _keys(state), state.held
