"""PV2-EVAL round 5: judge facts digest carries tool results; embedding key guard."""
from __future__ import annotations

import json

import pytest

from scripts.pv2_battery import guard, runner


def _trace(envelope="WHSU7382874 Incoming Quantity 40"):
    return [
        {"stage": "received", "facts": {"text": "stock srtwc286"}},
        {"stage": "looked_up", "facts": {"resolved": "LOOKEDUP_MARK"}},
        {"kind": "pv2_turn", "parser_out": {"asks": [{"act": "PARSER_OUT_MARK"}]}},
        {"kind": "tool", "name": "stock_check", "args": {"code": "SRTWC286"}, "envelope": envelope, "ms": 12},
        {"kind": "prompt_text", "text": "x"},
    ]


def test_limit_is_int_at_least_12000():
    assert isinstance(runner.FACTS_LIMIT, int) and runner.FACTS_LIMIT >= 12000


def test_digest_has_tool_name_args_and_envelope():
    d = runner.facts_digest(_trace())
    assert isinstance(d, str)
    for needle in ("WHSU7382874", "Incoming Quantity", "stock_check", "SRTWC286"):
        assert needle in d, needle


def test_digest_has_parser_out_and_looked_up():
    d = runner.facts_digest(_trace())
    assert "PARSER_OUT_MARK" in d and "LOOKEDUP_MARK" in d


def test_digest_handles_dict_envelope():
    d = runner.facts_digest(_trace(envelope={"rows": [{"whse": "WHSU7382874", "label": "Incoming Quantity"}]}))
    assert "WHSU7382874" in d and "Incoming Quantity" in d


def test_cap_respected_and_tool_kept_first():
    huge = "x" * 200000
    t = _trace(envelope="TOOLHEAD " + huge)
    t[1]["facts"] = {"resolved": huge}
    d = runner.facts_digest(t)
    assert len(d) <= runner.FACTS_LIMIT
    assert "TOOLHEAD" in d and "stock_check" in d


@pytest.mark.parametrize("bad", [None, {}, [], "str", 5, [None, 3, "x", {"kind": "tool"}], {"kind": "tool", "envelope": None}])
def test_digest_never_raises(bad):
    assert isinstance(runner.facts_digest(bad), str)


# ---- embedding key ----

def test_key_present_does_not_fetch():
    env = {"OPENAI_API_KEY": "sk-test-already"}
    def boom():
        raise AssertionError("fetch called")
    assert runner.ensure_embedding_key(env, boom) == "present"
    assert env["OPENAI_API_KEY"] == "sk-test-already"


@pytest.mark.parametrize("env", [{}, {"OPENAI_API_KEY": ""}])
def test_key_set_from_dev_key(env):
    assert runner.ensure_embedding_key(env, lambda: "sk-test-xxxxbG4A") == "set"
    assert env["OPENAI_API_KEY"] == "sk-test-xxxxbG4A"


@pytest.mark.parametrize("fetched", ["sk-test-prodXXXX", "", None])
def test_key_rejected_leaves_env_untouched(fetched):
    env = {"OPENAI_API_KEY": ""}
    with pytest.raises(guard.GuardError) as ei:
        runner.ensure_embedding_key(env, lambda: fetched)
    assert env["OPENAI_API_KEY"] == ""
    if fetched:
        assert fetched not in str(ei.value)


def test_key_skipped_opt_out():
    env = {"OPENAI_API_KEY": ""}
    assert runner.ensure_embedding_key(env, None) == "skipped"
    assert env["OPENAI_API_KEY"] == ""


def test_return_value_is_never_the_key():
    env = {}
    out = runner.ensure_embedding_key(env, lambda: "sk-test-xxxxbG4A")
    assert out in ("present", "set", "skipped") and "sk-test" not in out
