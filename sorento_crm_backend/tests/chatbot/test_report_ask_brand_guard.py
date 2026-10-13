"""CONTACT-BRAND-SCOPE x REPORT-ENGINE: the `crm_report_ask` contract entry and fetch guard.

Split out of `tests/test_report_ask_brand_scope.py`: these touch `app.services.chatbot`, which only
tests under `tests/chatbot/` may import (`test_import_boundary.py`).
"""
from __future__ import annotations

from tests._brand_scope_seed import db, world  # noqa: F401  (fixtures by name)


def test_the_tool_is_marked_filtered_in_the_contract() -> None:
    from app.services.chatbot.contracts import BRAND_SCOPE_TREATMENT

    assert BRAND_SCOPE_TREATMENT.get("crm_report_ask") == "filtered"


def test_the_fetch_guard_drops_a_product_row_of_another_brand(db, world) -> None:
    from app.services.chatbot.lanes.business import brand_guard

    result = {"group_by": "product", "rows": [
        {"rank": 1, "name": world.codes["sorento"], "qty": 3, "amount": 1000.0},
        {"rank": 2, "name": world.codes["mocha"], "qty": 2, "amount": 100.0},
        {"rank": 3, "name": world.codes["null"], "qty": 4, "amount": 9.0},
    ]}
    out = brand_guard.guard_result("crm_report_ask", result, [world.mocha.id], db)
    assert [r["name"] for r in out["rows"]] == [world.codes["mocha"]], out


def test_the_fetch_guard_leaves_a_sales_agent_ranking_alone(db, world) -> None:
    from app.services.chatbot.lanes.business import brand_guard

    result = {"group_by": "sales_agent", "rows": [{"rank": 1, "name": "ZZT-BS-AG1", "qty": 2, "amount": 100.0}]}
    assert brand_guard.guard_result("crm_report_ask", result, [world.mocha.id], db) == result
