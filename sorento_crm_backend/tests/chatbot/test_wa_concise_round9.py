"""WA-CONCISE round 9 (red-first): the PO part of a zero-stock block names the document
number instead of "placed", and one PO line prints once. Placeholder data only."""
from __future__ import annotations

from tests.chatbot import test_wa_concise_crossdomain as xd
from tests.chatbot import test_wa_concise_review_round2 as r2
from tests.chatbot._wa_concise_helpers import (
    INCOMING_TOOL,
    PO_TOOL,
    STOCK_TOOL,
    detailed_row,
    envelope_json,
)
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures re-exported by name
    seeded,
    stub_access,
    stub_parser,
)

HEAD = "*Product Code:* SRTX2\n*Incoming:* none\n*Stock:* 0\n"


def _po_line(number, ordered, outstanding, date, location, *, kind=None, code="SRTX2"):
    row = {
        "po_number": number, "product_code": code, "ordered_qty": ordered,
        "outstanding_qty": outstanding, "po_date": date, "location": location,
    }
    if kind:
        row["kind"] = kind
    return row


def _turn(rows, session_factory, stub_parser, stub_access, monkeypatch):
    def po(args):
        if not rows:
            return envelope_json(PO_TOOL, xd.EMPTY_PAYLOAD)
        return envelope_json(PO_TOOL, {"data": rows, "pagination": {"total": len(rows)}})

    tools = {
        INCOMING_TOOL: xd._incoming({}),
        STOCK_TOOL: r2._detailed_resp([detailed_row("Sorento", "SRTX2", "BRW", 0, 0)], ["Sorento"]),
        PO_TOOL: po,
    }
    said, _ = xd._ask_incoming(
        **r2._fx(session_factory, stub_parser, stub_access, monkeypatch), codes=["SRTX2"], tools=tools
    )
    return said


def test_a_two_lines_on_the_same_po_merge_into_one_block_with_summed_quantities(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    rows = [_po_line("202604-S0036", 36, 36, "2026-04-08", "BRW-BB") for _ in range(2)]

    said = _turn(rows, session_factory, stub_parser, stub_access, monkeypatch)

    assert "placed" not in said, said
    assert said == (
        HEAD + "*PO:* 202604-S0036\n*Ordered:* 72\n*Outstanding:* 72\n*PO date:* 2026-04-08\n"
        f"*Location:* BRW-BB\n\n{xd.OFFER}"
    ), said


def test_b_two_different_pos_are_two_consecutive_blocks_each_named(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    rows = [
        _po_line("PO-0001", 10, 10, "2026-09-11", "BRW"),
        _po_line("PO-0002", 20, 15, "2026-09-12", "BRW"),
    ]

    said = _turn(rows, session_factory, stub_parser, stub_access, monkeypatch)

    assert said == (
        HEAD
        + "*PO:* PO-0001\n*Ordered:* 10\n*Outstanding:* 10\n*PO date:* 2026-09-11\n*Location:* BRW\n"
        + "*PO:* PO-0002\n*Ordered:* 20\n*Outstanding:* 15\n*PO date:* 2026-09-12\n*Location:* BRW"
        + f"\n\n{xd.OFFER}"
    ), said


def test_c_an_spo_row_starts_with_the_spo_number(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    rows = [_po_line("SPO-0007", 8, 8, "2026-09-11", "BRW", kind="SPO")]

    said = _turn(rows, session_factory, stub_parser, stub_access, monkeypatch)

    assert said == (
        HEAD + "*SPO:* SPO-0007\n*Ordered:* 8\n*Outstanding:* 8\n*PO date:* 2026-09-11\n"
        f"*Location:* BRW\n\n{xd.OFFER}"
    ), said


def test_d_guard_nothing_on_order_still_prints_po_none(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    said = _turn([], session_factory, stub_parser, stub_access, monkeypatch)

    assert said == f"{HEAD}*PO:* none\n\n{xd.OFFER}", said
