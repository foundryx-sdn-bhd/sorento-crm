"""PV2-EVAL round 9: the judge facts hold everything the reply was built from. Fakes only."""
from __future__ import annotations

from scripts.pv2_battery import runner

LOOKED = {"stage": "looked_up", "facts": {"n": 1},
          "raw": {"rows": [{"doc": "ORDER 202610-0151", "code": "CKS1052-BL"}], "lane_text": "LANETEXT_MARK",
                  "miss": ["MISS_MARK"], "unresolved_suggestions": ["SUGG_MARK"]}}
CROSS = {"kind": "crossdomain", "rung": 2, "tool": "x", "rows": [{"po": "202606-S0105"}],
         "rendered": "PO 202606-S0105 CHAOZHOU"}
ACCESS = {"stage": "access", "facts": {"attributes": {"sales_orders.outstanding": True}}}
TOOL = {"kind": "tool", "name": "stock_check", "args": {}, "envelope": "TOOLENV_MARK"}
PARSE = {"kind": "pv2_turn", "parser_out": {"asks": [{"act": "PARSEOUT_MARK"}]}}


def test_looked_up_raw_is_in_the_digest():
    d = runner.facts_digest([LOOKED])
    for needle in ("ORDER 202610-0151", "CKS1052-BL", "LANETEXT_MARK", "MISS_MARK", "SUGG_MARK"):
        assert needle in d, needle


def test_crossdomain_event_is_in_the_digest():
    d = runner.facts_digest([CROSS])
    assert "CHAOZHOU" in d and "202606-S0105" in d


def test_access_attributes_are_in_the_digest():
    assert "sales_orders.outstanding" in runner.facts_digest([ACCESS])
    raw_only = {"stage": "access", "raw": {"attributes": {"stock.view": True}}}
    assert "stock.view" in runner.facts_digest([raw_only])


def test_all_parts_present_together():
    d = runner.facts_digest([ACCESS, LOOKED, CROSS, PARSE, TOOL])
    for needle in ("TOOLENV_MARK", "LANETEXT_MARK", "CHAOZHOU", "PARSEOUT_MARK", "sales_orders.outstanding"):
        assert needle in d, needle


def test_tool_envelopes_come_first_and_access_last():
    d = runner.facts_digest([ACCESS, LOOKED, CROSS, PARSE, TOOL])
    order = [d.index(m) for m in ("TOOLENV_MARK", "LANETEXT_MARK", "CHAOZHOU", "PARSEOUT_MARK", "sales_orders.outstanding")]
    assert order == sorted(order)


def test_cap_and_cut_marker_still_hold():
    huge = "q" * 300000
    trace = [dict(LOOKED, raw={"lane_text": huge}), dict(CROSS, rendered=huge),
             {"kind": "tool", "name": "t", "args": {}, "envelope": huge}, ACCESS]
    d = runner.facts_digest(trace)
    assert len(d) <= runner.FACTS_LIMIT + 40
    assert "(cut)" in d[-40:]
    assert '"tool": "t"' in d or "tool" in d[:200]
