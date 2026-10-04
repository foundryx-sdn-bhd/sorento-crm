"""CHAT-LANGUAGE slice 5 (AC-CL51..AC-CL55): the required-fields helper (#1445) and the
attachment gap line (#1437) read in ms / zh.

`documentation/plans/chatbot/chat-language-acceptance-criteria.md`, "Slice 5 (3 Oct)". Red-first:
none of these sentences is in `label_catalog.LABELS` yet, so an ms / zh turn prints English.

The English comes from the REAL helpers (`required_fields._miss_line`, `_pick_line`, the field
questions, `low_stock_ask.CANCELLED` / `GIVE_UP`), never retyped, then goes through the localizer
exactly as `engine._localize_result` does it (`Localizer.reply`). The catalog keys are the English
lines the helpers print, with the typed word as `{word}`; the compose gap line is the key
`{code} has no {types}.` (token names `code` and `types`).
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app.services.chatbot import label_catalog
from app.services.chatbot import required_fields as rf
from app.services.chatbot.label_catalog import INLINE, LABELS, Localizer
from app.services.chatbot.lanes.business import low_stock_ask as lsa
from tests.chatbot.test_low_stock_filter_ask import (
    GRANT,
    SUPPLIER_KEY,
    _ask,
    _console,
    _e,
    _reply,
)

APP = Path(rf.__file__).resolve().parents[2]  # .../app
WORD = "ZZT Kapal"
SENTINEL = "ZZTWORD"
OPTIONS = [[["ZZT-A1"], "ZZT-A1"], [["ZZT-A2"], "ZZT-A2"]]
PICK_TAIL = "\n1. ZZT-A1\n2. ZZT-A2"

# --------------------------------------------------------------------------- #
# THE WORDING TABLE (what the coder catalogs, exactly). `{word}` is the typed word.
# No em or en dashes. The literal "all" stays in Latin letters in every language.
# --------------------------------------------------------------------------- #
MS = {
    "category_question": 'Kategori produk yang mana? Balas dengan kategori (cth. water tap) atau "all".',
    "supplier_question": "Pembekal yang mana?",
    "miss_category": "Saya tidak mengenali '{word}' sebagai kategori.",
    "miss_supplier": "Saya tidak mengenali '{word}' sebagai pembekal.",
    "pick_category_all": 'Kategori mana yang anda maksudkan? Balas dengan nombor atau "all":',
    "pick_category": "Kategori mana yang anda maksudkan? Balas dengan nombor:",
    "pick_supplier_all": 'Pembekal mana yang anda maksudkan? Balas dengan nombor atau "all":',
    "pick_supplier": "Pembekal mana yang anda maksudkan? Balas dengan nombor:",
    "cancelled": "Laporan stok rendah dibatalkan.",
    "give_up": "Saya masih tidak dapat mengenali '{word}'. Minta laporan stok rendah sekali lagi dengan kategori atau \"all\".",
    "has_no": "{code} tiada {types}.",
}
ZH = {
    "category_question": '请问是哪个产品类别？请回复类别（例如 water tap）或 "all"。',
    "supplier_question": "请问是哪个供应商？",
    "miss_category": "我不认识 '{word}' 这个类别。",
    "miss_supplier": "我不认识 '{word}' 这个供应商。",
    "pick_category_all": '您指的是哪个类别？请回复编号或 "all"：',
    "pick_category": "您指的是哪个类别？请回复编号：",
    "pick_supplier_all": '您指的是哪个供应商？请回复编号或 "all"：',
    "pick_supplier": "您指的是哪个供应商？请回复编号：",
    "cancelled": "低库存报告已取消。",
    "give_up": "我仍然无法识别 '{word}'。请再次索取低库存报告，并提供类别或 \"all\"。",
    "has_no": "{code} 没有{types}。",
}
WORDING = {"ms": MS, "zh": ZH}

CATEGORY = lsa.CATEGORY
SUPPLIER = lsa.SUPPLIER
CATEGORY_NO_ALL = dataclasses.replace(CATEGORY, allow_all=False, required=True)
SUPPLIER_NO_ALL = dataclasses.replace(SUPPLIER, allow_all=False, required=True)


def _loc(lang: str) -> Localizer:
    return Localizer(lang, label_catalog.defaults(lang))


def _cases(lang: str):
    """(id, English reply from the real helpers, the expected localized reply)."""
    w = WORDING[lang]
    return [
        ("category_question", CATEGORY.question, w["category_question"]),
        ("supplier_question", SUPPLIER.question, w["supplier_question"]),
        ("miss_category", rf._miss_line(WORD, CATEGORY),
         w["miss_category"].format(word=WORD) + "\n\n" + w["category_question"]),
        ("miss_supplier", rf._miss_line(WORD, SUPPLIER),
         w["miss_supplier"].format(word=WORD) + "\n\n" + w["supplier_question"]),
        ("pick_category_all", rf._pick_line(CATEGORY, OPTIONS), w["pick_category_all"] + PICK_TAIL),
        ("pick_category", rf._pick_line(CATEGORY_NO_ALL, OPTIONS), w["pick_category"] + PICK_TAIL),
        ("pick_supplier_all", rf._pick_line(SUPPLIER, OPTIONS), w["pick_supplier_all"] + PICK_TAIL),
        ("pick_supplier", rf._pick_line(SUPPLIER_NO_ALL, OPTIONS), w["pick_supplier"] + PICK_TAIL),
        ("cancelled", lsa.CANCELLED, w["cancelled"]),
        ("give_up", lsa.GIVE_UP.format(word=WORD), w["give_up"].format(word=WORD)),
    ]


def _params():
    return [pytest.param(lang, cid, english, expected, id=f"{lang}-{cid}")
            for lang in ("ms", "zh") for cid, english, expected in _cases(lang)]


def _english_lines(english: str) -> list[str]:
    return [ln for ln in english.split("\n") if ln.strip() and not ln[:1].isdigit()]


# --------------------------------------------------------------------------- #
# AC-CL51: every required_fields / low_stock_ask sentence reads in ms / zh
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lang,cid,english,expected", _params())
def test_cl51_the_reply_reads_in_the_turn_language(lang, cid, english, expected):
    out = _loc(lang).reply(english)
    assert out == expected
    # The English sentence is gone, line by line.
    assert out != english
    for line in _english_lines(english):
        assert line not in out.split("\n"), (cid, line)


@pytest.mark.parametrize("lang,cid,english,expected", _params())
def test_cl51_the_typed_word_the_options_and_all_stay_verbatim(lang, cid, english, expected):
    out = _loc(lang).reply(english)
    assert out == expected  # translated, so the checks below are not vacuous
    if "{word}" in WORDING[lang].get(cid, "") or cid in ("miss_category", "miss_supplier", "give_up"):
        assert f"'{WORD}'" in out
    if cid.startswith("pick"):
        assert out.endswith(PICK_TAIL)
    if cid.endswith("_all") or cid in ("give_up", "category_question"):
        assert '"all"' in out


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_cl51_a_typed_word_that_looks_like_a_label_is_still_printed_as_typed(lang):
    """The word is a value: a typed 'Which supplier?' inside the quotes is not translated."""
    out = _loc(lang).reply(rf._miss_line("Which supplier?", CATEGORY))
    assert "'Which supplier?'" in out
    assert out.startswith(WORDING[lang]["miss_category"].format(word="Which supplier?"))


# --------------------------------------------------------------------------- #
# AC-CL52: an English turn is byte-identical
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("cid,english", [(c, e) for c, e, _x in _cases("ms")])
def test_cl52_an_english_turn_is_byte_identical(cid, english):
    assert label_catalog.IDENTITY.reply(english) == english
    assert Localizer("en", label_catalog.defaults("en")).reply(english) == english


def test_cl52_the_english_helpers_still_print_main_wording():
    assert CATEGORY.question == 'Which product category? Reply with a category (e.g. water tap) or "all".'
    assert SUPPLIER.question == "Which supplier?"
    assert rf._miss_line("x", SUPPLIER) == "I don't know 'x' as a supplier.\n\nWhich supplier?"
    assert rf._pick_line(CATEGORY, OPTIONS).startswith('Which category do you mean? Reply with a number or "all":\n1. ZZT-A1')
    assert rf._pick_line(CATEGORY_NO_ALL, OPTIONS).startswith("Which category do you mean? Reply with a number:\n")
    assert lsa.CANCELLED == "Low stock report cancelled."


# --------------------------------------------------------------------------- #
# AC-CL55: the catalog pin
# --------------------------------------------------------------------------- #


def _catalog_keys() -> list[str]:
    """The English lines the helpers print, the typed word as `{word}`; read off the real code."""
    keys: list[str] = []
    for spec in (CATEGORY, SUPPLIER):
        keys.append(spec.question)
        keys.extend(rf._miss_line(SENTINEL, spec).replace(SENTINEL, "{word}").split("\n\n")[:1])
    for spec in (CATEGORY, SUPPLIER, CATEGORY_NO_ALL, SUPPLIER_NO_ALL):
        keys.append(rf._pick_line(spec, OPTIONS).split("\n")[0])
    keys.append(lsa.CANCELLED)
    keys.append(lsa.GIVE_UP)  # already holds {word}
    return list(dict.fromkeys(keys))


@pytest.mark.parametrize("key", _catalog_keys())
def test_cl55_every_english_literal_is_a_catalog_key(key):
    assert key in LABELS, key
    assert set(LABELS[key]) >= {"ms", "zh"}, key


@pytest.mark.parametrize("key", _catalog_keys())
def test_cl55_every_sentence_is_inline_so_it_translates_after_another_sentence(key):
    assert key in INLINE, key


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_cl55_the_catalog_carries_exactly_the_stated_wording(lang):
    table = label_catalog.defaults(lang)
    w = WORDING[lang]
    expected = {
        CATEGORY.question: w["category_question"],
        SUPPLIER.question: w["supplier_question"],
        "I don't know '{word}' as a category.": w["miss_category"],
        "I don't know '{word}' as a supplier.": w["miss_supplier"],
        rf._pick_line(CATEGORY, OPTIONS).split("\n")[0]: w["pick_category_all"],
        rf._pick_line(CATEGORY_NO_ALL, OPTIONS).split("\n")[0]: w["pick_category"],
        rf._pick_line(SUPPLIER, OPTIONS).split("\n")[0]: w["pick_supplier_all"],
        rf._pick_line(SUPPLIER_NO_ALL, OPTIONS).split("\n")[0]: w["pick_supplier"],
        lsa.CANCELLED: w["cancelled"],
        lsa.GIVE_UP: w["give_up"],
    }
    for english, target in expected.items():
        assert table.get(english) == target, english


def test_cl55_the_source_literals_are_pinned():
    src = (APP / "services/chatbot/required_fields.py").read_text(encoding="utf-8")
    assert "I don't know '{word}' as a {spec.noun}.\\n\\n{spec.question}" in src
    assert 'f"Which {spec.noun} do you mean? Reply with a number"' in src
    assert """(' or "all":' if takes_all else ":")""" in src
    low = (APP / "services/chatbot/lanes/business/low_stock_ask.py").read_text(encoding="utf-8")
    assert 'question="Which supplier?"' in low
    assert "Low stock report cancelled." in low and "I still can't place '{word}'." in low


# --------------------------------------------------------------------------- #
# AC-CL54: "<code> has no <type>." (#1437)
# --------------------------------------------------------------------------- #

GAP_KEY = "{code} has no {types}."


def _compose_gap_text(lang: str | None, rows: list[tuple[str, str]], codes: list[str], asked: list[str],
                      lane_text: str = "I have attached the file(s) below.") -> str:
    """The product_attachment section `compose()` builds, with the turn's localizer on ctx."""
    from types import SimpleNamespace

    from app.services.chatbot.turn.compose import compose
    from app.services.chatbot.turn.policy import Policy
    from app.services.chatbot.turn.state import Focus, Profile, State
    from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

    env = {
        "domain": "product_attachment",
        "denied": False,
        "entities": [*codes, *asked],
        "product_codes": list(codes),
        "attachment_types": [{"name": n, "keys": [n]} for n in asked],
        "figures": [
            {"fields": [
                {"label": "Product Code", "value": c},
                {"label": "Attachment Type", "value": t},
                {"label": "File Name", "value": f"{c}.pdf"},
            ]}
            for c, t in rows
        ],
        "files": [{"url": f"https://example.test/{c}.pdf", "filename": f"{c}.pdf"} for c, _ in rows],
        "miss": [],
        "has_result": True,
        "tool_has_result": True,
        "unresolved": [],
        "error": None,
        "lane_text": lane_text,
    }
    row = {**_domain_row("product_attachment", narrowing={"product": "must_narrow_one"}),
           "label": "product attachments"}
    policy = Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)
    state = State(focus=Focus(), pending=None, profile=Profile(), turn_no=2)
    ctx = SimpleNamespace(localizer=_loc(lang)) if lang else None
    return compose([env], state, policy, ctx=ctx).text


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_cl54_compose_builds_the_gap_line_in_the_turn_language(lang):
    text = _compose_gap_text(lang, [("SRT1", "photo")], ["SRT1"], ["photo", "datasheet"])
    assert WORDING[lang]["has_no"].format(code="SRT1", types="datasheet") in text.split("\n"), text
    assert "has no" not in text, text


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_cl54_compose_two_products_each_get_their_own_localized_gap_line(lang):
    text = _compose_gap_text(lang, [("SRT0", "photo")], ["SRT0", "SRT1"], ["photo", "datasheet"])
    lines = text.split("\n")
    w = WORDING[lang]["has_no"]
    assert w.format(code="SRT0", types="datasheet") in lines, text
    joiner = label_catalog.defaults(lang)[" or "]
    assert w.format(code="SRT1", types=f"photo{joiner}datasheet") in lines, text
    assert "has no" not in text, text


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_cl54_compose_the_gap_line_below_the_footer_paragraph_keeps_its_place(lang):
    text = _compose_gap_text(lang, [("SRT1", "photo")], ["SRT1"], ["photo", "datasheet"],
                             lane_text="I have attached the file(s) below.\n\n_Data last updated: 03/10/2026 16:24:40_")
    gap = WORDING[lang]["has_no"].format(code="SRT1", types="datasheet")
    assert f"\n\n{gap}\n\n" in text, repr(text)


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_cl54_compose_two_missing_types_keep_their_names_and_use_the_localized_joiner(lang):
    """S1: SRT1 has no file of either asked type; the names print as typed, the joiner is the catalog's."""
    text = _compose_gap_text(lang, [("SRT0", "datasheet"), ("SRT0", "brochure")],
                             ["SRT0", "SRT1"], ["datasheet", "brochure"])
    joiner = label_catalog.defaults(lang)[" or "]
    types = f"datasheet{joiner}brochure"
    assert WORDING[lang]["has_no"].format(code="SRT1", types=types) in text.split("\n"), text
    assert " or " not in text and "has no" not in text, text


def test_cl54_compose_an_english_turn_keeps_the_english_gap_line():
    for lang in (None, "en"):
        text = _compose_gap_text(lang, [("SRT1", "photo")], ["SRT1"], ["photo", "datasheet"])
        assert "SRT1 has no datasheet." in text.split("\n"), text
    both = _compose_gap_text("en", [("SRT0", "datasheet"), ("SRT0", "brochure")],
                             ["SRT0", "SRT1"], ["datasheet", "brochure"])
    assert "SRT1 has no datasheet or brochure." in both.split("\n"), both


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_cl54_a_product_name_that_reads_like_the_gap_sentence_is_not_translated(lang):
    """Regression: inline matching turned `Basin that has no hole.` into `Basin that tiada hole.`."""
    english = "*Product Name:* Basin that has no hole."
    out = _loc(lang).reply(english)
    assert out.endswith(" Basin that has no hole."), out
    assert "has no hole" in out
    assert out != english  # the label itself still translates





def test_cl55_the_gap_line_is_a_catalog_key_and_direct_only():
    assert GAP_KEY in LABELS and set(LABELS[GAP_KEY]) >= {"ms", "zh"}
    assert GAP_KEY in label_catalog.DIRECT_ONLY
    assert GAP_KEY not in INLINE





# --------------------------------------------------------------------------- #
# AC-CL53: an ms low stock turn asks in Malay and the next ms reply is still the answer
# --------------------------------------------------------------------------- #


@pytest.fixture
def ms_console(session_factory, monkeypatch):
    c = _console(session_factory, monkeypatch, grants=[GRANT, SUPPLIER_KEY])
    c.session_vars = {"reply_language": "ms"}
    return c


def test_cl53_an_ms_turn_asks_in_malay_then_the_ms_reply_settles_the_field(ms_console):
    text, calls = ms_console.say(_ask(), "tunjuk laporan stok rendah")
    assert text == MS["category_question"]
    assert calls == [], "no run before the category is settled"
    # The answer is read from the slot, not from the Malay question: 'semua' settles it as all.
    _text, calls = ms_console.say(_reply(intent_hint=None, message_type="casual"), "semua")
    assert len(calls) == 1, "the field settled, the report ran"
    assert not calls[0].get("categories"), calls[0]


def test_cl53_an_ms_miss_is_said_in_malay_and_the_slot_survives(ms_console):
    ms_console.say(_ask(), "tunjuk laporan stok rendah")
    text, calls = ms_console.say(_reply(_e("kapal angkasa", "product")), "kapal angkasa")
    assert calls == []
    assert text == MS["miss_category"].format(word="kapal angkasa") + "\n\n" + MS["category_question"]
    # Still open: a real category word answers it.
    _text, calls = ms_console.say(_reply(_e("water closet", "product")), "water closet")
    (args,) = calls
    assert args.get("categories") == ["SRT-WC"], args


def test_cl53_an_ms_cancel_is_said_in_malay(ms_console):
    ms_console.say(_ask(), "tunjuk laporan stok rendah")
    text, calls = ms_console.say(_reply(intent_hint=None, message_type="casual"), "batal")
    assert calls == [] and text == MS["cancelled"]


def test_cl52_an_en_turn_through_the_engine_is_unchanged(session_factory, monkeypatch):
    c = _console(session_factory, monkeypatch, grants=[GRANT, SUPPLIER_KEY])
    text, calls = c.say(_ask(), "low stock report")
    assert text == lsa.QUESTION and calls == []
