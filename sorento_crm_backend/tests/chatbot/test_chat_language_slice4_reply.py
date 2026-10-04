"""CHAT-LANGUAGE slice 4, AC-CL40: `Localizer.reply`, the final reply pass, and the
catalog completeness of the slice 4 table (`chat-language-acceptance-criteria.md`, Slice 4).

Pure: no database. `reply(text)` = `lines(text)`, then each INLINE catalog sentence replaced
only where it starts a line or follows ". ", "? ", "! "; tokens verbatim; idempotent.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.chatbot import label_catalog
from app.services.chatbot.label_catalog import IDENTITY, LABELS, Localizer

APP = Path(__file__).resolve().parents[2] / "app" / "services"

#: English -> (ms, zh): the slice 4 table, verbatim from the UAC.
TABLE4: dict[str, tuple[str, str]] = {
    "stock": ("stok", "库存"),
    "orders": ("pesanan", "订单"),
    "incoming stock": ("stok masuk", "到货库存"),
    "promotions": ("promosi", "促销"),
    "forms": ("borang", "表格"),
    "product information": ("maklumat produk", "产品信息"),
    "product attachments": ("lampiran produk", "产品附件"),
    "resource attachments": ("lampiran sumber", "资料附件"),
    "goods receive": ("penerimaan barang", "收货"),
    "last in": ("kemasukan terakhir", "最近入库"),
    "outstanding purchase orders": ("pesanan belian tertunggak", "未完成采购订单"),
    "last purchase cost": ("kos belian terakhir", "最近采购成本"),
    "this request": ("permintaan ini", "此请求"),
    "*{label}* for {codes}:": ("*{label}* untuk {codes}:", "{codes} 的*{label}*："),
    "I could not fetch {label} just now, please try again.": (
        "Saya tidak dapat mendapatkan {label} sekarang, sila cuba lagi.",
        "暂时无法获取{label}，请稍后再试。",
    ),
    "*{label}*: this is not enabled for your account.": (
        "*{label}*: ini tidak diaktifkan untuk akaun anda.",
        "*{label}*：您的账户未开通此功能。",
    ),
    "Nothing on {names} either.": ("Tiada juga untuk {names}.", "{names} 也没有。"),
    "Not checked: {names}.": ("Tidak disemak: {names}.", "未检查：{names}。"),
    "I could not find {names}.": ("Saya tidak dapat menemui {names}.", "找不到 {names}。"),
    "I have attached the file(s) below.": ("Saya telah melampirkan fail di bawah.", "我已在下方附上文件。"),
    "Would you like me to escalate to {team} team?": (
        "Adakah anda mahu saya rujuk kepada pasukan {team}?",
        "需要我转交给 {team} 团队吗？",
    ),
    "Would you like me to escalate?": (
        "Adakah anda mahu saya rujuk kepada pasukan kami?",
        "需要我转交给相关团队吗？",
    ),
    "Would you like me to escalate to *{company}* {team} team?": (
        "Adakah anda mahu saya rujuk kepada pasukan {team} *{company}*?",
        "需要我转交给 *{company}* 的 {team} 团队吗？",
    ),
    "I am sorry the provided answer does not meet your requirements. Would you like me to escalate to {team} team?": (
        "Maaf, jawapan yang diberikan tidak memenuhi keperluan anda. Adakah anda mahu saya rujuk kepada pasukan {team}?",
        "抱歉，所提供的答案未能满足您的需求。需要我转交给 {team} 团队吗？",
    ),
    "I am sorry the provided answer does not meet your requirements. Would you like me to escalate this to our team?": (
        "Maaf, jawapan yang diberikan tidak memenuhi keperluan anda. Adakah anda mahu saya rujuk perkara ini kepada pasukan kami?",
        "抱歉，所提供的答案未能满足您的需求。需要我转交给我们的团队吗？",
    ),
    "No it's okay": ("Tidak mengapa", "不用了"),
    "Yes, escalate": ("Ya, rujuk", "是，转交"),
    "No, it's okay": ("Tidak, tidak mengapa", "不，不用了"),
    "Which team should take this?": ("Pasukan mana yang patut uruskan ini?", "应由哪个团队处理？"),
    "Which company do you mean?": ("Syarikat mana yang anda maksudkan?", "您指的是哪家公司？"),
    "Who should take this?": ("Siapa yang patut uruskan ini?", "应由谁处理？"),
    "Outstanding for which document?": ("Tertunggak untuk dokumen mana?", "查看哪种单据的未完成数量？"),
    "Which list would you like?": ("Senarai mana yang anda mahu?", "您要哪个列表？"),
    "Which one do you mean?": ("Yang mana satu anda maksudkan?", "您指的是哪一个？"),
    "Which kind of file do you need?": ("Jenis fail apa yang anda perlukan?", "您需要哪种文件？"),
    "Which item do you mean? Reply with a rank number from 1 to {count}.": (
        "Item mana yang anda maksudkan? Balas dengan nombor kedudukan dari 1 hingga {count}.",
        "您指的是哪个项目？请回复 1 到 {count} 之间的排名数字。",
    ),
    "Please reply with a number from {min} to {max}.": (
        "Sila balas dengan nombor dari {min} hingga {max}.",
        "请回复 {min} 到 {max} 之间的数字。",
    ),
    "How many units for each?": ("Berapa unit untuk setiap satu?", "每个需要多少件？"),
    "How many units of {code}?": ("Berapa unit {code}?", "{code} 需要多少件？"),
    "Which one do you need {qty} of?": (
        "Yang mana satu anda perlukan sebanyak {qty}?",
        "您需要 {qty} 件的是哪一个？",
    ),
    "Which one?": ("Yang mana satu?", "哪一个？"),
    "{typed} x {qty}: which one?": ("{typed} x {qty}: yang mana satu?", "{typed} x {qty}：哪一个？"),
    "{typed} matches {total} products. Which one?": (
        "{typed} sepadan dengan {total} produk. Yang mana satu?",
        "{typed} 匹配到 {total} 个产品。哪一个？",
    ),
    "Couldn't find {shown}. Did you mean {label}?": (
        "Tidak dapat menemui {shown}. Adakah anda maksudkan {label}?",
        "找不到 {shown}。您是指 {label} 吗？",
    ),
    "Couldn't find {shown}. Did you mean:": (
        "Tidak dapat menemui {shown}. Adakah anda maksudkan:",
        "找不到 {shown}。您是指：",
    ),
    "Please refer to your salesman.": ("Sila rujuk jurujual anda.", "请联系您的销售员。"),
    "Sorry, you are not allowed to access {team}": (
        "Maaf, anda tidak dibenarkan mengakses {team}",
        "抱歉，您无权访问 {team}",
    ),
    "Please specify your demand quantity": ("Sila nyatakan kuantiti yang anda perlukan", "请说明您需要的数量"),
    "Okay, noted.": ("Baik, dicatat.", "好的，已记录。"),
    "Escalation declined.": ("Rujukan dibatalkan.", "已取消转交。"),
    "Sorry, I ran into a problem understanding that. Please try again in a moment.": (
        "Maaf, saya menghadapi masalah memahami mesej itu. Sila cuba lagi sebentar lagi.",
        "抱歉，我暂时无法理解您的信息，请稍后再试。",
    ),
    "Sorry, I didn't get that. What would you like to change in the ranking?": (
        "Maaf, saya tidak faham. Apa yang anda mahu ubah dalam senarai kedudukan?",
        "抱歉，我没听明白。您想如何调整排名？",
    ),
    "Low stock report is not enabled for your account.": (
        "Laporan stok rendah tidak diaktifkan untuk akaun anda.",
        "您的账户未开通低库存报告。",
    ),
    "Could not run the low stock report right now.": (
        "Laporan stok rendah tidak dapat dijalankan sekarang.",
        "暂时无法生成低库存报告。",
    ),
    "Both": ("Kedua-duanya", "两者"),
    "Which customer is this outstanding report for?": (
        "Laporan tertunggak ini untuk pelanggan mana?",
        "这份未完成报告是哪个客户的？",
    ),
    "Which category do you mean? Reply with one code: {codes}": (
        "Kategori mana yang anda maksudkan? Balas dengan satu kod: {codes}",
        "您指的是哪个类别？请回复一个代码：{codes}",
    ),
    "I could not match any product code in that photo.": (
        "Saya tidak dapat memadankan sebarang kod produk dalam gambar itu.",
        "我无法在那张照片中匹配到任何产品代码。",
    ),
    "What would you like me to do with it?": ("Apa yang anda mahu saya lakukan dengannya?", "您希望我怎么处理？"),
    "Ask again with the correct code.": ("Sila tanya semula dengan kod yang betul.", "请用正确的代码再问一次。"),
    "I read {codes} from that photo.": ("Saya membaca {codes} daripada gambar itu.", "我从那张照片中读到 {codes}。"),
}


def _loc(lang: str) -> Localizer:
    return Localizer(lang, {en: entry[lang] for en, entry in LABELS.items() if lang in entry})


OFFER_MS = "Adakah anda mahu saya rujuk kepada pasukan {team}?"
OFFER_ZH = "需要我转交给 {team} 团队吗？"


# --------------------------------------------------------------------------- #
# AC-CL40: reply()
# --------------------------------------------------------------------------- #

SAMPLES = [
    "Would you like me to escalate?",
    "No stock found for SRTWC286.\n\nWould you like me to escalate to customer service team?",
    "Warehouse: BRW\nPlease refer to your salesman.",
    "plain text with nothing in the catalog",
    "",
]


@pytest.mark.parametrize("text", SAMPLES)
def test_cl40_identity_for_en_and_identity_localizer(text):
    assert IDENTITY.reply(text) == text
    assert Localizer("en", {}).reply(text) == text


@pytest.mark.parametrize("text", SAMPLES)
def test_cl40_an_empty_table_is_identity_whatever_the_language(text):
    assert Localizer("ms", {}).reply(text) == text


def test_cl40_lines_rules_still_apply_then_inline_sentences():
    out = _loc("ms").reply("Warehouse: BRW\nHow many units do you need?\nWould you like me to escalate?")
    assert out == "Gudang: BRW\nBerapa unit yang anda perlukan?\n" + "Adakah anda mahu saya rujuk kepada pasukan kami?"


def test_cl40_a_sentence_at_the_start_of_a_line_is_replaced_ms_and_zh():
    text = "Would you like me to escalate to warehouse team?"
    assert _loc("ms").reply(text) == OFFER_MS.format(team="warehouse")
    assert _loc("zh").reply(text) == OFFER_ZH.format(team="warehouse")


@pytest.mark.parametrize("boundary", [". ", "? ", "! "])
def test_cl40_a_sentence_after_a_sentence_end_is_replaced(boundary):
    head = f"Done{boundary[0]}"
    out = _loc("ms").reply(f"{head} Would you like me to escalate?")
    assert out == f"{head} Adakah anda mahu saya rujuk kepada pasukan kami?"


def test_cl40_a_sentence_after_a_newline_is_replaced():
    out = _loc("ms").reply("ZZT product line\nWould you like me to escalate?")
    assert out == "ZZT product line\nAdakah anda mahu saya rujuk kepada pasukan kami?"


def test_cl40_the_miss_sentence_with_an_uncatalogued_lead_still_translates_the_offer():
    # "Couldn't find: X." is catalogued since the crew-tester round; this lead never is.
    out = _loc("ms").reply('ZZNOPE999 matched nothing (product). Would you like me to escalate to warehouse team?')
    assert out == "ZZNOPE999 matched nothing (product). " + OFFER_MS.format(team="warehouse")


@pytest.mark.parametrize(
    "text",
    [
        "XYZWould you like me to escalate?",  # glued to a word
        "ZZT Would you like me to escalate? tray",  # product name, only a space before
        "ZZT-Would you like me to escalate?",  # after a hyphen
        "Would you like me to escalate",  # shape does not run through its final punctuation
        "Please refer to your salesman",  # no final full stop
        "Do not Please refer to your salesman.",  # mid-sentence, no boundary
    ],
)
def test_cl40_a_catalog_sentence_not_at_a_boundary_is_not_replaced(text):
    assert _loc("ms").reply(text) == text
    assert _loc("zh").reply(text) == text


def test_cl40_template_tokens_come_back_verbatim():
    names = "ZZT-A$1, \\g<1> & 42"
    out = _loc("ms").reply(f"Done. I could not find {names}.")
    assert out == f"Done. Saya tidak dapat menemui {names}."
    out = _loc("zh").reply("Which one do you need 1,000 of?")
    assert out == "您需要 1,000 件的是哪一个？"
    assert _loc("ms").reply("How many units of SRTWC286-SH?") == "Berapa unit SRTWC286-SH?"
    assert _loc("zh").reply("How many units of SRTWC286-SH?") == "SRTWC286-SH 需要多少件？"


def test_cl40_team_names_and_codes_are_never_translated():
    out = _loc("ms").reply("Would you like me to escalate to customer service team?")
    assert out == "Adakah anda mahu saya rujuk kepada pasukan customer service?"
    assert "Customer" not in out and "stok" not in out


def test_cl40_the_company_offer_uses_its_own_template():
    out = _loc("ms").reply("Would you like me to escalate to *Sorento* customer service team?")
    assert out == "Adakah anda mahu saya rujuk kepada pasukan customer service *Sorento*?"
    out = _loc("zh").reply("Would you like me to escalate to *Sorento* customer service team?")
    assert out == "需要我转交给 *Sorento* 的 customer service 团队吗？"


def test_cl40_the_apology_offer_is_one_catalog_sentence_not_two_halves():
    out = _loc("ms").reply(
        "I am sorry the provided answer does not meet your requirements. "
        "Would you like me to escalate to warehouse team?"
    )
    assert out == (
        "Maaf, jawapan yang diberikan tidak memenuhi keperluan anda. "
        "Adakah anda mahu saya rujuk kepada pasukan warehouse?"
    )
    out = _loc("zh").reply(
        "I am sorry the provided answer does not meet your requirements. "
        "Would you like me to escalate this to our team?"
    )
    assert out == "抱歉，所提供的答案未能满足您的需求。需要我转交给我们的团队吗？"


def test_cl40_a_whole_dealer_miss_reply_renders_the_catalog_strings():
    text = "No stock found for SRTWC286.\n\nWould you like me to escalate to customer service team?"
    assert _loc("ms").reply(text) == (
        "Tiada stok ditemui untuk SRTWC286.\n\n"
        "Adakah anda mahu saya rujuk kepada pasukan customer service?"
    )
    assert _loc("zh").reply(text) == "未找到 SRTWC286 的库存。\n\n需要我转交给 customer service 团队吗？"


def test_cl40_two_inline_sentences_in_one_line_are_both_replaced():
    out = _loc("ms").reply("Which one do you mean? Please refer to your salesman.")
    assert out == "Yang mana satu anda maksudkan? Sila rujuk jurujual anda."


def test_cl40_the_joined_new_line_sentence_pair_of_a_pick():
    out = _loc("ms").reply("Couldn't find STWC2867. Did you mean:\n1. SRTWC286-SH\n2. SRTWC286-SH-P")
    assert out == "Tidak dapat menemui STWC2867. Adakah anda maksudkan:\n1. SRTWC286-SH\n2. SRTWC286-SH-P"


@pytest.mark.parametrize("lang", ["ms", "zh"])
@pytest.mark.parametrize(
    "text",
    [
        "No stock found for SRTWC286.\n\nWould you like me to escalate to customer service team?",
        "Warehouse: BRW\nCouldn't find STWC2867. Did you mean SRTWC286-SH? Please refer to your salesman.",
        "Which one? Okay, noted. How many units for each?",
        "Not checked: A, B.\nI could not find C.",
    ],
)
def test_cl40_idempotent(lang, text):
    loc = _loc(lang)
    once = loc.reply(text)
    assert once != text
    assert loc.reply(once) == once


def test_cl40_an_uncatalogued_reply_is_unchanged():
    text = "Here is a product named Would you like me to escalate? Pro\nSRTWC286-SH x 2"
    assert _loc("ms").reply(text) == text


# --------------------------------------------------------------------------- #
# AC-CL40: catalog completeness
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("english", list(TABLE4))
def test_cl40_every_slice_4_key_is_in_labels_with_ms_and_zh(english):
    assert english in LABELS, english
    assert LABELS[english]["ms"] == TABLE4[english][0]
    assert LABELS[english]["zh"] == TABLE4[english][1]


def _source() -> str:
    parts = []
    files = [p for p in (APP / "chatbot").rglob("*.py") if p.name != "label_catalog.py"]
    files.append(APP / "chatbot_reply_copy.py")
    for path in files:
        raw = path.read_text(encoding="utf-8")
        # Implicit concatenation of adjacent literals: join them so a wrapped sentence reads whole.
        raw = re.sub(r"(?<!\\)\"\s*\n\s*[fFrR]?\"", "", raw)
        raw = re.sub(r"(?<!\\)'\s*\n\s*[fFrR]?'", "", raw)
        parts.append(raw)
    return "\n".join(parts)


def _fixed_fragments(english: str) -> list[str]:
    """The text between placeholders. Source spells a `{token}` as `{expr}` or `{{token}}`."""
    return [f for f in re.split(r"\{[a-z_]+\}", english) if f]


@pytest.mark.parametrize("english", list(TABLE4))
def test_cl40_each_english_literal_still_exists_in_source(english):
    source = _source()
    for fragment in _fixed_fragments(english):
        variants = {fragment, fragment.replace("'", "\\'"), fragment.replace("'", "’")}
        assert any(v in source for v in variants), f"{fragment!r} (of {english!r}) is not in the chatbot source"
