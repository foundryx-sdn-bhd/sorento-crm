"""ORDER-BROADEN console pass (evidence; not collected by the suite).

Run by copying into `sorento_crm_backend/tests/chatbot/` and running it by path:

    cp documentation/plans/chatbot/evidence/order-broaden/console_pass_order_broaden.py \
       sorento_crm_backend/tests/chatbot/ && cd sorento_crm_backend && \
    SORENTO_ENV_FILE=.env.ci-tests venv/bin/pytest tests/chatbot/console_pass_order_broaden.py -q -p no:warnings

The sandbox has no provider egress, so the PARSER VERDICT IS STUBBED to the patched
prompt's worked example (steps 1 to 3) and to the plain readings of steps 4 and 5;
everything after the parser is the real engine (`_avail_mode_console.AvailConsole`: the
resolver over seeded rows, apply, fetch, the MCP presenter, compose). The orders list is
answered by the REAL `GET /api/v1/order-management/orders` over SEEDED delivery orders for
HANLIM TRADING SDN BHD (three DOs with an actual delivery date inside 28/09 to 04/10,
products other than SRTWC286-SH, plus one outside the week), rendered by the REAL
presenter, the wiring `test_samantha_27sep_r6_do_list_brand.py` established. The
incoming and stock tools answer from the console's stock facts through the real
`eta_policy.dealer_view` and presenter. Prints a PASS/FAIL table for the hand-test steps
in `laneboard/scripts/1495.md` and names the git head it ran on.
"""
from __future__ import annotations

import subprocess
import uuid
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: F401 - first app import, resolves the guards cycle
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.base import set_company_scope
from app.models.order import Order, OrderLine
from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope

from tests._mc_lookup_seed import customer, warehouse
from tests.chatbot._avail_mode_console import AvailConsole, Stock
from tests.chatbot._r9_engine_console import _present_response, product, stock
from tests.chatbot._turn_helpers import verdict
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture
from tests.test_orders_brand_filter import _seed_superadmin

WINDOW = {"date_mode": "range", "date_filter_start": "2026-09-28", "date_filter_end": "2026-10-04"}
ORDERS = "crm_order_management_orders_list"
ORDERS_ROUTE = "/api/v1/order-management/orders"
INCOMING = ("crm_incoming_stock_list", "crm_incoming_stock_by_product")
HANLIM = "HANLIM TRADING SDN BHD"
#: HANLIM's delivery orders: number -> (product code, actual delivery date). Three inside
#: the week for products other than SRTWC286-SH, one outside it.
DELIVERIES = {
    "DO-OB-0929": ("SRTWC287-S-150", date(2026, 9, 29)),
    "DO-OB-1001": ("ELP3754", date(2026, 10, 1)),
    "DO-OB-1003": ("SRTWC286-SH-NEW-P", date(2026, 10, 3)),
    "DO-OB-0910": ("ELP3754", date(2026, 9, 10)),
}


def _seed_deliveries(session_factory, codes: dict[str, str]) -> str:
    db = session_factory()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name=HANLIM)
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    for number, (code, delivered) in DELIVERIES.items():
        order = Order(
            id=str(uuid.uuid4()), order_number=number, customer_id=cust.id, debtor_name=cust.customer_name,
            is_cancelled=False, company_id=DEFAULT_COMPANY_ID, actual_delivery_date=delivered,
        )
        db.add(order)
        db.flush()
        db.add(OrderLine(id=str(uuid.uuid4()), order_id=order.id, product_id=codes[code], warehouse_id=wh.id,
                         quantity=2, line_sequence=1, company_id=DEFAULT_COMPANY_ID))
    db.commit()
    return cust.id


class OwnerConsole(AvailConsole):
    """The owner's dealer, with the orders list answered by the real route over the
    seeded deliveries and every other tool by the availability console's own stubs."""

    def __init__(self, session_factory, monkeypatch, stub_access, **facts: Stock) -> None:
        super().__init__(session_factory, monkeypatch, stub_access, phone="+60100000953", **facts)
        self.customer_id = _seed_deliveries(session_factory, self.uuid_of)
        route_db = session_factory()
        principal = _seed_superadmin(route_db)

        def _override_db():
            yield route_db

        async def _override_scope():
            scope = frozenset({DEFAULT_COMPANY_ID})
            set_company_scope(route_db, scope)
            return scope

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = lambda: principal
        app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
        app.dependency_overrides[apply_company_scope] = _override_scope
        client = TestClient(app)
        present = _present_response()
        avail_stub = MCPRuntimeClient.call_tool  # the AvailConsole's own fake, installed by super()

        def real_orders_or_stub(mcp, name: str, args: dict[str, Any]) -> str:
            if name != ORDERS:
                return avail_stub(mcp, name, args)
            self.tool_calls.append((name, args))
            params = {k: v for k, v in args.items() if v is not None and k not in ("contact_id", "space_id")}
            resp = client.get(ORDERS_ROUTE, params=params)
            assert resp.status_code == 200, resp.text
            return present(name, resp.text)

        monkeypatch.setattr(MCPRuntimeClient, "call_tool", real_orders_or_stub)


@pytest.fixture
def console(session_factory, monkeypatch, stub_access):
    c = OwnerConsole(
        session_factory, monkeypatch, stub_access,
        SRTWC286_SH=Stock(on_hand=0, eta=date(2026, 10, 29)),
        SRTWC286_SH_NEW_200=Stock(on_hand=0, eta=date(2026, 10, 29)),
        SRTWC286_SH_NEW_P=Stock(on_hand=0, eta=date(2026, 10, 29)),
    )
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


def _in_week(numbers: list[str]) -> list[str]:
    return [n for n in numbers if DELIVERIES[n][1] >= date(2026, 9, 28)]


def test_console_pass(console, capsys):
    c = console
    rows = []

    def step(n, message, v, checks):
        before = len(c.tool_calls)
        text = c.say(message, v)
        new = c.tool_calls[before:]
        rows.append((n, message, [(name, ok(text, new)) for name, ok in checks], text))

    def orders_call(calls):
        return next((a for nm, a in calls if nm == ORDERS), None)

    step(1, "check delivery for hanlim srtwc286-sh last week",
         verdict(domain_hint="order", intent_hint="check_order", domain_in_message=True,
                 entities=[product("srtwc286-sh"), {"raw": "hanlim", "hint": "customer", "canonical_code": None,
                                                    "current_message": True, "confident": True}],
                 **WINDOW),
         [("order MISS for HANLIM + SRTWC286-SH names the week",
           lambda t, _: "no order from 28/09/2026 to 04/10/2026 matched" in t and "HANLIM" in t),
          ("orders route called with the week and the product",
           lambda _, calls: (a := orders_call(calls)) is not None and a.get("actual_delivery_date_from") == "2026-09-28"
                            and a.get("actual_delivery_date_to") == "2026-10-04" and bool(a.get("product_ids"))),
          ("no ETA / stock line", lambda t, _: "ETA" not in t)])
    step(2, "all products",
         verdict(domain_hint="order", intent_hint="check_order", domain_in_message=False, entities=[],
                 entity_op="reuse", broaden_axis="product", broaden_to="all"),
         [("orders route: same week, HANLIM kept, no product filter",
           lambda _, calls: (a := orders_call(calls)) is not None and a.get("actual_delivery_date_from") == "2026-09-28"
                            and a.get("actual_delivery_date_to") == "2026-10-04" and not a.get("product_ids")
                            and bool(a.get("customer_ids"))),
          ("every in-week DO listed (3), the out-of-week one not",
           lambda t, _: all(n in t for n in _in_week(list(DELIVERIES))) and "DO-OB-0910" not in t),
          ("header names HANLIM, no product code", lambda t, _: "HANLIM" in t.split("\n")[0] and "SRTWC286-SH" not in t.split("\n")[0]),
          # The DO list states its scope in its own four-line header (`own_header`), so
          # the week reads "Dates: ..." on line 3, never a second "Order date:" line.
          ("the week stated in the list's own header", lambda t, _: t.split("\n")[2] == "Dates: 28/09/2026 to 04/10/2026"),
          ("no incoming call, no ETA line", lambda t, calls: not any(nm in INCOMING for nm, _ in calls) and "ETA" not in t)])
    step(3, "all customers",
         verdict(domain_hint="order", intent_hint="check_order", domain_in_message=False, entities=[],
                 entity_op="reuse", broaden_axis="customer", broaden_to="all"),
         [("orders route: same week, no customer, no product",
           lambda _, calls: (a := orders_call(calls)) is not None and a.get("actual_delivery_date_from") == "2026-09-28"
                            and not a.get("customer_ids") and not a.get("product_ids")),
          ("the in-week DOs listed, no ETA line", lambda t, _: all(n in t for n in _in_week(list(DELIVERIES))) and "ETA" not in t)])
    step(4, "any incoming for srtwc286-sh?",
         verdict(domain_hint="incoming", intent_hint="check_incoming", domain_in_message=True,
                 entities=[product("srtwc286-sh")]),
         [("ETA line shown", lambda t, _: "SRTWC286-SH: ✅ ETA 29/10/2026" in t),
          ("no Order date line", lambda t, _: "Order date" not in t),
          ("incoming call carries no eta_from/eta_to",
           lambda _, calls: any(nm in INCOMING and not a.get("eta_from") and not a.get("eta_to") for nm, a in calls)),
          ("refer line", lambda t, _: "Please refer to your salesman." in t)])
    step(5, "check stock srtwc286-sh-new-200 x 500",
         stock(product("srtwc286-sh-new-200", 500)),
         [("availability answer", lambda t, _: t.startswith("SRTWC286-SH-NEW-200 x 500:")),
          ("no Order date line", lambda t, _: "Order date" not in t)])

    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    with capsys.disabled():
        print(f"\nConsole pass on head {head}\n\n| Step | Message | Check | Result |\n|---|---|---|---|")
        for n, message, results, _ in rows:
            for name, ok in results:
                print(f"| {n} | `{message}` | {name} | {'PASS' if ok else 'FAIL'} |")
        print("\nReplies:")
        for n, message, _, text in rows:
            print(f"\n[{n}] {message}\n-> " + text.replace("\n", "\n   "))
    assert all(ok for _, _, results, _ in rows for _, ok in results)
