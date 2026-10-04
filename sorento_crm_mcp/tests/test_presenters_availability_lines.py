"""Chatbot stock ask v2 S3, R14 (lavish review): the four fixed sentences.
AC-SA312, AC-SA313, chatbot-stock-ask-v2-24sep-acceptance-criteria.md.
REFER-SALESMAN (owner ruling 30 Sep 2026, AC-RS01): the verdict closes as its own sentence
and the refer is exactly "Please refer to your salesman." on every branch that refers.
"""
from __future__ import annotations

import re

from sorento_crm_mcp.presenters import _availability_line


def _entry(**over):
    base = {
        "product_id": "11111111-1111-4111-8111-111111111111",
        "product_code": "SRT-CODE",
        "product_name": "Widget",
        "needs_quantity": False,
        "requested_qty": 50,
        "branch": "in_stock",
        "cap_unset": None,
        "category_name": "Wiper Blades",
        "eta": None,
        "packing_list": None,
    }
    base.update(over)
    return base


def test_too_big_sentence():
    line = _availability_line(_entry(product_code="CWCX604", requested_qty=300, branch="too_big"))
    assert line == (
        "CWCX604 x 300: 🚫 the quantity is more than what I can confirm here. "
        "Please refer to your salesman."
    )


def test_too_big_sentence_identical_when_cap_unset():
    # R14: the sentence itself never differs by WHY it is too_big (cap set too low
    # vs cap never set) - `cap_unset` is agent-notification-only context (S4), not
    # something the dealer reads.
    capped = _availability_line(_entry(requested_qty=20, branch="too_big", cap_unset=False))
    uncapped = _availability_line(_entry(requested_qty=20, branch="too_big", cap_unset=True))
    assert capped == uncapped


def test_in_stock_sentence():
    line = _availability_line(_entry(product_code="SRT5674", requested_qty=50, branch="in_stock"))
    assert line == "SRT5674 x 50: ✅ Please refer to your salesman."


def test_incoming_sentence_names_the_eta():
    line = _availability_line(
        _entry(product_code="SRTW2000", requested_qty=150, branch="incoming", eta="19/10/2026")
    )
    assert line == "SRTW2000 x 150: ❌ ETA 19/10/2026."


def test_no_incoming_sentence():
    line = _availability_line(_entry(product_code="SRT5674", requested_qty=150, branch="no_incoming"))
    assert line == (
        "SRT5674 x 150: ❌ No stock and no incoming. Please refer to your salesman."
    )


def test_every_line_starts_with_code_x_quantity():
    for branch, extra in (
        ("too_big", {}),
        ("in_stock", {}),
        ("incoming", {"eta": "02/11/2026"}),
        ("no_incoming", {}),
    ):
        line = _availability_line(_entry(product_code="SRT6536", requested_qty=7, branch=branch, **extra))
        assert line.startswith("SRT6536 x 7:")


def test_asked_order_multi_product_reply_reads_line_by_line():
    # Plan sample (g): "Need SRT5674 x 50 and CWCX604 x 300."
    entries = [
        _entry(product_code="SRT5674", requested_qty=50, branch="in_stock"),
        _entry(product_code="CWCX604", requested_qty=300, branch="too_big"),
    ]
    lines = [_availability_line(e) for e in entries]
    assert lines == [
        "SRT5674 x 50: ✅ Please refer to your salesman.",
        "CWCX604 x 300: 🚫 the quantity is more than what I can confirm here. "
        "Please refer to your salesman.",
    ]


def test_falls_back_to_product_name_never_the_id():
    line = _availability_line(
        _entry(product_code=None, product_name="Widget", requested_qty=10, branch="in_stock")
    )
    assert line.startswith("Widget x 10:")
    assert "11111111-1111-4111-8111-111111111111" not in line


def test_no_running_low_no_purchase_no_uuid_in_any_sentence():
    for branch, extra in (
        ("too_big", {}),
        ("in_stock", {}),
        ("incoming", {"eta": "19/10/2026"}),
        ("no_incoming", {}),
    ):
        line = _availability_line(_entry(branch=branch, **extra))
        assert "running low" not in line
        assert "purchase" not in line
        assert "11111111-1111-4111-8111-111111111111" not in line


def test_ac_sa312_no_digit_of_ours_besides_qty_and_eta_date():
    """Product code and category name are digit-free on purpose, so a regex
    over the rendered line finds only Q and the ETA's three date components."""
    line = _availability_line(
        _entry(product_code="SRT-ABC", requested_qty=150, branch="incoming", eta="19/10/2026")
    )
    assert re.findall(r"\d+", line) == ["150", "19", "10", "2026"]

    for branch in ("too_big", "in_stock", "no_incoming"):
        line = _availability_line(_entry(product_code="SRT-ABC", requested_qty=42, branch=branch))
        assert re.findall(r"\d+", line) == ["42"]


def test_every_refer_line_ends_with_the_one_refer_sentence():
    """AC-RS01 / AC-RS03: no "to proceed", no "salesperson", no name, on any branch."""
    for branch in ("too_big", "in_stock", "no_incoming"):
        line = _availability_line(_entry(product_code="SRT1", requested_qty=3, branch=branch))
        # AVAIL-MODE-REPLIES: the in-stock verdict is now the tick alone, so the refer
        # sentence follows it rather than a full stop.
        assert re.search(r"(\.|✅) Please refer to your salesman\.$", line), line
        assert "to proceed" not in line and "salesperson" not in line
        assert line.count("Please refer") == 1
