"""STAFF-STOCK-ETA rescope (owner ruling 13 Oct 2026): the stock reply's PO block prints
open PO lines of the same product, same PO document, same location and same date as
ONE line, ordered and outstanding summed. Lines that differ in any of those stay apart.

Live case (staff "stock SRTWC286"): one PO held two open lines of SRTWC286-SH-PP, 36 + 36,
same location and PO date, and the block printed the same five lines twice. The block
never prints the PO number (owner ruling D3, 12 Sep 2026), so two such lines read as a
repeat and only cost message length.
"""
from __future__ import annotations

from tests.chatbot.test_crossdomain_ladder import _po_row, _run, _row_block

_LADDER = {"inventory": ["incoming", "purchase_order"]}
_NO_INCOMING = {"answers": [], "has_result": False}


def _block(po_items: list[dict]) -> str:
    result, _calls = _run(
        ladder=_LADDER,
        incoming_response=_NO_INCOMING,
        po_response={"answers": po_items, "has_result": True},
    )
    return result["render"]["_xdBlock"]["block"]


def test_two_lines_same_po_location_and_date_print_as_one_summed_line() -> None:
    block = _block([_po_row("36", None), _po_row("36", None)])
    assert block.count("*Product Code:* SRTWC8517") == 1
    assert _row_block(qty="72") in block
    assert "*Ordered:* 36" not in block


def test_three_lines_sum_and_a_decimal_quantity_keeps_its_fraction() -> None:
    block = _block([_po_row("10", None), _po_row("2.5", None), _po_row("1", None)])
    assert block.count("*Product Code:* SRTWC8517") == 1
    assert _row_block(qty="13.5") in block


def test_a_different_po_document_stays_a_separate_line() -> None:
    block = _block([_po_row("36", None), _po_row("36", None, po_number="PO-2002")])
    assert block.count(_row_block(qty="36")) == 2
    assert "72" not in block


def test_a_different_location_stays_a_separate_line() -> None:
    block = _block([_po_row("36", None), _po_row("36", None, location="BRW-BB")])
    assert _row_block(qty="36") in block
    assert _row_block(qty="36", location="BRW-BB") in block
    assert "72" not in block


def test_a_different_po_date_stays_a_separate_line() -> None:
    block = _block([_po_row("36", None), _po_row("36", None, po_date="2026-06-01")])
    assert _row_block(qty="36") in block
    assert _row_block(qty="36", po_date="2026-06-01") in block
    assert "72" not in block


def test_a_po_line_and_an_spo_allocation_with_one_number_stay_apart() -> None:
    block = _block([_po_row("36", None), _po_row("36", None, kind="spo")])
    assert block.count(_row_block(qty="36")) == 2


def test_lines_with_no_po_number_are_never_merged() -> None:
    # No document number means "same document" cannot be established.
    block = _block([_po_row("36", None, po_number=None), _po_row("36", None, po_number=None)])
    assert block.count(_row_block(qty="36")) == 2


def test_a_non_numeric_quantity_is_never_summed() -> None:
    block = _block([_po_row("36", None), _po_row("n/a", None)])
    assert _row_block(qty="36") in block
    assert _row_block(qty="n/a") in block


def test_the_merged_line_keeps_the_first_line_position() -> None:
    block = _block(
        [
            _po_row("36", None),
            _po_row("5", None, location="BRW-BB"),
            _po_row("36", None),
        ]
    )
    merged = _row_block(qty="72")
    other = _row_block(qty="5", location="BRW-BB")
    assert block.index(merged) < block.index(other)
    assert block.count("*Product Code:* SRTWC8517") == 2
