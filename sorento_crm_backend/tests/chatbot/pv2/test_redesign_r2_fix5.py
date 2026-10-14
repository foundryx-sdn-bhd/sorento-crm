"""PV2 redesign R2, fix round 5 (re-review of fix 4). Red tests on the real `apply_turn` path, phrase-only product
parses, no model call. Pre-R2 (941fb867a) strings were rendered once from a `git archive` export and hard-coded.

B1 a valid pick on a which_held question raised by a Miss never replays the Miss (staff: no repeat; non-staff: no offer)
B2 the waiting ask keeps no unvetted attachment kinds        B3 not-found second item as typed (guard)
B4 the held-subject grant check decides alone for state saved before R2 (guard, killed by removing `held`)
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.services.chatbot.lanes.business import SALES_REPORT_NOT_ENABLED_MESSAGE
from app.services.chatbot.pv2 import turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile, render_memory
from tests.chatbot.pv2._r4a_memory import without_last_reply
from app.services.chatbot.pv2.schema import Clarify, ReportSpec
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, Option, PendingQuestion, dump_state
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held as mk_held, mk_state, out

PICK = out(ask(act="answers_bot_question", answer={"option_key": "1", "free_value": None}))
MOU = {"status": "ambiguous", "options": [{"key": k, "label": k, "payload": {"kind": "product", "key": k, "label": k}}
                                          for k in ("SRTWC286", "SRTWC287")]}


def _memory(st) -> str:
    mem = render_memory(st, MemoryProfile(audience="staff"), NOW)
    return without_last_reply(mem[mem.index("HELD"):mem.index("LAST ANSWER")])  # R4a: LAST ANSWER ROWS / HELD codes / LAST REPLY


# ------------------------------------------------------------------ B1 which_held picks never re-miss


def _miss_then_pick(can_offer_handover):
    st = mk_state(mk_held("order", {"customer": ["ABC"]}))
    miss = out(ask(act="refines_held", domain="incoming", entities=[entity("product", refers_to_held=True)]))
    st, acts = apply_turn(st, miss, resolver=FakeResolver({}), now=NOW, can_offer_handover=can_offer_handover)
    assert isinstance(acts[0], ClarifyAct) and acts[0].pending.kind == "clarify_which_held"
    return apply_turn(st, PICK, resolver=FakeResolver({}), now=NOW, can_offer_handover=can_offer_handover)


@pytest.mark.parametrize("can_offer", [False, True], ids=["staff", "non-staff"])
def test_b1_a_pick_on_a_missed_asks_which_held_reaches_a_lookup_on_the_first_pick(can_offer):
    st, acts = _miss_then_pick(can_offer)
    assert isinstance(acts[0], Lookup), f"pre-R2 answered the deferred ask; got {type(acts[0]).__name__}"
    assert st.pending is None and st.held.domain == "incoming"
    assert _memory(st) == ("HELD: domain=incoming\n  customer: ABC\nPENDING QUESTION: none\n"
                           "TOPICS (switch_back keys, newest first): none\n")


def test_b1_a_pick_on_a_flagged_which_held_reaches_a_lookup_on_the_first_pick():
    st = mk_state(mk_held("inventory", {"product": ["A1"]}))
    flagged = out(ask(act="refines_held", domain="order", entities=[entity("customer", refers_to_held=True)]),
                  clarify=Clarify(needed=True, phrase=None, about="which_held"))
    st, acts = apply_turn(st, flagged, resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], ClarifyAct) and acts[0].pending.kind == "clarify_which_held"
    st, acts = apply_turn(st, PICK, resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup), "the replayed refers_to_held customer is a Miss with only a product held"
    assert _memory(st) == ("HELD: domain=order\n  product: A1\nPENDING QUESTION: none\n"
                           "TOPICS (switch_back keys, newest first): none\n")


# ------------------------------------------------------------------ B2 the waiting ask keeps no unvetted kinds


def test_b2_the_waiting_ask_does_not_re_add_an_attachment_kind_the_held_product_does_not_carry():
    at = {"datasheet": {"status": "exact", "refs": [{"kind": "attachment_type", "key": "datasheet", "label": "datasheet"}]},
          "brochure": {"status": "ambiguous", "options": [{"key": "datasheet", "label": "datasheet",
                                                           "payload": {"kind": "attachment_type", "key": "datasheet"}}]}}
    r = FakeResolver({"entities": {"product|mou": MOU}, "attachment_types": at})
    first = out(ask(act="refines_held", domain="resource", attachment_type=["datasheet", "brochure"],
                    entities=[entity("product", phrase="mou", op="add")]))
    st, acts = apply_turn(mk_state(mk_held("resource", {"product": ["A1"]})), first, resolver=r, now=NOW)
    assert isinstance(acts[0], ClarifyAct)
    st, acts = apply_turn(st, PICK, resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup)
    assert st.held.attachment_type == "datasheet" and st.held.attachment_types == [], "pre-R2: datasheet only"
    assert _memory(st) == ("HELD: domain=resource\n  product: SRTWC286\n  attachment_type: datasheet\n"
                           "PENDING QUESTION: none\nTOPICS (switch_back keys, newest first): none\n")


# ------------------------------------------------------------------ B3 not-found second item as typed (UAC R2-6b)


def _summary(st, act):
    return (type(act).__name__, getattr(getattr(act, "pending", None), "kind", None),
            getattr(getattr(act, "pending", None), "asked", None), st.held.domain,
            {k: [r.key for r in v] for k, v in st.held.refs.items()})


def test_b3_a_not_found_second_item_is_reported_as_if_the_picked_product_had_been_typed():
    """Guard (green on db8f1534c's code): the pick's reply and state equal the one-turn "SRTWC286 + zzz" ask."""
    r = FakeResolver({"entities": {"product|mou": MOU,
                                   "product|SRTWC286": {"status": "exact", "refs": [{"kind": "product", "key": "SRTWC286",
                                                                                     "label": "SRTWC286"}]}}})
    from app.services.chatbot.pv2.state import new_state

    a = out(ask(act="new_topic", domain="order", entities=[entity("product", phrase="mou"), entity("customer", phrase="zzz")]))
    st, _ = apply_turn(new_state(NOW), a, resolver=r, now=NOW)
    st, acts = apply_turn(st, PICK, resolver=r, now=NOW)
    typed = out(ask(act="new_topic", domain="order",
                    entities=[entity("product", phrase="SRTWC286"), entity("customer", phrase="zzz")]))
    st2, acts2 = apply_turn(new_state(NOW), typed, resolver=r, now=NOW)
    assert _summary(st, acts[0]) == _summary(st2, acts2[0])


# ------------------------------------------------------------------ B4 the held subject alone decides (pre-R2 state)


def _old_pending():
    """A question saved before R2: `deferred`, no `origin`, no sid."""
    return PendingQuestion(kind="clarify_entity", asked='Which "mou" do you mean?', asked_turn=3,
                           options=[Option(key="1", label="SRTWC286", payload={"key": "SRTWC286", "kind": "product",
                                                                                 "label": "SRTWC286", "op": "set"})],
                           deferred={"domain": None, "attachment_types": [], "quantity": None})


def _garbled_pick_without_the_grant(held_, audience):
    state = mk_state(held_, pending_=_old_pending())
    calls = []
    ctx = turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(state), profile=MemoryProfile(audience=audience),
        parse=lambda m, u: out(ask(act="answers_bot_question", answer={"option_key": "9", "free_value": None})),
        resolver=FakeResolver({}), load_exchanges=lambda s: [], report_answer=lambda *a, **k: calls.append(a),
        granted_reveals=frozenset())
    from app.services.chatbot.pv2.answer import reply_for

    result = turn._run(ctx)
    text = result.direct_reply or "\n".join(filter(None, (reply_for(a, "en") for a in result.actions)))
    return result, text, calls


def _sales_report_held():
    h = mk_held("sales", {})
    h.report = ReportSpec(dataset=None, measures=["revenue"], group_by=[], top_n_per_group=None, filters=[], period=None,
                          compare_to=None, compare_period=None, grain=None, threshold=None, sort=None, top_n=None,
                          basis=None)
    return h


def _held_decides_report():
    result, text, calls = _garbled_pick_without_the_grant(_sales_report_held(), "dealer")
    assert result.direct_reply == SALES_REPORT_NOT_ENABLED_MESSAGE, text
    assert "SRTWC286" not in text and calls == []


def _held_decides_po():
    result, text, _ = _garbled_pick_without_the_grant(mk_held("purchase_order", {"product": ["A1"]}), "staff")
    assert result.verdict is None and "purchase order" in (result.direct_reply or "").lower(), text
    assert "SRTWC286" not in text


def test_b4_a_pre_r2_pending_on_a_held_sales_report_is_refused_once_the_grant_is_gone():
    _held_decides_report()


def test_b4_a_pre_r2_pending_on_a_held_purchase_order_is_refused_for_staff_without_the_grant():
    _held_decides_po()
