"""ORDER-BROADEN console pass (not collected by the suite: no `test_` prefix; run by path).

The sandbox has no provider egress, so the parser verdict is STUBBED to what the patched
prompt's worked example says (steps 1 to 3) and to the plain readings of steps 4 and 5;
everything after the parser (resolver over seeded codes, apply, fetch, the real MCP
presenter, compose) is the real engine, as in `_avail_mode_console.AvailConsole`. Prints a
PASS/FAIL table for the hand-test steps in `laneboard/scripts/1495.md`.
"""
from __future__ import annotations

from datetime import date

import pytest

from tests.chatbot._avail_mode_console import AvailConsole, Stock
from tests.chatbot._r9_engine_console import product, stock
from tests.chatbot._turn_helpers import verdict
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture

WINDOW = {"date_mode": "range", "date_filter_start": "2026-09-28", "date_filter_end": "2026-10-04"}
ORDERS = "crm_order_management_orders_list"
INCOMING = ("crm_incoming_stock_list", "crm_incoming_stock_by_product")


def _calls(c, *names):
    return [(n, a) for n, a in c.tool_calls if n in names]


def test_console_pass(session_factory, monkeypatch, stub_access, capsys):
    c = AvailConsole(
        session_factory, monkeypatch, stub_access, phone="+60100000952",
        SRTWC286_SH=Stock(on_hand=0, eta=date(2026, 10, 29)),
        SRTWC286_SH_NEW_200=Stock(on_hand=0, eta=date(2026, 10, 29)),
        SRTWC286_SH_NEW_P=Stock(on_hand=0, eta=date(2026, 10, 29)),
    )
    rows = []

    def step(n, message, v, checks):
        before = len(c.tool_calls)
        text = c.say(message, v)
        new = c.tool_calls[before:]
        results = [(name, ok(text, new)) for name, ok in checks]
        rows.append((n, message, results, text))

    step(1, "check delivery for srtwc286-sh last week",
         verdict(domain_hint="order", intent_hint="check_order", domain_in_message=True,
                 entities=[product("srtwc286-sh")], **WINDOW),
         [("order MISS names the week", lambda t, _: "no order from 28/09/2026 to 04/10/2026 matched" in t),
          ("no ETA / stock line", lambda t, _: "ETA" not in t)])
    step(2, "all products",
         verdict(domain_hint="order", intent_hint="check_order", domain_in_message=False, entities=[],
                 entity_op="reuse", broaden_axis="product", broaden_to="all"),
         [("orders tool, same window, no product filter",
           lambda _, calls: any(n == ORDERS and a.get("actual_delivery_date_from") == "2026-09-28"
                                and a.get("actual_delivery_date_to") == "2026-10-04" and not a.get("product_ids")
                                for n, a in calls)),
          ("no incoming call", lambda _, calls: not any(n in INCOMING for n, a in calls)),
          ("no ETA line", lambda t, _: "ETA" not in t)])
    step(3, "all customers",
         verdict(domain_hint="order", intent_hint="check_order", domain_in_message=False, entities=[],
                 entity_op="reuse", broaden_axis="customer", broaden_to="all"),
         [("orders tool, same window", lambda _, calls: any(
             n == ORDERS and a.get("actual_delivery_date_from") == "2026-09-28" for n, a in calls)),
          ("no ETA line", lambda t, _: "ETA" not in t)])
    step(4, "any incoming for srtwc286-sh?",
         verdict(domain_hint="incoming", intent_hint="check_incoming", domain_in_message=True,
                 entities=[product("srtwc286-sh")]),
         [("ETA lines shown", lambda t, _: "ETA 29/10/2026" in t),
          ("no Order date line", lambda t, _: "Order date" not in t),
          ("incoming call carries no eta_from/eta_to", lambda _, calls: any(
              n in INCOMING and not a.get("eta_from") and not a.get("eta_to") for n, a in calls)),
          ("refer line", lambda t, _: "Please refer to your salesman." in t)])
    step(5, "check stock srtwc286-sh-new-200 x 500",
         stock(product("srtwc286-sh-new-200", 500)),
         [("availability answer", lambda t, _: t.startswith("SRTWC286-SH-NEW-200 x 500:")),
          ("no Order date line", lambda t, _: "Order date" not in t)])

    with capsys.disabled():
        print("\n| Step | Message | Check | Result |\n|---|---|---|---|")
        for n, message, results, _ in rows:
            for name, ok in results:
                print(f"| {n} | `{message}` | {name} | {'PASS' if ok else 'FAIL'} |")
        print("\nReplies:")
        for n, message, _, text in rows:
            print(f"\n[{n}] {message}\n-> " + text.replace("\n", "\n   "))
    assert all(ok for _, _, results, _ in rows for _, ok in results)
