import json

import httpx
import pytest

from scripts.chatbot_eval.budget import cost_usd
from scripts.chatbot_eval.judge import AnthropicJudge
from scripts.chatbot_eval.run import main
from tests.chatbot_eval import fakes
from tests.chatbot_eval.fakes import case_dict, make_outcome, write_case
from tests.chatbot_eval.test_runner import mk_case

JUDGE = "tests.chatbot_eval.fakes:FakeJudge"


def _patch(monkeypatch, seen):
    def send(self, request, **kw):
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        body = {
            "content": [{"type": "text", "text": '{"passed": true, "note": "ok"}'}],
            "usage": {"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 10},
        }
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx.Client, "send", send)


def test_anthropic_judge_default_model_key_and_usage(monkeypatch):
    monkeypatch.delenv("EVAL_JUDGE_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-1")
    seen = {}
    _patch(monkeypatch, seen)
    case = mk_case()
    case.expect.judge_note = "be polite"
    res = AnthropicJudge().score(case, make_outcome("hello"))
    assert seen["body"]["model"] == "claude-sonnet-5-5"
    assert seen["headers"]["x-api-key"] == "test-key-1"
    assert res.passed is True
    assert res.tokens == {"prompt": 150, "cached": 50, "output": 10}


def test_anthropic_judge_model_env_override(monkeypatch):
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "claude-test-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    seen = {}
    _patch(monkeypatch, seen)
    case = mk_case()
    case.expect.judge_note = "n"
    AnthropicJudge().score(case, make_outcome("hello"))
    assert seen["body"]["model"] == "claude-test-model"


def test_sonnet_prices():
    assert cost_usd({"prompt": 1_000_000, "cached": 0, "output": 0}, "claude-sonnet-5-5") == pytest.approx(2.00)
    assert cost_usd({"prompt": 1_000_000, "cached": 1_000_000, "output": 0}, "claude-sonnet-5-5") == pytest.approx(0.10)
    assert cost_usd({"prompt": 0, "cached": 0, "output": 1_000_000}, "claude-sonnet-5-5") == pytest.approx(10.00)


def _data(tmp_path, n_cases, n_cal):
    fakes.JUDGE_CALLS.clear()
    fakes.JUDGE_CFG.update(agree=True, tokens={"prompt": 300_000, "cached": 0, "output": 0})  # ~$0.60 on sonnet
    d = tmp_path / "data"
    for i in range(n_cases):
        c = case_dict(f"k{i}")
        c["expect"]["judge_note"] = "be polite"
        write_case(d, c)
    for i in range(n_cal):
        label = "y" if i % 2 == 0 else "n"
        write_case(d / "calibration", {"case": case_dict(f"cal{i}{label}"), "reply_text": "TESTCODE-1 ok", "human_passed": label == "y"}, name=f"cal{i}.json")
    return d


def _real_calls():
    return [c for c in fakes.JUDGE_CALLS if not c.startswith("cal")]


def _run(tmp_path, d, monkeypatch, *extra):
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "claude-sonnet-5-5")
    out = tmp_path / "o"
    rc = main(["--data-dir", str(d), "--engine", "tests.chatbot_eval.fakes:FakeEngine", "--judge", JUDGE, "--out", str(out), *extra])
    return rc, json.loads((out / "report.json").read_text())


def test_judge_budget_cap_stops_judge_run_continues(tmp_path, monkeypatch):
    d = _data(tmp_path, 5, 1)
    rc, rep = _run(tmp_path, d, monkeypatch, "--judge-budget-usd", "1.0", "--budget-usd", "0.01")
    assert rc == 0
    assert 1 <= len(_real_calls()) < 5
    assert rep["judge_stopped"] == "budget"
    assert rep["judge_usd"] > 0
    assert rep["unverified"] >= 1
    assert rep["stopped_reason"] is None


def test_judge_default_budget_is_two_dollars(tmp_path, monkeypatch):
    d = _data(tmp_path, 6, 1)
    rc, rep = _run(tmp_path, d, monkeypatch)
    assert rc == 0
    # 0.60 calibration, then calls start at 0.60, 1.20, 1.80 (< 2.0) and stop once past it
    assert len(_real_calls()) == 3
    assert rep["judge_stopped"] == "budget"


def test_no_calibration_items_judge_never_called(tmp_path, monkeypatch):
    d = _data(tmp_path, 3, 0)
    rc, rep = _run(tmp_path, d, monkeypatch)
    assert fakes.JUDGE_CALLS == []
    assert rep["judge_status"] == "uncalibrated"
