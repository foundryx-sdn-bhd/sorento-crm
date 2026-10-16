import httpx

from scripts.chatbot_eval.cases import Expect, load_cases
from scripts.chatbot_eval.engine import HttpTurnEngine
from scripts.chatbot_eval.grade import grade
from scripts.chatbot_eval.runner import run_case
from tests.chatbot_eval.fakes import case_dict, make_outcome, write_case
from tests.chatbot_eval.test_runner import mk_case


def ex(outcome):
    return Expect(outcome=outcome, acceptable_alternates=[], must=[], options=[], must_not=[], judge_note=None)


def test_load_cases_accepts_silent(tmp_path):
    write_case(tmp_path, case_dict("s1", outcome="silent"))
    res = load_cases(tmp_path)
    assert [c.id for c in res.cases] == ["s1"] and res.invalid == []


def test_silent_blank_reply_correct():
    assert grade(ex("silent"), make_outcome(""), None).label == "correct"


def test_silent_non_blank_reply_wrong():
    v = grade(ex("silent"), make_outcome("hello there"), None)
    assert v.label == "wrong" and "reply_when_silent_expected" in v.reasons


def test_silent_tool_errors_error():
    assert grade(ex("silent"), make_outcome("", tool_errors=["boom"]), None).label == "error"


def test_answer_blank_stays_error():
    assert grade(ex("answer"), make_outcome(""), None).label == "error"


def _delegated(monkeypatch):
    body = {"turn_id": "t", "is_test": True, "reply": None, "delegate": "business", "actions": []}
    monkeypatch.setattr(httpx.Client, "send", lambda self, request, **kw: httpx.Response(200, json=body, request=request))
    return HttpTurnEngine("http://fake.invalid", "k", 5)


def test_silent_delegated_correct(monkeypatch):
    case = mk_case()
    case.expect = ex("silent")
    assert run_case(_delegated(monkeypatch), case, "final q", "r").verdict.label == "correct"


def test_answer_delegated_stays_error(monkeypatch):
    assert run_case(_delegated(monkeypatch), mk_case(), "final q", "r").verdict.label == "error"
