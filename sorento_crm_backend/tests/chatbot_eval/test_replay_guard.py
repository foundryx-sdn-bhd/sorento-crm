"""EVAL-BENCH: `_replay_one` refuses a session factory that is not the one `_rolled_back` yields.

A raw call with a plain factory committed 585 replayed turns into the replay DB. Synthetic data only.
"""
from __future__ import annotations

import re

import pytest

from app.services.chatbot import engine as engine_mod
from scripts.chatbot_eval import replay as replay_mod
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import seeded, stub_access  # noqa: F401
from tests.chatbot_eval.test_replay_live import LAST4, VARIANT, items_for, live_env  # noqa: F401
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


def test_red_live_one_refuses_a_raw_factory_before_the_db_is_touched(session_factory, recorded, live_env):
    before = _turn_count(session_factory)
    total = replay_mod._new_usage()
    total.update(max_calls=50, max_usd=10.0, stop=None, require_key_last4=LAST4)
    item = items_for(recorded)[0]
    with pytest.raises((ValueError, RuntimeError), match=re.compile(r"_rolled_back|replay_rows|live_rows")):
        replay_mod._live_one(item, recorded, session_factory, total)
    assert _turn_count(session_factory) == before
    assert live_env["envelopes"] == []


def test_red_the_rolled_back_mark_is_cleared_when_the_block_exits(session_factory, recorded):
    with replay_mod._rolled_back(session_factory) as fac:
        assert getattr(fac, "_rolled_back", False) is True
    assert getattr(fac, "_rolled_back", False) is False
    with pytest.raises((ValueError, RuntimeError), match=re.compile(r"_rolled_back|replay_rows")):
        replay_mod._replay_one(recorded, fac)


def test_red_replay_one_runs_the_engine_with_offload_off(session_factory, recorded, monkeypatch):
    seen = []
    real = engine_mod.run_turn

    def spy(envelope, **kw):
        seen.append(kw)
        return real(envelope, **kw)

    monkeypatch.setattr(engine_mod, "run_turn", spy)
    replay_mod.replay_rows([recorded], session_factory=session_factory)
    assert seen and seen[0].get("offload") is False, seen
