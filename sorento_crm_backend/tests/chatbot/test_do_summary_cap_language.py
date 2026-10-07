"""DO-SUMMARY-CAP, the chat-language half (reviewer should-fix, 7 Oct 2026): the trailer the
capped By product / By customer group ends with is a fixed sentence the outstanding report
prints, so it must be in `label_catalog.LABELS` or a Malay or Chinese dealer reads one English
line inside a translated summary (slice 3 of PLAN-chat-language-2oct.md: the backend translates
the finished report line by line through `Localizer.lines`).

Pinned the way `test_chat_language_slice3.py` pins its table: literals here, so a drifting
catalog fails loudly, plus the presenter cross-check so the catalog key and the sentence the MCP
presenter writes cannot part ways.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.services.chatbot import label_catalog
from app.services.chatbot.label_catalog import Localizer

REPO_ROOT = Path(__file__).resolve().parents[3]

TRAILERS: dict[str, tuple[str, str]] = {
    # R3 (owner ruling, 7 Oct 2026): the trailer counts only, four forms: the noun, singular
    # for K = 1. No bracket, no pointer; the closing offer line is the pointer.
    "... and {k} more products": ("... dan {k} lagi produk", "... 还有 {k} 个产品"),
    "... and {k} more product": ("... dan {k} lagi produk", "... 还有 {k} 个产品"),
    "... and {k} more customers": ("... dan {k} lagi pelanggan", "... 还有 {k} 个客户"),
    "... and {k} more customer": ("... dan {k} lagi pelanggan", "... 还有 {k} 个客户"),
}


def _loc(lang: str) -> Localizer:
    return Localizer(lang, label_catalog.defaults(lang))


def test_every_trailer_form_is_catalogued_with_ms_and_zh():
    for english, (ms, zh) in TRAILERS.items():
        assert english in label_catalog.LABELS, english
        entry = label_catalog.LABELS[english]
        assert entry["ms"] == ms and entry["zh"] == zh, english
        for lang in ("ms", "zh"):
            assert label_catalog.tokens_match(english, entry[lang]), (english, lang)
            assert label_catalog.defaults(lang)[english] == entry[lang]


@pytest.mark.parametrize(
    "line, ms, zh",
    [
        ("... and 292 more products", "... dan 292 lagi produk", "... 还有 292 个产品"),
        ("... and 1 more product", "... dan 1 lagi produk", "... 还有 1 个产品"),
        ("... and 3 more customers", "... dan 3 lagi pelanggan", "... 还有 3 个客户"),
        ("... and 1 more customer", "... dan 1 lagi pelanggan", "... 还有 1 个客户"),
    ],
)
def test_lines_translates_the_trailer_and_keeps_k(line: str, ms: str, zh: str):
    assert _loc("ms").lines(line) == ms
    assert _loc("zh").lines(line) == zh
    # English stays English, byte for byte.
    assert _loc("en").lines(line) == line


def test_the_capped_summary_translates_whole_with_no_english_line_left():
    """The MCP golden of the prod shape, through `Localizer.lines`: every fixed line changes,
    every row keeps its code and figures, and no `more products` survives in English."""
    golden = REPO_ROOT / "sorento_crm_mcp" / "tests" / "fixtures" / "outstanding" / "outstanding-report-do-capped.txt"
    text = golden.read_text().rstrip("\n")
    out = _loc("ms").lines(text)
    assert "more products" not in out, out
    assert "... dan 292 lagi produk\n" in out, out
    assert "balas" not in out.split("Balas 1 untuk")[0], out
    assert "Balas 1 untuk senarai pesanan penghantaran." in out, out
    assert "SRTWT7020: 4 (O/S: 4)" in out and "SRTWT7001: 96 (O/S: 96)" in out, out
    assert out.count("\n") == text.count("\n")


def test_the_presenter_writes_the_sentence_the_catalog_keys():
    """The catalog key and the presenter's f-string are two copies of one sentence (the backend
    image cannot import the MCP package); pin the fragments so neither can drift alone."""
    mcp_root = REPO_ROOT / "sorento_crm_mcp"
    source_path = mcp_root / "sorento_crm_mcp" / "presenters.py"
    if not source_path.exists():  # pragma: no cover - only where the package is not on disk
        pytest.skip("sorento_crm_mcp is not on disk in this environment")
    source = source_path.read_text()
    assert '"... and {extra} more {noun}{plural}"' in source
    assert "for the full list" not in source and "(reply {position}" not in source
    assert '"customer_name": "customer", "product_code": "product"' in source
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    from sorento_crm_mcp import presenters

    rows = [{"product_code": f"P{i:03d}", "do_qty": 1, "pending_qty": 1} for i in range(23)]
    lines = presenters._outstanding_capped_group(
        rows, name_key="product_code", total_key="do_qty", outstanding_key="pending_qty"
    )
    assert lines[-1] == "... and 3 more products"
    assert _loc("zh").lines(lines[-1]) == "... 还有 3 个产品"
    assert _loc("ms").lines(lines[-1]) == "... dan 3 lagi produk"
