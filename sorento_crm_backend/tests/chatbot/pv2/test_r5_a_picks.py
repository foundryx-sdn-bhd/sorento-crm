"""PV2 R5 slice A (UAC R5-1, R5-4): a number / ordinal pick acts on the row it names. Red tests, no LLM, no DB.

Parses are handwritten; the person's words live only in docstrings (never in the parse, never matched).
The ordinal wordings used as fresh paraphrases: 'no 2', 'number two', 'yang kedua' (ms), '第二' (zh),
'the dealer one', 'the end-user one too'.

Tests marked "# guard: passes today" pin existing behaviour the fix must keep; the rest are red on HEAD.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.chatbot.pv2 import bridge, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, Option
from app.services.chatbot.turn import pending as v1p
from tests.chatbot.pv2.carry_fakes import (
    NOW,
    FakeResolver,
    ask,
    held as mk_held,
    mk_state,
    option,
    out,
    pending,
)

TODAY = NOW.date()
CODES = ["SRTWB0101", "SRTWB0202", "SRTWB0303"]


def _stale_rows(n=8):
    """What the last ANSWER printed: a different list (LAST ANSWER ROWS), so a pick on the wrong source shows."""
    return [Option(key=str(i), label=f"OLDROW{i}", payload={"kind": "row", "row_kind": "product", "of": n})
            for i in range(1, n + 1)]


def _keys(st, kind="product"):
    return [r.key for r in st.held.refs.get(kind, [])]


# ------------------------------------------------------------------ pending lists


def _dym_state(**kw):
    """A PV2 did-you-mean list of three products (keys are positions, payload key is the code)."""
    opts = [option(str(n), c, {"kind": "product", "key": c, "label": c, "op": "set"}) for n, c in enumerate(CODES, 1)]
    q = pending("clarify_did_you_mean", opts, asked="Did you mean one of these?\n1. A\n2. B\n3. C")
    return mk_state(mk_held("inventory"), pending_=q, last_rows=_stale_rows(), **kw)


def _confirm_state():
    """A yes/no confirm of one guessed customer."""
    yes = option("yes", "LIM TRADING SDN BHD", {"kind": "customer", "key": "CUST-LIM", "label": "LIM TRADING SDN BHD", "op": "set"})
    no = option("no", "No", {"value": False})
    q = pending("clarify_confirm", [yes, no], asked="Did you mean LIM TRADING SDN BHD?")
    return mk_state(mk_held("order"), pending_=q, last_rows=_stale_rows())


def _v1_roster_state(kind="customer_pick", n=3, answered=None, labels=None, domain="order"):
    labels = labels or [f"CUSTOMER {i}" for i in range(1, n + 1)]
    opts = [{"code": f"C{i}", "label": lab, "position": i, "entity_type": "customer"} for i, lab in enumerate(labels, 1)]
    payload = {"domain": domain}
    if answered:
        payload["answered_positions"] = answered
    v1 = v1p.ask(kind, opts, payload=payload, asked_at_turn=3)
    st = mk_state(mk_held(domain), last_rows=_stale_rows())
    return turn.mirror_v1_pending(st, v1, last_reply=f"Which one?\n" + "\n".join(f"{i}. {l}" for i, l in enumerate(labels, 1)))


# parse shapes for "the second one"
def _row_pick_refine(n):
    return out(ask(act="refines_held", domain="inventory", row_pick=n))


def _row_pick_answer(n, language="en"):
    return out(ask(act="answers_bot_question", row_pick=n), language=language)


def _free_answer(n, language="en"):
    return out(ask(act="answers_bot_question", answer={"option_key": None, "free_value": str(n)}), language=language)


def _same_list_again(old, acts):
    return isinstance(acts[0], ClarifyAct) and [o.label for o in acts[0].pending.options][: len(old.pending.options)] == [
        o.label for o in old.pending.options
    ]


# ------------------------------------------------------------------ (a) PV2 clarify_did_you_mean


@pytest.mark.parametrize("make,language", [
    (_row_pick_answer, "en"),   # 'no 2'
    (_free_answer, "en"),       # 'number two' parsed to free_value "2"
    (_row_pick_answer, "ms"),   # 'yang kedua'
    (_row_pick_answer, "zh"),   # '第二'
    (_row_pick_refine, "en"),   # the parser read it as a refine; the open list still owns the number
], ids=["answer-row-pick", "answer-free-value", "ms-ordinal", "zh-ordinal", "refine-row-pick"])
def test_dym_pick_two_acts_on_option_two_not_the_old_answer_rows(make, language):
    """RED: HEAD refuses a bare number on a did-you-mean (answer paths) and indexes LAST ANSWER ROWS (refine path)."""
    st0 = _dym_state()
    parsed = make(2, language) if make is not _row_pick_refine else make(2)
    st, acts = apply_turn(st0, parsed, resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _keys(st) == [CODES[1]], _keys(st)
    assert st.pending is None
    assert not _same_list_again(st0, acts)


def test_dym_option_key_pick_still_works():  # guard: passes today
    st, acts = apply_turn(_dym_state(), out(ask(act="answers_bot_question", answer={"option_key": "2", "free_value": None})),
                          resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup) and _keys(st) == [CODES[1]]


def test_dym_pick_with_clarify_needed_is_still_read():
    """RED: a clarify flag beside a clear pick ('no 2, wait which?') must not turn the pick into a miss."""
    parsed = out(ask(act="answers_bot_question", row_pick=2),
                 clarify={"needed": True, "phrase": "2", "about": "entity"})
    st, acts = apply_turn(_dym_state(), parsed, resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _keys(st) == [CODES[1]]


@pytest.mark.parametrize("make", [_row_pick_answer, _free_answer, _row_pick_refine], ids=["row-pick", "free-value", "refine"])
def test_dym_pick_past_the_end_asks_again_once_and_never_acts(make):
    """Seven on a 3-item list: the same names asked once more, no product held, no wrong row from the old rows.
    RED for the refine path (HEAD acts on OLDROW7); the answer paths already miss (guard)."""
    st0 = _dym_state()
    st, acts = apply_turn(st0, make(7), resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__
    assert [o.label for o in st.pending.options][:3] == CODES
    assert st.pending.misses == 1
    assert _keys(st) == [] and not any(isinstance(a, Lookup) for a in acts)


# ------------------------------------------------------------------ (b) PV2 clarify_confirm


@pytest.mark.parametrize("make", [_row_pick_answer, _free_answer, _row_pick_refine], ids=["row-pick", "free-value", "refine"])
def test_confirm_pick_one_is_the_guessed_option(make):
    """RED: 'the first one' / '1' to 'Did you mean X?' takes X; HEAD refuses a number on a confirm."""
    st0 = _confirm_state()
    st, acts = apply_turn(st0, make(1), resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _keys(st, "customer") == ["CUST-LIM"]
    assert st.pending is None


def test_confirm_yes_still_works():  # guard: passes today
    st, acts = apply_turn(_confirm_state(), out(ask(act="answers_bot_question", answer={"option_key": "yes", "free_value": None})),
                          resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup) and _keys(st, "customer") == ["CUST-LIM"]


# ------------------------------------------------------------------ (c) a mirrored v1 roster


@pytest.mark.parametrize("make", [_row_pick_answer, _free_answer, _row_pick_refine], ids=["row-pick", "free-value", "refine"])
def test_v1_roster_pick_two_bridges_as_open_question_answer_two(make):
    """RED: HEAD carries no pick (free value) or acts on the old answer rows (refine)."""
    st0 = _v1_roster_state()
    assert st0.pending is not None and st0.pending.kind == "v1_open_question"
    st, acts = apply_turn(st0, make(2), resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert acts[0].pending_kind == "v1_open_question"
    verdict = bridge.to_v1_verdict(st, acts[0], today=TODAY)
    oqa = verdict["open_question_answer"]
    assert oqa and oqa["mode"] == "pick" and oqa["picked"] == [2], oqa


def test_v1_roster_option_key_pick_bridges_the_pick():  # guard: passes today
    st, acts = apply_turn(_v1_roster_state(), out(ask(act="answers_bot_question", answer={"option_key": "3", "free_value": None})),
                          resolver=FakeResolver({}), now=NOW)
    verdict = bridge.to_v1_verdict(st, acts[0], today=TODAY)
    assert verdict["open_question_answer"]["picked"] == [3]


def test_v1_roster_pick_past_the_end_never_bridges_a_pick():
    """guard: passes today (a refine on a stale row misses at the resolver); must keep missing, never act."""
    st, acts = apply_turn(_v1_roster_state(), _row_pick_refine(7), resolver=FakeResolver({}), now=NOW)
    assert not any(isinstance(a, Lookup) and a.pending_kind == "v1_open_question" and a.picked for a in acts)
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__


# ------------------------------------------------------------------ no pending: a row_pick on the shown answer rows


def _answer_rows_state():
    rows = [Option(key="1", label="SRTWC286-SH", payload={"kind": "row", "row_kind": "product", "of": 2}),
            Option(key="2", label="SRTWT7203", payload={"kind": "row", "row_kind": "product", "of": 2})]
    return mk_state(mk_held("inventory", {"product": ["A1"]}), last_rows=rows)


_RESOLVE_7203 = {"entities": {"product|SRTWT7203": {"status": "exact", "refs": [
    {"kind": "product", "key": "RES-7203", "label": "SRTWT7203"}]}}}


def test_no_pending_refine_row_pick_acts_on_the_shown_row():  # guard: passes today (R4a)
    st, acts = apply_turn(_answer_rows_state(), _row_pick_refine(2), resolver=FakeResolver(_RESOLVE_7203), now=NOW)
    assert isinstance(acts[0], Lookup) and _keys(st) == ["RES-7203"]


@pytest.mark.parametrize("act", ["new_topic", "answers_bot_question"])
def test_no_pending_row_pick_acts_on_the_shown_row_whatever_the_act(act):
    """RED: HEAD honours a row_pick only under refines_held; 'number two' parsed as another act is dropped."""
    parsed = out(ask(act=act, domain="inventory", row_pick=2))
    st, acts = apply_turn(_answer_rows_state(), parsed, resolver=FakeResolver(_RESOLVE_7203), now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _keys(st) == ["RES-7203"]


# ------------------------------------------------------------------ R5-4 promo copy roster


def _promo_state(answered=None):
    return _v1_roster_state(kind="tier_pick", n=2, answered=answered, labels=["Dealer copy", "End-user copy"], domain="promotion")


def _bridged(st0, parsed):
    st, acts = apply_turn(st0, parsed, resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], Lookup), f"the promo pick must be acted on, got {type(acts[0]).__name__}"
    return bridge.to_v1_verdict(st, acts[0], today=TODAY), acts[0]


def test_promo_pick_by_option_key_is_bridged():  # guard: passes today
    verdict, _ = _bridged(_promo_state(), out(ask(act="answers_bot_question", answer={"option_key": "1", "free_value": None})))
    assert verdict["open_question_answer"]["picked"] == [1]


def test_promo_pick_by_free_value_is_bridged_not_dropped():
    """RED: 'the dealer one' arriving as free_value alone leaves HEAD with no pick at all (v1 then says
    it did not catch the choice). The verdict must carry the pick, as a position or as the typed value on an entity."""
    verdict, _ = _bridged(_promo_state(), out(ask(act="answers_bot_question", answer={"option_key": None, "free_value": "dealer one"})))
    oqa = verdict["open_question_answer"] or {}
    carried_as_entity = any("dealer one" in (e.get("raw") or "") for e in verdict["entities"])
    assert oqa.get("mode") == "pick" or carried_as_entity, (oqa, verdict["entities"])


def test_promo_pick_by_ordinal_row_pick_is_bridged():
    """RED: 'the end-user one' read as the second printed copy."""
    verdict, _ = _bridged(_promo_state(), _row_pick_answer(2))
    assert verdict["open_question_answer"]["picked"] == [2]


def test_promo_second_copy_after_the_first_was_answered_is_mirrored_and_bridged():
    """RED: a roster with `answered_positions` is not mirrored on HEAD (tests/chatbot/pv2/test_fix_r7f.py:76-80
    pins `pending is None` for a product picker answered at position 7), so 'the end-user one too' finds no
    open question. The new expectation: the tier / copy roster stays pickable after one pick."""
    st0 = _promo_state(answered=[1])
    assert st0.pending is not None and st0.pending.kind == "v1_open_question", st0.pending
    verdict, _ = _bridged(st0, out(ask(act="answers_bot_question", answer={"option_key": "2", "free_value": None})))
    assert verdict["open_question_answer"]["picked"] == [2]


def test_promo_second_copy_by_ordinal_after_the_first_was_answered():
    """RED: same, the parser gave a row_pick for 'the other copy too'."""
    st0 = _promo_state(answered=[1])
    verdict, act = _bridged(st0, _row_pick_answer(2))
    assert verdict["open_question_answer"]["picked"] == [2]
