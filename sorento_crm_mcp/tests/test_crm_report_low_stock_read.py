"""Phase 2 RED tests - REPORTS-STEP2 S5a AC-27: `crm_report` renders a FULL low stock read body
(the `build_low_stock_view` model plus `report_kind` and `run_date`, no `status`) as a chat reply.

Pinned by the tester: the body carries `columns`, `rows` (cells in `columns` order), `counts.low`,
`run.as_of` and `run_date`; an item is LOW when its `BRW on hand` is below its `Reorder level`, and the
items print by shortage (level minus on hand), largest first. The reply has one fact per line
("Label: value"): `As of: <run_date>` and `Low items: <n>`, then one line per low item naming its code and
description; the supplier is printed on the item line only when the body has a `Supplier` column.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sorento_crm_mcp.presenters import present_response  # noqa: E402

COLUMNS = ["Item code", "Description", "Category", "BRW on hand", "Reorder level", "Reorder qty",
           "Suggested qty", "Suggestion", "Order qty", "Dealer o/s", "Supplier", "BRW PO qty",
           "BRW incoming qty", "Last in qty", "Last in date", "Remarks"]
SUPPLIER = "Zenith Sanitaryware Sdn Bhd"
ITEMS = [  # code, description, on hand, level, supplier
    ("ZZT-LOW-1", "Wall hung closet", 2, 20, SUPPLIER),
    ("ZZT-LOW-2", "Basin mixer", 5, 8, SUPPLIER),
    ("ZZT-LOW-3", "Rain shower set", 0, 30, SUPPLIER),
    ("ZZT-OK-1", "Bidet spray", 90, 20, SUPPLIER),
]


def _row(code, desc, on_hand, level, supplier, with_supplier):
    cells = {"Item code": code, "Description": desc, "Category": "ZZT-CAT", "BRW on hand": on_hand,
             "Reorder level": level, "Reorder qty": "", "Suggested qty": 0, "Suggestion": "", "Order qty": "",
             "Dealer o/s": "", "Supplier": supplier, "BRW PO qty": "", "BRW incoming qty": "",
             "Last in qty": "", "Last in date": "", "Remarks": ""}
    return [cells[c] for c in COLUMNS if with_supplier or c != "Supplier"]


def _body(with_supplier=True):
    columns = [c for c in COLUMNS if with_supplier or c != "Supplier"]
    return {
        "report_kind": "low_stock", "run_date": "2026-09-10",
        "run": {"run_id": "r1", "as_of": "2026-09-10", "generated_at": "2026-09-10T06:00:00"},
        "split": "none", "columns": columns,
        "rows": [_row(*i, with_supplier) for i in ITEMS],
        "sheets": [], "facets": {"suppliers": [], "categories": []},
        "counts": {"rows": 4, "low": 3, "sheets": 2}, "over_cap": False, "max_rows": 5000,
        "filename": "low-stock-10092026.xlsx",
    }


def _reply(body):
    env = json.loads(present_response("crm_report", json.dumps(body)))
    return env["response"], env


def test_ac27_a_full_low_stock_body_renders_the_run_date_and_the_low_count():
    text, env = _reply(_body())
    lines = text.splitlines()
    assert "As of: 2026-09-10" in lines, text
    assert "Low items: 3" in lines, text
    assert env.get("has_result") is True, env


def test_ac27_the_low_items_print_by_shortage_largest_first_and_the_others_do_not():
    text, _env = _reply(_body())
    codes = [c for c in ("ZZT-LOW-3", "ZZT-LOW-1", "ZZT-LOW-2") if c in text]
    assert codes == ["ZZT-LOW-3", "ZZT-LOW-1", "ZZT-LOW-2"], text
    assert text.index("ZZT-LOW-3") < text.index("ZZT-LOW-1") < text.index("ZZT-LOW-2"), text
    assert "ZZT-OK-1" not in text, text
    assert "Rain shower set" in text and "Wall hung closet" in text, text


def test_ac27_the_supplier_is_named_only_when_the_body_has_the_supplier_column():
    with_supplier, _ = _reply(_body(with_supplier=True))
    without, _ = _reply(_body(with_supplier=False))
    assert SUPPLIER in with_supplier, with_supplier
    assert SUPPLIER not in without and "Supplier" not in without, without


def test_ac27_it_never_says_the_report_could_not_run():
    for with_supplier in (True, False):
        text, _env = _reply(_body(with_supplier))
        assert "could not run" not in text.lower() and "couldn't" not in text.lower(), text


def test_ac27_one_fact_per_line_with_no_dot_joins_or_dashes():
    text, _env = _reply(_body())
    assert " · " not in text and chr(0x2014) not in text and chr(0x2013) not in text, text


def test_ac27_no_snapshot_and_preparing_are_unchanged_plain_lines():
    for status, message in (("no_snapshot", "There is no low stock snapshot yet."),
                            ("preparing", "The low stock snapshot is still being prepared.")):
        text, _env = _reply({"report_kind": "low_stock", "status": status, "message": message})
        assert message in text, (status, text)
