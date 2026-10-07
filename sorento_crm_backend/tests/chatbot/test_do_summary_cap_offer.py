"""DO-SUMMARY-CAP, AC-1607, the backend half: the pending detail offer the chatbot lane
reads off a RENDERED outstanding summary (`fetch._outstanding_offer_from_text`) is the
same roster whether or not the presenter capped a `*_By product_*` / `*_By customer_*`
group and closed it with the trailer line `... and K more products (reply P for the delivery
order list)`. The backend image cannot import the MCP package (the presenter lives there), so
this side pins the trailer bytes as a literal, the way `test_outstanding_lane` pins the
offer lines; the MCP suite pins the same from its side
(`sorento_crm_mcp/tests/test_presenters_outstanding_cap.py`).

"reply 1 for the delivery order list" in the trailer is a pointer at the offer the summary
already ends with (R2: it names that list by the offer line's own words), never a second offer: the roster must carry exactly the positions the closing line(s) state.
"""
from __future__ import annotations

from app.services.chatbot.lanes.business.fetch import _outstanding_offer_from_text

_HEADER = "Product: all\nCustomer: Dealer A Sdn Bhd\nLocation: all\nOrder date: all\n\n"

_DO_BLOCK_CAPPED = (
    "*Delivery order outstanding*\n"
    "Delivery orders: 148\n"
    "DO qty: 1,039\n"
    "Delivered: 0\n"
    "Outstanding: 1,039\n"
    "DO date range: 12/03/2026 to 05/10/2026\n"
    "*_By location_*\n"
    "BRW-IB: 693 (O/S: 693)\n"
    "MWH-IB: 346 (O/S: 346)\n"
    "*_By product_*\n"
    + "".join(f"SRTWT{7000 + i:04d}: 3 (O/S: 3)\n" for i in range(1, 21))
)

_SO_BLOCK_CAPPED = (
    "*Sales order outstanding*\n"
    "Sales orders: 12\n"
    "Ordered: 2,411\n"
    "Transferred to DO: 0\n"
    "Outstanding: 2,411\n"
    "Order date range: 05/01/2026 to 28/08/2026\n"
    "*_By location_*\n"
    "MWH-IB: 1,211 (O/S: 1,211)\n"
    "BRW-IB: 1,200 (O/S: 1,200)\n"
    "*_By customer_*\n"
    + "".join(f"Dealer {i:02d} Sdn Bhd: 100 (O/S: 100)\n" for i in range(1, 21))
)


def test_do_only_capped_summary_reads_the_same_single_offer():
    trailer = "... and 292 more products (reply 1 for the delivery order list)\n"
    closing = "\nReply 1 for the delivery order list."
    capped = _HEADER + _DO_BLOCK_CAPPED + trailer + closing
    uncapped = _HEADER + _DO_BLOCK_CAPPED + closing
    expected = [{"idx": 1, "label": "Delivery order list", "value": "do"}]
    assert _outstanding_offer_from_text(uncapped) == expected
    assert _outstanding_offer_from_text(capped) == expected


def test_both_scope_capped_summary_reads_the_same_three_option_offer():
    so_trailer = "... and 7 more customers (reply 1 for the sales order list)\n"
    do_trailer = "... and 3 more customers (reply 2 for the delivery order list)\n"
    closing = "\nReply with a number for detail:\n1. Sales order list\n2. Delivery order list\n3. Both lists"
    capped = _HEADER + _SO_BLOCK_CAPPED + so_trailer + "\n" + _DO_BLOCK_CAPPED + do_trailer + closing
    uncapped = _HEADER + _SO_BLOCK_CAPPED + "\n" + _DO_BLOCK_CAPPED + closing
    expected = [
        {"idx": 1, "label": "Sales order list", "value": "so"},
        {"idx": 2, "label": "Delivery order list", "value": "do"},
        {"idx": 3, "label": "Both lists", "value": "both"},
    ]
    assert _outstanding_offer_from_text(uncapped) == expected
    assert _outstanding_offer_from_text(capped) == expected


def test_a_trailer_alone_is_no_offer():
    """The trailer never stands in for the closing line: a text with the trailer but no
    offer line arms nothing (so the trailer cannot conjure a roster the customer cannot
    see at the bottom of the reply)."""
    assert _outstanding_offer_from_text(
        _HEADER + _DO_BLOCK_CAPPED + "... and 292 more products (reply 1 for the delivery order list)"
    ) == []
