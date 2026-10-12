"""REPORTS-STEP2 hand test 1 (h6): the ranking reply prints its period as dd/mm/yyyy, like the sales report."""
from __future__ import annotations

from sorento_crm_mcp.presenters import _report_ask

BODY = {
    "status": "ok", "message": None, "basis": "delivered", "basis_label": "delivered sales", "measure": "amount",
    "group_by": "sales_agent", "group_label": "Sales agent", "date_from": "2026-09-01", "date_to": "2026-09-30",
    "filters": [{"key": "brand", "label": "Brand", "values": ["SORENTO"]}],
    "rows": [{"rank": 1, "name": "SA01", "qty": 30, "amount": 1200.0}],
    "more": 0, "total_count": 1, "total": {"qty": 30, "amount": 1200.0},
}


def test_h6_the_period_line_is_dd_mm_yyyy() -> None:
    text = _report_ask(BODY)
    assert "Period: 01/09/2026 to 30/09/2026" in text.splitlines(), text
    assert "2026-09-01" not in text, text
