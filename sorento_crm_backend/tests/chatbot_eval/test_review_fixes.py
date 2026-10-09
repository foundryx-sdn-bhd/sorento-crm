"""Fix round 1 red tests. Calibration file shape used by the CLI judge tests:
<data-dir>/calibration/<name>.json = {"case": <case dict>, "reply_text": str, "human_passed": bool}
(the calibration dir must not be treated as case files by load_cases)."""

import json
import time
from pathlib import Path

import httpx
import pytest

from scripts.chatbot_eval.budget import Budget
from scripts.chatbot_eval.cases import Expect
from scripts.chatbot_eval.engine import HttpTurnEngine
from scripts.chatbot_eval.grade import Verdict, grade
from scripts.chatbot_eval.report import build_report
from scripts.chatbot_eval.run import main
from scripts.chatbot_eval.runner import CaseResult, NotDryRunError, RunResult, run_all, run_case
from tests.chatbot_eval import fakes
from tests.chatbot_eval.fakes import RecordingEngine, case_dict, make_outcome, write_case
from tests.chatbot_eval.test_runner import mk_case

ENGINE = "tests.chatbot_eval.fakes:FakeEngine"


def ex(outcome="answer", **kw):
    base = dict(outcome=outcome, acceptable_alternates=[], must=[], options=[], must_not=[], judge_note=None)
    base.update(kw)
    return Expect(**base)


# 1
def test_first_turn_state_is_empty_dict_then_carried():
    eng = RecordingEngine()
    run_case(eng, mk_case(history=[{"role": "user", "text": "u1"}]), "final q", "r")
    assert eng.calls[0][1] == {}
    assert eng.calls[1][1] == {"n": 1}


# 2
def test_run_id_scoped_per_case_and_variant():
    cases = [mk_case("a", variants=[{"kind": "typo", "text": "v1"}]), mk_case("b")]
    eng = RecordingEngine()
    run_all(eng, cases, variants="all", run_id="RUN", max_parallel=1)
    ids = sorted(set(c[2] for c in eng.calls))
    assert ids == ["RUN:a", "RUN:a:typo", "RUN:b"]


def test_run_case_history_turns_share_the_case_run_id():
    eng = RecordingEngine()
    run_all(eng, [mk_case("a", history=[{"role": "user", "text": "u1"}])], run_id="RUN")
    assert {c[2] for c in eng.calls} == {"RUN:a"}


# 3
def test_cli_refuses_budget_with_http_engine_without_trace_db(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("EVAL_TRACE_DB_URL", raising=False)
    d = tmp_path / "data"
    write_case(d, case_dict("k0"))
    rc = main(["--data-dir", str(d), "--engine", "http", "--base-url", "http://fake.invalid", "--budget-usd", "1"])
    assert rc == 2
    assert capsys.readouterr().err.strip()


# 4
def _case():
    return mk_case(text="final q")


def _http():
    return HttpTurnEngine("http://fake.invalid", "k", 5)


GOOD = {"turn_id": "t", "is_test": True, "reply": {"text": "hi"}, "actions": [{"kind": "send", "dry_run": True}]}


@pytest.mark.parametrize("exc", [httpx.ReadTimeout, httpx.ConnectError])
def test_transport_failure_becomes_tool_error(monkeypatch, exc):
    monkeypatch.setattr(time, "sleep", lambda s: None)

    def send(self, request, **kw):
        raise exc("x", request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    out = _http().turn(_case(), "q", None, "r")
    assert out.tool_errors == [f"transport: {exc.__name__}"]


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_retryable_statuses_retried(monkeypatch, status):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    calls = []

    def send(self, request, **kw):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(status, json={}, request=request)
        return httpx.Response(200, json=GOOD, request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    out = _http().turn(_case(), "q", None, "r")
    assert len(calls) == 2 and out.reply_text == "hi"


def test_retry_after_honoured(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    calls = []

    def send(self, request, **kw):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={}, headers={"Retry-After": "7"}, request=request)
        return httpx.Response(200, json=GOOD, request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    _http().turn(_case(), "q", None, "r")
    assert 7 in [float(s) for s in slept]


# 5
class LiveOnSecond(RecordingEngine):
    def turn(self, case, text, state, run_id):
        out = super().turn(case, text, state, run_id)
        if self.n == 2:
            out.dry_run = False
        return out


def test_not_dry_run_aborts_whole_run():
    cases = [mk_case(f"c{i}") for i in range(5)]
    eng = LiveOnSecond()
    with pytest.raises(NotDryRunError):
        run_all(eng, cases, run_id="r", max_parallel=1)
    assert len(eng.calls) == 2


def test_not_dry_run_message_hides_hidden_case_id():
    eng = LiveOnSecond()
    cases = [mk_case("c0", split="hidden"), mk_case("SECRETHIDDENID", split="hidden")]
    with pytest.raises(NotDryRunError) as ei:
        run_all(eng, cases, run_id="r", max_parallel=1)
    assert "SECRETHIDDENID" not in str(ei.value)


# 6
def _judge_data(tmp_path, agree, n_cal=10):
    fakes.JUDGE_CALLS.clear()
    fakes.JUDGE_CFG.update(agree=agree, tokens={"prompt": 0, "cached": 0, "output": 0})
    d = tmp_path / "data"
    for i in range(3):
        c = case_dict(f"k{i}")
        c["expect"]["judge_note"] = "be polite"
        write_case(d, c)
    cal = d / "calibration"
    for i in range(n_cal):
        label = "y" if i % 2 == 0 else "n"
        write_case(cal, {"case": case_dict(f"cal{i}{label}"), "reply_text": "TESTCODE-1 ok", "human_passed": label == "y"}, name=f"cal{i}.json")
    return d


def _report(out):
    return json.loads(next(f for f in out.rglob("*.json")).read_text())


def test_judge_disagreeing_not_applied(tmp_path):
    d, out = _judge_data(tmp_path, agree=False), tmp_path / "o"
    rc = main(["--data-dir", str(d), "--engine", ENGINE, "--judge", "tests.chatbot_eval.fakes:FakeJudge", "--out", str(out)])
    assert rc == 0
    assert not [c for c in fakes.JUDGE_CALLS if not c.startswith("cal")]
    assert _report(out)["judge_status"] == "uncalibrated"


def test_judge_without_calibration_items_not_applied(tmp_path):
    d, out = _judge_data(tmp_path, agree=True, n_cal=0), tmp_path / "o"
    main(["--data-dir", str(d), "--engine", ENGINE, "--judge", "tests.chatbot_eval.fakes:FakeJudge", "--out", str(out)])
    assert not fakes.JUDGE_CALLS
    assert _report(out)["judge_status"] == "uncalibrated"


def test_judge_calibrated_is_called(tmp_path):
    d, out = _judge_data(tmp_path, agree=True), tmp_path / "o"
    rc = main(["--data-dir", str(d), "--engine", ENGINE, "--judge", "tests.chatbot_eval.fakes:FakeJudge", "--out", str(out)])
    assert rc == 0
    assert len([c for c in fakes.JUDGE_CALLS if not c.startswith("cal")]) == 3
    assert _report(out)["judge_status"] == "calibrated"


def test_judge_cost_counts_against_budget(tmp_path):
    d = _judge_data(tmp_path, agree=True)
    fakes.JUDGE_CFG["tokens"] = {"prompt": 1_000_000, "cached": 0, "output": 0}
    rc = main(["--data-dir", str(d), "--engine", ENGINE, "--judge", "tests.chatbot_eval.fakes:FakeJudge", "--budget-usd", "0.5"])
    assert rc == 3


# 7
@pytest.mark.parametrize(
    "e",
    [
        ex("clarify", options=[]),
        ex("answer", must=[]),
        ex("not_supported", must=[]),
        ex("handover", must=[]),
        ex("answer", acceptable_alternates=["handover"], must=[]),
    ],
)
def test_no_deterministic_criteria_needs_judge(e):
    v = grade(e, make_outcome("some reply TESTCODE-1"), None)
    assert v.needs_judge is True


def test_with_criteria_and_no_note_does_not_need_judge():
    assert grade(ex(must=[{"code": "TESTCODE-1"}]), make_outcome("TESTCODE-1"), None).needs_judge is False


def test_report_counts_unverified():
    def cr(cid, nj):
        return CaseResult(case_id=cid, split="dev", topic_code="t", language="en", variant_kind=None,
                          verdict=Verdict(label="correct", reasons=[], needs_judge=nj), outcome=make_outcome("x"),
                          latency_ms=1, tokens={"prompt": 0, "cached": 0, "output": 0})
    r = build_report(RunResult(results=[cr("1", True), cr("2", True), cr("3", False)], stopped_reason=None, wall_s=1.0))
    assert r["unverified"] == 2
    assert r["by_topic"]["t"]["correct"] == 3


# 8
@pytest.mark.parametrize("reply,ok", [("AP4844", False), ("SO4844X", False), ("we have 4844", True)])
def test_int_number_whole_token(reply, ok):
    assert (grade(ex(must=[{"number": 4844}]), make_outcome(reply), None).label == "correct") is ok


@pytest.mark.parametrize("reply,ok", [("1630.00 left", True), ("1,630 left", True), ("1630 pcs", True), ("16300", False)])
def test_number_forms(reply, ok):
    assert (grade(ex(must=[{"number": 1630}]), make_outcome(reply), None).label == "correct") is ok


# 9
def test_delegated_turn_is_error(monkeypatch):
    body = {"turn_id": "t", "is_test": True, "reply": None, "delegate": "business", "actions": []}
    monkeypatch.setattr(httpx.Client, "send", lambda self, request, **kw: httpx.Response(200, json=body, request=request))
    res = run_case(_http(), _case(), "final q", "r")
    assert res.verdict.label == "error"
    assert any("delegated" in r for r in res.verdict.reasons)


# 10
def test_out_inside_repo_refused(tmp_path):
    d = tmp_path / "data"
    write_case(d, case_dict("k0"))
    inside = Path(__file__).resolve().parent / "_should_not_exist_out"
    try:
        rc = main(["--data-dir", str(d), "--engine", ENGINE, "--out", str(inside)])
        assert rc == 2
        assert not inside.exists()
    finally:
        if inside.is_dir():
            for f in inside.rglob("*"):
                f.unlink() if f.is_file() else None
            inside.rmdir()


# 2a / 2b / 2c (engine memory is contact-scoped, not run-scoped)
class ResettingEngine(RecordingEngine):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.events = []

    def reset_contact(self, case):
        self.events.append(("reset", case.id))

    def turn(self, case, text, state, run_id):
        self.events.append(("turn", case.id, text))
        return super().turn(case, text, state, run_id)


def test_reset_contact_once_before_each_cases_first_turn():
    cases = [mk_case("a", history=[{"role": "user", "text": "u1"}]), mk_case("b")]
    eng = ResettingEngine()
    run_all(eng, cases, run_id="r", max_parallel=1)
    assert eng.events == [
        ("reset", "a"), ("turn", "a", "u1"), ("turn", "a", "final q"),
        ("reset", "b"), ("turn", "b", "final q"),
    ]


def test_engine_without_reset_contact_still_runs():
    out = run_all(RecordingEngine(), [mk_case("a")], run_id="r")
    assert len(out.results) == 1


class OverlapEngine(RecordingEngine):
    def __init__(self):
        super().__init__()
        import threading
        self.lock = threading.Lock()
        self.active = {}
        self.max_seen = {}

    def turn(self, case, text, state, run_id):
        rid = case.contact["respond_id"]
        with self.lock:
            self.active[rid] = self.active.get(rid, 0) + 1
            self.max_seen[rid] = max(self.max_seen.get(rid, 0), self.active[rid])
        time.sleep(0.02)
        try:
            return super().turn(case, text, state, run_id)
        finally:
            with self.lock:
                self.active[rid] -= 1


def test_same_contact_never_concurrent():
    cases = []
    for i in range(6):
        c = mk_case(f"s{i}")
        c.contact = {"respond_id": "SHARED"}
        cases.append(c)
    for i in range(3):
        c = mk_case(f"o{i}")
        c.contact = {"respond_id": f"OTHER{i}"}
        cases.append(c)
    eng = OverlapEngine()
    out = run_all(eng, cases, run_id="r", max_parallel=4)
    assert len(out.results) == 9
    assert eng.max_seen["SHARED"] == 1


def test_db_contact_reset_requires_eval_database_name():
    from scripts.chatbot_eval.trace import DbContactReset

    for bad in ("postgresql://u:p@127.0.0.1:1/sorento_crm", "postgresql://u:p@127.0.0.1:1/prod_copy", "postgresql://u:p@127.0.0.1:1/"):
        with pytest.raises(ValueError):
            DbContactReset(bad)
    DbContactReset("postgresql://u:p@127.0.0.1:1/sorento_eval_run1")  # no connect at init
