"""EVAL-BENCH: the in-process replay installs the company scope listeners, and refuses without them.

`replay.py run|live` drives `engine.run_turn` in this process. Only app/main.py and worker.py
register the SELECT filter, so a bench run read the candidate / resolver / breakdown tables with
no company or brand scope while the MCP fetch (HTTP, scoped) was scoped. Synthetic data only.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.models.base import set_brand_scope, set_company_scope
from app.models.product import Brand, Product
from app.services import company_scope as cs
from app.services.company_scope import DEFAULT_COMPANY_ID
from scripts.chatbot_eval import replay as replay_mod
from tests._mc_lookup_seed import MOCHA_ID, product, seed_mocha
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import seeded, stub_access  # noqa: F401
from tests.chatbot_eval.test_replay import recorded  # noqa: F401
from tests.chatbot_eval.test_replay_failfast import _cli, _preflight
from tests.chatbot_eval.test_replay_live import live_env  # noqa: F401


@pytest.fixture
def fresh_flag(monkeypatch):
    """A process where nothing has registered yet; a spy stands in for the real registration."""
    calls: list[int] = []

    def spy() -> None:
        calls.append(1)
        monkeypatch.setattr(cs, "_INSTALLED", True)

    monkeypatch.setattr(cs, "_INSTALLED", False)
    monkeypatch.setattr(cs, "register_company_scope_listeners", spy)
    return calls


@pytest.mark.parametrize("cmd", ["run", "live"])
def test_red_the_command_registers_the_scope_listeners(cmd, fresh_flag, tmp_path, session_factory, recorded, live_env, monkeypatch, capsys):  # noqa: F811
    _preflight(monkeypatch, {"a1"}, {"a1"})
    _cli(cmd, tmp_path, session_factory, recorded, monkeypatch)
    assert fresh_flag, "register_company_scope_listeners was never called"
    assert cs._INSTALLED is True


@pytest.mark.parametrize("cmd", ["run", "live"])
def test_red_the_preflight_refuses_when_the_listeners_are_not_installed(cmd, tmp_path, session_factory, recorded, live_env, monkeypatch, capsys):  # noqa: F811
    _preflight(monkeypatch, {"a1"}, {"a1"})
    monkeypatch.setattr(cs, "_INSTALLED", False)
    monkeypatch.setattr(cs, "register_company_scope_listeners", lambda: None)
    rc, out = _cli(cmd, tmp_path, session_factory, recorded, monkeypatch)
    text = capsys.readouterr()
    shown = text.out + text.err
    assert rc == 2, shown
    assert "scope" in shown, shown
    assert (not out.exists()) or out.read_text() == ""


def test_red_a_fresh_process_that_ran_the_replay_setup_filters_a_plain_product_query():
    """The pytest process installs the listener for the whole run (tests/conftest.py), so the check runs
    in a fresh interpreter that never loads it: only the replay's own setup can have registered it."""
    import json
    import os
    import subprocess
    import sys

    backend = os.getcwd()
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([backend, os.environ.get("PYTHONPATH", "")]))
    probe = os.path.join(backend, "tests", "chatbot_eval", "_scope_probe.py")
    done = subprocess.run([sys.executable, probe], cwd=backend, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-800:]
    result = json.loads(done.stdout.strip().splitlines()[-1])
    assert result["scoped"] is True, result


def _unset_the_test_default_scope(monkeypatch):
    """tests/conftest.py gives every test session the Sorento scope; take that away for the test."""
    import sys

    from sqlalchemy import event
    from sqlalchemy.orm import Session

    fn = next(m._default_company_scope_for_tests for m in list(sys.modules.values())
              if getattr(m, "_default_company_scope_for_tests", None))
    event.remove(Session, "after_begin", fn)
    return lambda: event.listen(Session, "after_begin", fn)


def test_red_the_grounding_tier_reads_under_the_rows_contact_scope(session_factory, recorded, monkeypatch):  # noqa: F811
    from app.services.chatbot import engine
    from app.services.chatbot.contracts import Envelope
    from app.services.chatbot.head import grounding

    whole = replay_mod.replay_rows([recorded], session_factory=session_factory)[0]
    contact = engine._contact_respond_id(Envelope(**recorded["envelope"]))
    expected = engine._contact_company_scope(session_factory, contact)
    expected_brand = engine._contact_brand_scope(session_factory, contact)
    seen: list[tuple[Any, Any]] = []
    real = grounding.load_vocabulary

    def spy(db):
        seen.append((db.info.get("company_scope"), db.info.get("brand_scope")))
        return real(db)

    monkeypatch.setattr(grounding, "load_vocabulary", spy)
    restore = _unset_the_test_default_scope(monkeypatch)
    try:
        tier = replay_mod.replay_rows([recorded], session_factory=session_factory, stage="grounding")[0]
    finally:
        restore()
    assert seen == [(expected, expected_brand)], seen
    assert tier["misses"] == [], tier
    assert tier["stages"]["grounded"] == whole["stages"]["grounded"], (tier, whole)


def test_red_replay_rows_and_live_rows_refuse_when_the_listeners_are_not_installed(session_factory, recorded, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cs, "_INSTALLED", False)
    with pytest.raises(RuntimeError, match="scope"):
        replay_mod.replay_rows([recorded], session_factory=session_factory)
    with pytest.raises(RuntimeError, match="scope"):
        replay_mod.live_rows([], [recorded], session_factory=session_factory, max_calls=1, max_usd=1,
                             require_key_last4="0000")


@pytest.mark.parametrize("cmd", ["run", "live"])
def test_red_the_preflight_refuses_when_enforcement_is_switched_off(cmd, tmp_path, session_factory, recorded, live_env, monkeypatch, capsys):  # noqa: F811
    _preflight(monkeypatch, {"a1"}, {"a1"})
    monkeypatch.setattr(cs, "_ENFORCE", False)
    rc, out = _cli(cmd, tmp_path, session_factory, recorded, monkeypatch)
    text = capsys.readouterr()
    assert rc == 2 and "COMPANY_SCOPE_ENFORCE" in text.out + text.err, text
