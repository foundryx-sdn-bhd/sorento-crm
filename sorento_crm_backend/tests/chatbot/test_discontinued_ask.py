"""DISCONTINUED-ASK (PR #1535, card `documentation/plans/chatbot/CARD-discontinued-ask-08oct.md`).

Owner, 8 Oct 2026: "9613-bl is discon or?" was answered with the plain product card. The
parser already reads the question as `requested_attributes: ["discontinued"]`; the reply
layer printed the card, whose only status signal is the `(PRODUCT DISCONTINUED)` marker on
a discontinued product, so a LIVE product's answer said nothing at all.

Rules pinned here (key on the parsed field only, never on user text):
  R1 a product answer whose `requested_attributes` carries "discontinued" says the status
     of EVERY product, both ways. Owner rulings 8 Oct: the status alone is answered by
     `CODE: Not discontinued.` / `CODE: Discontinued.` lines only, no card (Q1 a, Q4 a);
     with another property asked, the card plus `*Discontinued:* Yes / No` (Q1 a); a
     discontinued code names its stored alternatives (Q5 a, #1533's `alternatives` key);
     translated for ms / zh;
  R3 an availability-only (dealer) contact's discontinued question is never rewritten into
     a stock check, even when the parser copied a quantity from a quoted stock line.
"""
from __future__ import annotations

from typing import Any

from app.services.chatbot import engine
from app.services.chatbot.lanes.business import fetch


def _product(
    code: str, *, discontinued: Any, alternatives: list | None = None, company: str | None = None
) -> dict:
    item = {
        "title": code,
        "fields": ([{"key": "company_name", "label": "Company", "value": company}] if company else []) + [
            {"key": "product_code", "label": "Product Code", "value": code},
            {"key": "description", "label": "Description", "value": f"SORENTO SHOWER SET {code}"},
            {"key": "list_price", "label": "List Price", "value": "MYR 710.00"},
            {"key": "dimensions", "label": "Dimensions", "value": "Not defined"},
        ],
        "flags": {"discontinued": discontinued},
    }
    if alternatives is not None:
        item["alternatives"] = alternatives
    return item


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


def _body(reply: str) -> str:
    """The reply without the data-freshness footer (none in these envelopes) and edge blanks."""
    return reply.strip()


def test_the_status_alone_is_answered_by_the_status_line_only():
    """Q1 (a) / Q4 (a): no intro, no numbering, no card."""
    assert _body(_answer(_product("SRTWT9613-BL", discontinued=False), attrs=["discontinued"])) == (
        "SRTWT9613-BL: Not discontinued."
    )
    assert _body(_answer(_product("AP8050A", discontinued=True), attrs=["discontinued"])) == (
        "AP8050A: Discontinued."
    )


def test_a_mixed_family_is_one_line_per_code_in_the_tool_order():
    reply = _answer(
        _product("SRTWC286-SH-200", discontinued=False),
        _product("SRTWC286-SH-NEW-P", discontinued=False),
        _product("SRTWC286-SH-P", discontinued=True),
        attrs=["discontinued"],
    )
    assert _body(reply) == (
        "SRTWC286-SH-200: Not discontinued.\n\n"
        "SRTWC286-SH-NEW-P: Not discontinued.\n\n"
        "SRTWC286-SH-P: Discontinued."
    )


def test_the_same_code_from_two_companies_is_said_once():
    reply = _answer(
        _product("SRTWT9613-BL", discontinued=False),
        _product("SRTWT9613-BL", discontinued=False),
        attrs=["discontinued"],
    )
    assert _body(reply) == "SRTWT9613-BL: Not discontinued."


def test_a_discontinued_code_names_its_alternative():
    """Q5 (a): #1533 puts the stored -NEW link on the item; plain code, no stock figure."""
    reply = _answer(
        _product("SRTBF11821", discontinued=True, alternatives=[{"product_code": "SRTBF11821-NEW"}]),
        attrs=["discontinued"],
    )
    assert _body(reply) == "SRTBF11821: Discontinued.\nAlternative: SRTBF11821-NEW"


def test_a_live_code_never_names_an_alternative():
    reply = _answer(
        _product("SRTWC286-SH-200", discontinued=False, alternatives=["SRTWC286-SH-NEW-200"]),
        attrs=["discontinued"],
    )
    assert _body(reply) == "SRTWC286-SH-200: Not discontinued."


def test_with_another_property_the_card_gains_a_discontinued_field_line():
    """Q1 (a): "price and discontinued?" keeps the card; the field line replaces the marker."""
    reply = _answer(
        _product("SRTWT9613-BL", discontinued=False),
        _product("SRTBF11821", discontinued=True, alternatives=["SRTBF11821-NEW"]),
        attrs=["price", "discontinued"],
    )
    assert "Here are the matching products." in reply
    assert "*List Price:* MYR 710.00" in reply
    assert "1. *Product Code:* SRTWT9613-BL" in reply
    live, gone = reply.split("2. ")
    assert live.rstrip().endswith("*Dimensions:* Not defined\n*Discontinued:* No")
    assert "*Dimensions:* Not defined\n*Discontinued:* Yes\nAlternative: SRTBF11821-NEW" in gone
    assert "PRODUCT DISCONTINUED" not in reply


def test_the_status_lines_translate_for_malay_and_chinese():
    from app.services.chatbot import label_catalog
    from app.services.chatbot.label_catalog import Localizer

    only = _answer(
        _product("SRTWT9613-BL", discontinued=False),
        _product("SRTBF11821", discontinued=True, alternatives=["SRTBF11821-NEW"]),
        attrs=["discontinued"],
    )
    card = _answer(_product("SRTBF11821", discontinued=True), attrs=["price", "discontinued"])
    ms = Localizer("ms", label_catalog.defaults("ms"))
    zh = Localizer("zh", label_catalog.defaults("zh"))
    assert ms.reply(only).strip() == (
        "SRTWT9613-BL: Tidak dihentikan.\n\nSRTBF11821: Dihentikan.\nAlternatif: SRTBF11821-NEW"
    )
    assert zh.reply(only).strip() == "SRTWT9613-BL：未停产。\n\nSRTBF11821：已停产。\n替代型号: SRTBF11821-NEW"
    assert "*Dihentikan:* Ya" in ms.reply(card)
    assert "*已停产:* 是" in zh.reply(card)


def test_a_code_whose_companies_disagree_names_the_company_on_each_line():
    """Reviewer round 1 (blocking): products are unique per company, so one code can be live
    in one company and discontinued in another. Two bare contradicting lines would be a
    confident wrong answer; each line names its company instead."""
    reply = _answer(
        _product("X1", discontinued=False, company="Sorento"),
        _product("X1", discontinued=True, company="Mocha"),
        attrs=["discontinued"],
    )
    assert _body(reply) == "X1 (Sorento): Not discontinued.\n\nX1 (Mocha): Discontinued."


def test_disagreeing_rows_without_a_company_each_say_it_on_their_own_card():
    """Reviewer round 2: with no Company field to tell the rows apart, each row prints its
    card with `*Discontinued:* Yes / No`, never two bare contradicting lines."""
    reply = _answer(
        _product("X1", discontinued=False),
        _product("X1", discontinued=True),
        attrs=["discontinued"],
    )
    assert "X1: Not discontinued." not in reply and "X1: Discontinued." not in reply
    assert reply.count("*Product Code:* X1") == 2
    assert "*Discontinued:* No" in reply and "*Discontinued:* Yes" in reply


def test_companies_that_agree_are_one_line_without_a_company():
    reply = _answer(
        _product("X1", discontinued=True, company="Sorento"),
        _product("X1", discontinued=True, company="Mocha"),
        attrs=["discontinued"],
    )
    assert _body(reply) == "X1: Discontinued."


def test_an_unknown_status_is_never_said_either_way():
    """A row whose flag is not a boolean prints its card, never a guessed status."""
    for flag in (None, "false"):
        reply = _answer(_product("X1", discontinued=flag), attrs=["discontinued"])
        assert "discontinued" not in reply.lower(), flag
        assert "*Product Code:* X1" in reply, flag


def test_alternatives_are_said_once_and_never_the_code_itself():
    reply = _answer(
        _product("X1", discontinued=True, alternatives=["X1", "X1-NEW", {"product_code": "X1-NEW"}]),
        attrs=["discontinued"],
    )
    assert _body(reply) == "X1: Discontinued.\nAlternative: X1-NEW"


def test_the_data_freshness_footer_stays_under_the_status_lines():
    """The footer is not the card: production stamps `last_updated_at` on the product reply."""
    envelope = _envelope(_product("SRTWT9613-BL", discontinued=False))
    envelope["last_updated_at"] = "2026-10-03T18:42:40"
    out = fetch.output_structurer(envelope, {"semantic_input": {"requested_attributes": ["discontinued"]}})
    assert out["response"].startswith("SRTWT9613-BL: Not discontinued.\n\n_Data last updated: ")


def test_a_stock_reply_is_untouched_by_a_discontinued_ask():
    """Reviewer round 1, kill test K1: only a PRODUCT answer becomes status lines."""
    envelope = {
        "result_type": "stock_compact",
        "intro": "Stock summary for the requested products.",
        "items": [_product("SRTWT9613-BL", discontinued=True)],
        "has_result": True,
    }
    asked = fetch.output_structurer(dict(envelope), {"semantic_input": {"requested_attributes": ["discontinued"]}})
    plain = fetch.output_structurer(dict(envelope), {"semantic_input": {"requested_attributes": []}})
    assert asked["response"] == plain["response"]
    assert "PRODUCT DISCONTINUED" in asked["response"]


def test_other_yes_and_no_values_are_not_translated():
    """The field line's translation is a whole catalogued line, so no other boolean moves."""
    from app.services.chatbot import label_catalog
    from app.services.chatbot.label_catalog import Localizer

    ms = Localizer("ms", label_catalog.defaults("ms"))
    assert ms.reply("*Customer:* No\nYes") == "*Pelanggan:* No\nYes"


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
