import json

from scripts.chatbot_eval.run import main
from tests.chatbot_eval.fakes import case_dict, write_case


def test_regrade_keeps_source_run_metadata(tmp_path):
    d, old, new = tmp_path / "data", tmp_path / "old", tmp_path / "new"
    write_case(d, case_dict("k0", split="hidden"))
    old.mkdir()
    row = {
        "case_id": "k0", "split": "hidden", "topic_code": "stock", "language": "en", "variant_kind": None,
        "label": "correct", "reasons": [], "latency_ms": 5, "tokens": {"prompt": 0, "cached": 0, "output": 0},
        "reply_text": "Stock of TESTCODE-1 is 12 units", "branch_kind": "answer", "delegated": False,
        "tool_errors": [], "lane_notes": [], "previous_bot_reply": None,
    }
    (old / "results.jsonl").write_text(json.dumps(row) + "\n")
    (old / "report.json").write_text(json.dumps({
        "prompt_version": "abc", "engine": "http", "run_id": "r1",
        "started_at": "2026-10-10T00:00:00Z", "wall_s": 123.0, "variants": "one",
    }))
    rc = main(["--data-dir", str(d), "--engine", "tests.chatbot_eval.fakes:NeverEngine",
               "--regrade", str(old), "--split", "hidden", "--out", str(new)])
    assert rc == 0
    rep = json.loads((new / "report.json").read_text())
    assert rep["prompt_version"] == "abc"
    assert rep["engine"] == "http"
    assert rep["run_id"] == "r1"
    assert rep["started_at"] == "2026-10-10T00:00:00Z"
    assert rep["wall_s"] == 123.0
    assert rep["variants"] == "one"
    assert rep["split"] == "hidden"
    assert rep["regraded_from"] == str(old)
    assert rep["regraded_at"]


def test_regrade_keeps_prompt_provenance(tmp_path, monkeypatch):
    monkeypatch.delenv("EVAL_TRACE_DB_URL", raising=False)
    d, old, new = tmp_path / "data", tmp_path / "old", tmp_path / "new"
    write_case(d, case_dict("k0"))
    old.mkdir()
    row = {
        "case_id": "k0", "split": "dev", "topic_code": "stock", "language": "en", "variant_kind": None,
        "label": "correct", "reasons": [], "latency_ms": 5, "tokens": {"prompt": 0, "cached": 0, "output": 0},
        "reply_text": "TESTCODE-1 ok", "branch_kind": "answer", "delegated": False,
        "tool_errors": [], "lane_notes": [], "previous_bot_reply": None,
    }
    prompt = {"id": "u-1", "version": 50, "template_md5": "abc", "source": "pin"}
    (old / "results.jsonl").write_text(json.dumps(row) + "\n")
    (old / "report.json").write_text(json.dumps({"prompt": prompt, "prompt_versions_seen": {"50": 2}, "prompt_mismatch": None}))
    rc = main(["--data-dir", str(d), "--engine", "tests.chatbot_eval.fakes:NeverEngine", "--regrade", str(old), "--out", str(new)])
    assert rc == 0
    rep = json.loads((new / "report.json").read_text())
    assert rep["prompt"] == prompt
    assert rep["prompt_versions_seen"] == {"50": 2}
    assert rep["prompt_mismatch"] is None
