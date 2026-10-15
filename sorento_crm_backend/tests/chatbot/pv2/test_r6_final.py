"""PV2 R6 final round: pins for clauses the reviewer found unpinned (they pass today and must go red when the clause is
broken) and reds for the should-fix items. Handwritten parses in the shape the live parser emits; no LLM, no DB.

Pins: K02 `answering` exclusion, K04 `_unclear_frame` on a miss, K11 a bare number with nothing open is a shown row,
K14 `_referents` order, K18 `last_menu` cleared on a turn that answers nothing.
Reds: `last_menu` cleared on the flagged and the escalation early returns; a held customer GROUP is one option;
a dealer's domain menu hides the staff-only domains.
"""
from __future__ import annotations

import pytest

from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, Option, ResolvedRef
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held as mk_held, mk_state, option, out, pending

MENU = [ResolvedRef(kind="product", key="MENUP", label="MENUP")]


def _row(n, label, kind, parent=None, of=3):
    payload = {"kind": "row", "row_kind": kind, "of": of}
    if parent:
        payload["in"] = parent
    return Option(key=str(n), label=label, payload=payload)


def _which_held(act="unclear"):
    return out(ask(act=act), clarify={"needed": True, "about": "which_held", "phrase": None})


def _labels(st):
    return [o.label for o in st.pending.options] if st.pending else []


# ------------------------------------------------------------------ K02 / K04: an unclear ask that carries something and misses


def _open_numbered_question_state():
    opts = [option(str(n), c, {"kind": "product", "key": c, "label": c, "op": "set"}) for n, c in enumerate(["PA1", "PB2"], 1)]
    st = mk_state(mk_held("inventory", {"product": ["A1"]}), pending_=pending("clarify_entity", opts, asked="Which product?"),
                  last_rows=[_row(1, "SRTWB1096", "product", of=2), _row(2, "SRTWT7203", "product", of=2)])
    return st


def _unclear_with_a_frame_that_misses():
    """'and what about orders last month, the ninth one?': a domain, a period and a row number past the shown rows."""
    return out(ask(act="unclear", domain="order", row_pick=9,
                   period={"preset": "last_month", "n": None, "date_from": None, "date_to": None}),
               clarify={"needed": True, "about": "other", "phrase": None})


def test_k02_an_unclear_ask_that_carries_a_frame_and_misses_asks_its_own_question_not_a_retry_of_the_open_one():  # guard: passes today
    st0 = _open_numbered_question_state()
    st, acts = apply_turn(st0, _unclear_with_a_frame_that_misses(), resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__
    assert acts[0].reask is False and acts[0].pending.kind != "clarify_entity", (acts[0].reask, acts[0].pending.kind)


def test_k04_the_domain_and_period_of_an_unclear_ask_that_misses_stay_in_held():  # guard: passes today
    st, acts = apply_turn(_open_numbered_question_state(), _unclear_with_a_frame_that_misses(), resolver=FakeResolver({}), now=NOW)
    assert st.held.domain == "order", st.held.domain
    assert st.held.period is not None and st.held.period.preset == "last_month"


# ------------------------------------------------------------------ K11: a bare number with nothing open is a shown row


@pytest.mark.parametrize("answer", [{"option_key": "2", "free_value": None}, {"option_key": None, "free_value": "2"}],
                         ids=["option-key", "free-value"])
def test_k11_a_bare_number_with_nothing_open_names_the_shown_row(answer):  # guard: passes today
    """'the second one' parsed as an answer with no question open: row 2 of the last answer."""
    st0 = mk_state(mk_held("inventory", {"product": ["A1"]}),
                   last_rows=[_row(1, "SRTWC286-SH", "product", of=2), _row(2, "SRTWT7203", "product", of=2)])
    r = FakeResolver({"entities": {"product|SRTWT7203": {"status": "exact", "refs": [
        {"kind": "product", "key": "RES-7203", "label": "SRTWT7203"}]}}})
    st, acts = apply_turn(st0, out(ask(act="answers_bot_question", answer=answer)), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert [x.key for x in st.held.refs["product"]] == ["RES-7203"]


# ------------------------------------------------------------------ K14: the order of what a reference can mean


def test_k14_a_reference_offers_lines_and_printed_rows_then_held_then_topics_then_the_last_menu_in_that_order():  # guard: passes today
    """'show me those' with no entity: one DO's lines come first, then what is held, the saved topics' products, and the
    records of the menu the last pick answered."""
    st0 = mk_state(
        mk_held("order", {"customer": ["HELDCUST"]}),
        topics=[mk_held("inventory", {"product": ["TOPICP"]})],
        last_rows=[_row(1, "202610-0151", "delivery_order"), _row(2, "LINE1", "product", parent=1), _row(3, "LINE2", "product", parent=1)],
    )
    st0.last_menu = list(MENU)
    st, acts = apply_turn(st0, _which_held(), resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__
    assert _labels(st) == ["LINE1", "LINE2", "HELDCUST", "TOPICP", "MENUP"], _labels(st)


# ------------------------------------------------------------------ K18 / last_menu on every early return


def _with_a_menu():
    st = mk_state(mk_held("inventory", {"product": ["A1"]}))
    st.last_menu = list(MENU)
    return st


def test_k18_a_turn_that_answers_nothing_clears_the_last_menu():  # guard: passes today
    r = FakeResolver({"entities": {"product|SRTWC286": {"status": "exact", "refs": [
        {"kind": "product", "key": "SRTWC286", "label": "SRTWC286"}]}}})
    parsed = out(ask(act="new_topic", domain="inventory", entities=[entity("product", phrase="SRTWC286")]))
    st, acts = apply_turn(_with_a_menu(), parsed, resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup) and st.last_menu == []


def test_the_flagged_early_return_clears_the_last_menu():
    """RED: a flag with nothing to run (no ask carries anything) returns early; HEAD leaves the old menu behind."""
    parsed = out(ask(act="unclear"), clarify={"needed": True, "about": "other", "phrase": None})
    st, acts = apply_turn(_with_a_menu(), parsed, resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], ClarifyAct)
    assert st.last_menu == [], st.last_menu


def test_the_escalation_early_return_clears_the_last_menu():
    """RED: 'get me a person' returns a hand-over before any ask; the old menu must not survive it."""
    parsed = out(ask(act="small_talk"), escalation={"requested": True, "team_key": None})
    st, acts = apply_turn(_with_a_menu(), parsed, resolver=FakeResolver({}), now=NOW)
    assert st.last_menu == [], st.last_menu


# ------------------------------------------------------------------ a held customer group is one option


def test_which_held_offers_a_held_customer_group_once_not_one_same_named_option_per_account():
    """RED: a group held as two accounts (one ref per key, one label) is listed '1. ACME TRADING SDN BHD / 2. ACME
    TRADING SDN BHD' by HEAD (owner rule: the group name once); the one option still carries both accounts."""
    refs = [ResolvedRef(kind="customer", key=k, label="ACME TRADING SDN BHD") for k in ("K1", "K2")]
    h = mk_held("order")
    h.refs = {"customer": refs}
    st, acts = apply_turn(mk_state(h), _which_held(), resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], ClarifyAct)
    assert _labels(st) == ["ACME TRADING SDN BHD"], _labels(st)
    payload = st.pending.options[0].payload
    assert set(payload.get("keys") or [payload.get("key")]) == {"K1", "K2"}, payload


# ------------------------------------------------------------------ the dealer's domain menu


def _domain_question(**kw):
    parsed = out(ask(act="unclear"), clarify={"needed": True, "about": "domain", "phrase": None})
    st0 = mk_state(mk_held(None, {"product": ["SRTWC286"]}))
    return apply_turn(st0, parsed, resolver=FakeResolver({}), now=NOW, **kw)


STAFF_ONLY = {"purchase_cost", "sales", "purchase_order"}


def _domains(st):
    return {o.payload.get("key") for o in st.pending.options}


def test_a_dealers_domain_menu_hides_the_staff_only_domains():
    """RED: 'what would you like to check?' lists last purchase cost, sales and outstanding purchase orders to an
    availability-only dealer; HEAD builds the menu without looking at the contact."""
    st, acts = _domain_question(stock_availability_only=True)
    assert isinstance(acts[0], ClarifyAct) and st.pending.options
    assert not (_domains(st) & STAFF_ONLY), _domains(st)
    assert "inventory" in _domains(st)


def test_a_staff_contacts_domain_menu_still_lists_them():  # guard: passes today
    st, acts = _domain_question()
    assert STAFF_ONLY <= _domains(st), _domains(st)
