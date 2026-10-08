"""DISCONTINUED-ASK (PR #1535, card `documentation/plans/chatbot/CARD-discontinued-ask-08oct.md`).

Owner, 8 Oct 2026: "9613-bl is discon or?" was answered with the plain product card. The
parser already reads the question as `requested_attributes: ["discontinued"]`; the reply
layer printed the card, whose only status signal is the `(PRODUCT DISCONTINUED)` marker on
a discontinued product, so a LIVE product's answer said nothing at all.

Rules pinned here (key on the parsed field only, never on user text):
  R1 a product answer whose `requested_attributes` carries "discontinued" says the status
     of EVERY product, both ways;
  R3 an availability-only (dealer) contact's discontinued question is never rewritten into
     a stock check, even when the parser copied a quantity from a quoted stock line.
"""
from __future__ import annotations

from app.services.chatbot import engine
from app.services.chatbot.lanes.business import fetch


def _product(code: str, *, discontinued: bool) -> dict:
    return {
        "title": code,
        "fields": [
            {"key": "product_code", "label": "Product Code", "value": code},
            {"key": "description", "label": "Description", "value": f"SORENTO SHOWER SET {code}"},
            {"key": "list_price", "label": "List Price", "value": "MYR 710.00"},
            {"key": "dimensions", "label": "Dimensions", "value": "Not defined"},
        ],
        "flags": {"discontinued": discontinued},
    }


def _envelope(*items: dict) -> dict:
    return {
        "result_type": "products",
        "intro": "Here are the matching products.",
        "items": list(items),
        "has_result": True,
    }


def _answer(*items: dict, attrs: list[str]) -> str:
    out = fetch.output_structurer(_envelope(*items), {"semantic_input": {"requested_attributes": attrs}})
    return out["response"]


# --------------------------------------------------------------------------- #
# R1: the status, both ways                                                     #
# --------------------------------------------------------------------------- #


def test_a_live_product_is_said_to_be_not_discontinued():
    """The screenshot: SRTWT9613-BL is live, and today's reply carried no word about it."""
    reply = _answer(_product("SRTWT9613-BL", discontinued=False), attrs=["discontinued"])
    assert "SRTWT9613-BL" in reply
    assert "not discontinued" in reply.lower()


def test_a_discontinued_product_is_said_to_be_discontinued():
    reply = _answer(_product("SRTBF11821", discontinued=True), attrs=["discontinued"])
    assert "SRTBF11821" in reply
    assert "discontinued" in reply.lower()
    assert "not discontinued" not in reply.lower()


def test_every_product_of_a_mixed_family_gets_its_own_status():
    reply = _answer(
        _product("SRTWC286-SH-200", discontinued=False),
        _product("SRTWC286-SH-P", discontinued=True),
        attrs=["discontinued"],
    )
    lines = reply.splitlines()
    live = [ln for ln in lines if "SRTWC286-SH-200" in ln and "not discontinued" in ln.lower()]
    gone = [
        ln for ln in lines
        if "SRTWC286-SH-P" in ln and "discontinued" in ln.lower() and "not discontinued" not in ln.lower()
    ]
    assert live, reply
    assert gone, reply


def test_without_the_discontinued_ask_a_live_product_answer_is_unchanged():
    """Control: a plain product ask keeps today's card and says nothing about status."""
    reply = _answer(_product("SRTWT9613-BL", discontinued=False), attrs=[])
    assert "discontinued" not in reply.lower()
    assert "*List Price:* MYR 710.00" in reply


# --------------------------------------------------------------------------- #
# R3: a dealer's discontinued question stays a product question                 #
# --------------------------------------------------------------------------- #


def _verdict(**over) -> dict:
    base = {
        "intent_hint": "check_product",
        "domain_hint": "master_products",
        "requested_attributes": ["discontinued"],
        "entities": [{"raw": "SRTWT9613-BL", "hint": "product", "canonical_code": "SRTWT9613-BL", "quantity": 5}],
        "asks": [],
    }
    base.update(over)
    return base


def test_a_discontinued_question_with_a_quoted_quantity_is_not_rewritten_to_stock():
    """Turn 2 of the screenshot: quoting "SRTWT9613-BL x 5: ..." the parser may copy the 5
    onto the entity; the question is still about status, never a stock check."""
    verdict = _verdict()
    out = engine._as_a_stock_ask_by_its_own_words(verdict, "mean discontinued model?")
    assert out["domain_hint"] == "master_products"
    assert out["intent_hint"] == "check_product"


def test_a_product_ask_with_a_quantity_and_no_discontinued_attribute_still_reads_as_stock():
    """Control: AVAIL-MODE-REPLIES' own case ("srt5764 xx 10" read as check_product) keeps
    its rewrite."""
    verdict = _verdict(
        requested_attributes=[],
        entities=[{"raw": "srt5764", "hint": "product", "canonical_code": "SRT5764", "quantity": 10}],
    )
    out = engine._as_a_stock_ask_by_its_own_words(verdict, "srt5764 xx 10")
    assert out["domain_hint"] == "inventory"
    assert out["intent_hint"] == "check_stock"
