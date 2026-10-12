"""Per-layer scoring. Assumed shapes (contract round 12):
expect.understood = {domain?, intent?, entities?: [str]}; expect.expected_resolved = [str]; expect.tool = {name?, params?: {}}.
Row digest: {"parser": {domain, intent, entities}, "resolved": [{entities, ...}], "tools": [{"tool", "args"}]}.
Layers are checked through --regrade over hand-built stored rows (no engine), plus one live run for the row key."""

import json

import pytest

from scripts.chatbot_eval.cases import load_cases
from scripts.chatbot_eval.run import main
from tests.chatbot_eval.fakes import case_dict, write_case

NEVER = "tests.chatbot_eval.fakes:NeverEngine"
GOOD = "TESTCODE-1 ok"  # meets the default case's must code
BAD = "nothing useful"  # fails it
OK_DIGEST = {
    "parser": {"domain": "stock", "intent": "check", "entities": ["TESTCODE-1"]},
    "resolved": [{"tool": "crm_stock", "entities": ["TESTCODE-1"]}],
    "tools": [{"tool": "crm_stock", "args": {"code": "TESTCODE-1", "qty": 3, "x": 1}}],
}


def label(**expect_extra):
    c = case_dict("k0")
    c["expect"].update(expect_extra)
    return c


def row(cid, reply, digest):
    return {
        "case_id": cid, "split": "dev", "topic_code": "stock", "language": "en", "variant_kind": None,
        "label": "x", "reasons": [], "latency_ms": 5, "tokens": {"prompt": 0, "cached": 0, "output": 0},
        "reply_text": reply, "branch_kind": "answer", "delegated": False, "tool_errors": [], "lane_notes": [],
        "previous_bot_reply": None, "trace_digest": digest, "replay_digests": [],
    }


def regrade(tmp_path, cases, rows, name="new"):
    d, old, new = tmp_path / "data", tmp_path / "old", tmp_path / name
    d.mkdir(exist_ok=True)
    for c in cases:
        write_case(d, c)
    old.mkdir(exist_ok=True)
    (old / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (old / "report.json").write_text(json.dumps({}))
    assert main(["--data-dir", str(d), "--engine", NEVER, "--regrade", str(old), "--out", str(new)]) == 0
    out_rows = {r["case_id"]: r for r in (json.loads(x) for x in (new / "results.jsonl").read_text().splitlines() if x.strip())}
    return out_rows, json.loads((new / "report.json").read_text())


def test_load_cases_accepts_layer_fields_and_defaults(tmp_path):
    write_case(tmp_path, label(understood={"domain": "stock"}, expected_resolved=["A"], tool={"name": "t"}))
    write_case(tmp_path, case_dict("plain"))
    cases = {c.id: c for c in load_cases(tmp_path).cases}
    assert cases["k0"].expect.understood == {"domain": "stock"}
    assert cases["k0"].expect.expected_resolved == ["A"] and cases["k0"].expect.tool == {"name": "t"}
    p = cases["plain"].expect
    assert not p.understood and not p.expected_resolved and not p.tool


def d_with(**over):
    d = json.loads(json.dumps(OK_DIGEST))
    d.update(over)
    return d


@pytest.mark.parametrize("extra,digest,layer,want", [
    ({"understood": {"domain": "STOCK", "intent": "Check", "entities": ["testcode-1"]}}, OK_DIGEST, "understood", "pass"),
    ({"understood": {"domain": "stock", "intent": "other"}}, OK_DIGEST, "understood", "fail"),
    ({"understood": {"entities": ["MISSING-9"]}}, OK_DIGEST, "understood", "fail"),
    ({}, OK_DIGEST, "understood", "na"),
    ({"understood": {"domain": "stock"}}, d_with(parser={}), "understood", "no_trace"),
    ({"expected_resolved": ["testcode-1"]}, OK_DIGEST, "resolved", "pass"),
    ({"expected_resolved": ["ZZZ-9"]}, OK_DIGEST, "resolved", "fail"),
    ({}, OK_DIGEST, "resolved", "na"),
    ({"expected_resolved": ["TESTCODE-1"]}, d_with(resolved=[]), "resolved", "no_trace"),
    ({"tool": {"name": "crm_stock", "params": {"code": "testcode-1", "qty": 3}}}, OK_DIGEST, "tool", "pass"),
    ({"tool": {"name": "crm_stock", "params": {"qty": "3"}}}, OK_DIGEST, "tool", "fail"),
    ({"tool": {"name": "crm_other"}}, OK_DIGEST, "tool", "fail"),
    ({"tool": {"name": "crm_stock", "params": {"code": "NOPE"}}}, OK_DIGEST, "tool", "fail"),
    ({}, OK_DIGEST, "tool", "na"),
    ({"tool": {"name": "crm_stock"}}, d_with(tools=[]), "tool", "no_trace"),
])
def test_layer_states(tmp_path, extra, digest, layer, want):
    rows, _ = regrade(tmp_path, [label(**extra)], [row("k0", GOOD, digest)])
    assert rows["k0"]["layers"][layer] == want
    assert set(rows["k0"]["layers"]) == {"understood", "resolved", "tool", "reply"}


def test_reply_layer_from_verdict(tmp_path):
    rows, _ = regrade(tmp_path, [label(), {**case_dict("k1")}], [row("k0", GOOD, OK_DIGEST), row("k1", BAD, OK_DIGEST)])
    assert rows["k0"]["layers"]["reply"] == "pass"
    assert rows["k1"]["layers"]["reply"] == "fail"


def test_verdict_label_unchanged_by_failing_layers(tmp_path):
    rows, _ = regrade(tmp_path, [label(understood={"domain": "wrong-domain"})], [row("k0", GOOD, OK_DIGEST)])
    assert rows["k0"]["layers"]["understood"] == "fail"
    assert rows["k0"]["label"] == "correct"


def test_report_by_layer_counts_and_none_pass_rate(tmp_path):
    cases = [label(understood={"domain": "stock"}), {**case_dict("k1"), "expect": {**case_dict("k1")["expect"], "understood": {"domain": "other"}}}]
    rows = [row("k0", GOOD, OK_DIGEST), row("k1", GOOD, OK_DIGEST)]
    _, rep = regrade(tmp_path, cases, rows)
    bl = rep["by_layer"]
    assert bl["understood"] == {"pass": 1, "fail": 1, "na": 0, "no_trace": 0, "pass_rate": 0.5}
    assert bl["tool"] == {"pass": 0, "fail": 0, "na": 2, "no_trace": 0, "pass_rate": None}
    assert bl["reply"]["pass"] == 2


def test_first_failing_layer(tmp_path):
    def c(cid, **extra):
        d = case_dict(cid)
        d["expect"].update(extra)
        return d

    cases = [
        c("x", understood={"domain": "nope"}, tool={"name": "nope"}),
        c("y", expected_resolved=["ZZZ-9"]),
        c("z"),
        c("w", understood={"domain": "nope"}),  # reply passes: not counted
        c("v", tool={"name": "nope"}),
    ]
    rows = [row("x", BAD, OK_DIGEST), row("y", BAD, OK_DIGEST), row("z", BAD, OK_DIGEST),
            row("w", GOOD, OK_DIGEST), row("v", BAD, OK_DIGEST)]
    _, rep = regrade(tmp_path, cases, rows)
    got = {k: v for k, v in rep["first_failing_layer"].items() if v}
    assert got == {"understood": 1, "resolved": 1, "tool": 1, "reply": 1}


def test_regrade_recomputes_layers_after_label_gains_understood(tmp_path):
    rows1, _ = regrade(tmp_path, [case_dict("k0")], [row("k0", GOOD, OK_DIGEST)], name="n1")
    assert rows1["k0"]["layers"]["understood"] == "na"
    rows2, _ = regrade(tmp_path, [label(understood={"domain": "wrong-domain"})], [row("k0", GOOD, OK_DIGEST)], name="n2")
    assert rows2["k0"]["layers"]["understood"] == "fail"


def test_live_run_rows_carry_layers(tmp_path):
    d, out = tmp_path / "data", tmp_path / "o"
    write_case(d, case_dict("k0"))
    assert main(["--data-dir", str(d), "--engine", "tests.chatbot_eval.fakes:DigestEngine", "--out", str(out)]) == 0
    r = json.loads((out / "results.jsonl").read_text().splitlines()[0])
    assert set(r["layers"]) == {"understood", "resolved", "tool", "reply"}


def test_non_ascii_understood_entity_matches(tmp_path):
    digest = d_with(parser={"domain": "stock", "intent": "check", "entities": ["水龙头 X1"]})
    rows, _ = regrade(tmp_path, [label(understood={"entities": ["水龙头"]})], [row("k0", GOOD, digest)])
    assert rows["k0"]["layers"]["understood"] == "pass"


def test_non_ascii_expected_resolved_matches(tmp_path):
    digest = d_with(resolved=[{"entities": ["Café Sdn Bhd"]}])
    rows, _ = regrade(tmp_path, [label(expected_resolved=["Café"])], [row("k0", GOOD, digest)])
    assert rows["k0"]["layers"]["resolved"] == "pass"


def test_harness_error_result_has_reply_fail_layer(tmp_path):
    d, out = tmp_path / "data", tmp_path / "o"
    write_case(d, case_dict("boom"))
    main(["--data-dir", str(d), "--engine", "tests.chatbot_eval.fakes:ExplodingEngine", "--out", str(out)])
    r = json.loads((out / "results.jsonl").read_text().splitlines()[0])
    assert r["layers"]["reply"] == "fail"
    rep = json.loads((out / "report.json").read_text())
    assert rep["by_layer"]["reply"]["fail"] == 1
    assert rep["first_failing_layer"]["reply"] == 1
