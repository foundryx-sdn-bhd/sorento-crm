"""PV2 R5 fix round 1 (review gaps), red tests, no LLM, no DB.

  2  a new topic in another domain is not swallowed as the answer to an open confirm
  3  a repeated confirm counts as a miss even when held changed between the two turns (guard)
  4  promo copy pick, driven through v1's own `apply` (the roster matcher), not only the bridged verdict
  5  the grant pre-check judges the ask apply converts to an answer (refuse BEFORE any resolver call)
  6  a UUID-shaped free value typed at a v1 customer roster never reaches v1 as a carried `uuid` entity
  7  an account pick through a containing-name resolver (returns both accounts) applies only the named one
(Item 1 lives in test_r5_b_kind.py.) Fresh paraphrases: 'no, what promotions are on', 'A/C I only', 'the end-user one',
'show me stock instead'. Tests marked "# guard: passes today" pin behaviour the fix must keep.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.chatbot.pv2 import bridge, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.schema import Entity
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, Option, dump_state
from app.services.chatbot.turn import pending as v1p
from tests.chatbot.pv2.carry_fakes import (
    NOW,
    FakeResolver,
    ask,
    entity,
    held as mk_held,
    mk_state,
    option,
    out,
    pending,
)
from tests.chatbot.pv2.test_r5_a_picks import _v1_roster_state

TODAY = NOW.date()
FAMILY = "ACME TRADING SDN BHD"
ACC1, ACC2 = f"{FAMILY} [A/C I]", f"{FAMILY} [A/C II]"
K1, K2 = "CUST-ACC-1", "CUST-ACC-2"


def _keys(st, kind="customer"):
    return [r.key for r in st.held.refs.get(kind, [])]


# ------------------------------------------------------------------ 2 a new topic elsewhere is not an answer


def _lim_confirm():
    yes = option("yes", "LIM TRADING SDN BHD", {"kind": "customer", "key": "CUST-LIM", "label": "LIM TRADING SDN BHD", "op": "set"})
    no = option("no", "No", {"value": False})
    return mk_state(mk_held("order"), pending_=pending("clarify_confirm", [yes, no], asked="Did you mean LIM TRADING SDN BHD?"))


@pytest.mark.parametrize("domain", ["promotion", "incoming"])
def test_a_no_with_a_new_topic_in_another_domain_runs_that_domain(domain):
    """RED: HEAD converts any new_topic carrying an option key into the answer ('no'), so the other domain's ask is
    swallowed and the user is asked openly for a customer name. ('no, what promotions are on' / 'no, anything incoming')"""
    parsed = out(ask(act="new_topic", domain=domain, answer={"option_key": "no", "free_value": None}))
    st, acts = apply_turn(_lim_confirm(), parsed, resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert acts[0].domain == domain and st.held.domain == domain
    assert not st.held.refs.get("customer")


def test_a_new_topic_with_an_option_key_and_no_domain_still_answers_the_question():  # guard: passes today
    parsed = out(ask(act="new_topic", domain=None, answer={"option_key": "yes", "free_value": None}))
    st, acts = apply_turn(_lim_confirm(), parsed, resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup) and _keys(st) == ["CUST-LIM"]


def test_a_new_topic_with_an_option_key_in_the_held_domain_still_answers_the_question():  # guard: passes today
    parsed = out(ask(act="new_topic", domain="order", answer={"option_key": "yes", "free_value": None}))
    st, acts = apply_turn(_lim_confirm(), parsed, resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup) and _keys(st) == ["CUST-LIM"]


# ------------------------------------------------------------------ 3 the repeated-confirm guard (mutation pin)


class _GuessResolver(FakeResolver):
    """A containing-name search that finds one partial match: a guess (`guess=True`), as the real resolver reports it."""

    def resolve_entities(self, ents, *, held):
        res = super().resolve_entities(ents, held=held)
        for r in res:
            r.guess = True
        return res


_LIM_GUESS = {"status": "ambiguous", "options": [
    {"key": "CUST-LIM", "label": "LIM TRADING SDN BHD",
     "payload": {"kind": "customer", "key": "CUST-LIM", "label": "LIM TRADING SDN BHD"}}]}


def _confirm_with_a_held_product():
    s = _lim_confirm()
    s.held = mk_held("order", {"product": ["SRTWC286"]})
    return s


def _asks_lim_again(act, extra=()):
    return out(ask(act=act, domain="order", entities=[entity("customer", phrase="lim"), *extra]))


@pytest.mark.parametrize("make,label", [
    (lambda: _asks_lim_again("new_topic"), "new-topic-wipes-the-held-product"),
    (lambda: _asks_lim_again("refines_held", [entity("brand", phrase="itali")]), "refine-adds-a-brand"),
], ids=lambda v: v if isinstance(v, str) else "")
def test_the_same_confirm_asked_again_counts_as_a_miss_even_when_held_changed(make, label):  # guard: passes today
    """The confirm re-asked word for word is a MISS on it whatever else held did meanwhile (R5-3: a confirm's subject
    is its guess, not the held refs). Removing the CONFIRM exemption in `_repeat_is_a_miss` makes the held change
    hide the repeat: reask False, misses 0."""
    cfg = {"entities": {"customer|lim": _LIM_GUESS,
                        "brand|itali": {"status": "exact", "refs": [{"kind": "brand", "key": "ITALI", "label": "ITALI"}]}}}
    r = _GuessResolver(cfg)
    st, acts = apply_turn(_confirm_with_a_held_product(), make(), resolver=r, now=NOW)
    assert isinstance(acts[0], ClarifyAct) and acts[0].pending.kind == "clarify_confirm", type(acts[0]).__name__
    assert acts[0].reask is True and acts[0].pending.misses == 1, (acts[0].reask, acts[0].pending.misses)


# ------------------------------------------------------------------ 4 promo copy through v1's own matcher


def _tier(position, label, code):
    return {"position": position, "label": label, "code": code, "uuid": None, "uuids": [],
            "payload": {"value": code}, "entity_type": "tier"}


def _v1_promo(answered=None):
    payload = {"domain": "promotion"}
    if answered:
        payload["answered_positions"] = answered
    return v1p.ask("tier_pick", [_tier(1, "Dealer copy", "dealer"), _tier(2, "End-user copy", "end_user")],
                   payload=payload, asked_at_turn=3)


def _through_v1(v1_pending, parsed):
    """PV2 apply -> bridge -> v1's own `apply` over the same open roster; returns v1's state after the turn."""
    from app.services.chatbot.turn.apply import apply as v1_apply
    from app.services.chatbot.turn.state import Profile, State
    from tests.chatbot._turn_helpers import build_policy

    base = mk_state(mk_held("promotion"))
    st = turn.mirror_v1_pending(base, v1_pending, last_reply="Which copy?\n1. Dealer copy\n2. End-user copy")
    assert st.pending is not None, "the roster must be mirrored"
    st2, acts = apply_turn(st, parsed, resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    verdict = bridge.to_v1_verdict(st2, acts[0], today=TODAY)
    v1_state, plan = v1_apply(State(focus=bridge.focus_from_v2(acts[0].held), pending=v1_pending, profile=Profile()),
                              verdict, build_policy())
    return v1_state


@pytest.mark.parametrize("key,tier", [("1", ["dealer"]), ("2", ["end_user"])], ids=["dealer-copy", "end-user-copy"])
def test_an_option_key_pick_makes_v1_select_that_copy(key, tier):  # guard: passes today
    v1_state = _through_v1(_v1_promo(), out(ask(act="answers_bot_question", answer={"option_key": key, "free_value": None})))
    assert v1_state.focus.tier == tier, v1_state.focus.tier


@pytest.mark.parametrize("label,tier", [("End-user copy", ["end_user"]), ("dealer copy", ["dealer"])], ids=["exact", "case-folded"])
def test_a_free_value_equal_to_the_printed_label_is_selected_by_v1s_own_matcher(label, tier):  # guard: passes today
    v1_state = _through_v1(_v1_promo(), out(ask(act="answers_bot_question", answer={"option_key": None, "free_value": label})))
    assert v1_state.focus.tier == tier, v1_state.focus.tier


def test_the_other_copy_after_one_was_sent_is_selected_by_v1():  # guard: passes today
    """'the end-user one too' once the dealer copy (position 1) went out."""
    v1_state = _through_v1(_v1_promo(answered=[1]), out(ask(act="answers_bot_question", answer={"option_key": "2", "free_value": None})))
    assert v1_state.focus.tier == ["end_user"], v1_state.focus.tier


def test_a_free_value_that_is_not_a_printed_label_selects_nothing_here():  # guard: passes today
    """Matching a loose wording is the parser's job (it sends the option key); code never guesses it."""
    v1_state = _through_v1(_v1_promo(), out(ask(act="answers_bot_question", answer={"option_key": None, "free_value": "the dealer one"})))
    assert v1_state.focus.tier != ["dealer"], v1_state.focus.tier


# ------------------------------------------------------------------ 5 the grant pre-check sees the converted act

SO_GRANT = "sales_orders.outstanding"


def _open_so_question_state():
    """An SO ask's customer menu is open (its origin names a sales order); the contact has since lost the SO grant."""
    so = Entity(kind="sales_order", key=None, phrase=None, op="set", refers_to_held=False, doc_number="SO410001")
    origin = ask(act="new_topic", domain="order", entities=[so]).model_dump(mode="json")
    opts = [option(str(n), lab, {"kind": "customer", "key": f"CK{n}", "label": lab, "op": "set"})
            for n, lab in enumerate(["ACME TRADING", "BETA TRADING"], 1)]
    q = pending("clarify_entity", opts, asked="Which customer?\n1. ACME TRADING\n2. BETA TRADING")
    q.origin = origin
    return mk_state(mk_held("order"), pending_=q)


@pytest.mark.parametrize("parsed", [
    out(ask(act="refines_held", domain="order", row_pick=2)),
    out(ask(act="refines_held", domain="order", answer={"option_key": None, "free_value": "2"})),
    out(ask(act="new_topic", domain="order", row_pick=2)),
], ids=["refine-row-pick", "refine-free-value", "new-topic-row-pick"])
def test_a_pick_continuing_an_so_ask_is_refused_before_any_lookup_when_the_grant_is_gone(parsed):
    """RED: apply turns the refine into an answer to the SO question, but the pre-check judged only the refine, so
    the SO origin goes unjudged; the refusal then comes (if at all) after apply, with actions already built."""
    from app.services.chatbot.lanes.business.fetch import SO_NOT_ENABLED_MESSAGE

    r = FakeResolver({})
    ctx = turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(_open_so_question_state()), profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: parsed, resolver=r, load_exchanges=lambda s: [], granted_reveals=frozenset({"delivery_orders.outstanding"}))
    res = turn._run(ctx)
    assert res.direct_reply == SO_NOT_ENABLED_MESSAGE, (res.direct_reply, res.verdict)
    assert res.actions == [], "refused before apply ran"
    assert r.calls == [], r.calls


# ------------------------------------------------------------------ 6 a UUID typed at a customer roster


def test_a_uuid_typed_at_a_customer_roster_never_reaches_v1_as_a_carried_pick():
    """RED: HEAD turns the typed value into the carried-pick entity shape `{"uuid": <value>}` (bridge `_v1_entity` for
    a customer whose key is a uuid), which v1 trusts as a resolved customer id."""
    typed = "9b1f3c2e-5a47-4d0e-8c11-7f2e6a9d4b30"
    st = _v1_roster_state()
    parsed = out(ask(act="answers_bot_question", answer={"option_key": None, "free_value": typed}))
    st, acts = apply_turn(st, parsed, resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    verdict = bridge.to_v1_verdict(st, acts[0], today=TODAY)
    assert all(e.get("uuid") != typed for e in verdict["entities"]), verdict["entities"]
    assert all(e.get("confident") is not True for e in verdict["entities"] if typed in str(e.get("raw"))), verdict["entities"]


# ------------------------------------------------------------------ 7 the real containing-name resolver shape


class _ContainingResolver(FakeResolver):
    """What the real containing search answers for 'A/C I': 'A/C I' is inside both '[A/C I]' and '[A/C II]', so ONE
    family option comes back covering both accounts, flagged as a guess, with the accounts' own names."""

    def resolve_entities(self, ents, *, held):
        out_ = []
        for e in ents:
            self.calls.append(("entity", f"{e.kind}|{e.phrase}"))
            family = Option(key=K1, label=FAMILY, payload={
                "kind": "customer", "key": K1, "label": FAMILY, "keys": [K1, K2], "labels": [ACC1, ACC2]})
            out_.append(SimpleNamespace(status="ambiguous", refs=[], options=[family], phrase=e.phrase,
                                        unrecognised=False, guess=True, did_you_mean=False, specs=[], pool=None, more=0))
        return out_


def _family_confirm():
    yes = option("yes", FAMILY, {"kind": "customer", "key": K1, "keys": [K1, K2], "labels": [ACC1, ACC2],
                                 "label": FAMILY, "op": "set"})
    no = option("no", "No", {"value": False})
    q = pending("clarify_confirm", [yes, no], asked=f"Did you mean {FAMILY}?\n1. {ACC1}\n2. {ACC2}")
    return mk_state(mk_held("order", status="outstanding"), pending_=q)


@pytest.mark.parametrize("phrase", ["A/C I", "[A/C I]"])
def test_a_containing_name_search_that_returns_both_accounts_still_applies_only_the_named_one(phrase):
    """RED: HEAD takes the keys the resolver found ([K1, K2], the containing hit) as the pick and applies both."""
    parsed = out(ask(act="answers_bot_question", entities=[entity("customer", phrase=phrase)]))
    st, acts = apply_turn(_family_confirm(), parsed, resolver=_ContainingResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _keys(st) == [K1], _keys(st)
    assert st.held.status == "outstanding"
