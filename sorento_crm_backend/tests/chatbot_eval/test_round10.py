import json

from scripts.chatbot_eval.cases import Expect
from scripts.chatbot_eval.grade import grade
from scripts.chatbot_eval.report import GRADER_VERSION
from scripts.chatbot_eval.run import main
from tests.chatbot_eval.fakes import case_dict, make_outcome, write_case
from tests.chatbot_eval.test_trace import BODY, FakeReader, _turn

TRACE = [
    {"stage": "understood", "status": "ok",
     "facts": {"domain": "stock", "intent": "check_stock", "entities": ["TESTCODE-1"], "prompt_version": 52}},
    {"stage": "looked_up", "status": "ok", "raw": {"envelopes": [
        {"tool": "crm_stock", "entities": ["TESTCODE-1"], "miss": ["TESTCODE-2"], "unresolved": [], "error": None}]}},
    {"stage": "tooled", "status": "ok", "tool": "crm_stock", "args": {"code": "TESTCODE-1"}},
]


def test_grader_version_is_8():
    assert GRADER_VERSION >= 8


def test_trace_digest_populated(monkeypatch):
    out = _turn(monkeypatch, {**BODY, "turn_id": "t-1"}, FakeReader(TRACE))
    d = out.trace_digest
    p = d["parser"]
    assert p["domain"] == "stock" and p["intent"] == "check_stock"
    assert p["entities"] == ["TESTCODE-1"] and p["prompt_version"] == 52
    assert len(d["resolved"]) == 1
    r = d["resolved"][0]
    assert r["tool"] == "crm_stock" and r["miss"] == ["TESTCODE-2"] and r["entities"] == ["TESTCODE-1"]
    assert "unresolved" in r and "error" in r
    assert d["tools"] == [{"tool": "crm_stock", "args": {"code": "TESTCODE-1"}}]
    assert d["reply_text"] == "hi"


def test_empty_trace_digest_has_empty_fields(monkeypatch):
    out = _turn(monkeypatch, {**BODY, "turn_id": "t-2"}, FakeReader([]))
    d = out.trace_digest
    assert not d["parser"] and not d["resolved"] and not d["tools"]


def test_results_row_has_digests(tmp_path):
    d, out = tmp_path / "data", tmp_path / "o"
    c = case_dict("k0")
    c["history"] = [{"role": "user", "text": "u1"}, {"role": "user", "text": "u2"}]
    write_case(d, c)
    assert main(["--data-dir", str(d), "--engine", "tests.chatbot_eval.fakes:DigestEngine", "--out", str(out)]) == 0
    row = json.loads((out / "results.jsonl").read_text().splitlines()[0])
    assert row["trace_digest"]["parser"]["intent"] == "i3"
    assert [x["parser"]["intent"] for x in row["replay_digests"]] == ["i1", "i2"]


def ex(**kw):
    base = dict(outcome="answer", acceptable_alternates=[], must=[], options=[], must_not=[], judge_note=None)
    base.update(kw)
    return Expect(**base)


def g(e, reply, prev, **kw):
    return grade(e, make_outcome(reply, **kw), prev)


def test_repeat_meeting_rubric_is_correct():
    assert g(ex(must=[{"code": "TESTCODE-1"}]), "TESTCODE-1 left", "testcode-1 left").label == "correct"


def test_repeat_failing_rubric_is_stuck():
    assert g(ex(must=[{"code": "TESTCODE-1"}]), "nothing", "nothing").label == "stuck"


def test_non_repeat_failing_rubric_is_wrong():
    assert g(ex(must=[{"code": "TESTCODE-1"}]), "nothing", "something else").label == "wrong"


def test_tool_errors_still_first():
    assert g(ex(must=[{"code": "TESTCODE-1"}]), "TESTCODE-1", "TESTCODE-1", tool_errors=["boom"]).label == "error"
