"""Phase 2 RED tests - REPORTS-STEP2 S5a review: the args the lane builds for `crm_report` must pass the
REAL `crm_report` MCP signature, with nothing silently dropped (reviewer B1, B2, B3), for the outstanding
report, the sales report and the S2 ranking path.

The check is on the compiled MCP tool (`sorento_crm_mcp.server._compile_tool`): every arg KEY the lane sent
(private `_...` keys aside) must be a parameter of the tool, and every REQUIRED parameter must be present.
The route half of the same parity (the /report body equals the old route's body for the same legacy args)
is in `tests/reports_step2/test_s5a_parity.py`.

The scenarios are the golden ones of `test_reports_step2_s5a.py` plus: several product codes, a location
token, an outstanding order-date window, a sales ask with no date window, a sales channel, a detail pick
"2". Each scenario also names the arg key it exists to exercise, so a scenario that stops producing it
fails instead of passing vacuously.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.chatbot import test_reports_step2_lane as step2
from tests.chatbot import test_reports_step2_s5a as s5a
from tests.chatbot.test_outstanding_lane import (
    CUSTOMER_NAME,
    CUSTOMER_UUID,
    PRODUCT_CODE,
    PRODUCT_UUID,
    REPORT_HIT,
    _qf,
    _run_turn,
    _seed_contact,
)
from tests.chatbot.test_report_ask_lane import _link_dealer, _parser_output, _rank, _e  # noqa: F401
from tests.chatbot.test_reports_step2_lane import _refine, _s2_harness, _t1  # noqa: F401
from tests.chatbot.test_reports_step2_s5a import _harness  # noqa: F401  (autouse fixture by name)
from tests.chatbot.test_sales_report_drill_lane import ATTRS as SALES_ATTRS
from tests.chatbot.test_sales_report_drill_lane import CUSTOMER_MATCH, _seed_warehouses
from tests.chatbot.test_sales_report_lane import SALES_REPORT_HIT
from tests.chatbot.test_report_ask_lane import console  # noqa: F401  (fixture by name)

OTHER_CODE, OTHER_UUID = "SRTWT7500", "cccccccc-cccc-cccc-cccc-cccccccccccc"
BOTH_PRODUCTS = {
    PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE},
    OTHER_CODE: {"uuid": OTHER_UUID, "entity_type": "product", "canonical_code": OTHER_CODE},
}


#: Generic keys the entity transformer puts on EVERY tool call and no report tool declares (the old tools
#: dropped them too): not a filter, so not a loss.
NOISE = frozenset({"access_levels", "date_mode", "is_active"})


def _signature():
    mcp_root = str(Path(__file__).resolve().parents[3] / "sorento_crm_mcp")
    if mcp_root not in sys.path:
        sys.path.append(mcp_root)
    try:
        from sorento_crm_mcp.catalog import CATALOG
        from sorento_crm_mcp.server import _compile_tool
    except ImportError:  # pragma: no cover
        pytest.skip("sorento_crm_mcp is not importable in this environment")
    spec = next(s for s in CATALOG if s.name == "crm_report")
    params = inspect.signature(_compile_tool(spec)).parameters
    allowed = {n for n in params if n != "ctx"}
    required = {n for n, p in params.items() if n != "ctx" and p.default is inspect._empty}
    return allowed, required


def _check(calls, must_have: tuple[str, ...] = ()):
    allowed, required = _signature()
    sent = [(n, a) for n, a in calls if n == "crm_report"]
    assert sent, calls
    for _name, args in sent:
        keys = {k for k in args if not k.startswith("_") and k not in NOISE}
        assert not keys - allowed, f"silently dropped by the crm_report signature: {sorted(keys - allowed)}"
        assert not required - keys, f"required by the crm_report signature but not sent: {sorted(required - keys)}"
        for key in must_have:
            assert key in keys, (key, sorted(keys))


# ------------------------------------------------------------------ outstanding / sales report


def _outstanding_several_products(sf, mp):
    _seed_contact(sf, variables={})
    ents = [{"raw": c, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
            for c in (PRODUCT_CODE, OTHER_CODE)]
    return _run_turn(sf, mp, qf=_qf(order_status="so_outstanding", entities=ents),
                     text_body="SRTWT7445 and SRTWT7500 sales order outstanding", msg_id="ZZT-par-1",
                     attributes=s5a.OUT_ATTRS, matches=BOTH_PRODUCTS, mcp_response=REPORT_HIT)


def _outstanding_family_codes(sf, mp):
    """One typed prefix resolving to SEVERAL products (no code equals what was typed): the only emission
    for which the lane sends the several-code form `product_codes`."""
    from tests.chatbot.test_sales_report_lane import _family_resolve_services

    _seed_contact(sf, variables={})
    services = _family_resolve_services(token="SRT567", matches=[
        {"entity_type": "product", "canonical_code": "SRT5679", "uuid": "55555555-5555-5555-5555-555555555555"},
        {"entity_type": "product", "canonical_code": "SRT5670", "uuid": "66666666-6666-6666-6666-666666666666"},
    ])
    ents = [{"raw": "SRT567", "hint": "product", "canonical_code": None, "current_message": True,
             "confident": True}]
    return _run_turn(sf, mp, qf=_qf(order_status="so_outstanding", entities=ents),
                     text_body="SRT567 sales order outstanding", msg_id="ZZT-par-1b",
                     attributes=s5a.OUT_ATTRS, resolve_services=services, mcp_response=REPORT_HIT)


def _outstanding_location_token(sf, mp):
    _seed_contact(sf, variables={})
    _seed_warehouses(sf)
    ents = [{"raw": PRODUCT_CODE, "hint": "product", "canonical_code": None, "current_message": True,
             "confident": True},
            {"raw": "IB", "hint": "warehouse", "canonical_code": None, "current_message": True,
             "confident": True}]
    return _run_turn(sf, mp, qf=_qf(order_status="so_outstanding", entities=ents),
                     text_body="SRTWT7445 outstanding at IB", msg_id="ZZT-par-2",
                     attributes=s5a.OUT_ATTRS, matches=s5a.PRODUCT_MATCH, mcp_response=REPORT_HIT)


def _outstanding_order_dates(sf, mp):
    _seed_contact(sf, variables={})
    return _run_turn(
        sf, mp, qf=_qf(order_status="so_outstanding", date_filter_start="2026-07-01",
                       date_filter_end="2026-09-30"),
        text_body="SRTWT7445 sales order outstanding this quarter", msg_id="ZZT-par-3",
        attributes=s5a.OUT_ATTRS, matches=s5a.PRODUCT_MATCH, mcp_response=REPORT_HIT)


def _outstanding_detail_pick_2(sf, mp):
    s5a._seed_open_outstanding_detail(sf)
    return _run_turn(
        sf, mp, qf=_parser_output(message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                                  reference_positions=[2]),
        text_body="2", msg_id="ZZT-par-4", attributes=s5a.OUT_ATTRS, matches=s5a.PRODUCT_MATCH,
        mcp_response=REPORT_HIT)


def _sales_no_window(sf, mp):
    _seed_contact(sf, variables={})
    ents = [{"raw": CUSTOMER_NAME, "hint": "customer", "canonical_code": None, "current_message": True,
             "confident": True}]
    return _run_turn(sf, mp, qf=_qf(order_status="sales_report", entities=ents), text_body="my sales",
                     msg_id="ZZT-par-5", attributes=SALES_ATTRS, matches=CUSTOMER_MATCH,
                     mcp_response=SALES_REPORT_HIT)


def _sales_channel(sf, mp):
    _seed_contact(sf, variables={})
    ents = [{"raw": CUSTOMER_NAME, "hint": "customer", "canonical_code": None, "current_message": True,
             "confident": True}]
    return _run_turn(sf, mp, qf=_qf(order_status="sales_report", entities=ents, sales_channel="project"),
                     text_body="my project sales", msg_id="ZZT-par-6", attributes=SALES_ATTRS,
                     matches=CUSTOMER_MATCH, mcp_response=SALES_REPORT_HIT)


LANE_SCENARIOS = {
    **{k: (f, ()) for k, f in s5a.SCENARIOS.items()},
    # two codes the customer TYPED: the lane keeps the typed winner, one `product_code` (today's behaviour)
    "outstanding_two_typed_codes": (_outstanding_several_products, ("product_code",)),
    "outstanding_family_codes": (_outstanding_family_codes, ("product_codes",)),
    "outstanding_location_token": (_outstanding_location_token, ("location_token", "warehouse_codes")),
    "outstanding_order_dates": (_outstanding_order_dates, ("order_date_from", "order_date_to")),
    "outstanding_detail_pick_2": (_outstanding_detail_pick_2, ("detail",)),
    "sales_no_window": (_sales_no_window, ()),
    "sales_channel": (_sales_channel, ("channel",)),
}
LANE_SCENARIOS["outstanding_so"] = (s5a.SCENARIOS["outstanding_so"], ())
LANE_SCENARIOS["outstanding_no_grant"] = (s5a.SCENARIOS["outstanding_no_grant"], ("so_refused",))
LANE_SCENARIOS["outstanding_detail_pick"] = (s5a.SCENARIOS["outstanding_detail_pick"], ("detail",))


@pytest.mark.parametrize("name", sorted(LANE_SCENARIOS))
def test_review_the_lane_args_pass_the_real_crm_report_signature(name, session_factory, monkeypatch):
    scenario, must_have = LANE_SCENARIOS[name]
    _result, calls = scenario(session_factory, monkeypatch)
    _check(calls, must_have)


def test_review_the_no_so_grant_explicit_so_ask_keeps_its_refusal_line(session_factory, monkeypatch):
    result, calls = LANE_SCENARIOS["outstanding_no_grant"][0](session_factory, monkeypatch)
    _check(calls, ("so_refused",))
    assert "Sales order figures are not enabled for your account." in s5a._reply(result)


# ------------------------------------------------------------------ the S2 ranking path


def _drill_scoped(console_):
    _t1(console_)
    verdict = _parser_output(
        domain_hint="order", intent_hint="check_order", order_status="sales_ranking", entities=[],
        group_by="customer", top_n=10, rank_direction="top", rank_by=None, basis=None, measure=None,
        ranking_refine=False, ranking_drill="scoped", date_filter_start=None, date_filter_end=None)
    return console_.say(verdict, "top 10 customers for these sales agents")


RANKING = {
    "fresh_with_filters": (
        lambda c: c.say(_rank(_e("Sorento", "brand"), _e("ZZT CATEGORY", "category"), _e("AGENT A", "sales_agent"),
                              group_by="customer", top_n=5), "top 5 customers for Sorento"),
        ("brand_ids", "category_ids", "sales_agent_ids", "top_n", "group_by")),
    "refine_with_carried": (lambda c: (_t1(c), c.say(_refine(top_n=5), "top 5"))[1], ("carried",)),
    "drill_with_link_param": (_drill_scoped, ("customer_agent_ids",)),
}


@pytest.mark.parametrize("name", sorted(RANKING))
def test_review_the_ranking_args_pass_the_real_crm_report_signature(name, console):
    run, must_have = RANKING[name]
    _text, calls = run(console)
    _check([("crm_report", a) for a in calls], must_have)


def test_review_a_dealer_ranking_ask_passes_the_real_signature(console):
    _link_dealer(console)
    _text, calls = console.say(_rank(group_by="product", top_n=3), "top 3 products")
    _check([("crm_report", a) for a in calls])
