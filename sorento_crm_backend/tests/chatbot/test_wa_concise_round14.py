"""WA-CONCISE round 14 (red-first): the fold and the numbering follow a staff-edited label.

The turn localizer's table can hold a translation staff edited (translation_memory), e.g. ms
"Product Code" -> "Kod Barang" where the catalog says "Kod Produk". The matchers must read
the label the reply actually printed. Tested on the LIVE path: the primary block is rendered
by `fetch.output_structurer` with that localizer, folded by `_apply_crossdomain_render`, then
put through `Localizer.reply`. Placeholder data only."""
from __future__ import annotations

import json

import pytest

from app.services.chatbot import label_catalog
from app.services.chatbot.answer_bridge import _apply_crossdomain_render
from app.services.chatbot.block_numbering import renumber
from app.services.chatbot.label_catalog import Localizer
from app.services.chatbot.lanes.business import fetch
from tests.chatbot._wa_concise_helpers import (
    STOCK_TOOL,
    detailed_payload,
    detailed_row,
    envelope_json,
)

_XD = (
    "*Product Code:* SRTWT185-GM\n*Stock:* 0\n*Container:* IAAU1907074\n*ETA:* 2026-09-09\n"
    "*Incoming Quantity:* 200\n\U0001f6a9  *(PENDING ALLOCATION)*"
)
_RESULT = {"render": {"_xdBlock": {"any": True, "team": "warehouse", "block": _XD}}}


def _live(lang: str, edited: dict[str, str] | None = None) -> str:
    loc = Localizer(lang, {**label_catalog.defaults(lang), **(edited or {})})
    env = json.loads(
        envelope_json(STOCK_TOOL, detailed_payload([detailed_row("Sorento", "SRTWT185-GM", "BRW", 0, 56)]))
    )
    ctx = {
        "semantic_input": {},
        "access": {"attributes": ["inventory.sellable"]},
        "tool": STOCK_TOOL,
        "localizer": loc,
    }
    primary = fetch.output_structurer(env, ctx)["response"]
    folded = _apply_crossdomain_render(primary, _RESULT, answered=True, localizer=loc)
    return loc.reply(folded) if lang != "en" else folded


def test_staff_edited_product_code_label_still_merges_into_one_block():
    text = _live("ms", {"Product Code": "Kod Barang"})

    assert text.count("*Kod Barang:* SRTWT185-GM") == 1, text
    assert (
        "*Kod Barang:* SRTWT185-GM\n*BRW:* 0 (O/S: 56)\n*Kontena:* IAAU1907074\n*ETA:* 2026-09-09\n"
        "*Kuantiti Masuk:* 200\n\U0001f6a9  *(MENUNGGU PERUNTUKAN)*"
    ) in text, text


@pytest.mark.parametrize(
    ("lang", "label"), [("en", "Product Code"), ("ms", "Kod Produk"), ("zh", "产品代码")]
)
def test_guard_catalog_labels_and_english_still_merge_into_one_block(lang, label):
    text = _live(lang)

    assert text.count(f"*{label}:* SRTWT185-GM") == 1, text


def test_numbering_recognises_a_staff_edited_first_label():
    loc = Localizer("ms", {**label_catalog.defaults("ms"), "Product Code": "Kod Barang"})
    a = "*Kod Barang:* SRTA\n*BRW:* 5"
    b = "*Kod Barang:* SRTB\n*BRW:* 6"

    assert renumber([[a, b]], localizer=loc) == [[f"1. {a}", f"2. {b}"]]


def test_guard_numbering_with_the_catalog_label_is_unchanged():
    a = "*Kod Produk:* SRTA\n*BRW:* 5"
    b = "*Kod Produk:* SRTB\n*BRW:* 6"

    assert renumber([[a, b]]) == [[f"1. {a}", f"2. {b}"]]
