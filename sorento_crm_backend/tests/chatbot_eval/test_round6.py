import json

from scripts.chatbot_eval.cases import Expect
from scripts.chatbot_eval.grade import Verdict, grade
from scripts.chatbot_eval.report import GRADER_VERSION, build_report
from scripts.chatbot_eval.run import main
from scripts.chatbot_eval.runner import CaseResult, RunResult
from tests.chatbot_eval.fakes import case_dict, make_outcome, write_case

FAKE = "tests.chatbot_eval.fakes:FakeEngine"
NEVER = "tests.chatbot_eval.fakes:NeverEngine"


def ex(outcome, **kw):
    base = dict(outcome=outcome, acceptable_alternates=[], must=[], options=[], must_not=[], judge_note=None)
    base.update(kw)
    return Expect(**base)


# (a)
def test_criteria_less_primary_passes_with_needs_judge_despite_clarify_alternate():
    e = ex("answer", acceptable_alternates=["clarify"], options=["OPT-X", "OPT-Y"])
    v = grade(e, make_outcome("Here is the stock: 12 units"), None)
    assert v.label == "correct" and v.needs_judge is True


def test_criteria_less_alternate_still_skipped_when_primary_has_criteria():
    e = ex("clarify", options=["OPT-A", "OPT-B"], acceptable_alternates=["answer"])
    assert grade(e, make_outcome("Couldn't find it"), None).label == "wrong"


# (b)
def cr(cid, label="correct", nj=False):
    return CaseResult(case_id=cid, split="dev", topic_code="t", language="en", variant_kind=None,
                      verdict=Verdict(label=label, reasons=[], needs_judge=nj), outcome=make_outcome("x"),
                      latency_ms=1, tokens={"prompt": 0, "cached": 0, "output": 0})


def _rep(results, baseline=None):
    return build_report(RunResult(results=results, stopped_reason=None, wall_s=1.0), baseline=baseline)


def test_grader_version_and_strict_total_delta():
    base = _rep([cr("1"), cr("2", "wrong")])
    now = _rep([cr("1"), cr("2")], baseline=base)
    assert now["grader_version"] == GRADER_VERSION
    assert "strict_total" in now["delta"]
    assert now["delta"]["comparable"] is True


def test_baseline_with_other_grader_version_not_comparable():
    base = _rep([cr("1")])
    base["grader_version"] = GRADER_VERSION - 1
    assert _rep([cr("1")], baseline=base)["delta"]["comparable"] is False


# (c)
def _run(tmp_path, d, out, *extra, engine=FAKE):
    return main(["--data-dir", str(d), "--engine", engine, "--out", str(out), *extra])


def _rows(out):
    return [json.loads(x) for x in (out / "results.jsonl").read_text().splitlines() if x.strip()]


def test_results_rows_carry_regrade_fields(tmp_path):
    d, out = tmp_path / "data", tmp_path / "o"
    write_case(d, case_dict("k0"))
    assert _run(tmp_path, d, out) == 0
    row = _rows(out)[0]
    for k in ("reply_text", "branch_kind", "delegated", "tool_errors", "previous_bot_reply"):
        assert k in row, k


# (d)
def test_regrade_uses_stored_replies_and_current_labels(tmp_path):
    d, out1, out2 = tmp_path / "data", tmp_path / "o1", tmp_path / "o2"
    write_case(d, case_dict("k0"))
    write_case(d, case_dict("k1"))
    write_case(d, case_dict("k2"))
    assert _run(tmp_path, d, out1) == 0
    assert {r["label"] for r in _rows(out1)} == {"correct"}
    # label corrections: k0 now requires a code the stored reply lacks; k2 deleted
    c = case_dict("k0")
    c["expect"]["must"] = [{"code": "TESTCODE-MISSING"}]
    write_case(d, c)
    (d / "k2.json").unlink()
    rc = main(["--data-dir", str(d), "--engine", NEVER, "--regrade", str(out1), "--out", str(out2)])
    assert rc == 0
    labels = {r["case_id"]: r["label"] for r in _rows(out2)}
    assert labels == {"k0": "wrong", "k1": "correct"}
    rep = json.loads((out2 / "report.json").read_text())
    assert rep["regrade_dropped"] == 1
    assert rep["grader_version"] == GRADER_VERSION
