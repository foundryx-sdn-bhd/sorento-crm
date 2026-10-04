"""AVAIL-MODE-REPLIES (owner, 2 Oct 2026): availability-mode lines read as emoji, a short
in-stock answer names how many are available, and a dealer's ETA line is one line per code.

Plan: documentation/plans/chatbot/PLAN-avail-mode-replies-02oct.md, behaviour card rules 1-3.
"""
from __future__ import annotations

import json
import re

from sorento_crm_mcp.presenters import _availability_line, present_response


def _entry(**over):
    base = {
        "product_id": "11111111-1111-4111-8111-111111111111",
        "product_code": "SRT-CODE",
        "product_name": "Widget",
        "needs_quantity": False,
        "requested_qty": 50,
        "branch": "in_stock",
        "cap_unset": None,
        "category_name": "Basins",
        "eta": None,
        "packing_list": None,
        "available_qty": None,
    }
    base.update(over)
    return base


def test_in_stock_covering_q_is_a_tick():
    line = _availability_line(_entry(product_code="SRT5674", requested_qty=50))
    assert line == "SRT5674 x 50: ✅ Please refer to your salesman."


def test_in_stock_short_of_q_names_the_available_count():
    line = _availability_line(_entry(product_code="SRT5674", requested_qty=50, available_qty=30))
    assert line == "SRT5674 x 50: ✅ 30 available. Please refer to your salesman."


def test_incoming_is_a_cross_with_the_eta():
    line = _availability_line(
        _entry(product_code="SRTW2000", requested_qty=150, branch="incoming", eta="19/10/2026")
    )
    assert line == "SRTW2000 x 150: ❌ ETA 19/10/2026."


def test_no_incoming_is_a_cross():
    line = _availability_line(_entry(product_code="SRT5674", requested_qty=150, branch="no_incoming"))
    assert line == "SRT5674 x 150: ❌ No stock and no incoming. Please refer to your salesman."


def test_too_big_is_blocked_and_claims_nothing():
    """Owner v2 note 1 (2 Oct 2026): a blocked mark, never a tick or a cross, no count."""
    line = _availability_line(
        _entry(product_code="CWCX604", requested_qty=300, branch="too_big", available_qty=30)
    )
    assert line == (
        "CWCX604 x 300: \U0001F6AB the quantity is more than what I can confirm here. "
        "Please refer to your salesman."
    )
    assert "✅" not in line and "❌" not in line
    assert re.findall(r"\d+", line) == ["604", "300"]


def test_available_count_is_the_only_new_digit_and_only_on_a_short_in_stock():
    line = _availability_line(_entry(product_code="SRT-ABC", requested_qty=50, available_qty=30))
    assert re.findall(r"\d+", line) == ["50", "30"]
    for branch in ("no_incoming", "too_big"):
        other = _availability_line(
            _entry(product_code="SRT-ABC", requested_qty=50, branch=branch, available_qty=30)
        )
        assert re.findall(r"\d+", other) == ["50"], other


def test_no_word_got_stock_or_no_stock_left():
    """REFER-ONLY-FIXES (owner, 3 Oct 2026): the one exception is `no_incoming`, whose
    bare "No incoming" read as if stock were there; it says "No stock and no incoming"."""
    for branch, extra in (
        ("in_stock", {}),
        ("in_stock", {"available_qty": 3}),
        ("incoming", {"eta": "19/10/2026"}),
    ):
        line = _availability_line(_entry(branch=branch, **extra)).lower()
        assert "we have stock" not in line and "no stock" not in line, line


def _dealer_incoming(rows):
    raw = json.dumps({"data": rows, "dealer_view": True})
    return json.loads(present_response("crm_incoming_stock_by_product", raw))


def test_dealer_eta_is_one_line_per_code():
    out = _dealer_incoming(
        [
            {"product_code": "SRTW2000", "etas": ["19/10/2026", "02/11/2026"]},
            {"product_code": "MWT5727SS-CR", "etas": []},
        ]
    )
    assert [i["title"] for i in out["items"]] == [
        # Owner hand test, 3 Oct 2026: an ETA is a tick, none is "No ETA".
        "SRTW2000: \u2705 ETA 19/10/2026, 02/11/2026",
        "MWT5727SS-CR: No ETA",
    ]
    for item in out["items"]:
        assert "\n" not in item["title"]


def test_a_mixed_block_stamps_each_answered_entry_and_leaves_the_owed_one():
    """AVAIL-MODE-REPLIES rule 5: an answered line is printed beside a product still owed
    its quantity, so it is stamped for Customer asks on that turn; the owed one is not."""
    from sorento_crm_mcp.presenters import _stamp_refers

    stamped = _stamp_refers(
        [
            _entry(product_code="SRT5674", requested_qty=5),
            _entry(product_code="CWCX604", requested_qty=None, needs_quantity=True, branch=None),
        ]
    )
    assert stamped[0]["refers_to_salesman"] is True
    assert "refers_to_salesman" not in stamped[1]


def test_no_stock_and_no_incoming_says_both():
    """REFER-ONLY-FIXES (owner, 3 Oct 2026): `no_incoming` is reached only with no stock
    (`stock_ask_branch.branch`), so a bare "No incoming" read as if stock were there."""
    line = _availability_line(_entry(product_code="SRTWC286-SH", requested_qty=5, branch="no_incoming"))
    assert line == "SRTWC286-SH x 5: ❌ No stock and no incoming. Please refer to your salesman."
