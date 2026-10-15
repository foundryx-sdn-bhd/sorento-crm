"""PV2 R6 slice C (plan behaviour 4): a customer group is ONE customer. Red tests (real resolver on Postgres for the
resolution half, handwritten parses throughout).

Owner rule (ledger_family.group_names): the chatbot names a customer company by its GROUP NAME ONLY, never a ledger
marker, an account count or 'and N more'. Lead ruling 15 Oct (owner rules R4a): a phrase EQUAL to one family's group
name (as a group compares: case, punctuation, SDN BHD) is that one customer; a partial phrase stays a guess, asked as a
yes/no confirm showing the group name only, and nothing is applied until it is confirmed. Fresh paraphrases:
'hanlim trad', 'J & Y World Hardware', 'what about acfirst last month?'.
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
    "Acfirst Ceramics": (
        "ACFIRST CERAMICS SDN BHD", ["ACFIRST CERAMICS SDN BHD - [A/C I]", "ACFIRST CERAMICS SDN BHD - [A/C II]"]),
    "J & Y World Hardware": (
        "J & Y WORLD HARDWARE", ["J & Y WORLD HARDWARE - [A/C I]", "J & Y WORLD HARDWARE - [A/C II]"]),
}
PARTIAL = ["acfirst", "hanlim trad"]
GROUP_NAMED = ["Acfirst Ceramics", "J & Y World Hardware"]


#: Fixed customer codes (a hash of the phrase changed per process and collided).
CODE_OF = {phrase: f"GRP{n:02d}" for n, phrase in enumerate(sorted(FAMILIES))}


def _world(db, phrase):
    group, names = FAMILIES[phrase]
    for n, name in enumerate(names):
        S.customer(db, f"{CODE_OF[phrase]}{n}", name)
    contact = _contact(db)
    _salesperson(db, contact)
    resolver, _ = _resolver(db, contact)
    return resolver, group


def _say(resolver, phrase, **kw):
    parsed = out(ask(act="new_topic", domain="order", entities=[entity("customer", phrase=phrase)], **kw))
    return apply_turn(new_state(NOW), parsed, resolver=resolver, now=NOW, can_offer_handover=False)


@pytest.mark.parametrize("phrase,exact", [*((p, False) for p in PARTIAL), *((p, True) for p in GROUP_NAMED)])
def test_a_phrase_inside_one_groups_name_resolves_to_one_customer_labelled_by_the_group_name(db, phrase, exact):
    """A partial phrase is a guess with exactly ONE option, labelled by the group name; the group name itself is
    exact: one customer labelled by the group name, every account key."""
    resolver, group = _world(db, phrase)
    res = resolver.resolve_entities([entity("customer", phrase=phrase)], held=mk_held())[0]
    if not exact:
        assert res.status == "ambiguous" and res.guess, (res.status, res.guess)
        assert [o.label for o in res.options] == [group], [o.label for o in res.options]
        return
    assert res.status == "exact", (res.status, [o.label for o in res.options])
    assert {r.label for r in res.refs} == {group} and len(res.refs) == 2, [r.label for r in res.refs]


@pytest.mark.parametrize("phrase", GROUP_NAMED)
def test_the_turn_holds_that_one_customer_and_asks_nothing(db, phrase):
    """RED: HEAD asks 'Did you mean ...?' with the accounts listed, and holds no customer."""
    resolver, group = _world(db, phrase)
    st, acts = _say(resolver, phrase)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert {r.label for r in st.held.refs["customer"]} == {group}
    verdict = bridge.to_v1_verdict(st, acts[0], today=NOW.date())
    assert verdict["entities"], "v1 must be handed the customer, never 'all customers'"


@pytest.mark.parametrize("phrase", PARTIAL)
def test_a_partial_group_name_is_a_yes_no_confirm_showing_the_group_name_only(db, phrase):
    """RED: HEAD lists the accounts under 'Did you mean ...?'. Nothing is applied until it is confirmed."""
    resolver, group = _world(db, phrase)
    st, acts = _say(resolver, phrase)
    assert isinstance(acts[0], ClarifyAct) and acts[0].pending.kind == "clarify_confirm", type(acts[0]).__name__
    assert [o.key for o in acts[0].pending.options] == ["yes", "no"]
    reply = answer.replay_reply(st, acts)
    assert group in reply and "[A/C" not in reply, reply
    assert not any(ch.isdigit() for ch in reply), reply
    assert not st.held.refs.get("customer"), st.held


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


@pytest.mark.parametrize("act", ["answers_bot_question", "refines_held", "unclear"])
def test_no_lookup_without_the_customer_goes_to_v1_while_the_confirm_is_open(db, act):
    """RED: 'and last month?' while the confirm is open; HEAD re-asks it as a miss or runs the period for all customers.
    Nothing is held while it is open, so no Lookup may go to v1 without the customer; once confirmed, the Lookup holds
    the group's accounts and the period the reply carried."""
    resolver, group = _world(db, "acfirst")
    st, acts = _say(resolver, "A/C I")
    assert isinstance(acts[0], ClarifyAct) and st.pending is not None and st.pending.kind == "clarify_confirm"
    period = {"preset": "last_month", "n": None, "date_from": None, "date_to": None}
    st, acts = apply_turn(st, out(ask(act=act, domain="order", period=period)), resolver=resolver, now=NOW,
                          can_offer_handover=False)
    for a in acts:
        if isinstance(a, Lookup):
            assert a.held.refs.get("customer"), "a Lookup without the customer is 'Customer: all'"
            assert bridge.to_v1_verdict(st, a, today=NOW.date())["entities"]
    if not any(isinstance(a, Lookup) for a in acts):
        assert st.pending is not None and st.pending.kind == "clarify_confirm", st.pending
        yes = out(ask(act="answers_bot_question", answer={"option_key": "yes", "free_value": None}))
        st, acts = apply_turn(st, yes, resolver=resolver, now=NOW, can_offer_handover=False)
        assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert {r.label for r in st.held.refs.get("customer", [])} == {group}, st.held
    assert len(st.held.refs["customer"]) == 2
    assert st.held.period is not None and st.held.period.preset == "last_month", st.held.period


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
