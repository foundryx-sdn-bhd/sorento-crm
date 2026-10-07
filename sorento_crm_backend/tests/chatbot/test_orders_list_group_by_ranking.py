"""REPORT-ENGINE regression: a ranking's `group_by` never reaches the orders list.

The parser's `group_by` enum gained `sales_agent`, `brand`, `category` and `channel` for sales
rankings. `fetch.entity_ids_transformer` passes `semantic_input["group_by"]` straight to
`crm_order_management_orders_list`, whose route accepts only `ORDER_GROUP_BY_AXES` and answers
422 `invalid_group_by` for the rest, so "by outstanding" after a ranking (read as an outstanding
order lookup that still carries `group_by sales_agent`) ended in `I could not fetch orders just
now, please try again.`

Pinned from the route's own set (`order_service.ORDER_GROUP_BY_AXES`), not from a copy.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot.lanes.business import fetch
from app.services.order_service import ORDER_GROUP_BY_AXES
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_report_ask_lane import (  # noqa: F401 - fixtures + helpers
    _names,
    _say_all,
    _t1,
    console,
)

ORDERS_LIST = "crm_order_management_orders_list"
RANKING_ONLY_AXES = ["sales_agent", "brand", "category", "channel"]


def _trigger(group_by: str | None, **semantic: Any) -> dict[str, Any]:
    return {
        "entities": [],
        "tool": ORDERS_LIST,
        "semantic_input": {"group_by": group_by, "contact_id": "1", "space_id": "s", **semantic},
    }


@pytest.mark.parametrize("group_by", RANKING_ONLY_AXES)
@pytest.mark.parametrize("order_status", ["outstanding", None, "all"])
def test_a_ranking_only_group_by_is_never_sent_to_the_orders_list(group_by, order_status) -> None:
    assert group_by not in ORDER_GROUP_BY_AXES
    out = fetch.entity_ids_transformer(_trigger(group_by, order_status=order_status))
    assert out.get("group_by") in (None, *ORDER_GROUP_BY_AXES), out


@pytest.mark.parametrize("group_by", sorted(ORDER_GROUP_BY_AXES))
def test_an_axis_the_orders_route_accepts_is_still_sent(group_by) -> None:
    out = fetch.entity_ids_transformer(_trigger(group_by))
    assert out["group_by"] == group_by, out


def test_the_customer_axis_guard() -> None:
    assert fetch.entity_ids_transformer(_trigger("customer"))["group_by"] == "customer"


def _outstanding_after_a_ranking(**overrides: Any) -> dict[str, Any]:
    """The parser's reading of "by outstanding" typed after a sales ranking: an outstanding order
    lookup that still carries the ranking's axis."""
    base: dict[str, Any] = dict(
        message_type="business_query", domain_hint="order", intent_hint="check_order",
        order_status="outstanding", domain_in_message=True, entities=[], group_by="sales_agent",
        top_n=None, ranking_refine=None, measure=None, basis=None,
        date_filter_start=None, date_filter_end=None,
    )
    base.update(overrides)
    return _parser_output(**base)


@pytest.mark.parametrize("group_by", ["sales_agent", None])
def test_the_flow_ranking_then_by_outstanding_sends_no_ranking_axis_and_says_no_fetch_error(
    console, monkeypatch, group_by
) -> None:
    _t1(console)
    text, log = _say_all(console, monkeypatch, _outstanding_after_a_ranking(group_by=group_by), "by outstanding")
    assert "could not fetch" not in text.lower(), text
    for name, args in log:
        sent = args.get("group_by")
        assert sent in (None, *ORDER_GROUP_BY_AXES), (name, args)
