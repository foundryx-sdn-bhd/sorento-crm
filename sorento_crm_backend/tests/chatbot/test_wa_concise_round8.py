"""WA-CONCISE round 8 (red-first): every list block counts in the one numbering run, and no
tool's default opener prints above rows. Placeholder data only."""
from __future__ import annotations

import re

import pytest

from app.services.chatbot.block_numbering import renumber
from app.services.chatbot.lanes.business import fetch
from tests.chatbot.test_wa_concise_round6 import _compose

_NUM = re.compile(r"^(\d+)\. \*(?:SPO Number|Company|Product Code|Order Number):\* (\S+)", re.MULTILINE)


# ---- 1. numbering --------------------------------------------------------- #


def test_spo_block_then_two_stock_blocks_number_one_two_three():
    spo = "1. *SPO Number:* SPO-1\n*Product Code:* SRTA\n*Ordered:* 5"
    stock = (
        "1. *Company:* Sorento\n*Product Code:* SRTA\n*BRW:* 5\n\n"
        "2. *Company:* Mocha\n*Product Code:* SRTA\n*MOCHA-WH:* 6"
    )

    text = _compose(("spo", [], spo, None), ("inventory", ["SRTA"], stock, None))

    assert _NUM.findall(text) == [("1", "SPO-1"), ("2", "Sorento"), ("3", "Mocha")], text


def test_single_spo_block_and_one_stock_block_number_one_two():
    spo = "*SPO Number:* SPO-1\n*Product Code:* SRTA\n*Ordered:* 5"
    stock = "*Company:* Sorento\n*Product Code:* SRTA\n*BRW:* 5"

    text = _compose(("spo", [], spo, None), ("inventory", ["SRTA"], stock, None))

    assert _NUM.findall(text) == [("1", "SPO-1"), ("2", "Sorento")], text


@pytest.mark.parametrize(
    "paras",
    [
        ["11. SRTX", "12. SRTY"],
        ["1. Mon 28 Sep, stock for X", "2. Tue 29 Sep, orders for Y"],
        ["1. *Label:* https://x", "2. *Label:* https://y"],
    ],
)
def test_guard_lists_that_are_not_blocks_are_untouched(paras):
    assert renumber([paras]) == [paras]


def test_guard_one_line_company_note_is_never_counted_or_numbered():
    note = "*Mocha:* no incoming stock records found for product X."
    block = "*Product Code:* SRTA\n*Container:* IAAU1907074"

    assert renumber([[block], [note]]) == [[block], [note]]


def test_guard_report_header_lines_are_not_counted():
    header = "*Customer:* all\n*Product:* all"
    block = "*Product Code:* SRTA\n*BRW:* 5"

    assert renumber([[header, block]]) == [[header, block]]


# ---- 2. openers ----------------------------------------------------------- #

_OPENERS = [
    "Here is the last SPO line per product.",
    "Here is the PO placed I found.",
    "Here is the last purchase cost per product and location.",
    "Here are the incoming shipments I found.",
    "Here are the matching promotions.",
    "Here are the certificates I found.",
    "Here are the matching promotion products.",
    "Here are the product files I found.",
    "Here are the documents I found.",
    "Here are the forms I found.",
    "Here is the incoming stock I found.",
]


def _env(intro: str, rows: int) -> dict:
    return {
        "result_type": "generic",
        "intro": intro,
        "items": [
            {"title": f"ROW-{i}", "fields": [{"label": "Reference", "value": f"REF-{i}"}]}
            for i in range(rows)
        ],
        "has_result": rows > 0,
    }


@pytest.mark.parametrize("intro", _OPENERS)
def test_opener_is_dropped_when_a_row_block_prints(intro):
    out = fetch.output_structurer(_env(intro, 1), {"semantic_input": {}})

    assert intro not in out["response"], out["response"]
    assert out["response"].startswith("*Reference:* REF-0"), out["response"]


def test_guard_portal_link_intro_stays():
    env = {
        "result_type": "portal_link",
        "intro": "Here is the link you requested.",
        "items": [],
        "action_links": [{"label": "Open", "url": "https://example.test/x"}],
        "has_result": True,
    }

    out = fetch.output_structurer(env, {"semantic_input": {}})

    assert "Here is the link you requested." in out["response"], out["response"]


def test_guard_zero_rows_keeps_the_intro():
    out = fetch.output_structurer(_env("Here is the PO placed I found.", 0), {"semantic_input": {}})

    assert out["response"] == "Here is the PO placed I found."
