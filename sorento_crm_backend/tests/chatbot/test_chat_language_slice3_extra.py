"""CHAT-LANGUAGE slice 3 rulings not covered by the tester's file: bold detail rows, and a value
that equals a catalog key (but is not a VALUE_WORD) staying untouched."""
from __future__ import annotations

import pytest

from app.services.chatbot import label_catalog
from app.services.chatbot.label_catalog import VALUE_WORDS, Localizer


def _loc(lang: str) -> Localizer:
    return Localizer(lang, label_catalog.defaults(lang))


def test_bold_detail_rows_translate_the_label_and_keep_the_number_and_the_value():
    ms, zh = _loc("ms"), _loc("zh")
    assert ms.lines("1. *SO Number:* SO-1") == "1. *No. SO:* SO-1"
    assert ms.lines("*DO Qty:* 5") == "*Kuantiti DO:* 5"
    assert zh.lines("2. *DO Number:* DO-009") == "2. *DO 编号:* DO-009"
    assert zh.lines("*Order date:* 02/09/2026") == "*订单日期:* 02/09/2026"
    # An uncatalogued bold label, and a bold rank line, are untouched.
    assert ms.lines("*Foo:* bar") == "*Foo:* bar"
    assert ms.lines("1. *SRTWC286:* Qty 3") == "1. *SRTWC286:* Qty 3"


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_a_value_equal_to_a_non_value_word_catalog_key_is_untouched(lang):
    loc = _loc(lang)
    assert "Product" in label_catalog.LABELS and "Product" not in VALUE_WORDS
    assert loc.lines("Category: Product") == loc.table["Category"] + ": Product"
    assert loc.lines("Status: Status") == loc.table["Status"] + ": Status"
    assert loc.lines("Channel: Brand") == loc.table["Channel"] + ": Brand"
    assert loc.lines("*Category:* Total") == "*" + loc.table["Category"] + ":* Total"


def test_a_value_word_after_a_catalogued_label_does_translate():
    assert _loc("ms").lines("Product: all") == "Produk: semua"
    assert _loc("zh").lines("Ranked by: Amount") == "排名依据: 金额"


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_a_breakdown_row_named_like_a_catalog_label_stays_data(lang):
    loc = _loc(lang)
    assert loc.lines("Total: 3 (O/S: 0)") == "Total: 3 (O/S: 0)"
    assert loc.lines("Product: 1,204 (O/S: 6)") == "Product: 1,204 (O/S: 6)"
    # A real totals line still translates.
    assert loc.lines("Total: 51") == loc.table["Total"] + ": 51"
    assert loc.lines("Total: Qty 3, RM 5.00") == loc.table["Total"] + ": Qty 3, RM 5.00"
