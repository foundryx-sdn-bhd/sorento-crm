import pytest

from scripts.chatbot_eval.cases import Expect
from scripts.chatbot_eval.grade import grade
from tests.chatbot_eval.fakes import make_outcome

UUID = "123e4567-e89b-12d3-a456-426614174000"


def ex(outcome="answer", **kw):
    base = dict(outcome=outcome, acceptable_alternates=[], must=[], options=[], must_not=[], judge_note=None)
    base.update(kw)
    return Expect(**base)


def g(expect, reply, prev=None, **kw):
    return grade(expect, make_outcome(reply, **kw), prev)


def test_tool_errors_is_error():
    assert g(ex(), "fine", tool_errors=["boom"]).label == "error"


def test_blank_reply_is_error():
    assert g(ex(), "   ").label == "error"


def test_error_beats_stuck():
    assert g(ex(), "same", prev="same", tool_errors=["x"]).label == "error"


def test_stuck_normalised():
    assert g(ex(must=[{"code": "NOPE-1"}]), "Hello   There\n", prev="hello there").label == "stuck"


def test_stuck_beats_must_not():
    assert g(ex(must_not=["hello"]), "hello", prev="hello").label == "stuck"


def test_must_not_uuid():
    v = g(ex(), f"id is {UUID}", prev=None)
    assert v.label == "correct"  # no must_not set
    v = g(ex(must_not=["uuid"]), f"id is {UUID}")
    assert v.label == "wrong" and v.reasons


def test_must_not_uuid_absent_passes():
    assert g(ex(must_not=["uuid"]), "no ids here").label == "correct"


def test_must_not_stale_subject_case_insensitive():
    assert g(ex(must_not=["stale_subject:OLDWIDGET"]), "About oldwidget now").label == "wrong"
    assert g(ex(must_not=["stale_subject:OLDWIDGET"]), "About other").label == "correct"


def test_must_not_substring_case_insensitive():
    assert g(ex(must_not=["Sorry"]), "we are SORRY").label == "wrong"


def test_clarify_with_options():
    e = ex("clarify", options=["Red Widget", "Blue Widget"])
    assert g(e, "Do you mean red widget or BLUE widget?").label == "good_clarify"


def test_clarify_missing_option_wrong():
    e = ex("clarify", options=["Red Widget", "Blue Widget"])
    assert g(e, "Do you mean red widget?").label == "wrong"


def test_must_code_and_text():
    e = ex(must=[{"code": "TESTCODE-1"}, {"text": "in stock"}])
    assert g(e, "testcode-1 is IN STOCK").label == "correct"
    assert g(e, "testcode-1 is gone").label == "wrong"


@pytest.mark.parametrize("reply,ok", [("we have 1630 left", True), ("we have 1,630 left", True), ("we have 16300 left", False), ("we have 21630", False)])
def test_must_number(reply, ok):
    assert (g(ex(must=[{"number": "1630"}]), reply).label == "correct") is ok


@pytest.mark.parametrize("reply,ok", [("due 2026-10-03", True), ("due 03/10/2026", True), ("due 3/10/2026", True), ("due 10/03/2026", False), ("due soon", False)])
def test_must_date(reply, ok):
    assert (g(ex(must=[{"date": "2026-10-03"}]), reply).label == "correct") is ok


def test_alternate_honoured():
    e = ex("answer", acceptable_alternates=["not_supported"], must=[{"code": "TESTCODE-9"}])
    # primary fails (no code) but alternate not_supported with no must items... must still apply
    assert g(e, "TESTCODE-9 not supported").label == "correct"


def test_alternate_clarify_gives_good_clarify():
    e = ex("answer", acceptable_alternates=["clarify"], must=[{"code": "TESTCODE-9"}], options=["A one"])
    assert g(e, "which one: A one?").label == "good_clarify"


def test_nothing_passes_wrong():
    assert g(ex(must=[{"code": "TESTCODE-1"}]), "unrelated").label == "wrong"


def test_needs_judge_only_when_pass_and_note():
    assert g(ex(judge_note="be polite"), "hello").needs_judge
    assert not g(ex(must=[{"code": "TESTCODE-1"}]), "TESTCODE-1 here").needs_judge
    assert not g(ex(judge_note="be polite", must=[{"code": "ZZ"}]), "hello").needs_judge


def test_must_near_key_ignored_number_still_checked():
    e = ex(must=[{"number": 130, "near": "BRW"}])
    assert g(e, "BRW: 130").label == "correct"
    assert g(e, "BRW: 131").label == "wrong"
