"""PV2 R5 slice B (UAC R5-5): the question kind survives a pick. Red tests, no LLM, no DB.

Setup: an arrival / delivery-date question was held (domain order, status outstanding); v1 then asked which
customer (a mirrored `v1_open_question`, kind customer_pick). The user answers it. The bridged verdict must still be
the outstanding order question, must carry the customer pick, and must name the document axis so v1 does not ask
'Sales orders or delivery orders?' (turn_runtime.py: v1 asks that when status is outstanding and `document` is empty).
Observable pinned here: `verdict["document"]` is {"DO"} when a delivery order is held, {"SO"} when a sales order is
held, {"DO", "SO"} when no document kind is held.

Fresh paraphrases of the answer: 'the first one', 'ACME Trading please', 'untuk yang pertama'.
"""
from __future__ import annotations

import pytest

from app.services.chatbot.pv2 import bridge, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.state import Lookup, dump_state
from app.services.chatbot.turn import pending as v1p
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, held as mk_held, mk_state, out

TODAY = NOW.date()
LABELS = ["ACME TRADING SDN BHD", "BETA TRADING SDN BHD"]


def _state(refs):
    h = mk_held("order", refs, status="outstanding")
    opts = [{"code": f"C{i}", "label": lab, "position": i, "entity_type": "customer"} for i, lab in enumerate(LABELS, 1)]
    v1 = v1p.ask("customer_pick", opts, payload={"domain": "order"}, asked_at_turn=3)
    st = turn.mirror_v1_pending(mk_state(h), v1, last_reply="Which customer?\n1. ACME TRADING SDN BHD\n2. BETA TRADING SDN BHD")
    assert st.pending is not None and st.pending.kind == "v1_open_question"
    return st


def _by_key():
    return out(ask(act="answers_bot_question", answer={"option_key": "1", "free_value": None}))


def _by_name():
    return out(ask(act="answers_bot_question", answer={"option_key": None, "free_value": LABELS[0]}))


def _as_new_topic():
    """The parser read the reply as a fresh topic but still carried the answer to the open question."""
    return out(ask(act="new_topic", domain="order", answer={"option_key": "1", "free_value": None}))


SO_GRANT = "sales_orders.outstanding"
DO_GRANT = "delivery_orders.outstanding"


def _bridge(refs, parsed, granted=frozenset({SO_GRANT, DO_GRANT})):
    """The whole PV2 turn (grant pre-check included) for a dealer holding `granted`; returns the v1 verdict."""
    ctx = turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(_state(refs)), profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: parsed, resolver=FakeResolver({}), load_exchanges=lambda s: [],
        granted_reveals=frozenset(granted))
    res = turn._run(ctx)
    assert res.verdict is not None, res.direct_reply
    return res.verdict


def _carries_customer_pick(verdict):
    oqa = verdict["open_question_answer"] or {}
    named = any(LABELS[0] in (e.get("raw") or "") for e in verdict["entities"])
    return (oqa.get("mode") == "pick" and oqa.get("picked") == [1]) or named


PICKS = [("option-key", _by_key), ("free-value-name", _by_name), ("new-topic-with-answer", _as_new_topic)]
KINDS = [({"delivery_order": ["202610-0151"]}, {"DO"}), ({"sales_order": ["SO419122"]}, {"SO"}), ({}, {"DO", "SO"})]
KIND_IDS = ["held-DO", "held-SO", "nothing-held-with-SO-grant"]


@pytest.mark.parametrize("pick", [p for _, p in PICKS], ids=[n for n, _ in PICKS])
def test_the_pick_keeps_the_outstanding_order_question_and_carries_the_customer(pick):
    """RED for the free value and new_topic shapes (HEAD drops the status / the pick); the option-key shape keeps the
    status today but carries no document axis (next test)."""
    verdict = _bridge({}, pick())
    assert verdict["status"] == "outstanding", verdict["status"]
    assert verdict["domain_hint"] == "order", verdict["domain_hint"]
    assert _carries_customer_pick(verdict), (verdict["open_question_answer"], verdict["entities"])


@pytest.mark.parametrize("pick", [p for _, p in PICKS], ids=[n for n, _ in PICKS])
@pytest.mark.parametrize("refs,expected", KINDS, ids=KIND_IDS)
def test_the_pick_names_the_document_axis_so_v1_does_not_ask_which_document(pick, refs, expected):
    """RED: HEAD leaves `document` empty, so v1 asks 'Sales orders or delivery orders?' after the pick."""
    verdict = _bridge(refs, pick())
    assert set(verdict["document"] or []) == expected, verdict["document"]
    assert verdict["status"] == "outstanding"


def test_a_plain_pick_with_no_held_status_is_not_given_an_outstanding_question():  # guard: passes today
    """The kind rides on the HELD question: an inventory subject picked from the same menu stays a stock ask."""
    h = mk_held("inventory", {"product": ["SRTWC286"]})
    opts = [{"code": f"C{i}", "label": lab, "position": i, "entity_type": "customer"} for i, lab in enumerate(LABELS, 1)]
    st = turn.mirror_v1_pending(mk_state(h), v1p.ask("customer_pick", opts, payload={"domain": "inventory"}, asked_at_turn=3),
                                last_reply="Which customer?")
    st, acts = apply_turn(st, _by_key(), resolver=FakeResolver({}), now=NOW)
    verdict = bridge.to_v1_verdict(st, acts[0], today=TODAY)
    assert verdict["status"] in (None, "") and verdict["domain_hint"] == "inventory"


@pytest.mark.parametrize("pick", [p for _, p in PICKS], ids=[n for n, _ in PICKS])
def test_nothing_held_and_no_so_grant_gets_delivery_orders_only(pick):
    """RED: a dealer with only delivery-order grants must not be given the SO side (v1's both bucket would print
    'Sales order figures are not enabled'). Fresh case: the dealer asks the same question, a different customer menu."""
    verdict = _bridge({}, pick(), granted=frozenset({DO_GRANT}))
    assert set(verdict["document"] or []) == {"DO"}, verdict["document"]
    assert verdict["status"] == "outstanding"
