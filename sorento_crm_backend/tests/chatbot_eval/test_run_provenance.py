"""EVAL-BENCH D1 (AC1.4, AC1.5): the E2E runner reports the SERVER's sha and the provenance seen.

/health is faked at httpx; the engine is a local fake, so nothing reaches a network or an LLM.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from scripts.chatbot_eval.run import main
from tests.chatbot_eval.fakes import RecordingEngine, case_dict, make_outcome, write_case

REPO = Path(__file__).resolve().parents[3]
SERVER_SHA = "5e7ab1c"


def _digest(**over):
    p = {"domain": "d", "intent": "i", "entities": 0, "prompt_sha256": "p" * 64, "prompt_version": 3,
         "model": "gpt-test", "provider": "openai", "reasoning_effort": None, "code_sha": SERVER_SHA}
    p.update(over)
    return {"parser": p, "resolved": [], "tools": [], "branch_kind": None, "reply_text": "ok"}


class ProvEngine(RecordingEngine):
    calls_seen: list = []
    digests: list = []  # one per turn, cycled

    def turn(self, case, text, state, run_id):
        type(self).calls_seen.append(text)
        d = type(self).digests[(len(type(self).calls_seen) - 1) % len(type(self).digests)]
        return make_outcome("TESTCODE-1 ok", trace_digest=d, state={})


@pytest.fixture()
def health(monkeypatch):
    state = {"body": {"status": "healthy", "git_sha": SERVER_SHA}}

    def fake_send(self, request, **kw):
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json=state["body"], request=request)
        raise AssertionError(f"unexpected HTTP call {request.url}")

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    # The /health check applies to `--engine http` only, so the run is started as http and
    # the engine object is swapped for the local fake (no network turn calls).
    import scripts.chatbot_eval.run as run_mod

    monkeypatch.setattr(run_mod, "load_engine", lambda spec, **kw: ProvEngine())
    monkeypatch.delenv("EVAL_APP_SHA", raising=False)
    monkeypatch.delenv("EVAL_TRACE_DB_URL", raising=False)
    ProvEngine.calls_seen = []
    ProvEngine.digests = [_digest()]
    return state


def _run(tmp_path, n_cases=2):
    d, out = tmp_path / "data", tmp_path / "o"
    for i in range(n_cases):
        write_case(d, case_dict(f"k{i}"))
    rc = main(["--data-dir", str(d), "--engine", "http", "--base-url", "http://fake.invalid", "--out", str(out)])
    return rc, out


def test_ac1_4_git_sha_is_server_sha_runner_sha_is_checkout(tmp_path, health):
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    rc, out = _run(tmp_path)
    rep = json.loads((out / "report.json").read_text())
    assert rc == 0
    assert rep["git_sha"] == SERVER_SHA
    assert rep["runner_sha"] == head


def test_ac1_4_provenance_lists_distinct_values_not_mixed(tmp_path, health):
    rc, out = _run(tmp_path)
    rep = json.loads((out / "report.json").read_text())
    prov = rep["provenance"]
    assert prov["code_sha"] == [SERVER_SHA]
    assert prov["prompt_sha256"] == ["p" * 64]
    assert prov["prompt_version"] == [3]
    assert prov["model"] == ["gpt-test"]
    assert prov["reasoning_effort"] == [None]
    assert rep["provenance_mixed"] is False


def test_ac1_4_provenance_mixed_when_a_field_varies(tmp_path, health):
    ProvEngine.digests = [_digest(model="gpt-a"), _digest(model="gpt-b")]
    rc, out = _run(tmp_path)
    rep = json.loads((out / "report.json").read_text())
    assert sorted(rep["provenance"]["model"]) == ["gpt-a", "gpt-b"]
    assert rep["provenance"]["code_sha"] == [SERVER_SHA]
    assert rep["provenance_mixed"] is True


def test_ac1_5_refuses_to_start_without_health_git_sha(tmp_path, health):
    health["body"] = {"status": "healthy"}
    try:
        rc, _ = _run(tmp_path)
    except SystemExit as exc:
        rc = exc.code
    except Exception:  # noqa: BLE001 - a raised refusal is also a refusal
        rc = "raised"
    assert rc not in (0, None), "runner started although /health reported no git_sha"
    assert ProvEngine.calls_seen == [], "a turn was sent before the provenance check"


@pytest.mark.parametrize("sha", ["unknown", "", "  "])
def test_health_git_sha_unknown_or_empty_is_refused_like_missing(tmp_path, health, sha):
    health["body"] = {"status": "healthy", "git_sha": sha}
    try:
        rc, _ = _run(tmp_path)
    except SystemExit as exc:
        rc = exc.code
    except Exception:  # noqa: BLE001
        rc = "raised"
    assert rc not in (0, None), f"runner started on git_sha={sha!r}"
    assert ProvEngine.calls_seen == []


def test_provenance_missing_flag_when_no_digest_carries_provenance(tmp_path, health):
    ProvEngine.digests = [{}]
    rc, out = _run(tmp_path)
    rep = json.loads((out / "report.json").read_text())
    assert rep.get("provenance_missing") is True


def test_provenance_missing_flag_is_false_when_digests_carry_it(tmp_path, health):
    rc, out = _run(tmp_path)
    rep = json.loads((out / "report.json").read_text())
    assert rep.get("provenance_missing") is False
