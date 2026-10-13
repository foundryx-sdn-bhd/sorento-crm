"""ORDERBACK-DELTA extension (owner, 13 Oct 2026 22:40): one purchasing email line per SAVED
line, its REMARK saying what was decided, in the board's own words
(`sorento_crm_frontend/.../_shared/lib/supplyVocabulary.ts`) and the owner's Excel spelling.

`saved_line_remark(snapshot)` reads ONE frozen line snapshot (`ProjectSupplyService._snapshot`)
and returns `(remark, stock_location)`; STOCK LOCATION is the SO line's own location. Pure: no database.
"""
from __future__ import annotations

from app.services.project_order_inquiry_service import saved_line_remark


def _snap(*components, own="BRW-IB", **extra):
    return {"location": own, "open_qty": "10", "components": list(components), **extra}


def _buy(qty="10"):
    return {"kind": "buy", "qty": qty}


def _reserve(qty, location, rung=None):
    return {"kind": "reserve", "qty": qty, "source_location": location, "rung": rung}


def _borrow(qty, location, rung=None, supply_document=None):
    return {
        "kind": "borrow", "qty": qty, "source": "other_location",
        "source_location": location, "rung": rung, "supply_document": supply_document,
    }


def test_buy_reads_order_at_the_lines_own_location():
    assert saved_line_remark(_snap(_buy())) == ("ORDER", "BRW-IB")


def test_an_order_back_buy_names_its_cited_document():
    assert saved_line_remark(_snap(_buy(), order_back=True, cited_document="SPO-2026/10-0032")) == (
        "ORDER BACK SPO-2026/10-0032", "BRW-IB",
    )
    assert saved_line_remark(_snap(_buy(), order_back=True)) == ("ORDER BACK", "BRW-IB")


def test_use_brw_reads_use_and_the_pool_code():
    """The shared site pool's own warehouse code is the plain site code (BRW); the line's
    own location carries the group suffix (BRW-BB). Owner, 13 Oct: "USE BRW"."""
    assert saved_line_remark(_snap(_reserve("10", "BRW", rung="pool"), own="BRW-BB")) == (
        "USE BRW", "BRW-BB",
    )


def test_use_own_location_reads_use_and_the_location():
    """Owner, 13 Oct: own location stock reads USE <own location code>; no RESERVE word."""
    assert saved_line_remark(_snap(_reserve("10", "BRW-BB"), own="BRW-BB")) == (
        "USE BRW-BB", "BRW-BB",
    )
    assert saved_line_remark(_snap(_reserve("10", "BRW-IC", rung="group_take"))) == (
        "USE BRW-IC", "BRW-IB",
    )


def test_a_borrow_reads_order_back_at_the_lines_own_location():
    """Owner, 13 Oct: just ORDER BACK; STOCK LOCATION is the SO line's own location, which
    is where it is ordered back to."""
    assert saved_line_remark(_snap(_borrow("10", "MWH-BB"))) == ("ORDER BACK", "BRW-IB")
    assert saved_line_remark(_snap(_borrow("10", "DC1-BB", rung="group_borrow"))) == (
        "ORDER BACK", "BRW-IB",
    )


def test_borrow_incoming_reads_the_document_number():
    component = _borrow("10", "BRW-IB", rung="supply_borrow", supply_document="SPO-2026/10-0032")
    assert saved_line_remark(_snap(component)) == ("SPO-2026/10-0032", "BRW-IB")


def test_use_incoming_reads_the_spo_numbers():
    snapshot = _snap(
        {"kind": "timely_spo", "qty": "10", "source_location": "BRW-IB", "rung": "group_take"},
        timely_spo_refs=[{"spo_number": "SPO-2026/10-0032"}, {"spo_number": "SPO-2026/10-0040"}],
    )
    assert saved_line_remark(snapshot) == ("SPO-2026/10-0032, SPO-2026/10-0040", "BRW-IB")


def test_several_parts_name_each_with_its_quantity():
    snapshot = _snap(_reserve("6", "BRW-IB"), _buy("4"))
    assert saved_line_remark(snapshot) == ("USE BRW-IB 6 & ORDER 4", "BRW-IB")


def test_a_zero_quantity_part_is_not_named():
    snapshot = _snap(_reserve("10", "BRW", rung="pool"), _buy("0"))
    assert saved_line_remark(snapshot) == ("USE BRW", "BRW-IB")


def test_a_local_buy_reads_local_buy():
    """Owner, 13 Oct (Q3): purchasing does not place a local Buy (R9), and the line still
    appears so the email matches Saved (N)."""
    assert saved_line_remark(_snap(_buy(), buy_origin="local")) == ("LOCAL BUY", "BRW-IB")


def test_a_bare_spo_number_ref_is_read_too():
    snapshot = _snap(
        {"kind": "timely_spo", "qty": "4", "source_location": "BRW-IB", "rung": "group_take"},
        timely_spo_refs=["SPO-2026/10-0032"],
    )
    assert saved_line_remark(snapshot) == ("SPO-2026/10-0032", "BRW-IB")
