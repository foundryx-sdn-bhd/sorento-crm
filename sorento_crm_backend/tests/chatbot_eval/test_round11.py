"""Assumed seam (the coder adds it):
scripts/chatbot_eval/prompts.py
  query_prompt(dsn, value: str | None) -> dict | None   # the only DB touch; value None = the production label
  resolve_prompt(dsn, value) -> {"id", "version", "template_md5", "source": "pin"|"label"}   # raises LookupError
resolve_prompt must call prompts.query_prompt through the module (so monkeypatching it works).
"""

import json

import pytest

from scripts.chatbot_eval import prompts
from scripts.chatbot_eval.prompts import resolve_prompt
from scripts.chatbot_eval.run import main
from tests.chatbot_eval.fakes import KwEngine, case_dict, write_case

DSN = "postgresql://u:p@127.0.0.1:1/sorento_eval_x"
UID = "11111111-2222-3333-4444-555555555555"
ROW = {"id": UID, "version": 50, "template_md5": "abc123"}


def fake_query(calls):
    def q(dsn, value):
        calls.append(value)
        if value in (None, "50", UID):
            return dict(ROW)
        return None

    return q


def test_resolve_by_version_and_uuid_and_label(monkeypatch):
    calls = []
    monkeypatch.setattr(prompts, "query_prompt", fake_query(calls))
    assert resolve_prompt(DSN, "50") == {**ROW, "source": "pin"}
    assert resolve_prompt(DSN, UID)["id"] == UID
    assert resolve_prompt(DSN, None) == {**ROW, "source": "label"}


def test_resolve_unmatched_raises(monkeypatch):
    monkeypatch.setattr(prompts, "query_prompt", fake_query([]))
    with pytest.raises(LookupError):
        resolve_prompt(DSN, "999")


def _data(tmp_path, n=2):
    d = tmp_path / "data"
    for i in range(n):
        write_case(d, case_dict(f"k{i}"))
    return d


def _go(tmp_path, monkeypatch, *extra, engine="tests.chatbot_eval.fakes:KwEngine", db=True, n=2):
    if db:
        monkeypatch.setenv("EVAL_TRACE_DB_URL", DSN)
    else:
        monkeypatch.delenv("EVAL_TRACE_DB_URL", raising=False)
    KwEngine.SEEN.clear()
    out = tmp_path / "o"
    rc = main(["--data-dir", str(_data(tmp_path, n)), "--engine", engine, "--out", str(out), *extra])
    rep = json.loads((out / "report.json").read_text()) if (out / "report.json").exists() else None
    return rc, rep


@pytest.mark.parametrize("value", ["50", UID])
def test_pin_resolves_to_uuid_for_engine_and_report(tmp_path, monkeypatch, value):
    monkeypatch.setattr(prompts, "query_prompt", fake_query([]))
    rc, rep = _go(tmp_path, monkeypatch, "--prompt-version", value)
    assert rc == 0
    assert any(kw.get("prompt_version") == UID for kw in KwEngine.SEEN)
    assert rep["prompt"] == {**ROW, "source": "pin"}


def test_unmatched_pin_exit_2_names_value_engine_never_built(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(prompts, "query_prompt", fake_query([]))
    rc, _ = _go(tmp_path, monkeypatch, "--prompt-version", "999", engine="tests.chatbot_eval.fakes:NeverEngine")
    assert rc == 2
    assert "999" in capsys.readouterr().err


def test_pin_without_db_url_exit_2(tmp_path, monkeypatch, capsys):
    rc, _ = _go(tmp_path, monkeypatch, "--prompt-version", "50", engine="tests.chatbot_eval.fakes:NeverEngine", db=False)
    assert rc == 2
    assert capsys.readouterr().err.strip()


def test_no_pin_with_db_records_label(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(prompts, "query_prompt", fake_query(calls))
    rc, rep = _go(tmp_path, monkeypatch)
    assert rc == 0 and None in calls
    assert rep["prompt"]["source"] == "label" and rep["prompt"]["id"] == UID


def test_no_db_no_pin_is_unknown_and_run_continues(tmp_path, monkeypatch):
    rc, rep = _go(tmp_path, monkeypatch, db=False)
    assert rc == 0
    assert rep["prompt"]["source"] == "unknown"


def test_prompt_versions_seen_counts_final_turn_digests(tmp_path, monkeypatch):
    monkeypatch.setattr(prompts, "query_prompt", fake_query([]))
    rc, rep = _go(tmp_path, monkeypatch, "--prompt-version", "50", engine="tests.chatbot_eval.fakes:DigestV50Engine")
    assert rc == 0
    assert rep["prompt_versions_seen"] == {"50": 2}
    assert not rep.get("prompt_mismatch")


def test_seen_version_differs_from_resolved_is_mismatch_exit_5(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(prompts, "query_prompt", fake_query([]))
    rc, rep = _go(tmp_path, monkeypatch, "--prompt-version", "50", engine="tests.chatbot_eval.fakes:DigestV51Engine")
    assert rc == 5
    assert rep is not None and rep["prompt_mismatch"] is True
    assert "mismatch" in capsys.readouterr().err.lower()
