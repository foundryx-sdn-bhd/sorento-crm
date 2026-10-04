"""CHAT-LANGUAGE crew-tester finding 1 (PR #1448, 3 Oct 2026): the cross-domain block a stock
answer carries (the incoming rows under a stock miss, its lead, flags and absence sentences),
the "Couldn't find: X." line and a direct incoming answer were never catalogued, so an ms / zh
stock ask still printed `*Product Code:*`, `*Container:*`, "No stock for X." and "No stock and
no incoming for X." in English (console cases A, B and the mid-chat switch failed on it).

Pure: no database. The block is rendered by the real `crossdomain_render` and `output_structurer`
in English, then put through the localizer exactly as the turn's final pass does.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from app.services.chatbot import label_catalog
from app.services.chatbot.label_catalog import LABELS, Localizer
from app.services.chatbot.lanes.business.answer import crossdomain_render
from app.services.chatbot.lanes.business.fetch import output_structurer
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture


def _loc(lang: str) -> Localizer:
    return Localizer(lang, label_catalog.defaults(lang))


def _presenters():
    repo_root = Path(__file__).resolve().parents[3]
    mcp_root = repo_root / "sorento_crm_mcp"
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    try:
        from sorento_crm_mcp import presenters
    except ImportError:  # pragma: no cover - only where the package is not on disk
        pytest.skip("sorento_crm_mcp is not importable in this environment")
    return presenters


_INCOMING_ITEM = {
    "fields": [
        {"key": "company_name", "label": "Company", "value": "SORENTO"},
        {"key": "product_code", "label": "Product Code", "value": "SRTWC286"},
        {"key": "shipping_container_number", "label": "Container", "value": "SEGU4008631"},
        {"key": "estimated_arrival_date", "label": "ETA", "value": "15/10/2026"},
        {"key": "remaining_incoming_quantity", "label": "Incoming Quantity", "value": 40},
        {"key": "warehouse_allocations", "label": "Warehouse Allocations", "value": "BRW-BB: 40"},
    ],
    "flags": {"discontinued": True, "unallocated": True},
}


def _block(*, items: list[dict], missing: list[dict], origin: str = "inventory") -> str:
    out = crossdomain_render(
        {"items": items, "has_result": bool(items)},
        zeroset={"active": True, "origin_domain": origin, "team": "warehouse", "missing": missing},
        validator={},
    )
    return out["_xdBlock"]["block"]


# --------------------------------------------------------------------------- #
# The cross-domain block, rendered for real and localized by the final pass
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "lang,lead,code_label,container,qty,alloc,flag_disc,flag_pending",
    [
        (
            "ms",
            "Tetapi ada stok MASUK (ETA) untuk produk yang diminta:",
            "Kod Produk",
            "Kontena",
            "Kuantiti Masuk",
            "Peruntukan Gudang",
            "PRODUK DIHENTIKAN",
            "MENUNGGU PERUNTUKAN",
        ),
        (
            "zh",
            "但所请求的产品有到货库存（ETA）：",
            "产品代码",
            "货柜",
            "到货数量",
            "仓库分配",
            "产品已停产",
            "待分配",
        ),
    ],
)
def test_incoming_rows_under_a_stock_miss_read_in_the_reply_language(
    lang, lead, code_label, container, qty, alloc, flag_disc, flag_pending
):
    english = _block(
        items=[_INCOMING_ITEM],
        missing=[{"code": "SRTWC286", "_n": "SRTWC286", "uuid": "u1"}],
    )
    assert "- *Company:* SORENTO" in english.split("\n")  # the shape this test is about
    out = _loc(lang).reply(english)
    lines = out.split("\n")
    assert lead in lines
    assert f"- *{ {'ms': 'Syarikat', 'zh': '公司'}[lang] }:* SORENTO" in lines
    assert f"*{code_label}:* SRTWC286" in lines
    assert f"*{container}:* SEGU4008631" in lines
    # Q3: ETA stays as printed, and so does its value.
    assert "*ETA:* 15/10/2026" in lines
    assert f"*{qty}:* 40" in lines
    assert f"*{alloc}:* BRW-BB: 40" in lines
    assert f"⚠️  *({flag_disc})*" in lines
    assert f"\U0001f6a9  *({flag_pending})*" in lines
    for english_word in ("Product Code", "Container", "Incoming Quantity", "INCOMING stock", "DISCONTINUED"):
        assert english_word not in out


@pytest.mark.parametrize(
    "lang,expected",
    [
        ("ms", "Tiada stok dan tiada stok masuk untuk SRTWC286, SRTWC287."),
        ("zh", "SRTWC286, SRTWC287 没有库存，也没有到货。"),
    ],
)
def test_no_stock_and_no_incoming_reads_in_the_reply_language(lang, expected):
    english = _block(
        items=[],
        missing=[
            {"code": "SRTWC286", "_n": "SRTWC286", "uuid": "u1"},
            {"code": "SRTWC287", "_n": "SRTWC287", "uuid": "u2"},
        ],
    )
    assert english == "No stock and no incoming for SRTWC286, SRTWC287."
    assert _loc(lang).reply(english) == expected


@pytest.mark.parametrize(
    "english,ms,zh",
    [
        (
            "No stock for SRTWC286-SH.",
            "Tiada stok untuk SRTWC286-SH.",
            "SRTWC286-SH 没有库存。",
        ),
        (
            "No incoming for SRTWC286-SH.",
            "Tiada stok masuk untuk SRTWC286-SH.",
            "SRTWC286-SH 没有到货。",
        ),
        (
            "No incoming and no stock for SRTWC286.",
            "Tiada stok masuk dan tiada stok untuk SRTWC286.",
            "SRTWC286 没有到货，也没有库存。",
        ),
        (
            "Stock is 0 at every location and no incoming for SRTWC286.",
            "Stok 0 di setiap lokasi dan tiada stok masuk untuk SRTWC286.",
            "SRTWC286 在所有位置的库存均为 0，且没有到货。",
        ),
        (
            "No incoming and stock is 0 at every location for SRTWC286.",
            "Tiada stok masuk dan stok 0 di setiap lokasi untuk SRTWC286.",
            "SRTWC286 没有到货，且所有位置的库存均为 0。",
        ),
        (
            "But here are the stock details for the requested products:",
            "Tetapi berikut ialah butiran stok untuk produk yang diminta:",
            "但以下是所请求产品的库存详情：",
        ),
        (
            'Couldn\'t find: "ZZNOPE999" (product).',
            'Tidak dapat menemui: "ZZNOPE999" (product).',
            '找不到："ZZNOPE999" (product)。',
        ),
    ],
)
def test_absence_sentences_read_in_the_reply_language(english, ms, zh):
    assert _loc("ms").reply(english) == ms
    assert _loc("zh").reply(english) == zh


def test_a_dealer_eta_line_translates_its_verdict_and_keeps_the_mark():
    """#1430's dealer incoming lines: `<code>: No ETA` and `<code>: <tick> ETA <date>`."""
    assert _loc("ms").tail("SRTW2000: No ETA") == "SRTW2000: Tiada ETA"
    assert _loc("zh").tail("SRTW2000: No ETA") == "SRTW2000: 暂无 ETA"
    assert _loc("ms").tail("SRTW2000: \u2705 ETA 19/10/2026") == "SRTW2000: \u2705 ETA 19/10/2026"
    # The no-incoming verdict, built from the presenter's own constant so it follows the wording.
    no_incoming = f"SRT5674 x 5: {_presenters()._AVAILABILITY_TAILS['no_incoming']}"
    assert no_incoming.startswith("SRT5674 x 5: \u274c No stock and no incoming.")
    assert _loc("ms").tail(no_incoming) == (
        "SRT5674 x 5: \u274c Tiada stok dan tiada stok masuk. Sila rujuk jurujual anda."
    )
    assert _loc("zh").tail(no_incoming) == "SRT5674 x 5: \u274c 没有库存，也没有到货。请联系您的销售员。"
    assert _loc("zh").tail("SRT5674 x 50: \u2705 30 available. Please refer to your salesman.") == (
        "SRT5674 x 50: \u2705 有 30 件。请联系您的销售员。"
    )
    assert _loc("ms").tail("SRT5674 x 50: \u2705 Please refer to your salesman.") == (
        "SRT5674 x 50: \u2705 Sila rujuk jurujual anda."
    )
    assert _loc("ms").tail("SRTW2000 x 150: \u274c ETA 19/10/2026.") == "SRTW2000 x 150: \u274c ETA 19/10/2026."


def test_couldnt_find_then_the_offer_both_translate_on_one_line():
    """The tester's turn 6 line: the miss and the offer share one line."""
    english = 'Couldn\'t find: "ZZNOPE999" (product). Would you like me to escalate to warehouse team?'
    assert _loc("ms").reply(english) == (
        'Tidak dapat menemui: "ZZNOPE999" (product). Adakah anda mahu saya rujuk kepada pasukan warehouse?'
    )


def test_a_stock_section_with_a_no_stock_line_then_the_incoming_block():
    """The block opens with its own "No stock for X." paragraph above the lead."""
    english = _block(items=[_INCOMING_ITEM], missing=[{"code": "SRTWC286", "_n": "SRTWC286", "uuid": "u1"}])
    assert english.startswith("No stock for SRTWC286.\n\nBut there is INCOMING stock (ETA)")
    out = _loc("ms").reply(english)
    assert out.startswith("Tiada stok untuk SRTWC286.\n\nTetapi ada stok MASUK (ETA)")
    assert "Product Code" not in out


# --------------------------------------------------------------------------- #
# The PO rung sentences (`answer._apply_crossdomain_rung._group_parts`)
# --------------------------------------------------------------------------- #

_PAIRS = [
    ("No stock", "no incoming"),
    ("No incoming", "no stock"),
    ("Stock is 0 at every location", "no incoming"),
    ("No incoming", "stock is 0 at every location"),
]


@pytest.mark.parametrize("lead,trail", _PAIRS)
@pytest.mark.parametrize("header", ["but PO is placed", "but stock is on order from the supplier"])
def test_every_po_rung_found_sentence_is_catalogued(lead, trail, header):
    english = f"{lead} and {trail} for {{codes}}, {header}:"
    assert english in LABELS
    line = english.replace("{codes}", "SRTWC191-G3")
    for lang in ("ms", "zh"):
        out = _loc(lang).reply(line + "\n*Product Code:* SRTWC191-G3\n*PO date:* 2026-08-10")
        first, code_line, date_line = out.split("\n")
        assert first != line and "SRTWC191-G3" in first
        assert code_line.endswith(":* SRTWC191-G3") and "Product Code" not in code_line
        assert date_line.endswith(":* 2026-08-10") and "PO date" not in date_line


@pytest.mark.parametrize("lead,trail", _PAIRS)
def test_every_po_rung_still_nothing_sentence_is_catalogued(lead, trail):
    english = f"{lead}, {trail} and nothing on order for {{codes}}."
    assert english in LABELS
    line = english.replace("{codes}", "SRTWC191-G3")
    for lang in ("ms", "zh"):
        out = _loc(lang).reply(line)
        assert out != line and "SRTWC191-G3" in out


def test_the_rung_wording_in_the_code_is_the_wording_catalogued():
    """Pins the catalog keys to the f-strings `_group_parts` builds, so a reword there reds here."""
    src = (Path(__file__).resolve().parents[2] / "app/services/chatbot/lanes/business/answer.py").read_text()
    assert 'f"{lead} and {trail} for {\', \'.join(found)}, {header}:\\n{po_lines}"' in src
    assert 'f"{lead}, {trail} and nothing on order for {\', \'.join(still_nothing)}."' in src
    assert '"but stock is on order from the supplier"' in src and '"but PO is placed"' in src
    assert 'lines.append(f"*PO date:* {_fmt_xd_value(po_date)}")' in src


# --------------------------------------------------------------------------- #
# Direct incoming answers (the three incoming tools render through the localizer)
# --------------------------------------------------------------------------- #


def test_incoming_tools_are_localized_tools():
    from app.services.chatbot.lanes.business import fetch

    assert {
        "crm_incoming_stock_list",
        "crm_incoming_stock_by_product",
        "crm_incoming_stock_shipments",
    } <= fetch._LOCALIZED_TOOLS


def test_presenter_incoming_labels_are_catalogued_except_the_abbreviations():
    p = _presenters()
    labels = {label for _key, label in p._CLEARANCE_PAIRS}
    labels |= {
        "Company",
        "Product Code",
        "Product Name",
        "Container",
        "Shipment Container",
        "Estimated Arrival Date",
        "Batch",
        "Incoming Quantity",
        "Warehouse Allocations",
        "Unallocated Quantity",
        "Shipment",
        "Total Incoming Quantity",
        "Distinct Products",
    }
    abbreviations = {"ETA", "ETC", "ETD"}  # Q3: printed as they are
    missing = sorted(lab for lab in labels - abbreviations if lab not in LABELS)
    assert missing == []
    for intro in (p._DEFAULT_INTRO["crm_incoming_stock_list"], p._DEFAULT_INTRO["crm_incoming_stock_shipments"]):
        assert intro in LABELS


def test_presenter_incoming_literals_are_the_ones_pinned_here():
    """The labels above are copied from the presenter; a reword there reds here."""
    src = (Path(__file__).resolve().parents[3] / "sorento_crm_mcp/sorento_crm_mcp/presenters.py").read_text()
    for literal in (
        '"Container"',
        '"Shipment Container"',
        '"Estimated Arrival Date"',
        '"Batch"',
        '"Incoming Quantity"',
        '"Warehouse Allocations"',
        '"Unallocated Quantity"',
        '"Total Incoming Quantity"',
        '"Distinct Products"',
        '"No ETA"',
    ):
        assert literal in src, literal


def _incoming_envelope() -> dict[str, Any]:
    return {
        "answers": [{"text": "Here is the incoming stock I found."}],
        "items": [
            {
                "title": "SRTWC286",
                "fields": [
                    {"key": "product_code", "label": "Product Code", "value": "SRTWC286"},
                    {"key": "shipping_container_number", "label": "Container", "value": "SEGU4008631"},
                    {"key": "estimated_arrival_date", "label": "ETA", "value": "15/10/2026"},
                    {"key": "remaining_incoming_quantity", "label": "Incoming Quantity", "value": 40},
                ],
            }
        ],
        "has_result": True,
    }


def test_a_direct_incoming_answer_renders_its_labels_in_the_reply_language():
    out = output_structurer(
        _incoming_envelope(),
        {"tool": "crm_incoming_stock_list", "localizer": _loc("ms")},
    )
    text = out["response"]
    assert "*Kontena:* SEGU4008631" in text
    assert "*Kuantiti Masuk:* 40" in text
    assert "*ETA:* 15/10/2026" in text
    assert "Container" not in text and "Incoming Quantity" not in text


# --------------------------------------------------------------------------- #
# The two final-pass rules this adds, and what they must never touch
# --------------------------------------------------------------------------- #


def test_a_dash_led_line_with_an_uncatalogued_label_is_unchanged():
    loc = _loc("ms")
    for line in ("- *BRW-BB:* 30", "- SRTWC286", "- *Product Code*"):
        assert loc.reply(line) == line


def test_a_dash_led_line_never_translates_its_value():
    assert _loc("ms").reply("- *Product Code:* Product Code") == "- *Kod Produk:* Product Code"


def test_a_flag_line_with_an_uncatalogued_flag_is_unchanged():
    loc = _loc("zh")
    for line in ("⚠️  *(SRTWC286)*", "⚠️  *(PRODUCT DISCONTINUED SOON)*", "*(PRODUCT DISCONTINUED)*"):
        assert loc.reply(line) == line


def test_english_is_untouched():
    english = _block(items=[_INCOMING_ITEM], missing=[{"code": "SRTWC286", "_n": "SRTWC286", "uuid": "u1"}])
    assert label_catalog.IDENTITY.reply(english) == english


# --------------------------------------------------------------------------- #
# AVAIL-MODE-REPLIES (#1430) rule 5 in ms: the miss line sits before the refer line
# --------------------------------------------------------------------------- #


def test_a_malay_dealer_eta_ask_names_the_miss_before_the_refer_line(session_factory, monkeypatch, stub_access):
    """#1430 slots "Couldn't find: X." between the ETA lines and the refer line by matching
    the English refer line; an ms turn's reply already ends in the translated one."""
    from datetime import date

    from tests.chatbot._avail_mode_console import AvailConsole, Stock
    from tests.chatbot._r9_engine_console import product, reply

    c = AvailConsole(
        session_factory, monkeypatch, stub_access, phone="+60000009801", SRTW2000=Stock(eta=date(2026, 10, 19))
    )
    out = c.say(
        "bila sampai SRTW2000 dan FOO99?",
        reply(entities=[product("SRTW2000"), product("FOO99")], domain_hint="incoming", intent_hint="check_incoming"),
    )
    blocks = out.split("\n\n")
    assert blocks[-1] == "Sila rujuk jurujual anda.", out
    assert blocks[-2] == "Tidak dapat menemui: FOO99.", out
    assert out.count("Sila rujuk jurujual anda.") == 1, out
    assert "SRTW2000" in blocks[0] and "19/10/2026" in blocks[0], out


def test_a_leading_number_token_never_swallows_the_code_prefix():
    """`{available} available. ...` opens with a token: it matches a number only, so neither
    `tail` nor the final pass reads "SRT5674 x 50: ✅ 30" as the count."""
    line = "SRT5674 x 50: ✅ 30 available. Please refer to your salesman."
    for lang in ("ms", "zh"):
        assert _loc(lang).tail(line).startswith("SRT5674 x 50: ✅ ")
        assert _loc(lang).reply(line).startswith("SRT5674 x 50: ✅ ")
    assert _loc("ms").text("30 available. Please refer to your salesman.") == "30 ada. Sila rujuk jurujual anda."
