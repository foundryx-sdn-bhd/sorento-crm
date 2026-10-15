"""PV2 R5 fix round 2, red tests, no LLM, no DB (reviewer blocker on d7f3c4ddc).

The rule "a new topic in another domain is a new ask" must compare with the OPEN QUESTION's own domain (its
origin's), not with held's.

  1  silent wrong-row pick: a "which product" menu from 'stock of this product?' after a two-line DO; the reply '2'
     parsed as new_topic/inventory picks the menu's option 2, never row 2 of the last answer
  2  a new topic in the question's own domain answers it even when held.domain differs
  3  guard: 'no, what promotions are on' stays a new promotion ask
  4  one message of two asks: the pre-check and apply agree on whether ask 1 answers the question
  5  the confirm repeat guard, with a case that needs it (held's refs change under a new topic)
"""
from __future__ import annotations

import pytest

from app.services.chatbot.pv2 import register, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, dump_state
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held as mk_held, mk_state, option, out, pending
from tests.chatbot.pv2.test_r5_d_doc_lines import TWO_LINE_DO, _resolver, _state, _this_product
from tests.chatbot.pv2.test_r5_fix1_picks import (
    _GuessResolver, _LIM_GUESS, _lim_confirm, _open_so_question_state,
)


def _products(st):
    return [x.key for x in st.held.refs.get("product", [])]


# ------------------------------------------------------------------ 1 the silent wrong row


def _which_line_menu():
    """Turn 1 through the real flow: 'stock of this product?' after a two-line DO asks which line."""
    st = _state(TWO_LINE_DO, True)
    st1, acts = apply_turn(st, _this_product("inventory"), resolver=_resolver(), now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__
    assert sorted(o.label for o in st1.pending.options) == ["SRTWB1096", "SRTWT7203"]
    # The last answer's printed rows still exist: row 2 of them is a DIFFERENT code than the menu's option 2.
    st1.last_rows = register.shown_rows(st1.register, max(e.turn for e in st1.register))
    return st1


@pytest.mark.parametrize("answer", [None, {"option_key": None, "free_value": "2"}], ids=["row-pick", "free-value"])
def test_the_menu_option_two_is_picked_never_row_two_of_the_last_answer(answer):
    """RED: HEAD reads the reply (new_topic in inventory, held domain order) as a new ask, so '2' is row 2 of the
    last answer or a re-ask. ('the second one' / 'yang kedua' land the same parse.)"""
    st1 = _which_line_menu()
    menu = [o.label for o in st1.pending.options]
    parsed = out(ask(act="new_topic", domain="inventory", row_pick=None if answer else 2, answer=answer))
    st2, acts = apply_turn(st1, parsed, resolver=_resolver(), now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _products(st2) == [menu[1]], (_products(st2), menu)


# ------------------------------------------------------------------ 2 the question's own domain answers


def _inventory_question_over_an_order_held(options, kind):
    st = mk_state(mk_held("order"), pending_=pending(kind, options, asked="Which one?"))
    st.pending.origin = ask(act="new_topic", domain="inventory", entities=[entity("customer", phrase="lim")]).model_dump(mode="json")
    return st


def test_a_new_topic_in_the_questions_own_domain_answers_by_option_key():
    """RED: held is 'order', the question was asked by an inventory ask; HEAD compares with held and treats the reply
    as a new ask."""
    yes = option("yes", "LIM TRADING SDN BHD", {"kind": "customer", "key": "CUST-LIM", "label": "LIM TRADING SDN BHD", "op": "set"})
    st = _inventory_question_over_an_order_held([yes, option("no", "No", {"value": False})], "clarify_confirm")
    parsed = out(ask(act="new_topic", domain="inventory", answer={"option_key": "yes", "free_value": None}))
    st2, acts = apply_turn(st, parsed, resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert [r.key for r in st2.held.refs.get("customer", [])] == ["CUST-LIM"]


@pytest.mark.parametrize("n,expected", [(1, "CK1"), (2, "CK2")])
def test_a_new_topic_in_the_questions_own_domain_answers_by_number(n, expected):
    """RED: same, a number."""
    opts = [option(str(i), lab, {"kind": "customer", "key": f"CK{i}", "label": lab, "op": "set"})
            for i, lab in enumerate(["ACME TRADING", "BETA TRADING"], 1)]
    st = _inventory_question_over_an_order_held(opts, "clarify_entity")
    st2, acts = apply_turn(st, out(ask(act="new_topic", domain="inventory", row_pick=n)), resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert [r.key for r in st2.held.refs.get("customer", [])] == [expected]


# ------------------------------------------------------------------ 3 guard: another domain stays a new ask


@pytest.mark.parametrize("held_domain", ["order", "inventory"])
def test_guard_a_no_with_a_new_promotion_topic_stays_a_new_promotion_ask(held_domain):  # guard: passes today
    yes = option("yes", "LIM TRADING SDN BHD", {"kind": "customer", "key": "CUST-LIM", "label": "LIM TRADING SDN BHD", "op": "set"})
    st = mk_state(mk_held(held_domain), pending_=pending("clarify_confirm", [yes, option("no", "No", {"value": False})]))
    st.pending.origin = ask(act="new_topic", domain="order", entities=[entity("customer", phrase="lim")]).model_dump(mode="json")
    parsed = out(ask(act="new_topic", domain="promotion", answer={"option_key": "no", "free_value": None}))
    st2, acts = apply_turn(st, parsed, resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup) and acts[0].domain == "promotion" and st2.held.domain == "promotion"


# ------------------------------------------------------------------ 4 two asks, one answer: both sides agree


def _run_two_asks(ask1_domain):
    """Held is 'order'; ask 0 moves held to 'inventory'; ask 1 is a number against the open SO-origin customer menu.
    A dealer without the SO grant."""
    parsed = out(ask(act="refines_held", domain="inventory", entities=[entity("product", phrase="SRTWC286")]),
                 ask(act="new_topic", domain=ask1_domain, row_pick=2))
    resolver = FakeResolver({"entities": {"product|SRTWC286": {"status": "exact", "refs": [
        {"kind": "product", "key": "SRTWC286", "label": "SRTWC286"}]}}})
    ctx = turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(_open_so_question_state()), profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: parsed, resolver=resolver, load_exchanges=lambda s: [],
        granted_reveals=frozenset({"delivery_orders.outstanding"}))
    return turn._run(ctx)


def test_ask_one_is_read_alike_by_the_precheck_and_apply_when_ask_zero_moved_held():
    """RED: ask 1 names inventory, the question's origin is an order ask: it is a NEW ask. HEAD's pre-check judges it
    against the start-of-turn held (order: not an answer) but apply judges it against the held ask 0 just changed
    (inventory: an answer), so the SO question is answered and replayed after the pre-check let it through."""
    res = _run_two_asks("inventory")
    answered = any(getattr(a, "pending_kind", None) or getattr(a, "picked", None) for a in res.actions)
    replayed = "SO410001" in (res.direct_reply or "")
    refused_early = res.actions == [] and "not enabled" in (res.direct_reply or "")
    assert refused_early or not (answered or replayed), (res.direct_reply, res.actions)


def test_guard_ask_one_in_the_questions_own_domain_is_refused_before_apply():  # guard: passes today
    """Ask 1 names the origin's domain (order): an answer on both sides, so the lost SO grant refuses the turn up front."""
    res = _run_two_asks("order")
    assert res.actions == [] and "not enabled" in (res.direct_reply or ""), (res.direct_reply, res.actions)


# ------------------------------------------------------------------ 5 the confirm repeat guard that needs the exemption


def test_the_same_confirm_asked_again_is_a_miss_when_a_new_topic_wipes_a_held_brand():
    """Held carries a brand; the new topic replaces the subject, so held's refs differ between the two turns. Without
    the CONFIRM exemption in `_repeat_is_a_miss` the repeated confirm is a fresh question (reask False, misses 0)."""
    st = _lim_confirm()
    st.held = mk_held("order", {"brand": ["ITALI"]})
    r = _GuessResolver({"entities": {"customer|lim": _LIM_GUESS}})
    parsed = out(ask(act="new_topic", domain="order", entities=[entity("customer", phrase="lim")]))
    st2, acts = apply_turn(st, parsed, resolver=r, now=NOW)
    assert isinstance(acts[0], ClarifyAct) and acts[0].pending.kind == "clarify_confirm", type(acts[0]).__name__
    assert acts[0].reask is True and acts[0].pending.misses == 1, (acts[0].reask, acts[0].pending.misses)
