"""DO-ASK-SIMPLIFY rules 3 and 4: a dealer's DO list ask carries a range of at most 31 days.

`documentation/plans/chatbot/PLAN-do-ask-simplify-2oct.md`. Owner, 2 Oct 2026:

* Q1 (a): the cap is 31 days, inclusive, rolling (not "same calendar month").
* Q2 (b): dealers only. A dealer is a contact linked to a customer and holding no office
  access type (`contact_customer_scope(...).enforced`), behind ONE helper
  (`do_ask.is_dealer`) the ACCESS-MODEL lane later swaps for the Dealer role.
* Q3 (a): a quantity ask on the same tools is exempt.
* Naming one or more DO/SO/order numbers needs no range.

No range: nothing is fetched and the bot asks which period, with suggestions. Too long:
nothing is fetched and the bot says so, with suggestions. Driven through `run_fetch`, the
lane seam, with a stub MCP that records every call. Today is pinned to Fri 2 Oct 2026.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from app.services.chatbot import do_ask
from app.services.chatbot.lanes import business
from app.services.chatbot.lanes.business.services import FetchServices

_CID = "6b52807a-537b-437d-9f55-12f7fda29df8"
_ORDER_ROWS = json.dumps(
    {
        "intro": "Here are the orders I found.",
        "items": [{"title": "202609-0916", "fields": [{"label": "Order Number", "value": "202609-0916"}]}],
        "has_result": True,
    }
)


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(do_ask, "today_myt", lambda: date(2026, 10, 2))


def _run(*, start=None, end=None, dealer=True, entities=None, requested=None, order_status=None):
    calls: list[str] = []

    def mcp_call(name, args):
        calls.append(name)
        return _ORDER_ROWS

    output = {
        "message_type": "business_query",
        "domain_hint": "order",
        "date_filter_start": start,
        "date_filter_end": end,
        "requested_attributes": requested or [],
        "order_status": order_status,
    }
    payload = {
        "_exit_kind": "continue",
        "gate": {
            "compatible_entities": entities
            if entities is not None
            else [
                {
                    "uuid": _CID,
                    "entity_type": "customer",
                    "code": "300-H001",
                    "display_name": "HANLIM TRADING SDN BHD [A/C I]",
                }
            ]
        },
        "ctx": {
            "parse": {"output": output},
            "contact": {"id": "437264483"},
            "customer_scope": {
                "enforced": dealer,
                "ids": [_CID] if dealer else [],
                "linked": [[_CID, "HANLIM TRADING SDN BHD [A/C I]", "300-H001"]] if dealer else [],
            },
        },
    }
    fragment = business.run_fetch(payload, services=FetchServices(mcp_call=mcp_call), dry_run=False)
    return json.dumps(fragment, default=str), calls


_ORDER_TOOLS = {"crm_order_management_orders_list", "crm_order_management_orders_by_product_list"}


def _fetched(calls: list[str]) -> bool:
    return bool(set(calls) & _ORDER_TOOLS)


# --- rule 3: no range ------------------------------------------------------------------ #


def test_a_dealer_ask_with_no_range_fetches_nothing_and_asks_which_period():
    said, calls = _run()
    assert not _fetched(calls), calls
    assert "Which period" in said, said
    assert "This month (Oct 2026)" in said, said
    assert "Last month (Sep 2026)" in said, said


def test_the_period_question_names_the_customer_once():
    said, _calls = _run()
    assert "Which period for HANLIM TRADING SDN BHD [A/C I]?" in said, said


def test_naming_an_order_number_needs_no_range():
    said, calls = _run(entities=[{"uuid": _CID, "entity_type": "order", "code": "202609-0916"}])
    assert _fetched(calls), said


def test_a_non_dealer_ask_with_no_range_fetches_nothing_and_asks_which_period():
    """Owner ruling 4 Oct 2026: the range rules apply to ALL contacts, not just dealers."""
    said, calls = _run(dealer=False)
    assert not _fetched(calls), calls
    assert "Which period" in said, said
    assert "This month (Oct 2026)" in said, said


def test_a_quantity_ask_needs_no_range():
    said, calls = _run(requested=["quantity"])
    assert _fetched(calls), said


# --- rule 4: at most 31 days ------------------------------------------------------------ #


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-10-01", "2026-10-31"),  # this month
        ("2026-09-21", "2026-09-27"),  # last week
        ("2026-01-01", "2026-01-31"),  # January, 31 days
        ("2026-09-15", "2026-10-10"),  # cross-month, 26 days
        ("2025-12-20", "2026-01-10"),  # year boundary, 22 days
        ("2026-10-02", "2026-10-02"),  # one day
    ],
)
def test_a_range_of_31_days_or_less_is_fetched(start, end):
    said, calls = _run(start=start, end=end)
    assert _fetched(calls), said


def test_january_to_june_is_refused_with_suggestions():
    said, calls = _run(start="2026-01-01", end="2026-06-30")
    assert not _fetched(calls), calls
    assert "6 months" in said and "01/01/2026 to 30/06/2026" in said, said
    assert "Jun 2026" in said and "Jan 2026" in said, said


def test_january_and_february_is_refused():
    said, calls = _run(start="2026-01-01", end="2026-02-28")
    assert not _fetched(calls), calls
    assert "2 months" in said, said


def test_suggestions_never_name_a_future_month():
    said, calls = _run(start="2026-01-01", end="2026-12-31")
    assert not _fetched(calls), calls
    assert "Oct 2026" in said and "Jan 2026" in said, said
    assert "Dec 2026" not in said, said


def test_32_days_is_refused():
    said, calls = _run(start="2026-09-01", end="2026-10-02")
    assert not _fetched(calls), calls


def test_a_start_with_no_end_is_measured_to_today():
    said, calls = _run(start="2026-08-01")
    assert not _fetched(calls), calls
    assert "01/08/2026 to 02/10/2026" in said, said


def test_a_start_with_no_end_inside_31_days_is_fetched():
    said, calls = _run(start="2026-09-20")
    assert _fetched(calls), said


def test_a_non_dealer_january_to_june_is_refused_too():
    """Owner ruling 4 Oct 2026: the 31-day cap applies to ALL contacts."""
    said, calls = _run(start="2026-01-01", end="2026-06-30", dealer=False)
    assert not _fetched(calls), calls
    assert "6 months" in said and "01/01/2026 to 30/06/2026" in said, said


# --- review round 1 ---------------------------------------------------------------------- #


def test_an_outstanding_bucket_ask_needs_no_range():
    """S3: the outstanding bucket lists DOs not yet delivered, which have no delivery date
    to range over."""
    said, calls = _run(order_status="outstanding", entities=[])
    assert "crm_order_management_orders_list" in calls, (calls, said)


def test_a_range_that_is_not_whole_months_is_said_in_days():
    said, _calls = _run(start="2026-09-01", end="2026-10-02")
    assert "That is 32 days (01/09/2026 to 02/10/2026)" in said, said


def test_whole_months_are_said_in_months():
    said, _calls = _run(start="2026-01-01", end="2026-06-30")
    assert "That is 6 months (01/01/2026 to 30/06/2026)" in said, said


def test_a_future_range_suggests_only_the_current_month():
    said, calls = _run(start="2026-11-01", end="2026-12-31")
    assert not _fetched(calls), calls
    assert "- Oct 2026" in said and "Nov 2026\\n" not in said and "- Nov 2026" not in said, said


def test_swapped_dates_are_measured_the_right_way_round():
    said, calls = _run(start="2026-06-30", end="2026-01-01")
    assert not _fetched(calls), calls
    assert "6 months (01/01/2026 to 30/06/2026)" in said, said
