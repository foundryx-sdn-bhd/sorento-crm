import json
import re
import subprocess
from pathlib import Path

from scripts.chatbot_eval.run import main
from tests.chatbot_eval.fakes import case_dict, write_case

FAKE = "tests.chatbot_eval.fakes:FakeEngine"
REPO = Path(__file__).resolve().parents[3]


def _run(tmp_path, monkeypatch, *extra, env_sha=None):
    if env_sha:
        monkeypatch.setenv("EVAL_APP_SHA", env_sha)
    else:
        monkeypatch.delenv("EVAL_APP_SHA", raising=False)
    monkeypatch.delenv("EVAL_TRACE_DB_URL", raising=False)
    d, out = tmp_path / "data", tmp_path / "o"
    write_case(d, case_dict("k0"))
    rc = main(["--data-dir", str(d), "--engine", FAKE, "--out", str(out), *extra])
    return rc, json.loads((out / "report.json").read_text())


def test_report_records_head_sha_and_dirty_flag(tmp_path, monkeypatch):
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    rc, rep = _run(tmp_path, monkeypatch)
    assert rc == 0
    assert re.fullmatch(r"[0-9a-f]{40}", rep["git_sha"]) and rep["git_sha"] == head
    assert isinstance(rep["git_dirty"], bool)


def test_env_overrides_git_sha(tmp_path, monkeypatch):
    rc, rep = _run(tmp_path, monkeypatch, env_sha="abc123")
    assert rc == 0 and rep["git_sha"] == "abc123"


def test_no_git_gives_none_and_run_continues(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "check_output", boom)
    rc, rep = _run(tmp_path, monkeypatch)
    assert rc == 0
    assert rep["git_sha"] is None and rep["git_dirty"] is None


def test_regrade_keeps_source_git_values(tmp_path, monkeypatch):
    d, old, new = tmp_path / "data", tmp_path / "old", tmp_path / "new"
    write_case(d, case_dict("k0"))
    old.mkdir()
    row = {
        "case_id": "k0", "split": "dev", "topic_code": "stock", "language": "en", "variant_kind": None,
        "label": "correct", "reasons": [], "latency_ms": 5, "tokens": {"prompt": 0, "cached": 0, "output": 0},
        "reply_text": "TESTCODE-1 ok", "branch_kind": "answer", "delegated": False, "tool_errors": [],
        "lane_notes": [], "previous_bot_reply": None,
    }
    (old / "results.jsonl").write_text(json.dumps(row) + "\n")
    (old / "report.json").write_text(json.dumps({"git_sha": "f" * 40, "git_dirty": True}))
    monkeypatch.delenv("EVAL_APP_SHA", raising=False)
    assert main(["--data-dir", str(d), "--engine", "tests.chatbot_eval.fakes:NeverEngine", "--regrade", str(old), "--out", str(new)]) == 0
    rep = json.loads((new / "report.json").read_text())
    assert rep["git_sha"] == "f" * 40 and rep["git_dirty"] is True
