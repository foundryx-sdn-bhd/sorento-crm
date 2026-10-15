"""PV2 R5 slice C (UAC R5-3): an account pick after a confirm / account menu is applied. Red tests, no LLM, no DB.

A customer family holds two ledger accounts, labelled by their bracketed markers ([A/C I], [A/C II]; see
app/services/ledger_family.py). The bot asked to confirm the family (or listed its accounts); the reply names ONE
account. That account only is applied (a single customer ref), the held question (an outstanding order ask) runs,
and the same confirm is never asked twice in a row.

Fresh paraphrases of the reply: 'A/C I only', 'the first ledger', 'the second one', 'akaun kedua'.
"""
from __future__ import annotations

import pytest

from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.state import ClarifyAct, Lookup
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held as mk_held, mk_state, option, out, pending

FAMILY = "ACME TRADING SDN BHD"
ACC1, ACC2 = f"{FAMILY} [A/C I]", f"{FAMILY} [A/C II]"
K1, K2 = "CUST-ACC-1", "CUST-ACC-2"

RESOLVER = {"entities": {
    "customer|A/C I": {"status": "exact", "refs": [{"kind": "customer", "key": K1, "label": ACC1}]},
    "customer|A/C II": {"status": "exact", "refs": [{"kind": "customer", "key": K2, "label": ACC2}]},
}}


def _held():
    return mk_held("order", status="outstanding")


def _confirm_state():
    """'Did you mean ACME TRADING SDN BHD?' whose yes is the whole family (both accounts)."""
    yes = option("yes", FAMILY, {"kind": "customer", "key": K1, "keys": [K1, K2], "label": FAMILY, "op": "set"})
    no = option("no", "No", {"value": False})
    return mk_state(_held(), pending_=pending("clarify_confirm", [yes, no], asked=f"Did you mean {FAMILY}?"))


def _menu_state():
    """The accounts listed: one numbered option per account."""
    opts = [option(str(n), label, {"kind": "customer", "key": k, "label": label, "op": "set"})
            for n, (k, label) in enumerate([(K1, ACC1), (K2, ACC2)], 1)]
    return mk_state(_held(), pending_=pending("clarify_entity", opts, asked="Which account?\n1. A/C I\n2. A/C II"))


def _customers(st):
    return [r.key for r in st.held.refs.get("customer", [])]


def _runs_the_held_question(st, acts):
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert acts[0].domain == "order" and st.held.domain == "order" and st.held.status == "outstanding"
    assert st.pending is None


def _named(phrase):
    return out(ask(act="answers_bot_question", entities=[entity("customer", phrase=phrase)]))


def _ordinal(n):
    return out(ask(act="answers_bot_question", row_pick=n))


def _free(n):
    return out(ask(act="answers_bot_question", answer={"option_key": None, "free_value": str(n)}))


# ------------------------------------------------------------------ the confirm of a family


@pytest.mark.parametrize("phrase,expected", [("A/C I", [K1]), ("A/C II", [K2])], ids=["first", "second"])
def test_confirm_reply_naming_one_account_applies_only_that_account(phrase, expected):
    """RED: HEAD finds no option key / free value in the reply and re-asks the confirm."""
    st, acts = apply_turn(_confirm_state(), _named(phrase), resolver=FakeResolver(RESOLVER), now=NOW)
    _runs_the_held_question(st, acts)
    assert _customers(st) == expected, _customers(st)


@pytest.mark.parametrize("make,n,expected", [
    (_ordinal, 2, None), (_free, 2, None), (_ordinal, 1, [K1, K2]),
], ids=["row-pick-second", "free-value-second", "row-pick-first"])
def test_confirm_reply_the_nth_one_is_its_stored_option(make, n, expected):
    """R6 lead ruling (re-scoped from R5-3): the confirm prints the group name only, so a number is its stored option
    as MEMORY numbers it: 1 is yes (the whole family), 2 is no (asks for the full name). An account is picked only
    by naming it (the tests above)."""
    st, acts = apply_turn(_confirm_state(), make(n), resolver=FakeResolver(RESOLVER), now=NOW)
    if expected is None:
        assert isinstance(acts[0], ClarifyAct) and acts[0].pending.kind == "clarify_entity", type(acts[0]).__name__
        assert _customers(st) == [], _customers(st)
        return
    _runs_the_held_question(st, acts)
    assert _customers(st) == expected, _customers(st)


def test_confirm_reply_read_as_a_refine_naming_one_account_applies_that_account_only():  # guard: passes today
    """The same reply parsed as a refine of the held question: one account, not the family."""
    parsed = out(ask(act="refines_held", domain="order", entities=[entity("customer", phrase="A/C II")]))
    st, acts = apply_turn(_confirm_state(), parsed, resolver=FakeResolver(RESOLVER), now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _customers(st) == [K2], _customers(st)


def test_confirm_yes_still_applies_the_whole_family():  # guard: passes today
    st, acts = apply_turn(_confirm_state(), out(ask(act="answers_bot_question", answer={"option_key": "yes", "free_value": None})),
                          resolver=FakeResolver(RESOLVER), now=NOW)
    assert isinstance(acts[0], Lookup) and _customers(st) == [K1, K2]


def test_the_same_reply_twice_never_asks_the_same_confirm_twice():
    """RED: HEAD re-asks the identical confirm on turn 1 and again on turn 2."""
    parsed = _named("A/C I")
    st1, acts1 = apply_turn(_confirm_state(), parsed, resolver=FakeResolver(RESOLVER), now=NOW)
    st2, acts2 = apply_turn(st1, parsed, resolver=FakeResolver(RESOLVER), now=NOW)
    asked = [a.pending for a in (*acts1, *acts2) if isinstance(a, ClarifyAct)]
    assert not any(p.kind == "clarify_confirm" for p in asked), [p.asked for p in asked]
    assert len({p.asked for p in asked}) == len(asked), [p.asked for p in asked]


def test_an_unreadable_reply_twice_does_not_repeat_the_identical_confirm_forever():  # guard: passes today
    """Nothing in the reply names an account; the confirm may be re-asked once (a miss) but the second
    unreadable turn must not be the same confirm again (the loop cap closes or changes the question)."""
    parsed = out(ask(act="answers_bot_question", answer={"option_key": None, "free_value": None}))
    st1, acts1 = apply_turn(_confirm_state(), parsed, resolver=FakeResolver({}), now=NOW)
    st2, acts2 = apply_turn(st1, parsed, resolver=FakeResolver({}), now=NOW)
    first = [a.pending.asked for a in acts1 if isinstance(a, ClarifyAct)]
    second = [a.pending.asked for a in acts2 if isinstance(a, ClarifyAct) and not a.closes]
    assert not (first and second and first[0] == second[0]), (first, second)


# ------------------------------------------------------------------ the account menu


def test_menu_option_key_pick_applies_that_account():  # guard: passes today
    st, acts = apply_turn(_menu_state(), out(ask(act="answers_bot_question", answer={"option_key": "2", "free_value": None})),
                          resolver=FakeResolver(RESOLVER), now=NOW)
    _runs_the_held_question(st, acts)
    assert _customers(st) == [K2]


@pytest.mark.parametrize("phrase,expected", [("A/C I", [K1]), ("A/C II", [K2])], ids=["first", "second"])
def test_menu_reply_naming_the_account_applies_it(phrase, expected):
    """RED: the reply names the account by its entity phrase and no option key; HEAD re-asks the menu."""
    st, acts = apply_turn(_menu_state(), _named(phrase), resolver=FakeResolver(RESOLVER), now=NOW)
    _runs_the_held_question(st, acts)
    assert _customers(st) == expected


def test_menu_reply_with_the_printed_account_name_applies_it():
    """RED: the printed account label as the free value ('ACME TRADING SDN BHD [A/C II]')."""
    parsed = out(ask(act="answers_bot_question", answer={"option_key": None, "free_value": ACC2}))
    st, acts = apply_turn(_menu_state(), parsed, resolver=FakeResolver(RESOLVER), now=NOW)
    _runs_the_held_question(st, acts)
    assert _customers(st) == [K2]


@pytest.mark.parametrize("make", [_ordinal, _free], ids=["row-pick", "free-value"])
def test_menu_the_second_one_applies_the_second_account(make):  # guard: passes today
    st, acts = apply_turn(_menu_state(), make(2), resolver=FakeResolver(RESOLVER), now=NOW)
    _runs_the_held_question(st, acts)
    assert _customers(st) == [K2]
