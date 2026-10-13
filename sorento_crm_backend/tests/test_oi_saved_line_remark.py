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


def test_an_order_back_buy_reads_order_back_without_its_document():
    """Owner, 9 Oct (Q-A) and 13 Oct: just ORDER BACK."""
    assert saved_line_remark(_snap(_buy(), order_back=True, cited_document="SPO-2026/10-0032")) == (
        "ORDER BACK", "BRW-IB",
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


def test_several_row_lines_fold_by_their_numbers_not_their_words():
    """Review S1: the fold reads what each row line adds to the order as data. A fresh
    ORDER 50 whose REMARK also names a product change, a settle adding 3 after a date word,
    and a withdrawn row of 5 read ORDER 53, CANCEL ORDER 5."""
    from types import SimpleNamespace
    from uuid import uuid4

    from app.services.project_order_inquiry_service import (
        _HANDOVER_PENDING_KEY,
        ProjectOrderInquiryService,
    )

    from ._pg_fixture import blank_session

    with blank_session() as db:
        pso_id = str(uuid4())
        line = SimpleNamespace(
            id=str(uuid4()), line_no=10, core_sales_order_line_id=None, delivery_date=None,
        )

        def item(remark, qty, was=None, keys=("ORDER",), adds="0"):
            return {
                "row_id": str(uuid4()), "so_line_id": line.id, "pso_id": pso_id,
                "verb_keys": list(keys), "adds_order": adds,
                "line": {"remark": remark, "qty": qty, "was": was},
            }

        db.info[_HANDOVER_PENDING_KEY] = [
            item("ORDER, NEW ITEM CODE", "50", adds="50"),
            item("DELAY, ORDER 3", "8", was={"qty": "5"}, adds="3"),
            item("CANCEL ALL", "0", was={"qty": "5"}, keys=("CANCEL_BALANCE",)),
        ]
        before = _snap(_buy("40"), open_qty="40", required_date="2026-11-01")
        now = _snap(_buy("53"), open_qty="53", required_date="2026-11-01")
        ProjectOrderInquiryService(db).record_saved_lines(
            pso_id=pso_id, saved=[(line, now, before)],
        )
        (told,) = db.info[_HANDOVER_PENDING_KEY]
        assert told["line"]["remark"] == "ORDER 53, CANCEL ORDER 5", told["line"]


def test_a_newly_short_reserve_is_a_change_to_tell():
    """Review S3: re-approved unchanged but now short of free stock - still news."""
    from app.services.project_order_inquiry_service import _saved_decision_key

    before = _snap(_reserve("10", "BRW", rung="pool"))
    now = _snap(_reserve("10", "BRW", rung="pool"), stock_short=[{"short": "3"}])
    assert _saved_decision_key(before) != _saved_decision_key(now)


def test_a_first_borrow_reads_its_decision_not_its_donor_hole_row():
    """Review S2: a first Confirm's Borrow whose donor hole row told one line prints the
    decision line (ORDER BACK, the line's qty, the line's own location), not the hole."""
    from types import SimpleNamespace
    from uuid import uuid4

    from app.services.project_order_inquiry_service import (
        _HANDOVER_PENDING_KEY,
        ProjectOrderInquiryService,
    )

    from ._pg_fixture import blank_session

    with blank_session() as db:
        pso_id = str(uuid4())
        line = SimpleNamespace(
            id=str(uuid4()), line_no=10, core_sales_order_line_id=None, delivery_date=None,
        )
        db.info[_HANDOVER_PENDING_KEY] = [{
            "row_id": str(uuid4()), "so_line_id": line.id, "pso_id": pso_id,
            "verb_keys": ["ORDER_BACK"], "adds_order": "4",
            "line": {"remark": "ORDER BACK", "qty": "4", "was": None, "location": "MWH-BB"},
        }]
        now = _snap(_borrow("10", "MWH-BB"))
        ProjectOrderInquiryService(db).record_saved_lines(
            pso_id=pso_id, saved=[(line, now, None)],
        )
        (told,) = db.info[_HANDOVER_PENDING_KEY]
        assert (told["line"]["remark"], told["line"]["qty"], told["line"]["location"]) == (
            "ORDER BACK", "10", "BRW-IB",
        ), told["line"]


def test_which_saved_lines_were_told_does_not_outlive_its_commit():
    """Review S1: the told set belongs to one write. A later write on the same session (a
    planning-change apply whose confirm returned before telling anything) must not forget
    its own DELAY / CANCEL BALANCE lines on an earlier Confirm's account."""
    from app.services.project_order_inquiry_service import (
        _SAVED_TOLD_KEY,
        register_order_inquiry_post_commit_dispatch,
    )

    from ._pg_fixture import blank_session

    register_order_inquiry_post_commit_dispatch()
    with blank_session() as db:
        db.info[_SAVED_TOLD_KEY] = {"pso": {"line"}}
        db.commit()
        assert _SAVED_TOLD_KEY not in db.info
