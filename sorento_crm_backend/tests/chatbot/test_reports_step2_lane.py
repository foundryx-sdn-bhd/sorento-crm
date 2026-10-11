"""Phase 2 RED tests - REPORTS-STEP2 S2: the sales ranking lane asks `crm_report`, holds ONE
`Focus.report` entry, refines / switches over it and marks carried facts (AC-11 to AC-18).

`documentation/plans/chatbot/reports-step2-acceptance-criteria.md`, section S2.

Full console turns through the real engine, lane and the WORKTREE's MCP presenter (the venv's
installed `sorento_crm_mcp` points at another checkout, so the fixture below swaps it in);
the parser and the route body are doubles. This file lives in `tests/chatbot/` because the
`session_factory` fixture is defined there. `Console.say` records the calls named `lane_tests.TOOL`,
which the autouse fixture sets to `crm_report`: a lane still calling `crm_report_ask` records nothing.

Pinned by the tester (the UAC does not spell them out):
* `Focus.report` args hold the route params the lane sent MINUS contact_id, space_id, `basis`
  and `measure` (the registry key replaces both); `shown` is `{"group_by", "rows": [{"name","keys"}]}`
  exactly as the old frame's `shown`.
* the focus wire key is `report`; the old key is `sales_ranking_frame` (flat: group_by, basis, measure, ...).
* `carried` is a list of ROUTE PARAM names: `date_from` and `date_to` for the period, `brand_ids` for
  a brand filter, `category_ids`, `customer_ids`, ... (a filter's `<key>_ids` / `warehouse_codes` /
  `channel` / `product_code`).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.chatbot import test_report_ask_lane as lane_tests
from tests.chatbot.test_report_ask_lane import (  # noqa: F401  (fixtures by name)
    DIMENSION_REFUSAL,
    PRODUCT_CODE,
    SEP,
    _e,
    _link_dealer,
    _parser_output,
    _rank,
    _reply,
    console,
)

AG1, AG2 = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
SEP_LAST = {"date_filter_start": "2026-08-01", "date_filter_end": "2026-08-31"}
EARLIER = " (from earlier)"
REPORT = "crm_report"


def _body(args: dict[str, Any]) -> dict[str, Any]:
    """The `/report` body: the old route body plus `measure_key`, `carried` and a Brand filter echo."""
    body = lane_tests.__dict__["_orig_fake_body"](args)
    group_by = args.get("group_by")
    body["rows"] = (
        [
            {"rank": 1, "name": "AGENT ONE", "qty": 30, "amount": 1200.0, "keys": [AG1]},
            {"rank": 2, "name": "AGENT TWO", "qty": 20, "amount": 800.0, "keys": [AG2]},
        ]
        if group_by
        else []
    )
    body["total_count"] = len(body["rows"])
    body["measure_key"] = args.get("measure")
    if args.get("carried") is not None:
        body["carried"] = list(args["carried"])
    body["filters"] = [{"key": "brand", "label": "Brand", "values": ["Sorento"]}] if args.get("brand_ids") else []
    return body


@pytest.fixture(autouse=True)
def _s2_harness(monkeypatch):
    """Record `crm_report` calls and serve the S2 route body. The MCP package comes the way its
    neighbours import it: the repo's own copy appended to `sys.path`, skipped where absent."""
    mcp_root = Path(__file__).resolve().parents[3] / "sorento_crm_mcp"
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    try:
        import sorento_crm_mcp.presenters  # noqa: F401
    except ImportError:  # pragma: no cover
        pytest.skip("sorento_crm_mcp is not importable in this environment")
    monkeypatch.setitem(lane_tests.__dict__, "_orig_fake_body", lane_tests._fake_body)
    monkeypatch.setattr(lane_tests, "_fake_body", _body)
    monkeypatch.setattr(lane_tests, "TOOL", REPORT)
    yield


def _refine(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="sales_ranking", ranking_refine=True,
        entities=[], group_by=None, top_n=None, measure=None, basis=None,
        date_filter_start=None, date_filter_end=None,
    )
    base.update(overrides)
    return _parser_output(**base)


def _find_focus(node: Any) -> dict[str, Any] | None:
    if isinstance(node, dict):
        if "required_ask" in node and "tasks" in node:
            return node
        for v in node.values():
            found = _find_focus(v)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_focus(v)
            if found is not None:
                return found
    return None


def _wire(console) -> dict[str, Any]:
    wire = _find_focus(console.session_vars)
    assert wire is not None, "no focus in the session"
    return wire


def _t1(console, **overrides):
    return console.say(_rank(_e("Sorento", "brand"), top_n=3, **overrides), "top 3 salesman for Sorento last month")


def _held(args: dict[str, Any]) -> dict[str, Any]:
    """A call's args as the held entry stores them."""
    out = {k: v for k, v in args.items() if k not in ("contact_id", "space_id", "basis", "measure", "carried")}
    return out


# ================================================================================ AC-11


def test_ac11_a_fresh_ranking_calls_crm_report_with_the_registry_key(console) -> None:
    text, calls = _t1(console)
    (args,) = calls
    assert args["measure"] == "delivered_amount", args
    assert "basis" not in args and "carried" not in args, args
    assert args["group_by"] == "sales_agent" and args["top_n"] == 3 and args["brand_ids"], args
    assert "AGENT ONE" in text and EARLIER not in text, text


@pytest.mark.parametrize(
    "kw, key", [({}, "delivered_amount"), ({"measure": "qty"}, "delivered_qty"), ({"basis": "ordered"}, "ordered_amount"),
                ({"basis": "outstanding", "measure": "qty"}, "outstanding_qty")],
)
def test_ac11_the_key_is_basis_underscore_figure(console, kw, key) -> None:
    _text, calls = console.say(_rank(_e("Sorento", "brand"), **kw), "top 3 salesman")
    (args,) = calls
    assert args["measure"] == key, args


def test_ac11_the_reply_equals_the_crm_report_ask_path_text(console) -> None:
    from sorento_crm_mcp.presenters import present_response

    text, calls = _t1(console)
    (args,) = calls
    old_body = lane_tests.__dict__["_orig_fake_body"](args)
    old_body.update(basis=args["measure"].split("_")[0], measure=args["measure"].split("_")[1])
    old_body["rows"] = _body(args)["rows"]
    old_body["total_count"] = len(old_body["rows"])
    old_body["filters"] = _body(args)["filters"]
    expected = json.loads(present_response("crm_report_ask", json.dumps(old_body)))["response"]
    assert text.strip().endswith(expected.strip()) or expected.strip() in text, (text, expected)


# ================================================================================ AC-12 / AC-13


def test_ac12_after_a_ranking_focus_report_holds_key_args_and_shown(console) -> None:
    _t1(console)
    wire = _wire(console)
    report = wire.get("report")
    assert isinstance(report, dict), wire
    assert report["measure"] == "delivered_amount", report
    args = report["args"]
    assert args["group_by"] == "sales_agent" and args["top_n"] == 3 and args["sort"] == "desc", args
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30" and args["brand_ids"], args
    for gone in ("contact_id", "space_id", "basis", "measure"):
        assert gone not in args, args
    assert report["shown"]["group_by"] == "sales_agent", report
    assert [r["name"] for r in report["shown"]["rows"]] == ["AGENT ONE", "AGENT TWO"], report


def test_ac12_the_old_frame_key_is_no_longer_written(console) -> None:
    _t1(console)
    assert _wire(console).get("report"), "Focus.report not written"
    assert not _wire(console).get("sales_ranking_frame"), _wire(console)


def test_ac12_focus_report_round_trips_through_the_wire() -> None:
    from app.services.chatbot.turn.state import Focus, focus_from_wire, focus_to_wire

    report = {"measure": "ordered_qty", "args": {"group_by": "product", "top_n": 5}, "shown": None}
    wire = focus_to_wire(Focus(report=report))
    assert wire["report"] == report and "sales_ranking_frame" not in wire or not wire.get("sales_ranking_frame")
    assert focus_from_wire(wire).report == report


def test_ac13_a_stored_old_frame_reads_as_a_report_entry() -> None:
    from app.services.chatbot.turn.state import focus_from_wire, focus_to_wire

    old = {"group_by": "customer", "basis": "ordered", "measure": "qty", "sort": "desc", "top_n": 3,
           "date_from": "2026-09-01", "date_to": "2026-09-30", "brand_ids": ["b1"]}
    focus = focus_from_wire({"sales_ranking_frame": old})
    assert focus.report is not None and focus.report["measure"] == "ordered_qty", focus.report
    args = focus.report["args"]
    assert args["group_by"] == "customer" and args["top_n"] == 3 and args["brand_ids"] == ["b1"], args
    assert "basis" not in args and "measure" not in args, args
    assert focus_to_wire(focus).get("report"), "the mapped entry is not written back as report"


def test_ac13_top_5_on_an_old_session_reruns_the_held_args_through_crm_report(console) -> None:
    _t1(console)
    wire = _wire(console)
    held = wire.pop("report")
    wire["sales_ranking_frame"] = {
        **held["args"], "basis": "delivered", "measure": "amount",
    }
    _text, calls = console.say(_refine(top_n=5), "top 5")
    (args,) = calls
    assert args["measure"] == "delivered_amount" and args["top_n"] == 5, args
    assert args["group_by"] == "sales_agent" and args["brand_ids"] == held["args"]["brand_ids"], args
    assert args["date_from"] == "2026-09-01", args


# ================================================================================ AC-14


def _refine_cases():
    return [
        ("top5", dict(top_n=5), {"top_n": 5}),
        ("qty", dict(measure="qty"), {"measure": "delivered_qty"}),
        ("ordered", dict(basis="ordered"), {"measure": "ordered_amount"}),
        ("dealer", dict(sales_channel="dealer"), {"channel": "dealer"}),
        ("lastmonth", SEP_LAST, {"date_from": "2026-08-01", "date_to": "2026-08-31"}),
    ]


@pytest.mark.parametrize("name, verdict, patch", _refine_cases(), ids=[c[0] for c in _refine_cases()])
def test_ac14_a_refine_patches_only_what_the_message_changes(console, name, verdict, patch) -> None:
    _text, first = _t1(console)
    (before,) = first
    _text, calls = console.say(_refine(**verdict), name)
    (after,) = calls
    expected = {**before, **patch}
    expected.pop("carried", None)
    got = {k: v for k, v in after.items() if k != "carried"}
    assert got == expected, (got, expected)


def test_ac14_a_category_word_adds_that_filter_and_keeps_the_rest(console) -> None:
    _text, first = _t1(console)
    (before,) = first
    _text, calls = console.say(_refine(entities=[_e("ZZT CATEGORY", "category")]), "only ZZT CATEGORY")
    (after,) = calls
    assert after["category_ids"] == [console.ids["category"]], after
    rest = {k: v for k, v in after.items() if k not in ("category_ids", "carried")}
    assert rest == {k: v for k, v in before.items() if k != "carried"}, (rest, before)


# ================================================================================ AC-15 (lane half)


def test_ac15_a_fresh_ask_carries_nothing(console) -> None:
    text, calls = _t1(console)
    assert "carried" not in calls[0] and EARLIER not in text, (calls, text)


def test_ac15_a_refine_names_the_carried_params_and_marks_those_lines(console) -> None:
    _t1(console)
    text, calls = console.say(_refine(top_n=5), "top 5")
    (args,) = calls
    carried = set(args["carried"])
    assert {"date_from", "date_to", "brand_ids"} <= carried, args
    assert not carried & {"top_n", "group_by", "sort"}, args
    lines = text.splitlines()
    assert any(l.startswith("Period:") and l.endswith(EARLIER) for l in lines), text
    assert any(l.startswith("Brand:") and l.endswith(EARLIER) for l in lines), text
    assert " · " not in text, text


def test_ac15_a_value_said_in_this_message_is_not_marked(console) -> None:
    _t1(console)
    text, calls = console.say(_refine(**SEP_LAST), "last month")
    (args,) = calls
    assert "date_from" not in args["carried"] and "date_to" not in args["carried"], args
    assert "brand_ids" in args["carried"], args
    period = next(l for l in text.splitlines() if l.startswith("Period:"))
    assert not period.endswith(EARLIER), text
    assert next(l for l in text.splitlines() if l.startswith("Brand:")).endswith(EARLIER), text


# ================================================================================ AC-16


def test_ac16_a_new_axis_global_ask_starts_a_fresh_entry(console) -> None:
    _t1(console)
    text, calls = console.say(
        _rank(group_by="customer", top_n=5, ranking_drill="global", **SEP_LAST), "top 5 customers last month"
    )
    (args,) = calls
    assert args["group_by"] == "customer", args
    assert "brand_ids" not in args and "carried" not in args, args
    assert EARLIER not in text, text
    report = _wire(console)["report"]
    assert report["args"]["group_by"] == "customer" and "brand_ids" not in report["args"], report


# ================================================================================ AC-17


def test_ac17_a_dealer_refine_to_the_dealer_channel_is_refused_and_nothing_runs(console) -> None:
    _link_dealer(console)
    _text, calls = console.say(_rank(group_by="product", top_n=3), "top 3 products")
    assert len(calls) == 1, calls
    text, calls = console.say(_refine(sales_channel="dealer"), "only dealer")
    assert text.strip() == DIMENSION_REFUSAL and calls == [], (text, calls)


def test_ac17_a_dealer_refine_over_a_held_staff_dimension_is_refused(console) -> None:
    _link_dealer(console)
    console.say(_rank(group_by="product", top_n=3), "top 3 products")
    wire = _wire(console)
    assert wire.get("report"), "Focus.report not written"
    wire["report"]["args"]["sales_agent_ids"] = [AG1]
    text, calls = console.say(_refine(top_n=5), "top 5")
    assert text.strip() == DIMENSION_REFUSAL and calls == [], (text, calls)


# ================================================================================ AC-18


def test_ac18_a_turn_that_is_not_a_report_ask_clears_the_entry(console) -> None:
    _t1(console)
    assert _wire(console).get("report"), "Focus.report not written"
    console.say(_reply(domain_hint="master_products", intent_hint="check_product"), "what is the weather")
    assert not _wire(console).get("report"), _wire(console)
    text, calls = console.say(_refine(top_n=5), "top 5")
    assert calls == [], calls
