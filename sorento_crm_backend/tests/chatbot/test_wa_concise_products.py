"""WA-CONCISE S4 (card v4): product list, List Price / Dimensions only when asked. AC-19.

UAC: documentation/plans/chatbot/wa-concise-acceptance-criteria.md. The decision needs what
the customer asked (`semantic_input.requested_attributes`), which the MCP presenter never
sees, so it is tested at `fetch.output_structurer` over the real presenter's envelope.
Placeholder data only.
"""
from __future__ import annotations

from tests.chatbot._wa_concise_helpers import PRODUCTS_TOOL, render

_PRICED = {
    "product_code": "SRTWT5844-GM",
    "product_name": "SRTWT5844-GM",
    "list_price": "45.5",
    "dimensions_length": 100,
    "dimensions_width": 50,
    "dimensions_height": 20,
    "specs": [{"key": "colour", "label": "Colour", "value": "Grey"}],
}
_BARE = {"product_code": "SRTWT5844-BL", "product_name": "SRTWT5844-BL"}


def _products(rows: list[dict], asked: list[str]) -> str:
    payload = {"data": rows, "pagination": {"total": len(rows)}}
    return render(PRODUCTS_TOOL, payload, {"semantic_input": {"requested_attributes": asked}})


def test_ac19_not_asked_prints_neither_line():
    text = _products([_PRICED], [])

    assert text == "*Product Code:* SRTWT5844-GM\n*Specs:* Colour: Grey", text


def test_ac19_not_asked_and_empty_prints_neither_line_not_defined_is_gone():
    text = _products([_BARE], [])

    assert text == "*Product Code:* SRTWT5844-BL", text
    assert "Not defined" not in text, text


def test_ac19_a_spec_ask_is_not_a_price_or_dimensions_ask():
    text = _products([_PRICED], ["colour"])

    assert "*List Price:*" not in text and "*Dimensions:*" not in text, text
    assert "*Colour:* Grey" in text, text


def test_ac19_price_ask_prints_list_price():
    text = _products([_PRICED], ["price"])

    assert "*List Price:* MYR 45.50" in text, text


def test_ac19_list_price_phrase_ask_prints_list_price():
    text = _products([_PRICED], ["list price"])

    assert "*List Price:* MYR 45.50" in text, text


def test_ac19_dimensions_ask_prints_dimensions():
    text = _products([_PRICED], ["dimensions"])

    assert "*Dimensions:* 100 x 50 x 20 mm" in text, text


def test_ac19_asked_and_empty_still_reads_not_defined():
    price = _products([_BARE], ["price"])
    dims = _products([_BARE], ["dimensions"])

    assert "*List Price:* Not defined" in price, price
    assert "*Dimensions:* Not defined" in dims, dims
