"""DO-SUMMARY-CAP (PLAN-do-summary-cap.md, AC-1601 to AC-1608): the outstanding
summary's `*_By product_*` / `*_By customer_*` group stops at the top N rows and closes
with one trailer line; counts and `*_By location_*` stay whole; the detail list is
unchanged.

Owner ruling 7 Oct 2026 (Option 1 from the #1515 plan question), after a prod reply
(contact 551644114, turn 11) printed 300+ `*_By product_*` lines, most of them
`<code>: 1 (O/S: 1)`. It amends AC-1103's "no line is ever elided" for this one group:
the route already ranks the rows (R10), so the top of the list is the part worth
reading and the trailer names what was left off.

Written red, before the presenter changes. The two new goldens under
`tests/fixtures/outstanding/` are the EXPECTED bytes (the first N rows as today's
renderer prints them, then the trailer), with their `documentation/plans/chatbot/
samples/` mirrors, the same two-copy rule `test_presenters_outstanding.py` explains.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from sorento_crm_mcp.presenters import (
    OUTSTANDING_GROUP_CAP,
    _outstanding_detail,
    _outstanding_report,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "outstanding"
_MOCK = json.loads((_FIXTURES / "outstanding-report-mock.json").read_text())

#: Both offer shapes the chatbot lane reads the pending offer off
#: (`lanes/business/fetch.py` `_SO_LIST_OFFER_RE` / `_DO_LIST_OFFER_RE`), copied here
#: because the backend image and this package cannot import each other; the backend
#: side pins the same patterns against a capped reply in
#: `tests/chatbot/test_do_summary_cap_offer.py`.
_SO_LIST_OFFER_RE = re.compile(r"(?m)^\d+\.\s*Sales order list\s*$|^Reply \d+ for the sales order list\.$")
_DO_LIST_OFFER_RE = re.compile(r"(?m)^\d+\.\s*Delivery order list\s*$|^Reply \d+ for the delivery order list\.$")


def _golden(name: str) -> str:
    return (_FIXTURES / name).read_text().rstrip("\n")


def _product_rows(quantities: list[int]) -> list[dict]:
    """`do_by_product` rows in the route's own rank order (outstanding desc, total desc,
    name asc): the quantities are given descending and the codes ascend, so a tie group
    is already in name order."""
    return [
        {"product_code": f"SRTWT{7000 + i:04d}", "do_qty": q, "pending_qty": q}
        for i, q in enumerate(quantities, start=1)
    ]


#: The prod shape, masked: a customer-subject DO-only report over the dealer's whole range.
#: 20 products carry the volume, then 80 rows of 3, 100 rows of 2 and 112 rows of 1 - 312
#: products, 1,039 units, which the two location rows and the block total tally with.
_PROD_SHAPE_TOP_20 = [96, 60, 48, 40, 36, 30, 24, 20, 18, 16, 15, 14, 12, 12, 10, 10, 8, 8, 6, 4]
_PROD_SHAPE_TAIL = [3] * 80 + [2] * 100 + [1] * 112


def prod_shape_report() -> dict:
    quantities = _PROD_SHAPE_TOP_20 + _PROD_SHAPE_TAIL
    total = sum(quantities)
    assert total == 1039 and len(quantities) == 312
    return {
        "product_code": None,
        "customer_name": "Dealer A Sdn Bhd",
        "location_token": None,
        "warehouse_codes": [],
        "order_date_from": None,
        "order_date_to": None,
        "so": None,
        "do": {
            "do_qty": total, "delivered_qty": 0, "pending_qty": total,
            "do_count": 148, "do_date_min": "2026-03-12", "do_date_max": "2026-10-05",
        },
        "do_by_location": [
            {"code": "BRW-IB", "do_qty": 693, "pending_qty": 693},
            {"code": "MWH-IB", "do_qty": 346, "pending_qty": 346},
        ],
        "do_by_product": _product_rows(quantities),
        "do_rows": [],
    }


def both_capped_report() -> dict:
    """The existing product-subject mock with 27 SO customers and 23 DO customers, so
    BOTH blocks cap and the two trailers must name DIFFERENT offer positions (SO list is
    1, DO list is 2 when both are offered)."""
    report = copy.deepcopy(_MOCK)
    so_q = [100] * 20 + [60] * 6 + [51]
    do_q = [30] * 20 + [20, 15, 5]
    assert sum(so_q) == report["so"]["ordered_qty"] == 2411
    assert sum(do_q) == report["do"]["do_qty"] == 640
    report["so_by_customer"] = [
        {"customer_name": f"Dealer {i:02d} Sdn Bhd", "ordered_qty": q, "outstanding_qty": q}
        for i, q in enumerate(so_q, start=1)
    ]
    report["do_by_customer"] = [
        {"customer_name": f"Dealer {i:02d} Sdn Bhd", "do_qty": q, "pending_qty": q}
        for i, q in enumerate(do_q, start=1)
    ]
    return report


def _group_lines(rendered: str, heading: str) -> list[str]:
    """The lines of one group: from its heading to the next heading, blank line or end."""
    after = rendered.split(heading + "\n", 1)[1]
    lines: list[str] = []
    for line in after.splitlines():
        if not line or line.startswith("*_"):
            break
        lines.append(line)
    return lines


# --------------------------------------------------------------------------
# AC-1601 / AC-1602 / AC-1604: the prod shape, byte-equal
# --------------------------------------------------------------------------


def test_cap_is_twenty():
    assert OUTSTANDING_GROUP_CAP == 20


def test_prod_shape_by_product_prints_twenty_rows_then_the_trailer():
    rendered = _outstanding_report(prod_shape_report())
    assert rendered == _golden("outstanding-report-do-capped.txt")
    product_lines = _group_lines(rendered, "*_By product_*")
    assert len(product_lines) == 21, product_lines
    assert product_lines[0] == "SRTWT7001: 96 (O/S: 96)"
    assert product_lines[19] == "SRTWT7020: 4 (O/S: 4)"
    assert product_lines[20] == "... and 292 more products (reply 1 for the full list)"
    # the 21st product is the first one left off
    assert "SRTWT7021" not in rendered
    # counts and the closing offer line are whole and unchanged
    assert "Delivery orders: 148" in rendered
    assert "DO qty: 1,039" in rendered
    assert "Outstanding: 1,039" in rendered
    assert rendered.rstrip().endswith("Reply 1 for the delivery order list.")


def test_the_trailer_is_the_last_line_of_its_group_and_prints_once():
    rendered = _outstanding_report(prod_shape_report())
    assert rendered.count("more products") == 1
    lines = rendered.splitlines()
    trailer_at = lines.index("... and 292 more products (reply 1 for the full list)")
    assert lines[trailer_at + 1] == "", lines[trailer_at : trailer_at + 2]


# --------------------------------------------------------------------------
# AC-1603: the position in the trailer is that block's offer position
# --------------------------------------------------------------------------


def test_both_scopes_so_trailer_says_reply_1_and_do_trailer_says_reply_2():
    rendered = _outstanding_report(both_capped_report())
    assert rendered == _golden("outstanding-report-both-capped.txt")
    assert "... and 7 more customers (reply 1 for the full list)" in rendered, rendered
    assert "... and 3 more customers (reply 2 for the full list)" in rendered, rendered
    so_trailer_at = rendered.index("... and 7 more customers")
    do_trailer_at = rendered.index("... and 3 more customers")
    do_block_at = rendered.index("*Delivery order outstanding*")
    assert so_trailer_at < do_block_at < do_trailer_at
    assert rendered.splitlines()[-3:] == ["1. Sales order list", "2. Delivery order list", "3. Both lists"]


def test_do_only_offer_do_trailer_says_reply_1():
    report = both_capped_report()
    report["so"] = None
    rendered = _outstanding_report(report)
    assert "... and 3 more customers (reply 1 for the full list)" in rendered, rendered
    assert "reply 2" not in rendered
    assert rendered.rstrip().endswith("Reply 1 for the delivery order list.")


def test_so_miss_beside_a_capped_do_block_still_says_reply_1():
    """A partial miss offers only the hit scope as position 1 (`Reply 1 for the delivery
    order list.`), so the DO trailer must follow the OFFER, not the block order."""
    report = both_capped_report()
    report["so"]["so_count"] = 0
    rendered = _outstanding_report(report)
    assert "No open sales order." in rendered
    assert "... and 3 more customers (reply 1 for the full list)" in rendered, rendered
    assert "reply 2" not in rendered
    assert "more customers (reply 1" in rendered and rendered.count("more customers") == 1


# --------------------------------------------------------------------------
# AC-1602 (singular) / AC-1604 (at the cap, below the cap): the boundary
# --------------------------------------------------------------------------


def test_exactly_twenty_rows_print_whole_with_no_trailer():
    report = prod_shape_report()
    report["do_by_product"] = _product_rows(_PROD_SHAPE_TOP_20)
    rendered = _outstanding_report(report)
    product_lines = _group_lines(rendered, "*_By product_*")
    assert len(product_lines) == 20, product_lines
    assert "more" not in rendered
    assert "..." not in rendered


def test_twenty_one_rows_print_twenty_and_a_singular_trailer():
    report = prod_shape_report()
    report["do_by_product"] = _product_rows(_PROD_SHAPE_TOP_20 + [3])
    rendered = _outstanding_report(report)
    product_lines = _group_lines(rendered, "*_By product_*")
    assert len(product_lines) == 21, product_lines
    assert product_lines[-1] == "... and 1 more product (reply 1 for the full list)"
    assert "1 more products" not in rendered


def test_twenty_one_customers_print_a_singular_customer_trailer():
    report = both_capped_report()
    report["so"] = None
    report["do_by_customer"] = report["do_by_customer"][:21]
    rendered = _outstanding_report(report)
    assert "... and 1 more customer (reply 1 for the full list)" in rendered, rendered
    assert "1 more customers" not in rendered


def test_one_product_prints_one_row_and_no_trailer():
    report = prod_shape_report()
    report["do_by_product"] = _product_rows([7])
    rendered = _outstanding_report(report)
    assert _group_lines(rendered, "*_By product_*") == ["SRTWT7001: 7 (O/S: 7)"]
    assert "more" not in rendered


def test_fourteen_customers_still_print_whole():
    """The AC-1103 golden shape from the 13 Sep lane, unchanged: 14 is under the cap."""
    report = both_capped_report()
    report["so"] = None
    report["do_by_customer"] = report["do_by_customer"][:14]
    rendered = _outstanding_report(report)
    assert len(_group_lines(rendered, "*_By customer_*")) == 14
    assert "more" not in rendered


# --------------------------------------------------------------------------
# AC-1605: By location and the counts are never capped
# --------------------------------------------------------------------------


def test_by_location_longer_than_the_cap_prints_whole():
    report = prod_shape_report()
    report["do_by_location"] = [
        {"code": f"WH-{i:02d}", "do_qty": 40 - i, "pending_qty": 40 - i} for i in range(25)
    ]
    rendered = _outstanding_report(report)
    location_lines = _group_lines(rendered, "*_By location_*")
    assert len(location_lines) == 25, location_lines
    assert location_lines[-1] == "WH-24: 16 (O/S: 16)"
    assert "more locations" not in rendered
    # and the product group beside it is still capped
    assert "... and 292 more products (reply 1 for the full list)" in rendered


# --------------------------------------------------------------------------
# AC-1606: the detail list behind the trailer is unchanged
# --------------------------------------------------------------------------


def test_detail_list_prints_every_row_with_no_trailer():
    report = prod_shape_report()
    report["do_rows"] = [
        {
            "do_number": f"DO-26-{i:04d}", "customer_name": "Dealer A Sdn Bhd",
            "product_code": f"SRTWT{7000 + i:04d}", "location": "BRW-IB",
            "do_qty": 1, "delivered_qty": 0, "pending_qty": 1, "do_date": "2026-09-01",
        }
        for i in range(1, 301)
    ]
    rendered = _outstanding_detail(report, "do")
    assert rendered.count("*DO Number:*") == 300
    assert "300. *DO Number:* DO-26-0300" in rendered
    assert "more" not in rendered


# --------------------------------------------------------------------------
# AC-1607: the trailer is invisible to the offer reader
# --------------------------------------------------------------------------


@pytest.mark.parametrize("builder", [prod_shape_report, both_capped_report])
def test_offer_patterns_match_the_same_lines_capped_or_not(builder):
    capped = _outstanding_report(builder())
    report = builder()
    for key in ("so_by_customer", "do_by_customer", "so_by_product", "do_by_product"):
        if report.get(key):
            report[key] = report[key][:OUTSTANDING_GROUP_CAP]
    uncapped = _outstanding_report(report)
    assert "more" not in uncapped
    assert _SO_LIST_OFFER_RE.findall(capped) == _SO_LIST_OFFER_RE.findall(uncapped)
    assert _DO_LIST_OFFER_RE.findall(capped) == _DO_LIST_OFFER_RE.findall(uncapped)
    for line in capped.splitlines():
        if line.startswith("... and"):
            assert not _SO_LIST_OFFER_RE.search(line) and not _DO_LIST_OFFER_RE.search(line)


# --------------------------------------------------------------------------
# AC-1608: dash guard over the new goldens and the live output
# --------------------------------------------------------------------------

_BANNED = ("\u2014", "\u2013", "\u2192", "->", "=>", "+N more", "\u2026")


@pytest.mark.parametrize("name", ["outstanding-report-do-capped.txt", "outstanding-report-both-capped.txt"])
def test_dash_guard_over_the_capped_goldens(name: str):
    text = _golden(name)
    for banned in _BANNED:
        assert banned not in text, f"{name} carries {banned!r}"
    assert not re.search(r"-{2,}", text)


def test_dash_guard_over_the_capped_live_output():
    for text in (_outstanding_report(prod_shape_report()), _outstanding_report(both_capped_report())):
        for banned in _BANNED:
            assert banned not in text
        assert not re.search(r"\+\d+ more", text)
