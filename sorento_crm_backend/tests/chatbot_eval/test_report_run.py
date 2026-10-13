import json

import pytest

from scripts.chatbot_eval.grade import Verdict
from scripts.chatbot_eval.report import build_report, render_summary
from scripts.chatbot_eval.run import main
from scripts.chatbot_eval.runner import CaseResult, RunResult
from tests.chatbot_eval.fakes import case_dict, make_outcome, write_case

TOK = {"prompt": 1000, "cached": 0, "output": 100}


def cr(cid, label, split="dev", topic="stock", lang="en", variant=None, lat=100):
    return CaseResult(
        case_id=cid, split=split, topic_code=topic, language=lang, variant_kind=variant,
        verdict=Verdict(label=label, reasons=[], needs_judge=False),
        outcome=make_outcome("SECRETREPLY text"), latency_ms=lat, tokens=dict(TOK),
    )


def run_of(results, stopped=None, wall=1.5):
    return RunResult(results=results, stopped_reason=stopped, wall_s=wall)


def test_counts_by_topic_and_language():
    r = build_report(run_of([
        cr("1", "correct", topic="stock", lang="en"),
        cr("2", "wrong", topic="stock", lang="ms"),
        cr("3", "good_clarify", topic="price", lang="en"),
    ]))
    assert r["by_topic"]["stock"]["correct"] == 1 and r["by_topic"]["stock"]["wrong"] == 1
    assert r["by_topic"]["price"]["good_clarify"] == 1
    assert r["by_language"]["en"]["correct"] == 1 and r["by_language"]["ms"]["wrong"] == 1


def test_underfit_is_original_pass_rate():
    r = build_report(run_of([
        cr("1", "correct"), cr("2", "good_clarify"), cr("3", "wrong"), cr("4", "wrong"),
        cr("5", "wrong", variant="typo"),
    ]))
    assert r["underfit"] == pytest.approx(0.5)


def test_gap_dev_hidden():
    r = build_report(run_of([
        cr("1", "correct", split="dev"), cr("2", "correct", split="dev"),
        cr("3", "correct", split="hidden"), cr("4", "wrong", split="hidden"),
    ]))
    assert r["gap_dev_hidden"] == pytest.approx(0.5)


def test_gap_dev_hidden_none_when_one_split():
    r = build_report(run_of([cr("1", "correct", split="dev")]))
    assert r["gap_dev_hidden"] is None


def test_gap_original_variant():
    r = build_report(run_of([
        cr("1", "correct"), cr("2", "correct"),
        cr("1", "correct", variant="typo"), cr("2", "wrong", variant="typo"),
    ]))
    assert r["gap_original_variant"] == pytest.approx(0.5)


def test_tokens_cost_latency_wall_stop():
    res = [cr(str(i), "correct", lat=l) for i, l in enumerate([10, 20, 30, 40, 100])]
    r = build_report(run_of(res, stopped="budget", wall=2.0))
    assert r["tokens"]["prompt"] == 5000 and r["tokens"]["output"] == 500
    assert r["est_usd"] > 0
    assert r["latency_p50_ms"] == 30
    assert 40 <= r["latency_p95_ms"] <= 100
    assert r["wall_s"] == 2.0 and r["stopped_reason"] == "budget"


def test_delta_vs_baseline():
    base = build_report(run_of([cr("1", "correct"), cr("2", "wrong")]))
    now = build_report(run_of([cr("1", "correct"), cr("2", "correct")]), baseline=base)
    assert now["delta"]["total"] == pytest.approx(0.5)
    assert now["delta"]["by_topic"]["stock"] == pytest.approx(0.5)


def test_render_summary_hidden_has_no_text_or_ids():
    rep = build_report(run_of([cr("CASEID-77", "wrong", split="hidden"), cr("CASEID-78", "correct", split="hidden")], stopped="budget"))
    s = render_summary(rep, "hidden")
    assert isinstance(s, str) and s
    assert "CASEID" not in s and "SECRETREPLY" not in s
    assert "stock" in s
    assert "stopped: budget" in s


# ---- run.main ----
ENGINE = "tests.chatbot_eval.fakes:FakeEngine"


def seed(tmp_path, n=2, split="dev"):
    d = tmp_path / "data"
    for i in range(n):
        write_case(d, case_dict(f"k{i}", split=split))
    return d


def test_main_exit_0_and_out_files(tmp_path):
    d, out = seed(tmp_path), tmp_path / "out"
    rc = main(["--data-dir", str(d), "--engine", ENGINE, "--out", str(out)])
    assert rc == 0
    files = list(out.rglob("*")) if out.is_dir() else [out]
    assert any(f.suffix == ".json" for f in files)
    jsonl = [f for f in files if f.suffix == ".jsonl"]
    assert jsonl
    lines = [json.loads(x) for x in jsonl[0].read_text().splitlines() if x.strip()]
    assert len(lines) == 2


def test_main_exit_2_on_invalid_cases(tmp_path):
    d = seed(tmp_path, 1)
    bad = case_dict("bad")
    bad.pop("expect")
    write_case(d, bad, name="bad.json")
    assert main(["--data-dir", str(d), "--engine", ENGINE]) == 2


def test_main_exit_3_on_budget(tmp_path):
    d = seed(tmp_path, 4)
    rc = main(["--data-dir", str(d), "--engine", "tests.chatbot_eval.fakes:BudgetBurner", "--budget-usd", "0.5"])
    assert rc == 3


def test_main_split_filter_and_baseline(tmp_path):
    d = seed(tmp_path, 1, "dev")
    write_case(d, case_dict("h", split="hidden"))
    out1, out2 = tmp_path / "o1", tmp_path / "o2"
    assert main(["--data-dir", str(d), "--engine", ENGINE, "--split", "dev", "--out", str(out1)]) == 0
    rep = next(f for f in out1.rglob("*.json"))
    assert main(["--data-dir", str(d), "--engine", ENGINE, "--split", "dev", "--baseline", str(rep), "--out", str(out2)]) == 0
    rep2 = json.loads(next(f for f in out2.rglob("*.json")).read_text())
    assert "delta" in rep2


def test_main_hidden_prints_no_message_text(tmp_path, capsys):
    d = seed(tmp_path, 1, "hidden")
    main(["--data-dir", str(d), "--engine", ENGINE, "--split", "hidden"])
    out = capsys.readouterr().out
    assert "how many TESTCODE-1" not in out and "k0" not in out
