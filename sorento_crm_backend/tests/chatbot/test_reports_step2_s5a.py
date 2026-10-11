"""Phase 2 RED tests - REPORTS-STEP2 S5a: the lane calls `crm_report` for the outstanding report and the
delivered sales report, with replies byte-identical to the old tools' (AC-24 to AC-26).

How "byte-identical" is pinned: `GOLDEN` below holds, per scenario, the reply text and the MCP call args the
lane produced BEFORE the switch (captured by running each scenario against the old lane, fake data, the real
worktree presenter). After the switch the same parser emission and the same route data must give the SAME
reply and the SAME args, except the tool name (`crm_report`) and the added `measure` key. The fake route
below answers `crm_report` with the old route body plus `report_kind`, which the presenter renders with the
old kind's presenter (S4).

Reuses the harness of `test_outstanding_lane.py` / `test_sales_report_lane.py` / `test_sales_report_drill_lane.py`.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.chatbot import test_outstanding_lane as ol
from tests.chatbot.test_engine import _parser_output
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
from tests.chatbot.test_sales_report_drill_lane import ATTRS as SALES_ATTRS
from tests.chatbot.test_sales_report_drill_lane import CUSTOMER_MATCH, _ask, _pick
from tests.chatbot.test_sales_report_lane import SALES_REPORT_HIT, _seed_open_detail
from tests.chatbot.test_outstanding_lane import _seed_open_outstanding_detail

OLD = {"crm_outstanding_report": "order_outstanding", "crm_sales_report": "delivered_sales"}
KIND = {"order_outstanding": "outstanding", "delivered_sales": "sales_report"}
PRODUCT_MATCH = {PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}}
OUT_ATTRS = ["sales_orders.outstanding"]


@pytest.fixture(autouse=True)
def _harness(monkeypatch):
    """The repo MCP package on the path (the repo idiom), and a fake route that serves BOTH the old
    tool names and `crm_report` (the old body plus `report_kind`, rendered by the presenter of its kind)."""
    mcp_root = Path(__file__).resolve().parents[3] / "sorento_crm_mcp"
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    try:
        import sorento_crm_mcp.presenters  # noqa: F401
    except ImportError:  # pragma: no cover
        pytest.skip("sorento_crm_mcp is not importable in this environment")
    original = ol._capturing_mcp

    def _capturing(response: Any = None):
        captured: list[tuple[str, dict[str, Any]]] = []
        inner, _unused = original(response)

        def _call(name: str, args: dict[str, Any]) -> Any:
            captured.append((name, dict(args)))
            if name != "crm_report" or not isinstance(response, dict):
                return inner(name, args)
            measure = args.get("measure")
            old = {"order_outstanding": "crm_outstanding_report", "delivered_sales": "crm_sales_report"}.get(measure)
            if old is None:
                return json.dumps({"has_result": False, "items": []})
            # Same route body the old branch builds, then the presenter of the `report_kind`.
            envelope = json.loads(inner(old, {k: v for k, v in args.items() if k != "measure"}))
            return json.dumps(envelope)

        return _call, captured

    monkeypatch.setattr(ol, "_capturing_mcp", _capturing)


def _reply(result) -> str:
    return (result.reply or {}).get("text") or ""


def _scenario_outstanding_so(sf, mp):
    _seed_contact(sf, variables={})
    result, calls = _run_turn(
        sf, mp, qf=_qf(order_status="so_outstanding"), text_body="SRTWT7445 sales order outstanding",
        msg_id="ZZT-s5a-o1", attributes=OUT_ATTRS, matches=PRODUCT_MATCH, mcp_response=REPORT_HIT)
    return result, calls


def _scenario_outstanding_do(sf, mp):
    _seed_contact(sf, variables={})
    return _run_turn(
        sf, mp, qf=_qf(order_status="do_outstanding"), text_body="SRTWT7445 delivery order outstanding",
        msg_id="ZZT-s5a-o2", attributes=OUT_ATTRS, matches=PRODUCT_MATCH, mcp_response=REPORT_HIT)


def _scenario_outstanding_no_grant(sf, mp):
    _seed_contact(sf, variables={})
    return _run_turn(
        sf, mp,
        qf=_qf(order_status="so_outstanding", requested_attributes=["quantity"], entities=[
            {"raw": CUSTOMER_NAME, "hint": "customer", "canonical_code": None, "current_message": True,
             "confident": True}]),
        text_body="outstanding SO for Dealer A", msg_id="ZZT-s5a-o3", attributes=[],
        matches={CUSTOMER_NAME: {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME}},
        mcp_response=REPORT_HIT)


def _scenario_outstanding_detail_pick(sf, mp):
    _seed_open_outstanding_detail(sf)
    return _run_turn(
        sf, mp,
        qf=_parser_output(message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                          reference_positions=[1]),
        text_body="1", msg_id="ZZT-s5a-o4", attributes=OUT_ATTRS, matches=PRODUCT_MATCH, mcp_response=REPORT_HIT)


def _scenario_sales_fresh(sf, mp):
    _seed_contact(sf, variables={})
    return _run_turn(
        sf, mp, qf=_qf(order_status="sales_report"), text_body="sales report for SRTWT7445",
        msg_id="ZZT-s5a-s1", attributes=SALES_ATTRS, matches=PRODUCT_MATCH, mcp_response=SALES_REPORT_HIT)


def _scenario_sales_drill_pick(sf, mp):
    _ask(sf, mp, msg_id="ZZT-s5a-s2a")
    return _pick(sf, mp, position=1, msg_id="ZZT-s5a-s2b")


def _scenario_sales_detail_pick_old_session(sf, mp):
    """A session stored before the switch still names `crm_sales_report` in its filters."""
    _seed_open_detail(sf, "sales_report_detail")
    return _run_turn(
        sf, mp,
        qf=_parser_output(message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                          reference_positions=[1]),
        text_body="1", msg_id="ZZT-s5a-s3", attributes=SALES_ATTRS, matches=PRODUCT_MATCH,
        mcp_response=SALES_REPORT_HIT)


SCENARIOS = {
    "outstanding_so": _scenario_outstanding_so,
    "outstanding_do": _scenario_outstanding_do,
    "outstanding_no_grant": _scenario_outstanding_no_grant,
    "outstanding_detail_pick": _scenario_outstanding_detail_pick,
    "sales_fresh": _scenario_sales_fresh,
    "sales_drill_pick": _scenario_sales_drill_pick,
    "sales_detail_pick_old_session": _scenario_sales_detail_pick_old_session,
}
MEASURE_OF = {
    "outstanding_so": "order_outstanding", "outstanding_do": "order_outstanding",
    "outstanding_no_grant": "order_outstanding", "outstanding_detail_pick": "order_outstanding",
    "sales_fresh": "delivered_sales", "sales_drill_pick": "delivered_sales",
    "sales_detail_pick_old_session": "delivered_sales",
}

#: Captured from the lane BEFORE the switch: {scenario: {"reply": str, "args": [call args, ...]}}.
GOLDEN: dict[str, Any] = {'outstanding_detail_pick': {'args': [{'_diagnostics': {'entities_in': 1,
                                                        'scalar_truncated': [],
                                                        'skipped': [],
                                                        'total_uuids_passed': 1,
                                                        'unmapped_types': []},
                                       'access_levels': [],
                                       'contact_id': '900000008',
                                       'date_mode': None,
                                       'detail': 'so',
                                       'is_active': None,
                                       'product_code': 'SRTWT7445',
                                       'scope': 'so',
                                       'space_id': '364817',
                                       'view': 'render'}],
                             'reply': 'Product: SRTWT7445\n'
                                      'Customer: all\n'
                                      'Location: all\n'
                                      'Order date: all\n'
                                      '\n'
                                      '1. *SO Number:* SO1\n'
                                      '*Customer:* Dealer A Sdn Bhd\n'
                                      '*Product:* Unassigned\n'
                                      '*Location:* BRW-IB\n'
                                      '*Ordered:* 10\n'
                                      '*Transferred to DO:* 3\n'
                                      '*Outstanding:* 7\n'
                                      '*Order Date:* 01/01/2026'},
 'outstanding_do': {'args': [{'_diagnostics': {'entities_in': 1,
                                               'scalar_truncated': [],
                                               'skipped': [],
                                               'total_uuids_passed': 1,
                                               'unmapped_types': []},
                              'access_levels': [],
                              'contact_id': '900000008',
                              'date_mode': None,
                              'is_active': None,
                              'product_code': 'SRTWT7445',
                              'scope': 'do',
                              'space_id': '364817',
                              'view': 'render'}],
                    'reply': 'Product: SRTWT7445\n'
                             'Customer: all\n'
                             'Location: all\n'
                             'Order date: all\n'
                             '\n'
                             '*Delivery order outstanding*\n'
                             'Delivery orders: 1\n'
                             'DO qty: 10\n'
                             'Delivered: 3\n'
                             'Outstanding: 7\n'
                             'DO date range: 03/02/2026\n'
                             '*_By location_*\n'
                             'BRW-IB: 10 (O/S: 7)\n'
                             '*_By customer_*\n'
                             'Dealer A Sdn Bhd: 10 (O/S: 7)\n'
                             '\n'
                             'Reply 1 for the delivery order list.'},
 'outstanding_no_grant': {'args': [{'_diagnostics': {'entities_in': 1,
                                                     'scalar_truncated': [],
                                                     'skipped': [],
                                                     'total_uuids_passed': 1,
                                                     'unmapped_types': []},
                                    'access_levels': [],
                                    'contact_id': '900000008',
                                    'customer_ids': ['bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'],
                                    'date_mode': None,
                                    'is_active': None,
                                    'scope': 'do',
                                    'so_refused': True,
                                    'space_id': '364817',
                                    'view': 'render'}],
                          'reply': 'Product: SRTWT7445\n'
                                   'Customer: all\n'
                                   'Location: all\n'
                                   'Order date: all\n'
                                   '\n'
                                   'Sales order figures are not enabled for your account.\n'
                                   '\n'
                                   '*Delivery order outstanding*\n'
                                   'Delivery orders: 1\n'
                                   'DO qty: 10\n'
                                   'Delivered: 3\n'
                                   'Outstanding: 7\n'
                                   'DO date range: 03/02/2026\n'
                                   '*_By location_*\n'
                                   'BRW-IB: 10 (O/S: 7)\n'
                                   '*_By customer_*\n'
                                   'Dealer A Sdn Bhd: 10 (O/S: 7)\n'
                                   '\n'
                                   'Reply 1 for the delivery order list.'},
 'outstanding_so': {'args': [{'_diagnostics': {'entities_in': 1,
                                               'scalar_truncated': [],
                                               'skipped': [],
                                               'total_uuids_passed': 1,
                                               'unmapped_types': []},
                              'access_levels': [],
                              'contact_id': '900000008',
                              'date_mode': None,
                              'is_active': None,
                              'product_code': 'SRTWT7445',
                              'scope': 'so',
                              'space_id': '364817',
                              'view': 'render'}],
                    'reply': 'Product: SRTWT7445\n'
                             'Customer: all\n'
                             'Location: all\n'
                             'Order date: all\n'
                             '\n'
                             '*Sales order outstanding*\n'
                             'Sales orders: 1\n'
                             'Ordered: 10\n'
                             'Transferred to DO: 3\n'
                             'Outstanding: 7\n'
                             'Order date range: 01/01/2026\n'
                             '*_By location_*\n'
                             'BRW-IB: 10 (O/S: 7)\n'
                             '*_By customer_*\n'
                             'Dealer A Sdn Bhd: 10 (O/S: 7)\n'
                             '\n'
                             'Reply 1 for the sales order list.'},
 'sales_detail_pick_old_session': {'args': [{'_diagnostics': {'entities_in': 1,
                                                              'scalar_truncated': [],
                                                              'skipped': [],
                                                              'total_uuids_passed': 1,
                                                              'unmapped_types': []},
                                             'access_levels': [],
                                             'contact_id': '900000008',
                                             'date_from': '2026-01-01',
                                             'date_mode': None,
                                             'date_to': '2026-12-31',
                                             'group_by': 'product',
                                             'is_active': None,
                                             'product_code': 'SRTWT7445',
                                             'space_id': '364817',
                                             'view': 'render'}],
                                   'reply': '*By product*, delivery date all\n'
                                            '*Total:* Qty 10, RM 100.00\n'
                                            '\n'
                                            '*Drill down:*\n'
                                            '1. Delivery orders'},
 'sales_drill_pick': {'args': [{'_diagnostics': {'entities_in': 1,
                                                 'scalar_truncated': [],
                                                 'skipped': [],
                                                 'total_uuids_passed': 1,
                                                 'unmapped_types': []},
                                'access_levels': [],
                                'contact_id': '900000008',
                                'customer_ids': ['bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'],
                                'date_mode': None,
                                'group_by': 'product',
                                'is_active': None,
                                'space_id': '364817',
                                'view': 'render'}],
                      'reply': '*By product*, delivery date all\n'
                               '*Total:* Qty 10, RM 100.00\n'
                               '\n'
                               '*Drill down:*\n'
                               '1. Delivery orders'},
 'sales_fresh': {'args': [{'_diagnostics': {'entities_in': 1,
                                            'scalar_truncated': [],
                                            'skipped': [],
                                            'total_uuids_passed': 1,
                                            'unmapped_types': []},
                           'access_levels': [],
                           'contact_id': '900000008',
                           'date_from': '2026-01-01',
                           'date_mode': None,
                           'date_to': '2026-12-31',
                           'is_active': None,
                           'product_code': 'SRTWT7445',
                           'space_id': '364817',
                           'view': 'render'}],
                 'reply': '*Customer:* Dealer A Sdn Bhd\n'
                          '*Product:* all\n'
                          '*Delivery date:* all\n'
                          '\n'
                          '*Total:* Qty 10, RM 100.00\n'
                          'Sep 2026: Qty 10, RM 100.00\n'
                          '\n'
                          '*Drill down:*\n'
                          '1. By product\n'
                          '2. Delivery orders'}}


@pytest.mark.skipif(not os.environ.get("S5A_DUMP"), reason="golden capture helper")
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_dump_golden(name, session_factory, monkeypatch):  # pragma: no cover
    result, calls = SCENARIOS[name](session_factory, monkeypatch)
    out = Path(os.environ["S5A_DUMP"])
    data = json.loads(out.read_text()) if out.exists() else {}
    data[name] = {"reply": _reply(result), "args": [a for _n, a in calls]}
    out.write_text(json.dumps(data, indent=1, sort_keys=True))


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_ac24_the_lane_calls_crm_report_with_the_measure_key(name, session_factory, monkeypatch):
    _result, calls = SCENARIOS[name](session_factory, monkeypatch)
    names = [n for n, _a in calls]
    assert names and set(names) == {"crm_report"}, names
    assert all(a.get("measure") == MEASURE_OF[name] for _n, a in calls), calls


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_ac24_the_args_are_todays_args_plus_the_measure(name, session_factory, monkeypatch):
    assert name in GOLDEN, "golden not captured"
    _result, calls = SCENARIOS[name](session_factory, monkeypatch)
    got = [{k: v for k, v in a.items() if k != "measure"} for _n, a in calls]
    assert got == GOLDEN[name]["args"]


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_ac24_ac25_the_reply_is_byte_identical_to_the_old_tools(name, session_factory, monkeypatch):
    assert name in GOLDEN, "golden not captured"
    result, _calls = SCENARIOS[name](session_factory, monkeypatch)
    assert _reply(result) == GOLDEN[name]["reply"]


def test_ac26_a_contact_without_the_outstanding_grant_still_gets_the_do_only_reply(session_factory, monkeypatch):
    result, calls = _scenario_outstanding_no_grant(session_factory, monkeypatch)
    assert calls and calls[0][0] == "crm_report", calls
    assert calls[0][1].get("scope") == "do" and calls[0][1].get("measure") == "order_outstanding", calls
    assert "Sales order figures are not enabled for your account." in _reply(result), _reply(result)
