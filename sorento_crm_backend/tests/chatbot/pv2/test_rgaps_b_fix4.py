"""PV2 redesign R-GAPS-b fix round 4 red tests: K1 (the staff cap line names what is missing and the subject), K2 (a carry
pick with a missed item looks up the found items), K3 (a stale held set code resolves like a fresh ask), K4 ("all"
replaces like a single pick). No model call."""
from __future__ import annotations

import re

import pytest

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.services.chatbot.pv2 import answer
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.schema import ReportSpec
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, new_state
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, out
from tests.chatbot.pv2.test_rgaps_b_fix1 import (
    SEVENTH, WHO, _amb, _c, _ex, _inv, _is_carry, _p, _pick, _prods, _set, _t, _turn, db,
)

R = FakeResolver({"entities": {
    "customer|hanlim": _ex("customer", "c-hanlim", "HANLIM"),
    "product|SRT391-GM": _ex("product", "SRT391-GM"),
    "product|SRT392": _ex("product", "SRT392"),
    "product|mou": _amb("product", ["MOU-1", "MOU-2"]),
}})


def _play(*parsed, can_offer=False, state=None, resolver=R):
    st, acts = state or new_state(NOW), None
    for p in parsed:
        st, acts = apply_turn(st, p, resolver=resolver, now=NOW, can_offer_handover=can_offer)
    return st, acts


def _lk(acts):
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    held = acts[0].held.refs
    return ({k: sorted(r.key for r in v) for k, v in held.items() if v}, [(m.kind, m.phrase) for m in acts[0].misses])


# ------------------------------------------------------------------ K1: the cap line names what is missing and the subject

NO_REPORT_PERIOD = ReportSpec(dataset="sales", measures=["revenue"], group_by=["customer"], top_n_per_group=None,
                              filters=[], period=None, compare_to=None, compare_period=None, grain=None,
                              threshold=None, sort=None, top_n=5, basis="delivered")


def _at_cap(first, repeat, resolver=R):
    st, acts = _play(first, resolver=resolver)
    for _ in range(3):
        st, acts = _play(repeat, state=st, resolver=resolver)
    return st, acts


def _assert_names_missing_and_subject(st, acts, subject, missing_word):
    assert isinstance(acts[0], ClarifyAct) and acts[0].pending.options == [], type(acts[0]).__name__
    reply = answer.replay_reply(st, acts)
    assert "I still need" in reply, reply
    assert "more detail" not in reply.lower(), f"generic cap line: {reply}"
    assert subject in reply, f"no subject: {reply}"
    assert missing_word in reply.lower(), f"missing word {missing_word!r} not named: {reply}"


def test_k1_entity_question_at_the_cap_names_the_product_and_the_held_customer():
    held = _t(_c("hanlim"), period=SEVENTH)
    st, _ = _play(held)
    repeat = _t(_p("mou"))
    for _ in range(3):
        st, acts = _play(repeat, state=st)
    _assert_names_missing_and_subject(st, acts, "HANLIM", "product")


def test_k1_guard_report_period_question_at_the_cap_names_the_period():
    """A report ask clears the held subject, so there is none to name: v1's bare "I still need the period." """
    st, _ = _play(_t(_p("SRT391-GM")))
    rep = out(ask(act="new_topic", domain="sales", report=NO_REPORT_PERIOD.model_copy(update={"measures": []})))
    st, acts = _play(rep, state=st)
    st, acts = _play(_pick(st.pending.options[0].key), state=st)
    assert st.pending is not None and st.pending.kind == "clarify_report_period", st.pending
    for _ in range(2):
        st, acts = _play(_pick("99"), state=st)
    reply = answer.replay_reply(st, acts)
    assert "I still need the period" in reply and "more detail" not in reply.lower(), reply


def test_k1_which_held_question_at_the_cap_names_what_is_missing_and_the_held_subject():
    from tests.chatbot.pv2.carry_fakes import held, mk_state

    state = mk_state(held("product_attachment", {"product": ["SRTWC286"]}))
    r = FakeResolver({"entities": {"product|SRTWC8066": _ex("product", "SRTWC8066")}})
    o = out(ask(act="refines_held", entities=[entity("product", phrase="SRTWC8066", op="add")]))
    st = state
    for _ in range(3):
        st, acts = apply_turn(st, o, resolver=r, now=NOW, can_offer_handover=False)
    _assert_names_missing_and_subject(st, acts, "SRTWC286", "product")


def test_k1_guard_carry_question_at_the_cap_names_the_customer_and_a_subject():
    # # owner rule 15 Oct: a named customer carries; only product then customer asks
    st, acts = _play(_t(_p("SRT391-GM"), period=SEVENTH), _t(_c("hanlim")))
    assert _is_carry(acts[0])
    for _ in range(2):
        st, acts = _play(_t(_c("hanlim")), state=st)
    reply = answer.replay_reply(st, acts)
    assert "I still need" in reply and "product" in reply.lower() and "SRT391-GM" in reply, reply
    assert "more detail" not in reply.lower(), reply


# ------------------------------------------------------------------ K2: a carry pick with a missed item


@pytest.fixture()
def dbk():
    from tests.chatbot.pv2 import _resolve_seed as S

    with S.resolve_session() as session:
        S.customer(session, "300-H001", "HANLIM TRADING SDN BHD")
        S.product(session, "SRT392", cat=S.category(session, class_label="Water Closet"))
        yield session


def _dturn(db, parsed, state=None, can_offer=False):
    from app.services.chatbot.pv2.resolve import DbResolver
    from tests.chatbot.pv2.test_rgaps_b_fix1 import PINNED

    return apply_turn(state or new_state(NOW), parsed, resolver=DbResolver(db, pinned=PINNED), now=NOW,
                      can_offer_handover=can_offer)


def _keys_of(acts, kind):
    return sorted(r.key for r in acts[0].held.refs.get(kind, []))


@WHO
@pytest.mark.parametrize("pick", ["1", "2"])
def test_k2_a_carry_pick_with_a_missed_item_looks_up_the_found_items_and_reports_the_miss(dbk, can_offer, pick):
    # # owner rule 15 Oct: a named customer carries; only product then customer asks
    miss_ask = out(ask(act="new_topic", domain="order", entities=[_c("HANLIM TRADING SDN BHD"), _c("ZZZ999")]))
    _, nocarry = _dturn(dbk, miss_ask, can_offer=can_offer)
    assert isinstance(nocarry[0], Lookup) and [m.phrase for m in nocarry[0].misses] == ["ZZZ999"], "guard: found + miss"
    first = out(ask(act="new_topic", domain="order", entities=[_p("SRT392")], period=SEVENTH))
    st, _ = _dturn(dbk, first, can_offer=can_offer)
    st, acts = _dturn(dbk, miss_ask, state=st, can_offer=can_offer)
    assert _is_carry(acts[0]), type(acts[0]).__name__
    st, acts = _dturn(dbk, _pick(pick), state=st, can_offer=can_offer)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _keys_of(acts, "customer") == _keys_of(nocarry, "customer")
    assert [(m.kind, m.phrase) for m in acts[0].misses] == [(m.kind, m.phrase) for m in nocarry[0].misses]
    assert len(_keys_of(acts, "product")) == (1 if pick == "1" else 0)


# ------------------------------------------------------------------ K3: a stale held set code resolves like a fresh ask


def _order_ask(code):
    return ask(act="new_topic", domain="order", entities=[entity("product", phrase=code)])


def _stale_vs_fresh(db, set_code, members):
    row = _set(db, set_code, [(p, 1) for p in members])
    st, _ = _turn(db, out(_order_ask(set_code)))
    row.is_active = False
    db.flush()
    stale_st, stale = _turn(db, _inv(act="refines_held"), state=st)
    fresh_st, fresh = _turn(db, _inv(entity("product", phrase=set_code)))
    return (stale_st, stale), (fresh_st, fresh)


def _held_codes(acts):
    return sorted(r.key for a in acts if isinstance(a, Lookup) for r in a.held.refs.get("product", []))


def test_k3a_a_stale_set_code_matching_a_product_family_holds_what_a_fresh_ask_holds(db):
    a, b = _prods(db, "SRTSETS2-A", "SRTSETS2-B")
    (stale_st, stale), (fresh_st, fresh) = _stale_vs_fresh(db, "SRTSETS2", [a, b])
    assert _held_codes(fresh), "the fresh ask finds the family"
    assert _held_codes(stale) == _held_codes(fresh), (_held_codes(stale), "vs fresh", _held_codes(fresh))
    assert [(m.kind, m.phrase) for m in stale[0].misses] == [(m.kind, m.phrase) for m in fresh[0].misses]


def test_k3b_a_stale_set_code_matching_nothing_gets_pv2s_own_not_found_offer(db):
    a, b = _prods(db, "QZMEMB-A", "QZMEMB-B")
    (stale_st, stale), (fresh_st, fresh) = _stale_vs_fresh(db, "KITXQ77", [a, b])
    assert isinstance(fresh[0], ClarifyAct) and fresh[0].pending.kind == "offer_doc_handover"
    assert isinstance(stale[0], ClarifyAct), type(stale[0]).__name__
    assert stale[0].pending.kind == fresh[0].pending.kind
    assert stale_st.pending is not None and stale_st.pending.kind == fresh_st.pending.kind, "open for yes"
    assert "KITXQ77" in answer.replay_reply(stale_st, stale)


# ------------------------------------------------------------------ K4: "all" replaces like a single pick


def _refine_mou_then(pick_key):
    held = _t(_c("hanlim"), _p("SRT391-GM"), period=SEVENTH)
    st, acts = _play(held, out(ask(act="refines_held", domain="order", entities=[_p("mou")])), _pick(pick_key))
    if _is_carry(acts[0]):
        st, acts = _play(_pick("1"), state=st)
    return _lk(acts)[0]


def test_k4_all_replaces_the_held_product_like_a_single_pick_does():
    assert _refine_mou_then("1")["product"] == ["MOU-1"], "guard: a single pick replaces"
    held = _refine_mou_then("all")
    assert held["product"] == ["MOU-1", "MOU-2"], f"all kept the old product: {held}"


def test_k4_guard_all_keeps_a_same_kind_sibling_from_the_same_ask():
    # # owner rule 15 Oct: a named customer carries; only product then customer asks (customer then products: no carry question; the branch below covers a stale build)
    two = out(ask(act="new_topic", domain="order", entities=[_p("SRT392"), _p("mou")]))
    st, acts = _play(_t(_c("hanlim"), period=SEVENTH), two, _pick("all"))
    if _is_carry(acts[0]):
        st, acts = _play(_pick("1"), state=st)
    assert _lk(acts)[0]["product"] == ["MOU-1", "MOU-2", "SRT392"]
