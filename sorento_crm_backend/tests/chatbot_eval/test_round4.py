from scripts.chatbot_eval.cases import Expect
from scripts.chatbot_eval.grade import Verdict, grade
from scripts.chatbot_eval.report import build_report, render_summary
from scripts.chatbot_eval.runner import CaseResult, RunResult
from tests.chatbot_eval.fakes import make_outcome


def ex(outcome, **kw):
    base = dict(outcome=outcome, acceptable_alternates=[], must=[], options=[], must_not=[], judge_note=None)
    base.update(kw)
    return Expect(**base)


def g(e, reply):
    return grade(e, make_outcome(reply), None)


CLARIFY_OR_ANSWER = dict(options=["OPT-A", "OPT-B"], acceptable_alternates=["answer"], must=[])


def test_criteria_less_alternate_does_not_pass_anything():
    assert g(ex("clarify", **CLARIFY_OR_ANSWER), "Couldn't find it").label == "wrong"


def test_clarify_with_options_still_passes_with_criteria_less_alternate():
    assert g(ex("clarify", **CLARIFY_OR_ANSWER), "Do you mean OPT-A or OPT-B?").label == "good_clarify"


def test_no_criteria_anywhere_passes_needs_judge():
    v = g(ex("answer", acceptable_alternates=["clarify"], must=[], options=[]), "anything at all")
    assert v.label == "correct" and v.needs_judge is True


def test_criteria_less_handover_alternate_does_not_rescue_missing_code():
    e = ex("answer", acceptable_alternates=["handover"], must=[{"code": "TESTCODE-X"}])
    assert g(e, "nothing relevant").label == "wrong"


def test_criteria_present_pass_not_needs_judge():
    e = ex("answer", acceptable_alternates=["clarify"], must=[{"code": "TESTCODE-1"}], options=[])
    v = g(e, "TESTCODE-1 here")
    assert v.label == "correct" and v.needs_judge is False


def cr(cid, label, nj):
    return CaseResult(case_id=cid, split="dev", topic_code="t", language="en", variant_kind=None,
                      verdict=Verdict(label=label, reasons=[], needs_judge=nj), outcome=make_outcome("x"),
                      latency_ms=1, tokens={"prompt": 0, "cached": 0, "output": 0})


def _rep():
    return build_report(RunResult(results=[cr("1", "correct", False), cr("2", "correct", False),
                                           cr("3", "correct", True), cr("4", "wrong", False)],
                                  stopped_reason=None, wall_s=1.0))


def test_strict_rates_exclude_needs_judge_passes():
    r = _rep()
    assert r["underfit"] == 0.75
    assert r["strict_pass_rate"] == 0.5
    assert r["strict_underfit"] == 0.5


def test_summary_prints_strict_line():
    s = render_summary(_rep(), "dev")
    assert "strict" in s.lower()
