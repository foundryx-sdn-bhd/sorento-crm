"""EVAL-BENCH: `_replay_one` refuses a session factory that is not the one `_rolled_back` yields.

A raw call with a plain factory committed 585 replayed turns into the replay DB. Synthetic data only.
"""
from __future__ import annotations

import re

import pytest

from scripts.chatbot_eval import replay as replay_mod
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import seeded, stub_access  # noqa: F401
from tests.chatbot_eval.test_replay import ChatbotTurn, recorded, _record, _turn_count  # noqa: F401


def test_red_a_raw_factory_is_refused_before_the_db_is_touched(session_factory, recorded):
    before = _turn_count(session_factory)
    with pytest.raises((ValueError, RuntimeError), match=re.compile(r"_rolled_back|replay_rows")):
        replay_mod._replay_one(recorded, session_factory)
    assert _turn_count(session_factory) == before


def test_guard_replay_rows_still_replays_and_rolls_back(session_factory, recorded):
    before = _turn_count(session_factory)
    out = replay_mod.replay_rows([recorded], session_factory=session_factory)
    assert len(out) == 1 and out[0]["stages"] and out[0]["skipped"] is None, out
    assert _turn_count(session_factory) == before


def test_guard_replay_one_works_inside_rolled_back(session_factory, recorded):
    before = _turn_count(session_factory)
    with replay_mod._rolled_back(session_factory) as factory:
        out = replay_mod._replay_one(recorded, factory)
    assert out["stages"] and out["skipped"] is None, out
    assert _turn_count(session_factory) == before
