"""WA-CONCISE S1 (card v4): stock replies, one fact per line. AC-1 to AC-7.

UAC: documentation/plans/chatbot/wa-concise-acceptance-criteria.md. Every expected string
is the UAC's, whole reply. Envelopes are the real presenter's over a raw tool payload,
then the real `fetch.output_structurer`. Placeholder data only.
"""
from __future__ import annotations

from app.services.chatbot.lanes.business import fetch
from tests.chatbot._wa_concise_helpers import (
    EMPTY_PAYLOAD,
    FOOTER,
    GRANTED,
    OLD_FOOTER,
    ORDERS_TOOL,
    PRODUCTS_TOOL,
    STOCK_TOOL,
    UNGRANTED,
    compact_entry,
    compact_payload,
    detailed_payload,
    detailed_row,
    render,
)


def _compact(entries, ctx=UNGRANTED) -> str:
    return render(STOCK_TOOL, compact_payload(entries), ctx)


def _detailed(rows, ctx=GRANTED) -> str:
    return render(STOCK_TOOL, detailed_payload(rows), ctx)


# ---- AC-1 ------------------------------------------------------------------ #


def test_ac1_compact_one_location_has_no_total():
    text = _compact([compact_entry("SRTWT5844-GM", [("BRW", 24, 10)])], GRANTED)

    assert text == f"*Product Code:* SRTWT5844-GM\n*BRW:* 24 (O/S: 10)\n\n{FOOTER}", text


def test_ac1_compact_one_location_ungranted_outstanding():
    text = _compact([compact_entry("SRTWT5844-GM", [("BRW", 24, 10)])], UNGRANTED)

    assert text == f"*Product Code:* SRTWT5844-GM\n*BRW:* 24\n\n{FOOTER}", text


# ---- AC-2 ------------------------------------------------------------------ #


def test_ac2_compact_more_than_one_location_keeps_total_and_numbers_blocks():
    text = _compact(
        [
            compact_entry("SRTWT5844-GM", [("BRW", 24, None)]),
            compact_entry("SRTSWT3001", [("BRW", 28, None), ("MWH", 108, None)]),
        ]
    )

    assert text == (
        "1. *Product Code:* SRTWT5844-GM\n*BRW:* 24\n\n"
        "2. *Product Code:* SRTSWT3001\n*Total:* 136\n*BRW:* 28\n*MWH:* 108\n\n"
        f"{FOOTER}"
    ), text


# ---- AC-3 ------------------------------------------------------------------ #


def test_ac3_zero_location_prints_as_today():
    text = _compact(
        [
            compact_entry("SRTSWT3001", [("BRW", 28, None), ("MWH", 108, None)]),
            compact_entry("SRTSWT3001-GM", [("BRW", 0, None)]),
        ]
    )

    assert text.endswith(f"2. *Product Code:* SRTSWT3001-GM\n*BRW:* 0\n\n{FOOTER}"), text
    assert "*Total:* 0" not in text, text


def test_ac3_compact_entry_with_no_location_keeps_total_zero():
    text = _compact([compact_entry("SRTSWT3001-BL", [])])

    assert text == f"*Product Code:* SRTSWT3001-BL\n*Total:* 0\n\n{FOOTER}", text


# ---- AC-4 ------------------------------------------------------------------ #


def test_ac4_detailed_rows_merge_per_company_and_product_across_companies():
    text = _detailed(
        [
            detailed_row("Sorento", "SRT6542-DIY", "BRW", 0, 233),
            detailed_row("Mocha", "SRT6542-DIY", "MOCHA-WH", 1),
        ]
    )

    assert text == (
        "1. *Company:* Sorento\n*Product Code:* SRT6542-DIY\n*BRW:* 0 (O/S: 233)\n\n"
        "2. *Company:* Mocha\n*Product Code:* SRT6542-DIY\n*MOCHA-WH:* 1\n\n"
        f"{FOOTER}"
    ), text


def test_ac4_one_company_has_no_company_line_and_multi_location_total_leads():
    text = _detailed(
        [
            detailed_row("Sorento", "P-ONE", "BRW", 28, 3, name="PRODUCT ONE"),
            detailed_row("Sorento", "P-ONE", "MWH", 108, 2, name="PRODUCT ONE"),
        ]
    )

    assert text == (
        "*Product Code:* P-ONE\n*Product Name:* PRODUCT ONE\n"
        "*Total:* 136 (O/S: 5)\n*BRW:* 28 (O/S: 3)\n*MWH:* 108 (O/S: 2)\n\n"
        f"{FOOTER}"
    ), text


def test_ac4_total_carries_outstanding_only_when_every_row_has_it():
    text = _detailed(
        [
            detailed_row("Sorento", "P-ONE", "BRW", 28, 3),
            detailed_row("Sorento", "P-ONE", "MWH", 108),
        ]
    )

    assert text == (
        f"*Product Code:* P-ONE\n*Total:* 136\n*BRW:* 28 (O/S: 3)\n*MWH:* 108\n\n{FOOTER}"
    ), text


def test_ac4_blocks_are_first_seen_order_and_warehouse_name_is_dropped():
    text = _detailed(
        [
            detailed_row("Sorento", "P-ONE", "BRW", 5),
            detailed_row("Sorento", "P-TWO", "BRW", 7),
            detailed_row("Sorento", "P-ONE", "MWH", 6),
        ]
    )

    assert text == (
        "1. *Product Code:* P-ONE\n*Total:* 11\n*BRW:* 5\n*MWH:* 6\n\n"
        "2. *Product Code:* P-TWO\n*BRW:* 7\n\n"
        f"{FOOTER}"
    ), text
    assert "*Warehouse:*" not in text and "WAREHOUSE X" not in text, text


# ---- AC-5 ------------------------------------------------------------------ #

_OPENERS = (
    "Stock details found for the requested products.",
    "Stock summary for the requested products.",
    "Here are the orders I found.",
    "Here are the delivered orders I found.",
    "Here are the matching products.",
)


def _order_row() -> dict:
    return {
        "order_number": "SMC202609-0055",
        "debtor_name": "CUSTOMER A (PROJECT-CASH)",
        "order_date": "08/09/2026",
        "order_status": "Picked Up / In Transit",
        "lines": [{"product_code": "SRTKS7547-NEW", "quantity": 1}],
        "updated_at": "2026-09-10T17:36:11",
    }


def test_ac5_stock_detailed_opener_removed():
    text = _detailed([detailed_row("Sorento", "P-ONE", "BRW", 5)])

    assert "Stock details found for the requested products." not in text, text
    assert text.startswith("*Product Code:* P-ONE"), text


def test_ac5_stock_compact_opener_removed():
    text = _compact([compact_entry("P-ONE", [("BRW", 5, None)])])

    assert "Stock summary for the requested products." not in text, text
    assert text.startswith("*Product Code:* P-ONE"), text


_DO_KEYS = [
    f"delivery_orders.{k}"
    for k in (
        "order_number", "customer", "order_date", "delivery_date", "status", "pickup_time",
        "transporter", "driver", "lorry_plate", "warehouse", "products",
    )
]


def test_ac5_orders_opener_removed():
    text = render(ORDERS_TOOL, {"data": [_order_row()], "pagination": {"total": 1}},
        {"semantic_input": {}, "access": {"attributes": _DO_KEYS}},
    )

    assert "Here are the orders I found." not in text, text
    assert text.startswith("*Order Number:* SMC202609-0055"), text


def test_ac5_delivered_orders_opener_removed():
    ctx = {"semantic_input": {"order_status": "delivered"}, "tool": ORDERS_TOOL, "access": {"attributes": _DO_KEYS}}
    text = render(ORDERS_TOOL, {"data": [_order_row()], "pagination": {"total": 1}}, ctx)

    assert "Here are the delivered orders I found." not in text, text
    assert text.startswith("*Order Number:* SMC202609-0055"), text


def test_ac5_products_opener_removed():
    payload = {
        "data": [{"product_code": "SRTWT5844-GM", "product_name": "SRTWT5844-GM"}],
        "pagination": {"total": 1},
    }
    text = render(PRODUCTS_TOOL, payload, UNGRANTED)

    assert "Here are the matching products." not in text, text
    assert text.startswith("*Product Code:* SRTWT5844-GM"), text


def test_ac5_zero_blocks_reply_is_unchanged():
    assert render(STOCK_TOOL, EMPTY_PAYLOAD) == "No matching results found."
    for opener in _OPENERS:
        assert opener not in render(STOCK_TOOL, EMPTY_PAYLOAD)


# ---- AC-6 ------------------------------------------------------------------ #


def test_ac6_footer_is_updated_without_seconds_on_a_stock_reply():
    text = _compact([compact_entry("P-ONE", [("BRW", 5, None)])])

    assert text.endswith(FOOTER), text
    assert "Data last updated" not in text, text
    assert OLD_FOOTER not in text, text


def test_ac6_compose_extras_slot_sits_above_the_new_footer():
    """`turn/compose.py:451` splits the lane text on the footer to slot "No stock found
    for X." above it. It must find the new `_Updated ..._` footer."""
    from app.services.chatbot.turn.compose import compose
    from app.services.chatbot.turn.policy import Policy
    from app.services.chatbot.turn.state import Focus, Profile, State
    from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

    row = {**_domain_row("inventory", narrowing={"product": "list_all"}), "label": "stock"}
    policy = Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)
    state = State(focus=Focus(), pending=None, profile=Profile(), turn_no=2)
    lane_text = f"*Product Code:* P-TWO\n*BRW:* 12\n\n{FOOTER}"
    env = {
        "domain": "inventory",
        "denied": False,
        "entities": ["P-ONE", "P-TWO"],
        "product_codes": ["P-ONE", "P-TWO"],
        "figures": [
            {"fields": [{"label": "Product Code", "value": "P-TWO"}, {"label": "BRW", "value": 12}]}
        ],
        "files": [],
        "miss": [],
        "has_result": True,
        "tool_has_result": True,
        "unresolved": [],
        "error": None,
        "lane_text": lane_text,
    }

    text = compose([env], state, policy, ctx=None).text

    assert text.index("No stock found for P-ONE.") < text.index(FOOTER), text
    assert text.endswith(FOOTER), text


def test_ac6_promo_reintro_finds_the_new_footer():
    """`answer.py:1564` `_DATA_LAST_UPDATED_RE`, used at 2100 to carry the freshness stamp
    across a rebuilt promo list, must match the new footer and not the old."""
    from app.services.chatbot.lanes.business import answer

    body = f"1. *Promotion:* PROMO A\n\n{FOOTER}"

    m = answer._DATA_LAST_UPDATED_RE.search(body)

    assert m is not None and m.group(0) == FOOTER, m


# ---- AC-7 (regression guard, passes today) --------------------------------- #


def test_ac7_dealer_availability_reply_is_byte_identical():
    def entry(code: str, qty: int, branch: str, tail: str) -> dict:
        return {
            "title": f"{code} x {qty}: {tail}",
            "fields": [],
            "flags": {"needs_quantity": False, "branch": branch},
        }

    envelope = {
        "result_type": "stock_availability",
        "intro": "",
        "items": [
            entry(
                "SRT-TOOBIG",
                250,
                "too_big",
                "the quantity is more than what I can confirm here. Please refer to your salesman.",
            ),
            entry("SRT-INSTOCK", 5, "in_stock", "yes, we have stock. Please refer to your salesman."),
        ],
        "has_result": True,
    }

    out = fetch.output_structurer(envelope, {"semantic_input": {}})

    assert out["response"] == (
        "SRT-TOOBIG x 250: the quantity is more than what I can confirm here. "
        "Please refer to your salesman.\n\n"
        "SRT-INSTOCK x 5: yes, we have stock. Please refer to your salesman."
    )


# ---- AC-20 / AC-21 (constraints, green today) ------------------------------ #


def test_ac20_ac21_values_untouched_and_no_dot_or_dash_in_changed_text():
    banned = (chr(0xB7), chr(0x2013), chr(0x2014))
    compact = _compact([compact_entry("SRTWT5844-GM", [("BRW", 24, 10)])], GRANTED)
    detailed = _detailed(
        [
            detailed_row("Sorento", "SRT6542-DIY", "BRW", 0, 233),
            detailed_row("Mocha", "SRT6542-DIY", "MOCHA-WH", 1),
        ]
    )

    assert "*BRW:* 24 (O/S: 10)" in compact, compact
    assert "*BRW:* 0 (O/S: 233)" in detailed, detailed
    for text in (compact, detailed):
        for b in banned:
            assert b not in text, (hex(ord(b)), text)
