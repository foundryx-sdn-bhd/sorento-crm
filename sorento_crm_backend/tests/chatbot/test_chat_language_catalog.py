"""CHAT-LANGUAGE slice 1 (stock), AC-CL01..AC-CL04: the label catalog and the Localizer.

Plan `documentation/plans/chatbot/PLAN-chat-language-2oct.md`, UAC
`documentation/plans/chatbot/chat-language-acceptance-criteria.md`. Pure: no database.
`app.services.chatbot.label_catalog` does not exist yet, so every test here is red on import.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.services.chatbot import label_catalog
from app.services.chatbot.label_catalog import IDENTITY, Localizer

# The slice-1 table, pinned here as literals so a drifting catalog fails loudly (the UAC's
# "Slice-1 catalog content (exact)").
SLICE1: dict[str, tuple[str, str]] = {
    "Company": ("Syarikat", "公司"),
    "Product Code": ("Kod Produk", "产品代码"),
    "Product Name": ("Nama Produk", "产品名称"),
    "Warehouse": ("Gudang", "仓库"),
    "System Location": ("Lokasi Sistem", "系统位置"),
    "Quantity On Hand": ("Kuantiti Ada", "现有数量"),
    "Outstanding": ("Tertunggak", "未交货"),
    "Total": ("Jumlah", "总数"),
    "Stock summary for the requested products.": (
        "Ringkasan stok untuk produk yang diminta.",
        "所请求产品的库存摘要。",
    ),
    "Stock details found for the requested products.": (
        "Butiran stok untuk produk yang diminta.",
        "所请求产品的库存详情。",
    ),
    "How many units do you need?": ("Berapa unit yang anda perlukan?", "您需要多少件？"),
    "yes, we have stock. Please refer to your salesman.": (
        "ya, stok ada. Sila rujuk jurujual anda.",
        "有库存。请联系您的销售员。",
    ),
    "no stock and no incoming at the moment. Please refer to your salesman.": (
        "tiada stok dan tiada barang masuk buat masa ini. Sila rujuk jurujual anda.",
        "目前没有库存，也没有到货。请联系您的销售员。",
    ),
    "the quantity is more than what I can confirm here. Please refer to your salesman.": (
        "kuantiti ini melebihi apa yang boleh saya sahkan di sini. Sila rujuk jurujual anda.",
        "这个数量超出我在这里可以确认的范围。请联系您的销售员。",
    ),
    "no stock at the moment, ETA {eta}.": (
        "tiada stok buat masa ini, ETA {eta}.",
        "目前没有库存，ETA {eta}。",
    ),
    "Data last updated: {ts}": ("Data dikemas kini: {ts}", "数据更新时间: {ts}"),
    "No stock found for {codes}.": (
        "Tiada stok ditemui untuk {codes}.",
        "未找到 {codes} 的库存。",
    ),
    "Here are the results.": ("Berikut ialah hasilnya.", "以下是结果。"),
    "PRODUCT DISCONTINUED": ("PRODUK DIHENTIKAN", "产品已停产"),
}

STOCK_FIELD_KEYS = {
    "company_name": "Company",
    "product_code": "Product Code",
    "product_name": "Product Name",
    "warehouse": "Warehouse",
    "system_location": "System Location",
    "quantity_on_hand": "Quantity On Hand",
    "open_so_qty": "Outstanding",
    "total_on_hand": "Total",
}


def _presenters():
    """The real MCP presenter, imported the way `test_stock_ask_availability_rendered_reply.py`
    does: the `sorento_crm_mcp` beside THIS checkout, appended so a stale install cannot win."""
    repo_root = Path(__file__).resolve().parents[3]
    mcp_root = repo_root / "sorento_crm_mcp"
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    try:
        from sorento_crm_mcp import presenters
    except ImportError:  # pragma: no cover - only where the package is not on disk
        pytest.skip("sorento_crm_mcp is not importable in this environment")
    return presenters


# --------------------------------------------------------------------------- #
# AC-CL01: completeness and token parity
# --------------------------------------------------------------------------- #


def test_ac_cl01_languages_are_en_ms_zh():
    assert label_catalog.LANGUAGES == ("en", "ms", "zh")


def test_ac_cl01_every_entry_has_ms_and_zh():
    assert label_catalog.LABELS, "the catalog is empty"
    for english, entry in label_catalog.LABELS.items():
        assert set(entry) >= {"ms", "zh"}, english
        assert entry["ms"].strip() and entry["zh"].strip(), english


def test_ac_cl01_every_translation_keeps_the_english_tokens():
    for english, entry in label_catalog.LABELS.items():
        for lang in ("ms", "zh"):
            assert label_catalog.tokens_match(english, entry[lang]), (english, lang)


def test_ac_cl01_slice1_content_is_exactly_the_reviewed_wording():
    for english, (ms, zh) in SLICE1.items():
        assert english in label_catalog.LABELS, english
        assert label_catalog.LABELS[english]["ms"] == ms, english
        assert label_catalog.LABELS[english]["zh"] == zh, english


def test_ac_cl01_defaults_cover_the_whole_catalog_and_en_is_empty():
    for lang in ("ms", "zh"):
        d = label_catalog.defaults(lang)
        assert set(d) == set(label_catalog.LABELS), lang
        for english, target in d.items():
            assert target == label_catalog.LABELS[english][lang]
    assert label_catalog.defaults("en") == {}


def test_tokens_are_the_sorted_placeholders():
    assert label_catalog.tokens("no stock at the moment, ETA {eta}.") == ["eta"]
    assert label_catalog.tokens("{ts} then {codes}") == ["codes", "ts"]
    assert label_catalog.tokens("plain") == []


def test_tokens_match_is_a_multiset_comparison():
    assert label_catalog.tokens_match("a {x} b", "z {x}")
    assert not label_catalog.tokens_match("a {x} b", "z")
    assert not label_catalog.tokens_match("a {x}", "z {x} {y}")
    assert not label_catalog.tokens_match("a {x}", "z {y}")
    assert not label_catalog.tokens_match("a {x} {x}", "z {x}")


# --------------------------------------------------------------------------- #
# AC-CL02: every stock-path label and sentence is catalogued
# --------------------------------------------------------------------------- #


def test_ac_cl02_field_keys_map_to_the_english_labels():
    for key, english in STOCK_FIELD_KEYS.items():
        assert label_catalog.FIELD_KEYS.get(key) == english, key
        assert english in label_catalog.LABELS, english


def test_ac_cl02_pinned_stock_list_is_catalogued():
    missing = [s for s in SLICE1 if s not in label_catalog.LABELS]
    assert missing == []


def test_ac_cl02_presenter_tails_and_intros_are_catalogued():
    p = _presenters()
    for branch, sentence in p._AVAILABILITY_TAILS.items():
        # #1430: each verdict opens with a status mark, which is not text.
        bare = label_catalog._STATUS_MARK.sub("", sentence, count=1)
        assert bare in label_catalog.LABELS, branch
    assert p.REFER_TO_SALESMAN == "Please refer to your salesman."
    assert p._STOCK_COMPACT_INTRO in label_catalog.LABELS
    assert p._AVAILABILITY_ASK in label_catalog.LABELS
    # The detailed stock intro and the generic fallback intro the renderer prints.
    assert "Stock details found for the requested products." in label_catalog.LABELS
    assert "Here are the results." in label_catalog.LABELS
    # The incoming tail is a template; the presenter prints it with the ETA filled in.
    assert label_catalog._STATUS_MARK.sub("", p._availability_tail({"branch": "incoming", "eta": "{eta}"}), count=1) in label_catalog.LABELS


def test_ac_cl02_presenter_stock_labels_are_catalogued():
    """Every label `_stock` / `_stock_compact` emit for a fixed field is catalogued."""
    p = _presenters()
    entry = {
        "product_code": "SRTSWT3001",
        "total_on_hand": 5,
        "locations": [{"warehouse_code": "BRW-BB", "quantity_on_hand": 5}],
    }
    b = p._Builder()
    p._stock_compact({"stock_summary": [entry]}, b)
    labels = [f["label"] for item in b.items for f in item["fields"]]
    fixed = [lb for lb in labels if lb != "BRW-BB"]
    assert fixed == ["Product Code", "Total"]
    for lb in fixed:
        assert lb in label_catalog.LABELS


# --------------------------------------------------------------------------- #
# Localizer
# --------------------------------------------------------------------------- #


@pytest.fixture(params=["ms", "zh"])
def lang(request):
    return request.param


@pytest.fixture
def loc(lang):
    return Localizer(lang, label_catalog.defaults(lang))


def test_identity_is_an_en_localizer_with_an_empty_table():
    assert isinstance(IDENTITY, Localizer)
    assert IDENTITY.label({"label": "Product Code", "value": "X"}) == "Product Code"
    assert IDENTITY.text("Stock summary for the requested products.") == (
        "Stock summary for the requested products."
    )
    assert IDENTITY.tail("SRTSWT3001 x 10: yes, we have stock. Please refer to your salesman.") == (
        "SRTSWT3001 x 10: yes, we have stock. Please refer to your salesman."
    )
    assert IDENTITY.text("no stock at the moment, ETA 15/10/2026.") == (
        "no stock at the moment, ETA 15/10/2026."
    )


def test_label_by_key_when_the_label_is_the_catalogued_english(loc, lang):
    f = {"key": "product_code", "label": "Product Code", "value": "SRTSWT3001"}
    assert loc.label(f) == SLICE1["Product Code"][0 if lang == "ms" else 1]
    f = {"key": "open_so_qty", "label": "Outstanding", "value": 7}
    assert loc.label(f) == SLICE1["Outstanding"][0 if lang == "ms" else 1]


def test_label_by_exact_text_when_there_is_no_key(loc, lang):
    idx = 0 if lang == "ms" else 1
    assert loc.label({"label": "Total", "value": 51}) == SLICE1["Total"][idx]
    assert loc.label({"label": "Warehouse", "value": "BUKIT RAJA"}) == SLICE1["Warehouse"][idx]


def test_a_catalogued_key_whose_label_differs_is_not_translated_by_key(loc):
    """The key route only applies while the label IS the catalogued English one: a
    presenter that relabelled the field (here the sellable wording) falls through to the
    exact-text route, finds nothing, and the label comes back unchanged."""
    f = {"key": "total_on_hand", "label": "Grand Sum", "value": 1}
    assert loc.label(f) == "Grand Sum"


def test_ac_cl03_values_and_unknown_labels_are_never_changed(loc):
    # A location code used as a label.
    assert loc.label({"label": "BRW-BB", "value": 30}) == "BRW-BB"
    # An uncatalogued label.
    assert loc.label({"label": "Foo Bar", "value": 1}) == "Foo Bar"
    assert loc.text("Foo Bar") == "Foo Bar"
    # A product code, a number, a date and a warehouse name through `text`.
    for value in ("SRTSWT3001", "51", "02/10/2026 09:15", "BUKIT RAJA", "Total value"):
        assert loc.text(value) == value
    assert loc.tail("Foo Bar") == "Foo Bar"


def test_ac_cl03_label_never_touches_the_value(loc):
    f = {"key": "product_code", "label": "Product Code", "value": "Total"}
    loc.label(f)
    assert f == {"key": "product_code", "label": "Product Code", "value": "Total"}


def test_text_exact_entry(loc, lang):
    idx = 0 if lang == "ms" else 1
    for english in (
        "Stock summary for the requested products.",
        "Stock details found for the requested products.",
        "How many units do you need?",
        "Here are the results.",
        "PRODUCT DISCONTINUED",
    ):
        assert loc.text(english) == SLICE1[english][idx]


def test_text_template_reinserts_the_value_verbatim(lang):
    loc = Localizer(lang, label_catalog.defaults(lang))
    out = loc.text("no stock at the moment, ETA 15/10/2026.")
    expected = {
        "ms": "tiada stok buat masa ini, ETA 15/10/2026.",
        "zh": "目前没有库存，ETA 15/10/2026。",
    }[lang]
    assert out == expected


def test_text_template_for_codes_and_footer(lang):
    loc = Localizer(lang, label_catalog.defaults(lang))
    codes = loc.text("No stock found for SRTSWT3001-GM.")
    ts = loc.text("Data last updated: 02/10/2026 09:15")
    if lang == "ms":
        assert codes == "Tiada stok ditemui untuk SRTSWT3001-GM."
        assert ts == "Data dikemas kini: 02/10/2026 09:15"
    else:
        assert codes == "未找到 SRTSWT3001-GM 的库存。"
        assert ts == "数据更新时间: 02/10/2026 09:15"


def test_template_must_match_the_whole_string(loc):
    # A longer string that merely contains the English shape is not a match.
    s = "note: no stock at the moment, ETA 15/10/2026. extra"
    assert loc.text(s) == s
    s2 = "Please say: Data last updated: x"
    assert loc.text(s2) == s2


def test_tail_translates_the_sentence_and_keeps_the_prefix(lang):
    loc = Localizer(lang, label_catalog.defaults(lang))
    yes = loc.tail("SRTSWT3001 x 10: yes, we have stock. Please refer to your salesman.")
    eta = loc.tail("SRTSWT3001 x 10: no stock at the moment, ETA 15/10/2026.")
    if lang == "ms":
        assert yes == "SRTSWT3001 x 10: ya, stok ada. Sila rujuk jurujual anda."
        assert eta == "SRTSWT3001 x 10: tiada stok buat masa ini, ETA 15/10/2026."
    else:
        assert yes == "SRTSWT3001 x 10: 有库存。请联系您的销售员。"
        assert eta == "SRTSWT3001 x 10: 目前没有库存，ETA 15/10/2026。"


def test_tail_keeps_everything_before_the_first_colon_space(lang):
    loc = Localizer(lang, label_catalog.defaults(lang))
    out = loc.tail("A: B x 3: yes, we have stock. Please refer to your salesman.")
    # The prefix is everything before the FIRST ": ", so "B x 3: yes..." is the sentence,
    # which is not a catalog entry and comes back unchanged.
    assert out == "A: B x 3: yes, we have stock. Please refer to your salesman."


def test_tail_without_a_colon_falls_back_to_text(loc, lang):
    assert loc.tail("Here are the results.") == SLICE1["Here are the results."][0 if lang == "ms" else 1]


def test_tail_with_an_uncatalogued_sentence_is_unchanged(loc):
    s = "SRTSWT3001 x 10: something we never said."
    assert loc.tail(s) == s


# --------------------------------------------------------------------------- #
# AC-CL04: a token-mismatched translation is rejected
# --------------------------------------------------------------------------- #


def test_ac_cl04_defaults_omit_an_entry_whose_translation_drops_a_token(monkeypatch):
    bad = {
        **label_catalog.LABELS,
        "no stock at the moment, ETA {eta}.": {"ms": "tiada stok buat masa ini.", "zh": "目前没有库存。"},
    }
    monkeypatch.setattr(label_catalog, "LABELS", bad)
    for lang in ("ms", "zh"):
        d = label_catalog.defaults(lang)
        assert "no stock at the moment, ETA {eta}." not in d
        # The rest of the catalog is unaffected.
        assert d["Total"] == label_catalog.LABELS["Total"][lang]


def test_ac_cl04_defaults_omit_an_entry_whose_translation_adds_a_token(monkeypatch):
    bad = {
        **label_catalog.LABELS,
        "Total": {"ms": "Jumlah {extra}", "zh": "总数 {extra}"},
    }
    monkeypatch.setattr(label_catalog, "LABELS", bad)
    assert "Total" not in label_catalog.defaults("ms")
    assert "Total" not in label_catalog.defaults("zh")


def test_identity_returns_everything_unchanged():
    for english in SLICE1:
        assert IDENTITY.text(english) == english
        assert IDENTITY.label({"label": english, "value": 1}) == english
    assert IDENTITY.label({"key": "total_on_hand", "label": "Total", "value": 1}) == "Total"
