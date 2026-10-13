"""WA-CONCISE review round 2 (red-first). Placeholder data only."""
from __future__ import annotations

import pytest

from tests.chatbot import test_wa_concise_crossdomain as xd
from tests.chatbot._wa_concise_helpers import (
    INCOMING_TOOL, PO_TOOL, STOCK_TOOL, EMPTY_PAYLOAD, detailed_payload, detailed_row, envelope_json,
)
from tests.chatbot.test_engine import CONTACT_ID  # noqa: F401
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures re-exported by name
    seeded, stub_access, stub_parser,
)

xd.UUIDS.update({
    "SRTX": "99990000-0000-0000-0000-000000000001",
    "SRTX2": "99990000-0000-0000-0000-000000000002",
    "SRTY1": "99990000-0000-0000-0000-000000000003",
})

def _po_rows(code, ordered=30):
    def _fn(args):
        return envelope_json(PO_TOOL, {"data": [{"po_number": "PO-0002", "product_code": code,
            "ordered_qty": ordered, "outstanding_qty": ordered, "po_date": "2026-09-11", "location": "BRW"}],
            "pagination": {"total": 1}})
    return _fn

def _detailed_resp(rows, companies):
    def _fn(args):
        payload = detailed_payload(rows)
        payload["lookup_companies"] = [{"name": n} for n in companies]
        return envelope_json(STOCK_TOOL, payload)
    return _fn

def _fx(session_factory, stub_parser, stub_access, monkeypatch):
    return dict(session_factory=session_factory, monkeypatch=monkeypatch, stub_parser=stub_parser, stub_access=stub_access)

def _row_x():
    r = detailed_row("Sorento", "SRTX", "BRW", 0, 233, name="LAMP")
    r["is_discontinued"] = True
    return r


INC_X = xd.incoming_shipment("SRTX", "IAAU1907074", "2026-09-09", 49, [("BRW", 35), ("BRW-BB", 4), ("BRW-SMC", 10)])
INC_X_LINES = (
    "*Container:* IAAU1907074\n*ETA:* 2026-09-09\n*Incoming Quantity:* 49\n"
    "*Warehouse Allocations:* BRW (35), BRW-BB (4), BRW-SMC (10)"
)
FLAG = "\u26a0\ufe0f  *(PRODUCT DISCONTINUED)*"
FOOTER = "\n\n_Updated 11/09/2026 17:26_"
OFFER = xd.OFFER
WH_OFFER = "Would you like me to escalate to warehouse team?"


def _stock_two(args):
    codes = xd._uuid_codes(args)
    rows = []
    if "SRTY1" in codes:
        rows.append(detailed_row("Sorento", "SRTY1", "BRW", 5))
    if "SRTX" in codes:
        rows.append(_row_x())
    return envelope_json(STOCK_TOOL, detailed_payload(rows))


def _no_footer(text: str) -> str:
    """The footer's position is the coder's call; its presence is not asserted here."""
    return text.replace(FOOTER, "")


# ---- B1: one block per company, PO lines once in the first ---------------- #


def _b1_tools(po):
    return {
        INCOMING_TOOL: xd._incoming({}),
        STOCK_TOOL: _detailed_resp(
            [detailed_row("Sorento", "SRTX2", "BRW", 0, 0), detailed_row("Mocha", "SRTX2", "MOCHA-WH", 0, 7)],
            ["Sorento", "Mocha"],
        ),
        PO_TOOL: po,
    }


def test_b1_two_companies_one_block_each_po_lines_once_in_the_first(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    said, _ = xd._ask_incoming(
        **_fx(session_factory, stub_parser, stub_access, monkeypatch),
        codes=["SRTX2"], tools=_b1_tools(_po_rows("SRTX2")),
    )

    assert said == (
        "1. *Company:* Sorento\n*Product Code:* SRTX2\n*Incoming:* none\n*Stock:* 0\n"
        "*PO:* PO-0002\n*Ordered:* 30\n*Outstanding:* 30\n*PO date:* 2026-09-11\n*Location:* BRW\n\n"
        "2. *Company:* Mocha\n*Product Code:* SRTX2\n*Incoming:* none\n*MOCHA-WH:* 0 (O/S: 7)\n\n"
        f"{OFFER}"
    ), said


def test_b1_two_companies_nothing_on_order_po_none_once_in_the_first(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    said, _ = xd._ask_incoming(
        **_fx(session_factory, stub_parser, stub_access, monkeypatch),
        codes=["SRTX2"], tools=_b1_tools(xd._po()),
    )

    assert said == (
        "1. *Company:* Sorento\n*Product Code:* SRTX2\n*Incoming:* none\n*Stock:* 0\n*PO:* none\n\n"
        "2. *Company:* Mocha\n*Product Code:* SRTX2\n*Incoming:* none\n*MOCHA-WH:* 0 (O/S: 7)\n\n"
        f"{OFFER}"
    ), said


def test_b1_guard_one_company_prints_po_lines_as_today(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    tools = {
        INCOMING_TOOL: xd._incoming({}),
        STOCK_TOOL: _detailed_resp([detailed_row("Sorento", "SRTX2", "BRW", 0, 0)], ["Sorento"]),
        PO_TOOL: _po_rows("SRTX2"),
    }
    said, _ = xd._ask_incoming(
        **_fx(session_factory, stub_parser, stub_access, monkeypatch), codes=["SRTX2"], tools=tools
    )

    assert said == (
        "*Product Code:* SRTX2\n*Incoming:* none\n*Stock:* 0\n*PO:* PO-0002\n*Ordered:* 30\n"
        f"*Outstanding:* 30\n*PO date:* 2026-09-11\n*Location:* BRW\n\n{OFFER}"
    ), said


# ---- B2: a stock ask that climbs to incoming loses nothing ---------------- #


def test_b2a_one_code_keeps_name_outstanding_and_flag_once(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    said, _ = xd._ask_stock(
        **_fx(session_factory, stub_parser, stub_access, monkeypatch),
        codes=["SRTX"],
        tools={
            INCOMING_TOOL: xd._incoming({"SRTX": INC_X}),
            STOCK_TOOL: _detailed_resp([_row_x()], ["Sorento"]),
            PO_TOOL: xd._po(),
        },
    )

    assert _no_footer(said) == (
        "*Product Code:* SRTX\n*Product Name:* LAMP\n*BRW:* 0 (O/S: 233)\n"
        f"{INC_X_LINES}\n{FLAG}\n\n{WH_OFFER}"
    ), said


def test_b2b_several_codes_each_code_prints_exactly_once(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    said, _ = xd._ask_stock(
        **_fx(session_factory, stub_parser, stub_access, monkeypatch),
        codes=["SRTY1", "SRTX"],
        tools={
            INCOMING_TOOL: xd._incoming({"SRTX": INC_X}),
            STOCK_TOOL: _stock_two,
            PO_TOOL: xd._po(),
        },
    )

    assert said.count("Product Code:* SRTX\n") == 1, said
    assert _no_footer(said) == (
        "1. *Product Code:* SRTY1\n*BRW:* 5\n\n"
        "2. *Product Code:* SRTX\n*Product Name:* LAMP\n*BRW:* 0 (O/S: 233)\n"
        f"{INC_X_LINES}\n{FLAG}\n\n{WH_OFFER}"
    ), said


# ---- S1: "cost" is a price ask -------------------------------------------- #


def test_s1_cost_ask_prints_list_price():
    from tests.chatbot._wa_concise_helpers import PRODUCTS_TOOL, render

    row = {"product_code": "SRTWT5844-GM", "product_name": "SRTWT5844-GM", "list_price": "45.5"}
    text = render(
        PRODUCTS_TOOL, {"data": [row], "pagination": {"total": 1}},
        {"semantic_input": {"requested_attributes": ["cost"]}},
    )

    assert "*List Price:* MYR 45.50" in text, text


# ---- N1: whole-token match for the named order ---------------------------- #


def test_n1_named_order_prefix_of_a_printed_number_keeps_the_scope_header():
    from app.services.chatbot.answer_bridge import apply_scope_block
    from app.services.chatbot.turn.compose import Answer

    body = "*Order Number:* SMC202609-0055\n*Status:* Delivered"
    out = apply_scope_block(
        Answer(text=body),
        domain="order",
        qf={"entities": [{"raw": "SMC202609-005", "hint": "order", "canonical_code": "SMC202609-005"}]},
        gate_json={"compatible_entities": [
            {"entity_type": "customer_order", "code": "SMC202609-005", "title": "SMC202609-005", "uuid": "ord-1"}
        ]},
        resolver_json=None,
    )

    assert out.text == (
        "Customer: all customers\nProduct: all products\nDelivery order: SMC202609-005\nDates: all dates"
        f"\n\n{body}"
    ), out.text


# ---- K8 / K9 guards (green now) ------------------------------------------- #


def test_k8_guard_set_header_survives_when_every_stock_block_gives_way():
    from app.services.chatbot.answer_bridge import _fold_blocks

    primary = "Showing 1 of 1 products.\n\n*Product Code:* SRTX\n*BRW:* 0\n\n_Updated 11/09/2026 17:26_"
    xd_text = "*Product Code:* SRTX\n*Stock:* 0\n*Container:* IAAU1907074"

    kept, rest = _fold_blocks(primary, xd_text)

    assert kept == "Showing 1 of 1 products.", kept
    assert rest == xd_text, rest


def test_k9_guard_quantity_stays_beside_the_code_in_the_folded_block():
    from app.services.chatbot.answer_bridge import _apply_crossdomain_render, _quantity_labels

    parser = {"entities": [{"raw": "M210-GM", "hint": "product", "quantity": 5}]}
    labels = _quantity_labels(parser)
    result = {"render": {"_xdBlock": {"any": True, "team": "purchasing", "block": "*Product Code:* M210-GM\n*Incoming:* none\n*Stock:* none"}}}

    text = _apply_crossdomain_render("", result, answered=False, covers=True, quantities=labels)

    assert "*Product Code:* M210-GM (x5)\n" in text, (labels, text)
