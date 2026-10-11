import json

from scripts.chatbot_eval.cases import Expect
from scripts.chatbot_eval.grade import Verdict, score_layers
from scripts.chatbot_eval.run import main
from scripts.chatbot_eval.trace import build_digest
from tests.chatbot_eval.fakes import case_dict, write_case

ENV = {
    "tool": "crm_outstanding_report", "tool_ran": True, "entities": ["TESTCODE-1"],
    "lane_ask": {"kind": "outstanding_detail", "filters": {"scope": "do", "product_code": "TESTCODE-1", "customer_ids": []}},
    "sales_ranking_frame": None, "low_stock_frame": None,
}


def lu(*envs):
    return {"stage": "looked_up", "status": "ok", "raw": {"envelopes": list(envs)}}


def tools_of(*envs, extra=()):
    return build_digest([lu(*envs), *extra], "reply", "answer")["tools"]


def test_tool_from_envelope_filters():
    assert {"tool": "crm_outstanding_report", "args": {"scope": "do", "product_code": "TESTCODE-1", "customer_ids": []}} in tools_of(ENV)


def test_sales_ranking_frame_merged_into_args():
    env = {**ENV, "tool": "crm_top_selling", "sales_ranking_frame": {"metric": "qty", "basis": "delivered", "group_by": "product"}}
    t = [x for x in tools_of(env) if x["tool"] == "crm_top_selling"]
    assert len(t) == 1
    assert t[0]["args"]["metric"] == "qty" and t[0]["args"]["group_by"] == "product" and t[0]["args"]["scope"] == "do"


def test_low_stock_frame_merged_and_later_keys_win():
    env = {**ENV, "low_stock_frame": {"scope": "so", "threshold": 5}}
    t = tools_of(env)[0]["args"]
    assert t["threshold"] == 5 and t["scope"] == "so"


def test_not_ran_or_toolless_envelopes_skipped():
    assert tools_of({**ENV, "tool_ran": False}, {**ENV, "tool": None}) == []


def test_probe_entries_still_included():
    probe = {"stage": "probe", "status": "ok", "tool": "crm_probe", "args": {"q": 1}}
    t = tools_of(ENV, extra=[probe])
    assert {"tool": "crm_probe", "args": {"q": 1}} in t
    assert any(x["tool"] == "crm_outstanding_report" for x in t)


def _layer(params):
    e = Expect(outcome="answer", acceptable_alternates=[], must=[], options=[], must_not=[], judge_note=None,
               tool={"name": "crm_outstanding_report", "params": params})
    digest = build_digest([lu(ENV)], "reply", "answer")
    return score_layers(e, digest, Verdict(label="correct", reasons=[], needs_judge=False))["tool"]


def test_tool_layer_passes_on_envelope_params():
    assert _layer({"scope": "do"}) == "pass"


def test_tool_layer_fails_on_wrong_param():
    assert _layer({"scope": "so"}) == "fail"


def test_results_rows_carry_needs_judge(tmp_path):
    d, out = tmp_path / "data", tmp_path / "o"
    write_case(d, case_dict("k0"))
    assert main(["--data-dir", str(d), "--engine", "tests.chatbot_eval.fakes:FakeEngine", "--out", str(out)]) == 0
    row = json.loads((out / "results.jsonl").read_text().splitlines()[0])
    assert isinstance(row["needs_judge"], bool)
