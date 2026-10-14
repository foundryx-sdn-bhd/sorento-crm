"""PV2 redesign R4a fix round 4: red tests, no LLM.

N1  a "no" to a one-option confirm (clarify_confirm) drops the guess and asks openly: no options, nothing held or
    fetched. en pinned exactly; ms / zh only "not the didn't-catch line".
L4  a non-en parse (ms or zh) wins over the carried language whenever v1's detector reads nothing.
    (The neutral-line and clear-typed-Malay guards are already in test_r4a_fix3.py and stay.)
"""
from __future__ import annotations

import pytest

from app.services.chatbot.pv2 import answer
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.state import ClarifyAct, Lookup
from tests.chatbot.pv2.carry_fakes import NOW, ask, entity, out
from tests.chatbot.pv2.test_core_engine_hook import fake_turn  # noqa: F401 - fixture
from tests.chatbot.pv2.test_r4a_fix3 import _numbered, _say, _two_turns, _world, db  # noqa: F401 - db is a fixture
from tests.chatbot.test_engine import seeded, stub_access, stub_parser  # noqa: F401 - fixtures


def _no(language="en"):
    return out(ask(act="answers_bot_question", answer={"option_key": "no", "free_value": None}), language=language)


def _confirmed(db, kind, phrase):
    r = _world(db)
    st, acts = _say(r, kind, phrase)
    assert isinstance(acts[0], ClarifyAct) and st.pending is not None, type(acts[0]).__name__
    return r, st


def test_n1_a_no_to_the_customer_confirm_asks_openly_with_no_options(db):
    """RED: HEAD answers "Sorry, I didn't catch your choice" and asks the same confirm again."""
    r, st = _confirmed(db, "customer", "lim")
    st, acts = apply_turn(st, _no(), resolver=r, now=NOW)
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__
    reply = answer.replay_reply(st, acts)
    assert reply == "Which customer do you mean? Please type the full name.", reply
    assert st.pending is not None and st.pending.options == [] and st.pending.kind != "clarify_confirm"
    assert not st.held.refs.get("customer"), "nothing held from the guess"
    assert not any(isinstance(a, Lookup) for a in acts), "nothing fetched"


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_n1_a_no_in_ms_or_zh_is_not_the_didnt_catch_line(db, lang):
    """RED: HEAD re-asks the confirm after the didn't-catch line (localized)."""
    r, st = _confirmed(db, "customer", "lim")
    confirm_text = st.pending.asked
    st, acts = apply_turn(st, _no(lang), resolver=r, now=NOW)
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__
    assert st.pending is not None and st.pending.options == [] and st.pending.kind != "clarify_confirm"
    assert st.pending.asked != confirm_text and "HANLIM" not in answer.replay_reply(st, acts)


def test_n1_a_no_to_the_brand_confirm_asks_which_brand_openly(db):
    """RED: same reason for the brand confirm."""
    r, st = _confirmed(db, "brand", "itali")
    st, acts = apply_turn(st, _no(), resolver=r, now=NOW)
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__
    reply = answer.replay_reply(st, acts)
    assert reply.startswith("Which brand do you mean?"), reply
    assert st.pending.options == [] and not st.held.refs.get("brand")


def test_n1_a_miss_after_the_open_question_goes_to_the_existing_miss_path(db):
    """RED: the open question exists (HEAD never gets here); an unknown name then is a miss (hand-over offer or a
    still-need line), never the guess confirm again."""
    r, st = _confirmed(db, "customer", "lim")
    st, acts = apply_turn(st, _no(), resolver=r, now=NOW)
    assert st.pending is not None and st.pending.options == [], "the open question is asked"
    parsed = out(ask(act="answers_bot_question", answer={"option_key": None, "free_value": "zzqx unknown trading"},
                     entities=[entity("customer", phrase="zzqx unknown trading")]))
    st, acts = apply_turn(st, parsed, resolver=r, now=NOW)
    assert not any(isinstance(a, Lookup) for a in acts)
    assert st.pending is None or st.pending.kind != "clarify_confirm", st.pending


def test_n1_guard_yes_still_holds_the_confirmed_customer(db):
    """GUARD."""
    r, st = _confirmed(db, "customer", "lim")
    st, acts = apply_turn(st, out(ask(act="answers_bot_question", answer={"option_key": "yes", "free_value": None})),
                          resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup) and [x.label for x in st.held.refs["customer"]] == ["HANLIM TRADING SDN BHD"]


# ------------------------------------------------------------------ L4


def test_l4_a_malay_parse_wins_over_the_carried_language_when_v1_reads_no_language(session_factory, monkeypatch, fake_turn):
    """RED (en carried, then "minta gambar SRTWC286", no v1 marker word, parsed ms): HEAD keeps the carried en."""
    saved = _two_turns(session_factory, monkeypatch, fake_turn, first=("orders list pls", "en"),
                       second=("minta gambar SRTWC286", "ms"))
    assert saved == ["en", "ms"], saved


def test_l4_a_chinese_parse_wins_over_the_carried_language_when_v1_reads_no_language(session_factory, monkeypatch, fake_turn):
    """RED: a zh parse of a line v1 reads as nothing (all Latin, e.g. a pinyin line) beats the carried en."""
    saved = _two_turns(session_factory, monkeypatch, fake_turn, first=("orders list pls", "en"),
                       second=("SRTWC286 daohuo shijian", "zh"))
    assert saved == ["en", "zh"], saved
