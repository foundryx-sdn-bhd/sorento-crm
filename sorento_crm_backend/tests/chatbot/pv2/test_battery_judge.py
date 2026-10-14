"""PV2-EVAL UAC-5: judge prompt, verdict parsing, injected client. No model call."""
from __future__ import annotations

from scripts.pv2_battery import judge

CLASSES = ["wrong_subject", "lost_ask", "should_clarify", "needless_clarify", "invented_data", "wrong_language",
           "leak", "false_gap", "permission", "handover_wrong", "stuck", "tone", "other"]


def kwargs(**over):
    kw = dict(history=[{"user": "hello", "bot": "hi there"}], text="stock for srtwc286 please",
              reply="SRTWC286-SH has 4 units.", files=2, expect={"kind": "answer", "about": "stock for the SRTWC286 family"},
              facts={"resolved": ["SRTWC286-SH"]}, audience="dealer_nogrant")
    kw.update(over)
    return kw


def test_model_and_classes():
    assert judge.MODEL == "claude-opus-5-5"
    assert list(judge.CLASSES) == CLASSES


def test_rubric_names_owner_rules():
    r = judge.RUBRIC.lower()
    for needle in ("clarify", "uuid", "false gap", "language"):
        assert needle in r, needle
    assert "code" in r and ("only" in r)


def test_prompt_contains_inputs():
    p = judge.build_judge_prompt(**kwargs())
    for needle in (judge.RUBRIC, "stock for srtwc286 please", "SRTWC286-SH has 4 units.",
                   "stock for the SRTWC286 family", "dealer_nogrant", "SRTWC286-SH", "hello"):
        assert needle in p, needle


def test_parse_plain_and_fenced():
    raw = '{"verdict": "fail", "classes": ["leak", "tone"], "reason": "code shown"}'
    v = judge.parse_verdict(raw)
    assert v["verdict"] == "fail" and v["classes"] == ["leak", "tone"] and v["reason"] == "code shown"
    v = judge.parse_verdict("```json\n" + raw + "\n```")
    assert v["verdict"] == "fail" and v["classes"] == ["leak", "tone"]
    assert judge.parse_verdict('{"verdict":"pass","classes":[],"reason":"ok"}')["verdict"] == "pass"


def test_unknown_class_becomes_other():
    v = judge.parse_verdict('{"verdict":"fail","classes":["made_up","leak"],"reason":"r"}')
    assert v["classes"] == ["other", "leak"]


def test_garbage_is_judge_error_never_pass():
    for raw in ("", "not json at all", "{broken", '{"verdict":"maybe","classes":[],"reason":"x"}',
                '{"classes":[],"reason":"x"}', "[]", "null"):
        v = judge.parse_verdict(raw)
        assert v["verdict"] == "judge_error", raw
        assert set(v) >= {"verdict", "classes", "reason"}


class FakeClient:
    def __init__(self, out):
        self.out, self.prompts = out, []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.out


def test_judge_turn_uses_client():
    c = FakeClient('{"verdict":"pass","classes":[],"reason":"fine"}')
    v = judge.judge_turn(c, **kwargs())
    assert v["verdict"] == "pass" and len(c.prompts) == 1
    assert "SRTWC286-SH has 4 units." in c.prompts[0]


def test_judge_turn_garbage_and_exception_never_pass():
    assert judge.judge_turn(FakeClient("???"), **kwargs())["verdict"] == "judge_error"


def test_agreement():
    assert judge.agreement(["pass", "fail", "pass", "fail"], ["pass", "fail", "fail", "fail"]) == 0.75
    assert judge.agreement(["pass"], ["judge_error"]) == 0.0
    assert judge.agreement(["pass", "fail"], ["pass", "fail"]) == 1.0
