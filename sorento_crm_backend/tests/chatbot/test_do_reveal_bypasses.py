"""DO-ASK-SIMPLIFY security review round 1 (PR #1433): two ways round the DO reveals.

* S1: naming a transporter filtered the DO list by it, so every row returned told the
  contact who carried it, without `delivery_orders.transporter`. Without the grant the
  ask is refused in one line before any fetch (review S5: never silently unfiltered).
* S2: the dealer range question ran before the customer-scope backstop
  (`fetch.entity_ids_transformer` raising `ScopeViolation`), so a customer outside the
  contact's links could be named in "Which period for X?". The backstop now answers first.

Driven through `run_fetch` with a stub MCP that records every call and its arguments.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from app.services.chatbot import do_ask
from app.services.chatbot.lanes import business
from app.services.chatbot.lanes.business.services import FetchServices

_LINKED = "6b52807a-537b-437d-9f55-12f7fda29df8"
_OTHER = "2a4575e0-836b-4a5d-8566-73223465020d"
_TRANSPORTER = "c2f38bdf-767a-4b04-b92d-1c0d56cfd4d3"
_ROWS = json.dumps({"intro": "Here are the orders I found.", "items": [], "has_result": False})
_ORDERS = "crm_order_management_orders_list"


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(do_ask, "today_myt", lambda: date(2026, 10, 2))


def _run(*, entities, granted=None, dealer=False, start="2026-10-01", end="2026-10-31"):
    calls: list[tuple[str, dict]] = []

    def mcp_call(name, args):
        calls.append((name, dict(args)))
        return _ROWS

    gate = {"compatible_entities": entities}
    payload = {
        "_exit_kind": "continue",
        "gate": gate,
        "ctx": {
            "parse": {
                "output": {
                    "message_type": "business_query",
                    "domain_hint": "order",
                    "date_filter_start": start,
                    "date_filter_end": end,
                }
            },
            "contact": {"id": "437264483"},
            "access": {"allowed": True, "attributes": granted},
            "customer_scope": {
                "enforced": dealer,
                "ids": [_LINKED] if dealer else [],
                "linked": [[_LINKED, "HANLIM TRADING SDN BHD [A/C I]", "300-H001"]] if dealer else [],
            },
        },
    }
    fragment = business.run_fetch(payload, services=FetchServices(mcp_call=mcp_call), dry_run=False)
    return json.dumps(fragment, default=str), calls, gate


def _transporter(display: str = "GT DELIVERY") -> dict:
    return {"uuid": _TRANSPORTER, "entity_type": "transporter", "code": "GT", "display_name": display}


def _customer(uuid: str = _LINKED, name: str = "HANLIM TRADING SDN BHD [A/C I]") -> dict:
    return {"uuid": uuid, "entity_type": "customer", "code": "300-H001", "display_name": name}


def _order_args(calls):
    found = [args for name, args in calls if name == _ORDERS]
    assert found, calls
    return found[0]


def test_a_transporter_ask_is_refused_without_the_grant():
    """Review S5: dropping the filter silently would answer a different question; the ask
    is refused in one line instead, and nothing is fetched."""
    said, calls, _gate = _run(entities=[_customer(), _transporter()], granted=[])
    assert not [name for name, _args in calls if name == _ORDERS], calls
    assert "transporter" in said.lower() and "GT DELIVERY" not in said, said


def test_a_transporter_filters_with_the_grant():
    _said, calls, _gate = _run(entities=[_customer(), _transporter()], granted=["delivery_orders.transporter"])
    assert _order_args(calls).get("transporter_ids") == [_TRANSPORTER]


def test_a_customer_outside_the_links_is_refused_before_the_period_question():
    said, calls, _gate = _run(
        entities=[_customer(_OTHER, "ZZT SOMEONE ELSE SDN BHD")], dealer=True, start=None, end=None
    )
    assert not [name for name, _args in calls if name == _ORDERS], calls
    assert "ZZT SOMEONE ELSE" not in said, said
    assert "Which period" not in said, said


