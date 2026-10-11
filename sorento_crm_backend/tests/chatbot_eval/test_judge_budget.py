import pytest

from scripts.chatbot_eval.budget import PRICES, Budget, cost_usd
from scripts.chatbot_eval.cases import Expect
from scripts.chatbot_eval.grade import Verdict
from scripts.chatbot_eval.judge import CALIBRATION_MIN, JudgeResult, apply_judge, calibrate
from tests.chatbot_eval.fakes import make_outcome
from tests.chatbot_eval.test_runner import mk_case


class StubJudge:
    def __init__(self, passes):
        self.passes = list(passes)

    def score(self, case, outcome):
        return JudgeResult(passed=self.passes.pop(0), note="n")


def test_calibration_min():
    assert CALIBRATION_MIN == 0.9


def test_calibrate_agreement():
    items = [(mk_case(), make_outcome("a"), h) for h in (True, True, False, False)]
    assert calibrate(StubJudge([True, True, False, True]), items) == pytest.approx(0.75)
    assert calibrate(StubJudge([True, True, False, False]), items) == 1.0


def v(label="correct"):
    return Verdict(label=label, reasons=[], needs_judge=True)


def test_apply_judge_uncalibrated_unchanged():
    out = apply_judge(v(), JudgeResult(passed=False, note="bad"), calibrated=False)
    assert out.label == "correct"
    assert "judge_uncalibrated" in out.reasons


def test_apply_judge_calibrated_fail_is_wrong():
    assert apply_judge(v(), JudgeResult(passed=False, note="bad"), calibrated=True).label == "wrong"


def test_apply_judge_calibrated_pass_keeps_label():
    assert apply_judge(v("good_clarify"), JudgeResult(passed=True, note="ok"), calibrated=True).label == "good_clarify"


def test_prices_contract():
    assert {"gpt-5.4-mini", "gpt-4.1-mini"} <= set(PRICES)


def test_cost_usd_math():
    t = {"prompt": 1_000_000, "cached": 400_000, "output": 100_000}
    # (600k * 0.75 + 400k * 0.075 + 100k * 4.5) / 1e6
    assert cost_usd(t, "gpt-5.4-mini") == pytest.approx(0.45 + 0.03 + 0.45)
    # (600k * 0.40 + 400k * 0.10 + 100k * 1.60) / 1e6
    assert cost_usd(t, "gpt-4.1-mini") == pytest.approx(0.24 + 0.04 + 0.16)


def test_budget_exceeded():
    b = Budget(0.5, "gpt-4.1-mini")
    assert b.spent == 0 and not b.exceeded
    b.add({"prompt": 1_000_000, "cached": 0, "output": 0})
    assert b.spent == pytest.approx(0.40) and not b.exceeded
    b.add({"prompt": 1_000_000, "cached": 0, "output": 0})
    assert b.exceeded
