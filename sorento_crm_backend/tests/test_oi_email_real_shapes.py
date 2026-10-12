"""EMAIL-SO-CHANGES: the no-REMARK email rendered for the before/after shapes changed OI rows
actually carry (read-only dev extract, 12 Oct 2026; only codes, quantities and dates kept).

Each `was` is what `_format_handover_was` hands the template for a row whose before values
differ from its current ones: only the fields that moved. SO381067 SSFH16 is the shape a naive
"previous_qty is set" read gets wrong (176 before and after, only its date moved).
"""
from __future__ import annotations

import pytest

from .test_oi_email_no_remark import ORDER, _line_table, _render
from .test_oi_email_print import _ctx, _line

SHAPES = [
    pytest.param(
        _line("SO314594", "BRW-IR", "B2154-NL", "280", "01/04/2027", "ORDER 98", {"qty": "182"}),
        ["LOCATION", "ITEM CODE", "QTY", "NEW QTY", "DELIVERY"],
        ["BRW-IR", "B2154-NL", "182", "280", "01/04/2027"],
        id="SO314594-qty-only",
    ),
    pytest.param(
        _line("SO381067", "BRW-BB", "SSFH16", "176", "29/03/2027", "DELAY",
              {"delivery_date": "05/05/2026"}),
        ["LOCATION", "ITEM CODE", "QTY", "DELIVERY", "NEW DATE"],
        ["BRW-BB", "SSFH16", "176", "05/05/2026", "29/03/2027"],
        id="SO381067-date-only-same-qty",
    ),
    pytest.param(
        _line("SO399057", "BRW-BB", "SRTWC194", "1", "22/09/2026", "DELAY, NEW ITEM CODE",
              {"item_code": "SRTWC193", "delivery_date": "15/05/2026"}),
        ["LOCATION", "ITEM CODE", "NEW ITEM CODE", "QTY", "DELIVERY", "NEW DATE"],
        ["BRW-BB", "SRTWC193", "SRTWC194", "1", "15/05/2026", "22/09/2026"],
        id="SO399057-item-and-date",
    ),
    pytest.param(
        _line("SO404939", "BRW-BB", "SRTWC8605-SC", "200", "02/11/2026", "NEW ITEM CODE",
              {"item_code": "SRTWC8605-SC-RL"}),
        ["LOCATION", "ITEM CODE", "NEW ITEM CODE", "QTY", "DELIVERY"],
        ["BRW-BB", "SRTWC8605-SC-RL", "SRTWC8605-SC", "200", "02/11/2026"],
        id="SO404939-item-only",
    ),
    pytest.param(
        _line("SO402757", "BRW-IB", "SRTWT6808", "239", "01/09/2026", "DELAY, ORDER 75",
              {"qty": "164", "delivery_date": "01/07/2026"}),
        ["LOCATION", "ITEM CODE", "QTY", "NEW QTY", "DELIVERY", "NEW DATE"],
        ["BRW-IB", "SRTWT6808", "164", "239", "01/07/2026", "01/09/2026"],
        id="SO402757-qty-and-date",
    ),
]


@pytest.mark.parametrize("line, header, row", SHAPES)
def test_real_change_shapes_render_before_and_after(line, header, row):
    body = _line_table(_render(_ctx(ORDER, [line])))
    assert body == [header, row], body
