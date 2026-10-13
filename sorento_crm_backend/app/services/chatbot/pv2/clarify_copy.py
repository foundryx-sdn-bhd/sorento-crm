"""PV2 clarify copy (CONTRACT-PV2-13oct.md section 5, last bullet): the question a
ClarifyAct / OfferHandover asks, and the reply that prints it with numbered options.

Options print their LABEL only (display names, `label_catalog` labels); an option key,
registry key, customer code or id never reaches the reply. A label that is itself an id
is replaced, never printed. No " · " separators.
"""
from __future__ import annotations

import uuid
from typing import Any

from app.services.chatbot.pv2.state import Option, PendingQuestion

_LANGS = ("en", "zh", "ms")

#: Entity kinds as the user reads them, per language.
_KIND_WORD: dict[str, dict[str, str]] = {
    "product": {"en": "product", "zh": "产品", "ms": "produk"},
    "customer": {"en": "customer", "zh": "客户", "ms": "pelanggan"},
    "brand": {"en": "brand", "zh": "品牌", "ms": "jenama"},
    "product_type": {"en": "product type", "zh": "产品类型", "ms": "jenis produk"},
    "warehouse": {"en": "warehouse", "zh": "仓库", "ms": "gudang"},
    "order": {"en": "order", "zh": "订单", "ms": "pesanan"},
    "spec": {"en": "value", "zh": "规格", "ms": "nilai"},
    "report_filter": {"en": "match", "zh": "匹配项", "ms": "padanan"},
}

_COPY: dict[str, dict[str, str]] = {
    "which": {
        "en": "Which one do you mean?",
        "zh": "您指的是哪一个？",
        "ms": "Yang mana satu yang anda maksudkan?",
    },
    "which_phrase": {
        "en": 'Which "{phrase}" do you mean?',
        "zh": "您说的“{phrase}”是指哪一个？",
        "ms": '"{phrase}" yang mana satu?',
    },
    "not_found": {
        "en": 'I couldn\'t find any {kind} named "{phrase}".',
        "zh": "找不到名为“{phrase}”的{kind}。",
        "ms": 'Saya tidak jumpa {kind} bernama "{phrase}".',
    },
    "spec_attribute": {
        "en": 'Which do you mean by "{phrase}"?',
        "zh": "您说的“{phrase}”是指哪一项？",
        "ms": '"{phrase}" itu merujuk kepada yang mana?',
    },
    "attachment_type": {
        "en": "Which kind of file do you need?",
        "zh": "您需要哪种文件？",
        "ms": "Fail jenis apa yang anda perlukan?",
    },
    "report_measure": {
        "en": "Which figure should I rank by?",
        "zh": "要按哪个数据排名？",
        "ms": "Mahu disusun mengikut angka apa?",
    },
    "report_period": {
        "en": "For which period?",
        "zh": "要查哪个时间段？",
        "ms": "Untuk tempoh bila?",
    },
    "other": {
        "en": "What would you like me to check?",
        "zh": "您想让我查什么？",
        "ms": "Apa yang anda mahu saya semak?",
    },
    "no_files": {
        "en": "I couldn't find files of that kind for this product.",
        "zh": "这个产品没有这类文件。",
        "ms": "Saya tidak jumpa fail jenis itu untuk produk ini.",
    },
    "filter_unavailable": {
        "en": "Filtering this report by {labels} is not available yet.",
        "zh": "报表暂时还不能按{labels}筛选。",
        "ms": "Penapisan laporan ini mengikut {labels} belum tersedia.",
    },
    "reask": {
        "en": "Sorry, I didn't catch your choice.",
        "zh": "抱歉，我没看明白您的选择。",
        "ms": "Maaf, saya kurang pasti pilihan anda.",
    },
    "nothing_held": {
        "en": "I'm not sure which one you mean, as nothing is selected yet.",
        "zh": "目前还没有选定任何项目，我不确定您指的是哪一个。",
        "ms": "Belum ada apa-apa yang dipilih, jadi saya kurang pasti yang mana satu.",
    },
    "offer_handover": {
        "en": "Would you like me to pass this to a colleague?",
        "zh": "需要我帮您转给同事处理吗？",
        "ms": "Mahu saya serahkan kepada rakan sekerja?",
    },
    "reply_number": {
        "en": "Reply with the number.",
        "zh": "请回复编号。",
        "ms": "Balas dengan nombor.",
    },
    "yes": {"en": "Yes", "zh": "是", "ms": "Ya"},
    "no": {"en": "No", "zh": "否", "ms": "Tidak"},
    "unnamed": {"en": "(unnamed)", "zh": "（未命名）", "ms": "(tiada nama)"},
}


def lang(language: str | None) -> str:
    """The copy language: en / zh / ms; "mixed" or anything else reads as en."""
    return language if language in _LANGS else "en"


def text(key: str, language: str | None, **fields: Any) -> str:
    template = _COPY[key][lang(language)]
    return template.format(**fields) if fields else template


def kind_word(kind: str | None, language: str | None) -> str:
    words = _KIND_WORD.get(kind or "")
    if words is None:
        return _KIND_WORD["product"][lang(language)] if kind is None else str(kind).replace("_", " ")
    return words[lang(language)]


def question_for(
    about: str | None,
    phrase: str | None,
    language: str | None,
    *,
    kind: str | None = None,
    not_found: bool = False,
    nothing_held: bool = False,
) -> str:
    """The question text recorded on a PendingQuestion (`asked`)."""
    if not_found and kind == "attachment_type":
        return text("no_files", language)
    if not_found and phrase:
        return text("not_found", language, kind=kind_word(kind, language), phrase=phrase)
    if nothing_held:
        return text("nothing_held", language)
    if about in ("entity", "which_held"):
        return text("which_phrase", language, phrase=phrase) if phrase else text("which", language)
    if about == "spec_attribute":
        return text("spec_attribute", language, phrase=phrase) if phrase else text("which", language)
    if about in ("attachment_type", "report_measure", "report_period"):
        return text(about, language)
    return text("other", language)


def handover_options(language: str | None) -> list[Option]:
    return [
        Option(key="yes", label=text("yes", language), payload={"kind": "handover", "value": True}),
        Option(key="no", label=text("no", language), payload={"kind": "handover", "value": False}),
    ]


def safe_label(label: str, language: str | None) -> str:
    """The label as printed: an id-shaped label (a bare UUID) is never printed."""
    candidate = (label or "").strip()
    try:
        uuid.UUID(candidate)
    except ValueError:
        return candidate or text("unnamed", language)
    return text("unnamed", language)


def render(pending: PendingQuestion, language: str | None, *, reask: bool = False) -> str:
    """The reply for a recorded question: (re-ask prefix,) the question, numbered option
    labels, and the reply hint when there are options."""
    lines: list[str] = []
    if reask:
        lines.append(text("reask", language))
    lines.append(pending.asked)
    if pending.options:
        lines.append("")
        lines.extend(f"{i}. {safe_label(o.label, language)}" for i, o in enumerate(pending.options, start=1))
        lines.append("")
        lines.append(text("reply_number", language))
    return "\n".join(lines)
