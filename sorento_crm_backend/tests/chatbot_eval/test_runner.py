import pytest

from scripts.chatbot_eval.budget import Budget
from scripts.chatbot_eval.cases import Case, Expect
from scripts.chatbot_eval.runner import NotDryRunError, run_all, run_case
from tests.chatbot_eval.fakes import RecordingEngine


def mk_case(cid="c1", history=None, variants=None, must=None, text="final q", split="dev"):
    return Case(
        id=cid, split=split, topic_code="stock", language="en", contact={"respond_id": "1"},
        history=history if history is not None else [],
        message={"text": text},
        variants=variants or [],
        expect=Expect(outcome="answer", acceptable_alternates=[], must=must or [], options=[], must_not=[], judge_note=None),
    )


def test_history_user_turns_replayed_in_order_with_state():
    hist = [{"role": "user", "text": "u1"}, {"role": "bot", "text": "b1"}, {"role": "user", "text": "u2"}]
    eng = RecordingEngine()
    res = run_case(eng, mk_case(history=hist), "final q", "run-1")
    assert [c[0] for c in eng.calls] == ["u1", "u2", "final q"]
    assert eng.calls[0][1] == {}
    assert eng.calls[1][1] == {"n": 1}
    assert eng.calls[2][1] == {"n": 2}
    assert all(c[2] == "run-1" for c in eng.calls)
    assert res.case_id == "c1" and res.variant_kind is None


def test_only_final_turn_graded():
    # replayed turns reply with junk; final reply satisfies must
    eng = RecordingEngine(replies={"u1": "junk"}, default="TESTCODE-1 ok")
    res = run_case(eng, mk_case(history=[{"role": "user", "text": "u1"}], must=[{"code": "TESTCODE-1"}]), "final q", "r")
    assert res.verdict.label == "correct"


def test_stuck_uses_last_replayed_reply():
    eng = RecordingEngine(replies={"u1": "same reply", "final q": "same reply"})
    res = run_case(eng, mk_case(history=[{"role": "user", "text": "u1"}], must=[{"code": "NOPE-1"}]), "final q", "r")
    assert res.verdict.label == "stuck"


def test_stuck_uses_history_bot_text_without_replay():
    eng = RecordingEngine(replies={"final q": "Same Reply"})
    res = run_case(eng, mk_case(history=[{"role": "bot", "text": "same reply"}], must=[{"code": "NOPE-1"}]), "final q", "r")
    assert res.verdict.label == "stuck"


def test_not_dry_run_aborts():
    with pytest.raises(NotDryRunError):
        run_case(RecordingEngine(dry_run=False), mk_case(), "final q", "r")
    with pytest.raises(NotDryRunError):
        run_all(RecordingEngine(dry_run=False), [mk_case()], run_id="r")


def test_variants_none():
    cases = [mk_case("a", variants=[{"kind": "typo", "text": "v1"}, {"kind": "slang", "text": "v2"}])]
    eng = RecordingEngine()
    out = run_all(eng, cases, variants="none", run_id="r")
    assert len(out.results) == 1 and out.stopped_reason is None
    assert [c[0] for c in eng.calls] == ["final q"]


def test_variants_all():
    cases = [mk_case("a", variants=[{"kind": "typo", "text": "v1"}, {"kind": "slang", "text": "v2"}])]
    out = run_all(RecordingEngine(), cases, variants="all", run_id="r")
    kinds = sorted(r.variant_kind or "original" for r in out.results)
    assert kinds == ["original", "slang", "typo"]


def test_variants_one_seed_deterministic():
    cases = [mk_case(f"c{i}", variants=[{"kind": f"k{i}{j}", "text": f"v{i}{j}"} for j in range(3)]) for i in range(8)]

    def kinds(seed):
        out = run_all(RecordingEngine(), cases, variants="one", run_id="r", seed=seed)
        return [(r.case_id, r.variant_kind) for r in out.results]

    a, b = kinds(5), kinds(5)
    assert a == b
    vk = [k for _, k in a if k is not None]
    assert len(vk) == len(cases)  # exactly one variant per case
    assert len(a) == len(cases) * 2  # original + one variant each


def test_budget_stops_before_next_turn():
    cases = [mk_case(f"c{i}") for i in range(5)]
    tokens = {"prompt": 1_000_000, "cached": 0, "output": 0}  # 0.40 usd each on gpt-4.1-mini
    eng = RecordingEngine(tokens=tokens)
    out = run_all(eng, cases, run_id="r", budget=Budget(0.5, "gpt-4.1-mini"))
    assert out.stopped_reason == "budget"
    assert len(eng.calls) == 2  # second turn pushes past cap; third never starts
    assert len(out.results) == 2


def test_no_budget_runs_all():
    out = run_all(RecordingEngine(), [mk_case("a"), mk_case("b")], run_id="r")
    assert out.stopped_reason is None and len(out.results) == 2
    assert out.wall_s >= 0
