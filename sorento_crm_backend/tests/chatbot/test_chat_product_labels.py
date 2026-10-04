"""CHAT-PRODUCT-LABELS (follow-up A of #1448): product info replies follow the reply language.

The product, promotion product and product attachment presenters, and the "Specs" / "not
recorded yet" / access-note text `output_structurer` adds, printed English labels for ms and zh
contacts because none of them had a catalog entry. The reply is rendered in English by the real
presenter and `output_structurer`, then put through the localizer exactly as the turn's final
pass (`engine._localize_result`) does. Product data values (names, descriptions, spec labels)
must come back unchanged. The manual-row test is Postgres only, on a blank schema.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.models.translation_memory import TranslationMemory
from app.services.chatbot import label_catalog
from app.services.chatbot.label_catalog import INLINE, LABELS, VALUE_WORDS, Localizer
from app.services.chatbot.lanes.business.fetch import output_structurer
from app.services.text_tokens import tokens
from tests._pg_fixture import blank_session

_DASH = "—"
_ACCESS_NOTE = f"I can't share the {{label}} {_DASH} please check with the office."

#: Every entry this lane adds: English -> (ms, zh).
NEW = {
    "Description": ("Keterangan", "描述"),
    "List Price": ("Harga Senarai", "标价"),
    "Dimensions": ("Dimensi", "尺寸"),
    "Not defined": ("Tidak ditetapkan", "未设定"),
    "Promotion": ("Promosi", "促销"),
    "Selling Price": ("Harga Jualan", "售价"),
    "Attachment Type": ("Jenis Lampiran", "附件类型"),
    "File Name": ("Nama Fail", "文件名"),
    "Certificate Number": ("No. Sijil", "证书编号"),
    "Valid Until": ("Sah Sehingga", "有效期至"),
    "Validity": ("Kesahan", "有效状态"),
    "File Link": ("Pautan Fail", "文件链接"),
    "Specs": ("Spesifikasi", "规格"),
    "not recorded yet": ("belum direkodkan", "尚未记录"),
    _ACCESS_NOTE: (
        "Saya tidak boleh berkongsi {label}. Sila semak dengan pejabat.",
        "我无法提供 {label}，请向办公室查询。",
    ),
    "Here are the matching products.": ("Berikut ialah produk yang sepadan.", "以下是匹配的产品。"),
    "Here are the matching promotion products.": (
        "Berikut ialah produk promosi yang sepadan.",
        "以下是匹配的促销产品。",
    ),
    "Here are the product files I found.": (
        "Berikut ialah fail produk yang saya temui.",
        "以下是我找到的产品文件。",
    ),
    # The presenter's validity words and its missing-link text (crew, 4 Oct): values of their
    # own label only.
    "Valid": ("Sah", "有效"),
    "Expiring soon": ("Akan tamat tempoh", "即将到期"),
    "Expired": ("Tamat tempoh", "已过期"),
    "Not yet valid": ("Belum sah", "尚未生效"),
    "Unknown": ("Tidak diketahui", "未知"),
    "(file link unavailable right now)": (
        "(pautan fail tidak tersedia buat masa ini)",
        "（文件链接暂时无法获取）",
    ),
}
_VALIDITY = ("Valid", "Expiring soon", "Expired", "Not yet valid", "Unknown")
_NO_LINK = "(file link unavailable right now)"
_LANG = {"ms": 0, "zh": 1}


def _loc(lang: str) -> Localizer:
    return Localizer(lang, label_catalog.defaults(lang))


def _t(english: str, lang: str) -> str:
    return NEW[english][_LANG[lang]]


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


_PRODUCT = {
    "company_name": "SORENTO",
    "product_code": "SRTWC286",
    "product_name": "Wall Hung Basin",
    # Product data that reads like catalogued text must never be translated.
    "description": "Promotion",
    "specs": [{"key": "width", "label": "Width", "value": 500, "unit": "mm"}],
}


def _english(tool: str, rows: list[dict]) -> str:
    raw = json.dumps({"data": rows})
    envelope = json.loads(_presenters().present_response(tool, raw))
    return output_structurer(envelope, {"tool": tool})["response"]


def _products_reply() -> str:
    return _english("crm_master_products_list", [dict(_PRODUCT)])


def _promotion_reply() -> str:
    product = {**_PRODUCT, "list_price": 120, "dimensions_length": 500, "dimensions_width": 400, "dimensions_height": 150}
    row = {"company_name": "SORENTO", "product": product, "promotion": {"description": "Raya Promo"}, "selling_price": 99}
    return _english("crm_marketing_promotion_products_list", [row])


def _attachments_reply() -> str:
    row = {
        "company_name": "SORENTO",
        "product": {**_PRODUCT, "dimensions_length": 500},
        "attachment": {"attachment_type": "Certificate", "original_filename": "cert.pdf"},
        "certificate": {"certificate_number": "C-1", "valid_until": "2027-01-01", "validity_state": "valid"},
    }
    return _english("crm_master_product_attachments_list", [row])


# --------------------------------------------------------------------------- #
# The catalog entries
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("english", sorted(NEW))
def test_every_new_entry_is_catalogued_in_ms_and_zh(english):
    assert LABELS[english] == {"ms": NEW[english][0], "zh": NEW[english][1]}


@pytest.mark.parametrize("english", sorted(NEW))
def test_every_new_entry_keeps_its_placeholders(english):
    for lang in ("ms", "zh"):
        assert sorted(tokens(LABELS[english][lang])) == sorted(tokens(english)), lang
        assert label_catalog.defaults(lang)[english] == LABELS[english][lang]


def test_the_catalog_source_stays_dash_free_and_the_ms_zh_text_carries_no_dash():
    source = Path(label_catalog.__file__).read_text(encoding="utf-8")
    assert _DASH not in source and "–" not in source
    for english in NEW:
        for lang in ("ms", "zh"):
            assert _DASH not in LABELS[english][lang] and "–" not in LABELS[english][lang]


def test_the_two_value_phrases_are_value_words_and_no_new_sentence_is_inline():
    assert {"Not defined", "not recorded yet"} <= VALUE_WORDS
    # The validity words and the link text are values of ONE label each, never global.
    assert not (set(_VALIDITY) | {_NO_LINK}) & VALUE_WORDS
    # Each new sentence prints as a line of its own, so `lines()` matches it whole.
    assert not set(NEW) & INLINE


# --------------------------------------------------------------------------- #
# A product info reply, end to end through the final pass
# --------------------------------------------------------------------------- #


def test_products_english_reply_is_what_the_presenter_prints():
    text = _products_reply()
    assert text.startswith("Here are the matching products.")
    for line in ("*Description:* Promotion", "*List Price:* Not defined", "*Dimensions:* Not defined", "*Specs:* Width: 500 mm"):
        assert line in text


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_products_reply_translates_labels_intro_and_not_defined(lang):
    text = _loc(lang).reply(_products_reply())
    assert text.startswith(_t("Here are the matching products.", lang))
    not_defined = _t("Not defined", lang)
    assert f"*{_t('List Price', lang)}:* {not_defined}" in text
    assert f"*{_t('Dimensions', lang)}:* {not_defined}" in text
    # Product data is untouched: the description that reads "Promotion", the spec label.
    assert f"*{_t('Description', lang)}:* Promotion" in text
    assert f"*{_t('Specs', lang)}:* Width: 500 mm" in text
    assert "Wall Hung Basin" in text and "SRTWC286" in text
    for english in ("Description:", "List Price", "Dimensions", "Not defined", "Specs:", "Here are"):
        assert english not in text


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_promotion_products_reply_translates_every_label(lang):
    text = _loc(lang).reply(_promotion_reply())
    assert text.startswith(_t("Here are the matching promotion products.", lang))
    assert f"*{_t('Promotion', lang)}:* Raya Promo" in text
    assert f"*{_t('Selling Price', lang)}:* MYR 99.00" in text
    assert f"*{_t('List Price', lang)}:* MYR 120.00" in text
    assert f"*{_t('Dimensions', lang)}:* 500 x 400 x 150 mm" in text
    for english in ("Promotion:", "Selling Price", "List Price", "Dimensions", "Here are"):
        assert english not in text


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_product_attachments_reply_translates_every_label(lang):
    text = _loc(lang).reply(_attachments_reply())
    assert text.startswith(_t("Here are the product files I found.", lang))
    for english, value in (
        ("Description", "Promotion"),
        ("Dimensions", "500 x - x - mm"),
        ("Attachment Type", "Certificate"),
        ("File Name", "cert.pdf"),
        ("Certificate Number", "C-1"),
        ("Valid Until", "2027-01-01"),
    ):
        assert f"*{_t(english, lang)}:* {value}" in text, english
    assert f"*{_t('Validity', lang)}:* {_t('Valid', lang)}" in text
    assert f"*{_t('File Link', lang)}:* {_t(_NO_LINK, lang)}" in text
    for english in ("Description:", "Attachment Type", "File Name", "Certificate Number", "Valid Until", "Validity:", "File Link", "unavailable"):
        assert english not in text
    # Data stays: the attachment type name and the file name.
    assert "Certificate" in text and "cert.pdf" in text


@pytest.mark.parametrize("lang", ["ms", "zh"])
@pytest.mark.parametrize("word", _VALIDITY)
def test_every_validity_word_translates_after_the_validity_label(lang, word):
    validity = _t("Validity", lang)
    assert _loc(lang).reply(f"*Validity:* {word}") == f"*{validity}:* {_t(word, lang)}"


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_validity_words_and_the_link_text_stay_as_printed_after_any_other_label(lang):
    loc = _loc(lang)
    status = LABELS["Status"][lang]
    # An order status value is printed as it is (#1448 owner Q3), whatever it reads.
    for word in _VALIDITY:
        assert loc.reply(f"*Status:* {word}") == f"*{status}:* {word}"
    assert loc.reply(f"*Status:* {_NO_LINK}") == f"*{status}:* {_NO_LINK}"
    assert loc.reply(f"*File Link:* Expired") == f"*{_t('File Link', lang)}:* Expired"
    assert loc.reply(f"*Validity:* {_NO_LINK}") == f"*{_t('Validity', lang)}:* {_NO_LINK}"
    # Inside a longer value it is data.
    assert loc.reply("*Validity:* Valid until 2027") == f"*{_t('Validity', lang)}:* Valid until 2027"


@pytest.mark.parametrize("lang", ["ms", "zh"])
@pytest.mark.parametrize("state", ["expired", "expiring_soon", "not_yet_valid", "unknown"])
def test_each_presenter_validity_state_reads_in_the_reply_language(lang, state):
    row = {
        "product": {"product_code": "SRTWC286"},
        "attachment": {"attachment_type": "Certificate", "original_filename": "cert.pdf", "url": "https://x/c.pdf"},
        "certificate": {"certificate_number": "C-1", "valid_until": "2027-01-01", "validity_state": state},
    }
    english = _english("crm_master_product_attachments_list", [row])
    word = _presenters()._VALIDITY_LABELS[state]
    assert f"*Validity:* {word}" in english
    assert f"*{_t('Validity', lang)}:* {_t(word, lang)}" in _loc(lang).reply(english)


# --------------------------------------------------------------------------- #
# The "not recorded yet" value and the access note (fetch.py)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_not_recorded_yet_translates_only_as_the_whole_value_of_a_catalogued_label(lang):
    loc = _loc(lang)
    container = LABELS["Container"][lang]
    assert loc.reply("*Container:* not recorded yet") == f"*{container}:* {_t('not recorded yet', lang)}"
    # An uncatalogued label (a spec registry label) keeps its line as printed.
    assert loc.reply("*Gatepass Number:* not recorded yet") == "*Gatepass Number:* not recorded yet"
    # Inside a longer value it is data.
    assert loc.reply("*Container:* not recorded yet at port") == f"*{container}:* not recorded yet at port"


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_the_access_note_translates_and_keeps_its_label_verbatim(lang):
    loc = _loc(lang)
    line = f"I can't share the eta delay date {_DASH} please check with the office."
    expected = _t(_ACCESS_NOTE, lang).replace("{label}", "eta delay date")
    assert loc.reply(line) == expected
    # One note per line, two notes, both translated.
    two = line + "\n" + line.replace("eta delay date", "gatepass")
    assert loc.reply(two) == expected + "\n" + _t(_ACCESS_NOTE, lang).replace("{label}", "gatepass")


def test_an_english_reply_is_unchanged():
    english = _products_reply() + "\n" + _attachments_reply()
    assert label_catalog.IDENTITY.reply(english) == english
    assert Localizer("en", label_catalog.defaults("en")).reply(english) == english


# --------------------------------------------------------------------------- #
# A staff-edited manual row still wins (Postgres)
# --------------------------------------------------------------------------- #


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def test_a_manual_row_for_a_new_label_wins_over_the_code_default(db):
    db.add(
        TranslationMemory(
            source_text="List Price",
            source_lang="en",
            target_lang="ms",
            target_text="Harga Katalog",
            source="manual",
        )
    )
    db.flush()
    loc = label_catalog.resolve(db, "ms")
    text = loc.reply(_products_reply())
    assert "*Harga Katalog:* Tidak ditetapkan" in text
    assert "Harga Senarai" not in text
    row = (
        db.query(TranslationMemory)
        .filter_by(source_text="List Price", source_lang="en", target_lang="ms")
        .one()
    )
    assert (row.target_text, row.source) == ("Harga Katalog", "manual")
    # The other new labels were seeded as `ai` rows on first use.
    seeded = {
        r.source_text: r.source
        for r in db.query(TranslationMemory).filter(
            TranslationMemory.target_lang == "ms", TranslationMemory.source_text.in_(list(NEW))
        )
    }
    assert set(seeded) == set(NEW)
    assert {s for k, s in seeded.items() if k != "List Price"} == {"ai"}
