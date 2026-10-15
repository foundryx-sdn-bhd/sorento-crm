"""PV2 R6 slice C (plan behaviour 4): a customer group is ONE customer. Red tests (real resolver on Postgres for the
resolution half, handwritten parses throughout).

Owner rule (ledger_family.group_names): the chatbot names a customer company by its GROUP NAME ONLY, never a ledger
marker, an account count or 'and N more'. A phrase matching one family whose group name contains the phrase is that
one customer. Fresh paraphrases: 'hanlim trad', 'what about acfirst last month?'.
"""
from __future__ import annotations

import pytest

from app.services.chatbot.pv2 import answer, bridge
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, new_state
from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import NOW, ask, entity, held as mk_held, mk_state, option, out, pending
from tests.chatbot.pv2.test_r5_g_unlinked import _contact, _resolver, _salesperson, db  # noqa: F401 - db is a fixture

FAMILIES = {
    "acfirst": ("ACFIRST CERAMICS SDN BHD", ["ACFIRST CERAMICS SDN BHD - [A/C I]", "ACFIRST CERAMICS SDN BHD - [A/C II]"]),
    "hanlim trad": ("HANLIM TRADING SDN BHD", ["HANLIM TRADING SDN BHD - [A/C I]", "HANLIM TRADING SDN BHD - [A/C II]"]),
}


def _world(db, phrase):
    group, names = FAMILIES[phrase]
    for n, name in enumerate(names):
        S.customer(db, f"GRP{abs(hash(phrase)) % 9999}{n}", name)
    contact = _contact(db)
    _salesperson(db, contact)
    resolver, _ = _resolver(db, contact)
    return resolver, group


def _say(resolver, phrase, **kw):
    parsed = out(ask(act="new_topic", domain="order", entities=[entity("customer", phrase=phrase)], **kw))
    return apply_turn(new_state(NOW), parsed, resolver=resolver, now=NOW, can_offer_handover=False)


@pytest.mark.parametrize("phrase", ["acfirst", "hanlim trad"])
def test_a_phrase_inside_one_groups_name_resolves_to_one_customer_labelled_by_the_group_name(db, phrase):
    """RED: HEAD returns a guess (status ambiguous) naming the accounts instead of the one customer."""
    resolver, group = _world(db, phrase)
    res = resolver.resolve_entities([entity("customer", phrase=phrase)], held=mk_held())[0]
    assert res.status == "exact", (res.status, [o.label for o in res.options])
    assert {r.label for r in res.refs} == {group}, [r.label for r in res.refs]


@pytest.mark.parametrize("phrase", ["acfirst", "hanlim trad"])
def test_the_turn_holds_that_one_customer_and_asks_nothing(db, phrase):
    """RED: HEAD asks 'Did you mean ...?' with the accounts listed, and holds no customer."""
    resolver, group = _world(db, phrase)
    st, acts = _say(resolver, phrase)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert {r.label for r in st.held.refs["customer"]} == {group}
    verdict = bridge.to_v1_verdict(st, acts[0], today=NOW.date())
    assert verdict["entities"], "v1 must be handed the customer, never 'all customers'"


def test_a_confirm_prints_the_group_name_only_never_an_account_list_or_count(db):
    """RED: the phrase 'A/C I' sits in the ledger markers, not in the group name, so a confirm is asked; HEAD prints
    '1. ... [A/C I]' '2. ... [A/C II]' (the owner's rule: group name only, no marker, no count)."""
    resolver, group = _world(db, "acfirst")
    st, acts = _say(resolver, "A/C I")
    assert isinstance(acts[0], ClarifyAct) and acts[0].pending.kind == "clarify_confirm", type(acts[0]).__name__
    reply = answer.replay_reply(st, acts)
    assert group in reply, reply
    assert "[" not in reply and "]" not in reply and "A/C" not in reply, reply
    assert not any(ch.isdigit() for ch in reply), reply


def test_the_guessed_customer_stays_held_while_the_confirm_is_open(db):
    """RED: HEAD holds no customer while it asks, so the next reply runs for all customers."""
    resolver, group = _world(db, "acfirst")
    st, acts = _say(resolver, "A/C I")
    assert isinstance(acts[0], ClarifyAct) and st.pending is not None and st.pending.kind == "clarify_confirm"
    assert {r.label for r in st.held.refs.get("customer", [])} == {group}, st.held


# ------------------------------------------------------------------ a domain / period reply on an open confirm


def _confirm_with_the_guess_held():
    yes = option("yes", "LIM TRADING SDN BHD", {"kind": "customer", "key": "CUST-LIM", "label": "LIM TRADING SDN BHD", "op": "set"})
    st = mk_state(mk_held("order", {"customer": ["CUST-LIM"]}),
                  pending_=pending("clarify_confirm", [yes, option("no", "No", {"value": False})], asked="Did you mean LIM TRADING SDN BHD?"))
    return st


@pytest.mark.parametrize("act", ["answers_bot_question", "refines_held", "unclear"])
def test_a_period_and_domain_reply_on_an_open_confirm_is_a_new_ask_that_keeps_the_customer(act):
    """RED: 'and last month?' while 'Did you mean LIM TRADING?' is open. The parser tags it whatever act; HEAD re-asks
    the confirm as a miss (answer / unclear) or loses the guess; the reply is a new ask over the guessed customer."""
    from tests.chatbot.pv2.carry_fakes import FakeResolver

    parsed = out(ask(act=act, domain="order", period={"preset": "last_month", "n": None, "date_from": None, "date_to": None}))
    st, acts = apply_turn(_confirm_with_the_guess_held(), parsed, resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert [r.key for r in st.held.refs.get("customer", [])] == ["CUST-LIM"]
    assert st.held.period is not None and st.held.period.preset == "last_month"
