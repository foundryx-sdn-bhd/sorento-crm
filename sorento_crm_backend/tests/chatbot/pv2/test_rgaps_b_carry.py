"""PV2 redesign R-GAPS-b, RG-1 / RG-1b (#1570): the carry-scope question and the staff loop cap. No model call.

Real `apply_turn` paths with the pure fakes. Staff = `can_offer_handover=False`; a dealer = True with a customer that is
not its own. Handwritten parses only.
"""
from __future__ import annotations

from datetime import date

import pytest

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.services.chatbot.pv2 import answer
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.schema import Period
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, OfferHandover, new_state
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, out

WHO = pytest.mark.parametrize("can_offer", [False, True], ids=["staff", "dealer (non-own customer)"])

R = FakeResolver({"entities": {
    "customer|hanlim": {"status": "exact", "refs": [{"kind": "customer", "key": "c-hanlim", "label": "HANLIM"}]},
    "product|SRT391-GM": {"status": "exact", "refs": [{"kind": "product", "key": "SRT391-GM", "label": "SRT391-GM"}]},
    "customer|abc": {"status": "ambiguous", "options": [
        {"key": k, "label": n, "payload": {"kind": "customer", "key": k, "label": n}}
        for k, n in (("c1", "ABC SDN BHD"), ("c2", "ABC TRADING"))]},
}})

SEVENTH = Period(preset=None, n=None, date_from=date(2026, 10, 7), date_to=date(2026, 10, 7))
OCTOBER = Period(preset=None, n=None, date_from=date(2026, 10, 1), date_to=date(2026, 10, 31))


def _turn1(can_offer):
    first = out(ask(act="new_topic", domain="order", entities=[entity("customer", phrase="hanlim")], period=SEVENTH))
    return apply_turn(new_state(NOW), first, resolver=R, now=NOW, can_offer_handover=can_offer)


def _turn2(can_offer, act="new_topic", **kw):
    st, _ = _turn1(can_offer)
    second = out(ask(act=act, domain="order", entities=[entity("product", phrase="SRT391-GM")], **kw))
    return apply_turn(st, second, resolver=R, now=NOW, can_offer_handover=can_offer)


def _pick(key):
    return out(ask(act="answers_bot_question", answer={"option_key": key, "free_value": None}))


def _keys(st, kind):
    return [r.key for r in st.held.refs.get(kind, [])]


def _is_carry_question(act):
    return isinstance(act, ClarifyAct) and "carry" in act.pending.kind


# ------------------------------------------------------------------ RG-1: the question


@WHO
@pytest.mark.parametrize("act", ["new_topic", "refines_held"])
def test_rg1_a_new_product_over_a_carried_customer_asks_only_the_customer_or_all_and_fetches_nothing(can_offer, act):
    st, acts = _turn2(can_offer, act=act)
    assert len(acts) == 1 and _is_carry_question(acts[0]), type(acts[0]).__name__
    assert len(acts[0].pending.options) == 2
    reply = answer.replay_reply(st, acts)
    assert "SRT391-GM" in reply and "HANLIM" in reply and "customers" in reply.lower(), reply


@WHO
def test_rg1b_pick_1_keeps_the_earlier_customer_and_the_new_product_with_the_earlier_period(can_offer):
    st, acts = _turn2(can_offer)
    st, acts = apply_turn(st, _pick("1"), resolver=R, now=NOW, can_offer_handover=can_offer)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _keys(st, "customer") == ["c-hanlim"] and _keys(st, "product") == ["SRT391-GM"]
    assert st.held.period is not None and st.held.period.date_from == date(2026, 10, 7)


@WHO
def test_rg1b_pick_2_clears_only_the_customer_and_keeps_the_earlier_period(can_offer):
    st, acts = _turn2(can_offer)
    st, acts = apply_turn(st, _pick("2"), resolver=R, now=NOW, can_offer_handover=can_offer)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _keys(st, "customer") == [] and _keys(st, "product") == ["SRT391-GM"]
    assert st.held.period is not None and st.held.period.date_from == date(2026, 10, 7)


@pytest.mark.parametrize("shape", ["answers_bot_question", "refines_held"])
def test_rg1b_a_subject_less_reply_while_the_question_is_open_re_asks_and_its_period_reaches_the_pick(shape):
    st, acts = _turn2(False)
    assert _is_carry_question(acts[0])
    reply = out(ask(act=shape, period=OCTOBER, answer={"option_key": None, "free_value": None}
                    if shape == "answers_bot_question" else None))
    st, acts = apply_turn(st, reply, resolver=R, now=NOW, can_offer_handover=False)
    assert _is_carry_question(acts[0]), f"{type(acts[0]).__name__}: the open question is asked again"
    st, acts = apply_turn(st, _pick("1"), resolver=R, now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], Lookup)
    assert st.held.period is not None and st.held.period.date_from == date(2026, 10, 1)
    assert _keys(st, "customer") == ["c-hanlim"]


# ------------------------------------------------------------------ RG-1b: where the question is not asked (guards: green today)


@WHO
def test_rg1b_guard_this_customer_carries_without_a_question(can_offer):
    st, _ = _turn1(can_offer)
    ents = [entity("customer", refers_to_held=True), entity("product", phrase="SRT391-GM")]
    st, acts = apply_turn(st, out(ask(act="new_topic", domain="order", entities=ents)), resolver=R, now=NOW,
                          can_offer_handover=can_offer)
    assert isinstance(acts[0], Lookup)
    assert _keys(st, "customer") == ["c-hanlim"] and _keys(st, "product") == ["SRT391-GM"]


@WHO
def test_rg1b_guard_a_new_ask_that_switches_to_inventory_is_no_carry_question(can_offer):
    st, _ = _turn1(can_offer)
    inv = out(ask(act="new_topic", domain="inventory", entities=[entity("product", phrase="SRT391-GM")]))
    st, acts = apply_turn(st, inv, resolver=R, now=NOW, can_offer_handover=can_offer)
    assert isinstance(acts[0], Lookup) and acts[0].domain == "inventory"


@WHO
def test_rg1b_guard_a_new_ask_that_opens_a_report_is_no_carry_question(can_offer):
    from app.services.chatbot.pv2.schema import ReportSpec

    rep = ReportSpec(dataset=None, measures=["revenue"], group_by=["product"], top_n_per_group=None, filters=[],
                     period=None, compare_to=None, compare_period=None, grain=None, threshold=None, sort=None,
                     top_n=5, basis="delivered")
    st, _ = _turn1(can_offer)
    parsed = out(ask(act="new_topic", domain="sales", report=rep, entities=[entity("product", phrase="SRT391-GM")]))
    st, acts = apply_turn(st, parsed, resolver=R, now=NOW, can_offer_handover=can_offer)
    assert not _is_carry_question(acts[0])


# ------------------------------------------------------------------ RG-1b: identical questions are misses; the staff cap


def _same_ambiguous_ask(st, can_offer):
    parsed = out(ask(act="new_topic", domain="order", entities=[entity("customer", phrase="abc")]))
    return apply_turn(st, parsed, resolver=R, now=NOW, can_offer_handover=can_offer, offer_mode="none")


def test_rg1b_the_second_identical_question_counts_as_a_miss():
    st, acts = _same_ambiguous_ask(new_state(NOW), False)
    assert isinstance(acts[0], ClarifyAct) and st.pending.misses == 0
    st, acts = _same_ambiguous_ask(st, False)
    assert isinstance(acts[0], ClarifyAct) and st.pending.misses == 1, "an identical new question is a miss"


def test_rg1b_the_third_identical_clarify_to_staff_is_v1s_still_need_line_with_no_options_and_no_offer():
    st = new_state(NOW)
    for _ in range(2):
        st, acts = _same_ambiguous_ask(st, False)
    st, acts = _same_ambiguous_ask(st, False)
    assert isinstance(acts[0], ClarifyAct) and not isinstance(acts[0], OfferHandover), type(acts[0]).__name__
    assert acts[0].pending.options == []
    reply = answer.replay_reply(st, acts)
    assert "I still need" in reply, reply


# ------------------------------------------------------------------ RG-1b: the contact's own customer (resolver scope)


@pytest.fixture()
def db():
    from tests.chatbot.pv2 import _resolve_seed as S

    with S.resolve_session() as session:
        yield session


def _db_turns(db, customer_scope, can_offer):
    """HANLIM + a period, then a new product; the real resolver reads `customer_scope` (what `scope_for_resolver`
    hands a dealer held to its linked customers)."""
    from app.services.chatbot.pv2.resolve import DbResolver
    from tests.chatbot.pv2 import _resolve_seed as S

    own = S.customer(db, "300-H001", "HANLIM TRADING SDN BHD")
    S.product(db, "SRT391-GM", cat=S.category(db, class_label="Water Closet"))
    scope = {"enforced": True, "ids": [own.id]} if customer_scope == "own" else {}
    resolver = DbResolver(db, pinned={"brand": set(), "product_type": set(), "attachment_type": set(), "spec_attribute": set()},
                          customer_scope=scope)
    first = out(ask(act="new_topic", domain="order", entities=[entity("customer", phrase="HANLIM TRADING SDN BHD")], period=SEVENTH))
    st, acts = apply_turn(new_state(NOW), first, resolver=resolver, now=NOW, can_offer_handover=can_offer)
    assert _keys(st, "customer"), "turn 1 holds the customer"
    second = out(ask(act="new_topic", domain="order", entities=[entity("product", phrase="SRT391-GM")]))
    return apply_turn(st, second, resolver=resolver, now=NOW, can_offer_handover=can_offer)


def test_rg1b_guard_a_carried_customer_inside_the_dealers_own_scope_gets_no_carry_question(db):
    st, acts = _db_turns(db, "own", can_offer=True)
    assert isinstance(acts[0], Lookup), f"{type(acts[0]).__name__}: v1 never asks about a contact's own customer"


def test_rg1b_the_same_carry_over_an_unscoped_resolver_is_asked(db):
    st, acts = _db_turns(db, "unscoped", can_offer=True)
    assert _is_carry_question(acts[0]), type(acts[0]).__name__
