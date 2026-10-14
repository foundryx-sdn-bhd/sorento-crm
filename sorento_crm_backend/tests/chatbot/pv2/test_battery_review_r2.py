"""PV2-EVAL round 7: DB-safety check precedes any DB read; cut facts are flagged. Fakes only."""
from __future__ import annotations

import types

import pytest

from scripts.pv2_battery import guard, judge, runner


def test_guard_install_runs_before_the_embedding_key_fetch(monkeypatch):
    calls: list[str] = []
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(runner, "bootstrap_env", lambda *a, **k: calls.append("bootstrap"))
    monkeypatch.setattr(runner, "ensure_worktree_mcp", lambda *a, **k: calls.append("mcp") or "/x")
    monkeypatch.setattr(guard, "harden_env", lambda *a, **k: calls.append("harden"))
    monkeypatch.setattr(guard, "check_env", lambda *a, **k: calls.append("check_env") or [])
    monkeypatch.setattr(guard, "install", lambda *a, **k: calls.append("install"))

    def fetch():
        calls.append("fetch")
        return None  # ensure_embedding_key then raises GuardError, which stops the run early

    monkeypatch.setattr(runner, "fetch_dev_openai_key", fetch)
    args = types.SimpleNamespace(no_embedding_key=False, battery_dir=None, ids=None, domain=None)
    with pytest.raises(Exception):
        runner.run(args)
    assert "fetch" in calls, calls
    safety = [i for i, c in enumerate(calls) if c in ("install", "check_env")]
    assert safety and min(safety) < calls.index("fetch"), calls


def _huge_trace():
    return [{"kind": "tool", "name": "stock_check", "args": {"c": "X"}, "envelope": "y" * 300000},
            {"kind": "tool", "name": "incoming", "args": {}, "envelope": "z" * 300000}]


def test_cut_facts_end_with_visible_marker():
    d = runner.facts_digest(_huge_trace())
    assert len(d) <= runner.FACTS_LIMIT + 40
    assert "(cut)" in d[-40:]


def test_uncut_facts_have_no_marker():
    d = runner.facts_digest([{"kind": "tool", "name": "t", "args": {}, "envelope": "small"}])
    assert "(cut)" not in d


def test_rubric_says_cut_facts_are_partial():
    low = judge.RUBRIC.lower()
    assert "cut" in low or "partial" in low
    assert "(cut)" in judge.RUBRIC or "cut" in low
