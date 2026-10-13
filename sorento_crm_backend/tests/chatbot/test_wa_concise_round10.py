"""WA-CONCISE round 10 (red-first): merged PO quantities sum exactly and never drop a row.
Placeholder data only."""
from __future__ import annotations

import pytest

from app.services.chatbot.lanes.business.answer import _crossdomain_rung_text


def _row(ordered, qty, number="PO-0001"):
    return {
        "kind": "po", "number": number, "product_code": "SRTX2", "ordered_qty": ordered,
        "qty": qty, "po_date": "2026-09-11", "location": "BRW",
    }


@pytest.mark.parametrize(
    ("first", "second", "total"),
    [("0.1", "0.2", "0.3"), ("2.125", "1.25", "3.375"), ("36", "36", "72")],
)
def test_same_po_quantities_sum_exactly(first, second, total):
    text = _crossdomain_rung_text([_row(first, first), _row(second, second)])

    assert text == (
        f"*PO:* PO-0001\n*Ordered:* {total}\n*Outstanding:* {total}\n*PO date:* 2026-09-11\n*Location:* BRW"
    ), text


@pytest.mark.parametrize("bad", [("n/a", "5"), ("5", "n/a")])
def test_a_non_numeric_quantity_keeps_both_rows_as_their_own_blocks(bad):
    first, second = bad

    text = _crossdomain_rung_text([_row(first, first), _row(second, second)])

    assert text == (
        f"*PO:* PO-0001\n*Ordered:* {first}\n*Outstanding:* {first}\n*PO date:* 2026-09-11\n*Location:* BRW\n"
        f"*PO:* PO-0001\n*Ordered:* {second}\n*Outstanding:* {second}\n*PO date:* 2026-09-11\n*Location:* BRW"
    ), text
