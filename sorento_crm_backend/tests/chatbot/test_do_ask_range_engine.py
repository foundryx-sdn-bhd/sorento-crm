"""DO-ASK-SIMPLIFY rules 3-4 through the REAL engine: the period question, then its answer.

`documentation/plans/chatbot/PLAN-do-ask-simplify-2oct.md`. A dealer (a contact linked to a
customer, no office access type) asks for deliveries with no date: nothing is fetched and the
bot asks which period. The dealer answers with a period only ("this month"): the SAME ask runs,
for the carried customer, over that window. No pending question is armed for this; the answer
is an ordinary dated message the engine carries onto the open ask.

Harness: `test_dealer_eta_stock_routing.py`'s Console (real engine, real routes through
TestClient, real MCP presenter; only the parser verdict is stubbed).
"""
from __future__ import annotations

import traceback
from datetime import date
from typing import Any

import pytest

from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import do_ask
from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_dealer_eta_stock_routing import Console, _seed
from tests.chatbot.test_engine import stub_access  # noqa: F401 - fixture

ORDERS_TOOL = "crm_order_management_orders_list"


@pytest.fixture
def dealer(session_factory, monkeypatch, stub_access):
    from app.main import app

    monkeypatch.setattr(do_ask, "today_myt", lambda: date(2026, 10, 2))
    _seed(session_factory, dealer=False, salesperson=True, shipments=False)
    console = Console(session_factory, monkeypatch, stub_access)
    calls: list[tuple[str, dict[str, Any]]] = []
    inner = MCPRuntimeClient.call_tool

    def recording(self, name, args):
        calls.append((name, dict(args)))
        return inner(self, name, args)

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", recording)
    console.calls = calls
    try:
        yield console
    finally:
        app.dependency_overrides.clear()


def _ask(**extra: Any) -> dict[str, Any]:
    return verdict(
        domain_hint="order",
        intent_hint="check_order",
        entities=[entity("ZZT Dealer", hint="customer")],
        **extra,
    )


def _period(start: str, end: str) -> dict[str, Any]:
    return verdict(
        domain_hint="order",
        intent_hint="check_order",
        domain_in_message=False,
        entities=[],
        date_filter_start=start,
        date_filter_end=end,
    )


def test_no_range_asks_which_period_and_fetches_nothing(dealer):
    reply = dealer.say("delivery to zzt dealer", _ask())
    assert dealer.calls == [], dealer.calls
    assert "Which period" in reply and "This month (Oct 2026)" in reply, reply


def test_the_answer_runs_the_same_ask_over_that_window(dealer):
    dealer.say("delivery to zzt dealer", _ask())
    dealer.say("this month", _period("2026-10-01", "2026-10-31"))
    orders = [args for name, args in dealer.calls if name == ORDERS_TOOL]
    assert len(orders) == 1, dealer.calls
    args = orders[0]
    assert args.get("actual_delivery_date_from") == "2026-10-01", args
    assert args.get("actual_delivery_date_to") == "2026-10-31", args
    assert args.get("customer_ids"), f"the carried customer must scope the fetch: {args}"


def test_a_range_over_31_days_is_refused_through_the_engine(dealer):
    reply = dealer.say(
        "delivery to zzt dealer january to june",
        _ask(date_filter_start="2026-01-01", date_filter_end="2026-06-30"),
    )
    assert dealer.calls == [], dealer.calls
    assert "6 months" in reply and "Jun 2026" in reply, reply


# --- owner ruling 4 Oct 2026: the same rules for an office contact (no customer links) ------ #


@pytest.fixture
def office(session_factory, monkeypatch, stub_access):
    """A contact with NO `respond_contact_customers` links (customer scope not enforced), and a
    customer that exists but is linked to nobody, so the ask can still name it."""
    from app.main import app
    from app.models.order import Customer
    from app.models.base import set_company_scope
    from tests.chatbot.test_rearch_s3_attribute_first import SORENTO
    import uuid

    monkeypatch.setattr(do_ask, "today_myt", lambda: date(2026, 10, 2))
    _seed(session_factory, dealer=False, salesperson=False, shipments=False)
    db = session_factory()
    set_company_scope(db, frozenset({SORENTO}))
    customer_id = str(uuid.uuid4())
    db.add(
        Customer(
            id=customer_id,
            customer_code=f"ZZT{uuid.uuid4().hex[:6]}",
            customer_name="ZZT Dealer Sdn Bhd",
            company_id=SORENTO,
        )
    )
    db.commit()
    console = Console(session_factory, monkeypatch, stub_access)
    calls: list[tuple[str, dict[str, Any]]] = []
    inner = MCPRuntimeClient.call_tool

    def recording(self, name, args):
        # An office contact has no linked customers, so the engine resolves the customer NAME
        # with the picker probe (`resolve_gate._run_probe`), which calls the orders tool itself
        # with whatever dates the verdict carries. That is name resolution, not the answer, so
        # it is not recorded; `calls` holds only the lane's own fetches. (A dealer's links
        # resolve the name without a probe, which is why the dealer fixture needs no filter.)
        if not any(frame.name == "_run_probe" for frame in traceback.extract_stack()):
            calls.append((name, dict(args)))
        return inner(self, name, args)

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", recording)
    console.calls = calls
    console.customer_id = customer_id
    try:
        yield console
    finally:
        app.dependency_overrides.clear()


def test_office_no_range_asks_which_period_and_fetches_nothing(office):
    reply = office.say("delivery to zzt dealer", _ask())
    assert office.calls == [], office.calls
    assert "Which period" in reply and "This month (Oct 2026)" in reply, reply


def test_office_period_answer_runs_once_over_the_window_scoped_to_the_named_customer(office):
    office.say("delivery to zzt dealer", _ask())
    office.say("this month", _period("2026-10-01", "2026-10-31"))
    orders = [args for name, args in office.calls if name == ORDERS_TOOL]
    assert len(orders) == 1, office.calls
    args = orders[0]
    assert args.get("actual_delivery_date_from") == "2026-10-01", args
    assert args.get("actual_delivery_date_to") == "2026-10-31", args
    assert office.customer_id in str(args.get("customer_ids")), (
        f"the follow-up must stay scoped to the customer the first ask named, never all customers: {args}"
    )


def test_office_range_over_31_days_is_refused_through_the_engine(office):
    reply = office.say(
        "delivery to zzt dealer january to june",
        _ask(date_filter_start="2026-01-01", date_filter_end="2026-06-30"),
    )
    assert office.calls == [], office.calls
    assert "6 months" in reply and "Jun 2026" in reply, reply
