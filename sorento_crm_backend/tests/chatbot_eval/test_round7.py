import json

from scripts.chatbot_eval.engine import HttpTurnEngine
from scripts.chatbot_eval.report import GRADER_VERSION, build_report
from scripts.chatbot_eval.run import main
from scripts.chatbot_eval.runner import RunResult
from scripts.chatbot_eval.trace import parse_trace
from tests.chatbot_eval.fakes import case_dict, write_case
from tests.chatbot_eval.test_runner import mk_case
from tests.chatbot_eval.test_trace import BODY, FakeReader, _turn

# parse_trace returns (tokens, tool_errors, lane_notes)


def looked_up(*envs):
    return [{"stage": "looked_up", "status": "ok", "raw": {"envelopes": list(envs)}}]


def test_str_reply_to_becomes_id_text():
    case = mk_case(text="final q")
    case.message = {"text": "final q", "reply_to": "quoted text"}
    env = HttpTurnEngine("http://fake.invalid", "k", 5).build_envelope(case, "final q", None, "r")
    assert env["message"]["message"]["replyTo"] == {"id": "", "text": "quoted text"}


def test_str_reply_to_not_on_earlier_turn():
    case = mk_case(text="final q")
    case.message = {"text": "final q", "reply_to": "quoted text"}
    env = HttpTurnEngine("http://fake.invalid", "k", 5).build_envelope(case, "earlier", None, "r")
    assert "replyTo" not in env["message"]["message"]


def test_dict_reply_to_unchanged():
    case = mk_case(text="final q")
    case.message = {"text": "final q", "reply_to": {"id": "m1", "text": "quoted"}}
    env = HttpTurnEngine("http://fake.invalid", "k", 5).build_envelope(case, "final q", None, "r")
    assert "replyTo" in env["message"]["message"]


def test_toolless_envelope_error_is_lane_note_not_tool_error():
    msg = "purchase_cost needs the purchase_orders.cost grant"
    _, errs, notes = parse_trace(looked_up({"tool": None, "error": msg}))
    assert errs == []
    assert notes == [msg] or any(msg in n for n in notes)


def test_envelope_with_tool_is_tool_error():
    _, errs, notes = parse_trace(looked_up({"tool": "crm_x", "error": "boom"}))
    assert len(errs) == 1 and "boom" in errs[0]
    assert notes == []


def test_returned_http_toolless_is_tool_error():
    msg = "GET /api/v1/x returned HTTP 422: subject_required"
    _, errs, notes = parse_trace(looked_up({"tool": None, "error": msg}))
    assert len(errs) == 1 and "422" in errs[0]
    assert notes == []


def test_failed_stage_still_tool_error():
    _, errs, _n = parse_trace([{"stage": "resolved", "status": "failed", "error": "BOOM-1"}])
    assert len(errs) == 1


def test_http_engine_exposes_lane_notes(monkeypatch):
    msg = "the described set qualifies nothing"
    reader = FakeReader(looked_up({"tool": None, "error": msg}))
    out = _turn(monkeypatch, {**BODY, "turn_id": "t-9"}, reader)
    assert out.tool_errors == []
    assert any(msg in n for n in out.lane_notes)


def test_results_row_carries_lane_notes(tmp_path):
    d, out = tmp_path / "data", tmp_path / "o"
    write_case(d, case_dict("k0"))
    assert main(["--data-dir", str(d), "--engine", "tests.chatbot_eval.fakes:LaneNoteEngine", "--out", str(out)]) == 0
    row = json.loads((out / "results.jsonl").read_text().splitlines()[0])
    assert row["lane_notes"] == ["note one"]


def test_grader_version_is_7():
    r = build_report(RunResult(results=[], stopped_reason=None, wall_s=0.0))
    assert r["grader_version"] == GRADER_VERSION


def _old_run(tmp_path, tool_errors):
    d, old, new = tmp_path / "data", tmp_path / "old", tmp_path / "new"
    write_case(d, case_dict("k0"))
    old.mkdir()
    row = {
        "case_id": "k0", "split": "dev", "topic_code": "stock", "language": "en", "variant_kind": None,
        "label": "error", "reasons": ["tool_errors"], "latency_ms": 5, "tokens": {"prompt": 0, "cached": 0, "output": 0},
        "reply_text": "Stock of TESTCODE-1 is 12 units", "branch_kind": "answer", "delegated": False,
        "tool_errors": tool_errors, "previous_bot_reply": None,
    }
    (old / "results.jsonl").write_text(json.dumps(row) + "\n")
    (old / "report.json").write_text(json.dumps({"grader_version": 6}))
    rc = main(["--data-dir", str(d), "--engine", "tests.chatbot_eval.fakes:NeverEngine", "--regrade", str(old), "--out", str(new)])
    assert rc == 0
    return json.loads((new / "results.jsonl").read_text().splitlines()[0])


def test_regrade_moves_toolless_entry_to_lane_notes(tmp_path):
    row = _old_run(tmp_path, ["None: the described set qualifies nothing"])
    assert row["label"] == "correct"
    assert row["tool_errors"] == []
    assert any("qualifies nothing" in n for n in row["lane_notes"])


def test_regrade_keeps_http_failure_as_error(tmp_path):
    row = _old_run(tmp_path, ["None: GET /x returned HTTP 422: y"])
    assert row["label"] == "error"
    assert len(row["tool_errors"]) == 1
