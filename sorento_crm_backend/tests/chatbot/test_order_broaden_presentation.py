"""ORDER-BROADEN (owner, 5 Oct 2026, AVAILABILITY contact, prompt v42): the presentation half.

"check delivery for last week" (HANLIM, SRTWC286-SH, 28/09 to 04/10) missed. "all products"
then answered the dealer incoming view with the carried window printed between its lines:

    SRTWC286-SH-NEW-200: ETA 29/10/2026
    Order date: 28/09/2026 to 04/10/2026

    SRTWC286-SH-NEW-P: ETA 29/10/2026

    Please refer to your salesman.

The jump to the incoming domain is the PARSER's `domain_hint` (the engine never infers a
domain from leftover focus; `turn/apply.py` sets `focus.domains` from the verdict), and
that half is a prompt rule (`test_order_broaden_live_parser.py`). This file pins the two
presentation rules that hold whatever the parser emits:

1. A dealer-view block never prints `date_line` (`turn/compose.py`): the window a fetch
   ran with is stated under a report header, never between one-line ETA answers.
2. A turn whose domain CHANGES clears the carried date window (`turn/apply.py`,
   `domain_change_drops_date_window`): a window narrows the question it was asked on,
   and a different domain is a different question (behaviour card Q2, recommendation
   no carry). The same domain keeps it: "all products" after an order MISS re-runs the
   SAME window with no product filter (card Q1).

Plan: `documentation/plans/chatbot/PLAN-order-broaden-05oct.md`. Postgres only (the
console seeds real products for the resolver).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.eta_policy import dealer_view
from app.services.chatbot.turn.compose import compose
from app.services.chatbot.turn.policy import Policy

from tests.chatbot._r9_engine_console import EngineConsole, _present_response, product
from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row, verdict
from tests.chatbot.test_chat_language_render import _state
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture

WINDOW = {"date_filter_start": "2026-09-28", "date_filter_end": "2026-10-04"}
DATE_LINE = "Order date: 28/09/2026 to 04/10/2026"
ORDERS = "crm_order_management_orders_list"
INCOMING = ("crm_incoming_stock_list", "crm_incoming_stock_by_product")
ETA = date(2026, 10, 29)


# --------------------------------------------------------------------------- #
# Rule 1: compose never prints the window on a dealer-view block
# --------------------------------------------------------------------------- #


def _policy(domain: str, label: str) -> Policy:
    row = {**_domain_row(domain, narrowing={"product": "list_all"}), "label": label}
    return Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)


def _dealer_envelope() -> dict[str, Any]:
    """The owner's turn as `turn_runtime.envelope_of` hands it to the composer: the
    presenter's dealer lines (`sorento_crm_mcp/presenters.py::_incoming_dealer`, one
    title per product, no field, `flags.dealer_view`) and the window stamped by the
    carried focus (`_spec_window` -> `_date_line`)."""
    lines = ["SRTWC286-SH-NEW-200: ✅ ETA 29/10/2026", "SRTWC286-SH-NEW-P: ✅ ETA 29/10/2026"]
    return {
        "domain": "incoming",
        "denied": False,
        "entities": [],
        "product_codes": [],
        "figures": [{"title": t, "fields": [], "flags": {"dealer_view": True}} for t in lines],
        "files": [],
        "miss": [],
        "has_result": True,
        "tool_has_result": True,
        "unresolved": [],
        "error": None,
        "lane_text": "\n\n".join(lines) + "\n\nPlease refer to your salesman.",
        "date_line": DATE_LINE,
    }


def _order_envelope() -> dict[str, Any]:
    return {
        "domain": "order",
        "denied": False,
        "entities": ["HANLIM TRADING SDN BHD"],
        "product_codes": [],
        "figures": [{"fields": [{"label": "Order No", "value": "SO-1"}]}],
        "files": [],
        "miss": [],
        "has_result": True,
        "tool_has_result": True,
        "unresolved": [],
        "error": None,
        "lane_text": "*orders* for HANLIM TRADING SDN BHD:\n1. SO-1",
        "date_line": DATE_LINE,
    }


def test_a_dealer_view_block_never_prints_the_order_date_line():
    text = compose([_dealer_envelope()], _state(), _policy("incoming", "incoming"), ctx=None).text
    assert "Order date" not in text, text
    assert text.startswith("SRTWC286-SH-NEW-200: ✅ ETA 29/10/2026\n\nSRTWC286-SH-NEW-P"), text


def test_an_order_block_still_states_the_window_under_its_header():
    """The control: browser pass 6 item 4 is unchanged for every non-dealer block."""
    text = compose([_order_envelope()], _state(), _policy("order", "orders"), ctx=None).text
    assert text.split("\n")[1] == DATE_LINE, text


# --------------------------------------------------------------------------- #
# Rule 2: the owner's journey through the real engine
# --------------------------------------------------------------------------- #


class DeliveryThenEtaConsole(EngineConsole):
    """The owner's dealer: the orders tool answers nothing (the MISS), the incoming
    tools answer the two SRTWC286-SH-NEW ETAs through the REAL `eta_policy.dealer_view`
    and the REAL MCP presenter, whatever ids and dates the call carries - so the reply
    is the owner's, and what the call carried is what the test reads."""

    def __init__(self, session_factory, monkeypatch, stub_access, *, phone: str) -> None:
        super().__init__(session_factory, monkeypatch, stub_access, phone=phone)
        present = _present_response()

        def fake_call_tool(client, name: str, args: dict[str, Any]) -> str:
            self.tool_calls.append((name, args))
            if name in INCOMING:
                rows = [
                    {"product_code": code, "estimated_arrival_date": ETA.isoformat()}
                    for code in ("SRTWC286-SH-NEW-200", "SRTWC286-SH-NEW-P")
                ]
                told = dealer_view({"data": rows}, asked=[r["product_code"] for r in rows])
                return present(name, json.dumps(told))
            return json.dumps({"answers": []})

        monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)

    def calls(self, *names: str) -> list[dict[str, Any]]:
        return [args for name, args in self.tool_calls if name in names]


@pytest.fixture
def console(session_factory, monkeypatch, stub_access):
    from tests.chatbot._turn_helpers import make_office_tier
    from tests.chatbot.test_engine import CONTACT_ID

    out = DeliveryThenEtaConsole(session_factory, monkeypatch, stub_access, phone="+60100000951")
    # V1-DOC-SCOPE: this contact has no customer links, so it reads the orders book only as
    # staff (an unlinked non-staff contact owns no delivery order).
    make_office_tier(session_factory, CONTACT_ID)
    return out


def _delivery_last_week() -> dict[str, Any]:
    """"check delivery for last week": an order ask over SRTWC286-SH, dated."""
    return verdict(
        domain_hint="order",
        intent_hint="check_order",
        domain_in_message=True,
        entities=[product("srtwc286-sh")],
        date_mode="range",
        **WINDOW,
    )


def _all_products(domain_hint: str, intent_hint: str) -> dict[str, Any]:
    """"all products" as the BROADEN AXIS rule reads it (product axis, dropped entirely,
    scope reused), under the domain the parser emitted."""
    return verdict(
        domain_hint=domain_hint,
        intent_hint=intent_hint,
        domain_in_message=False,
        entities=[],
        entity_op="reuse",
        broaden_axis="product",
        broaden_to="all",
    )


def test_the_owners_miss_then_all_products_under_the_v42_verdict(console):
    """What v42 emitted (domain_hint incoming): the presentation holds whatever the
    parser says. No window between the ETA lines, and no window on the incoming call."""
    miss = console.say("check delivery for last week", _delivery_last_week())
    assert "no order from 28/09/2026 to 04/10/2026 matched" in miss, miss

    text = console.say("all products", _all_products("incoming", "check_incoming"))
    assert "ETA 29/10/2026" in text, text
    assert "Order date" not in text, text
    incoming = console.calls(*INCOMING)
    assert incoming, console.tool_calls
    # `lanes/business/fetch.DATE_PARAMS`: the incoming tools' own window keys.
    assert not incoming[-1].get("eta_from") and not incoming[-1].get("eta_to"), (
        f"the delivery window must not ride a fetch in a different domain: {incoming[-1]!r}"
    )
    assert "domain_change_drops_date_window" in console.last_trace.rules_fired, console.last_trace.rules_fired


def test_all_products_after_the_miss_reruns_the_same_window_for_every_product(console):
    """The patched prompt's verdict (domain_hint order): the engine re-runs the delivery
    list over the SAME window with the product filter dropped (card Q1), never an ETA."""
    console.say("check delivery for last week", _delivery_last_week())
    first = console.calls(ORDERS)[-1]
    assert first.get("product_ids"), first

    text = console.say("all products", _all_products("order", "check_order"))
    assert "ETA" not in text, text
    assert not console.calls(*INCOMING), console.tool_calls
    second = console.calls(ORDERS)[-1]
    assert not second.get("product_ids"), f"the product filter must be dropped: {second!r}"
    # `lanes/business/fetch.DATE_PARAMS`: the orders tool's own window keys.
    assert second.get("actual_delivery_date_from") == WINDOW["date_filter_start"], second
    assert second.get("actual_delivery_date_to") == WINDOW["date_filter_end"], second
    assert "domain_change_drops_date_window" not in console.last_trace.rules_fired, console.last_trace.rules_fired
