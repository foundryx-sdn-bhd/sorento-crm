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
    "more": {
        "en": "...and {n} more.",
        "zh": "...还有 {n} 个。",
        "ms": "...dan {n} lagi.",
    },
    "reply_number": {
        "en": "Reply with the number.",
        "zh": "请回复编号。",
        "ms": "Balas dengan nombor.",
    },
    "yes": {"en": "Yes", "zh": "是", "ms": "Ya"},
    "no": {"en": "No", "zh": "否", "ms": "Tidak"},
    "unnamed": {"en": "(unnamed)", "zh": "（未命名）", "ms": "(tiada nama)"},
    # RG-6 (#1517): an ask with no product in a domain that needs one.
    "which_product": {
        "en": "Which product do you mean?",
        "zh": "您指的是哪个产品？",
        "ms": "Produk yang mana satu?",
    },
    # RG-1 (#1570): v1's carried-subject question and its two picks (label_catalog wording).
    "carry_scope": {
        "en": "{label} for {named}: only {subject} (from earlier) or all {plural}?",
        "zh": "{named} 的{label}：只看 {subject}（之前提到的）还是全部{plural}？",
        "ms": "{label} untuk {named}: hanya {subject} (yang disebut tadi) atau semua {plural}?",
    },
    "carry_only": {"en": "Only {subject}", "zh": "只看 {subject}", "ms": "Hanya {subject}"},
    "carry_all": {"en": "All {plural}", "zh": "全部{plural}", "ms": "Semua {plural}"},
    # RG-2 (#1582): a size none of the held products holds; axis names stay English until R3.
    "size_miss": {
        "en": "No {size} for {subject}. Sizes on file: {sizes}.",
        "zh": "{subject} 没有 {size}。现有尺寸：{sizes}。",
        "ms": "Tiada {size} untuk {subject}. Saiz yang ada: {sizes}.",
    },
}

#: RG-1 (#1570): the carried-subject question's ask word per domain and its kinds' plurals (v1's catalog).
_CARRY_LABEL: dict[str, dict[str, str]] = {
    "order": {"en": "Delivery", "zh": "送货", "ms": "Penghantaran"},
    "sales": {"en": "Sales", "zh": "销售", "ms": "Jualan"},
}
_KIND_PLURAL: dict[str, dict[str, str]] = {
    "product": {"en": "products", "zh": "产品", "ms": "produk"},
    "customer": {"en": "customers", "zh": "客户", "ms": "pelanggan"},
}

#: RG-1b (#1570): v1's loop-breaker line (`clarify_policy.missing_line`) per thing still missing,
#: (with the subject, without one).
_STILL_NEED: dict[str, dict[str, tuple[str, str]]] = {
    "period": {
        "en": ("I still need the period for {subject}.", "I still need the period."),
        "zh": ("我仍需要 {subject} 的时间段。", "我仍需要时间段。"),
        "ms": ("Saya masih perlukan tempoh untuk {subject}.", "Saya masih perlukan tempoh."),
    },
    "attachment_type": {
        "en": ("I still need the attachment type for {subject}.", "I still need the attachment type."),
        "zh": ("我仍需要 {subject} 的附件类型。", "我仍需要附件类型。"),
        "ms": ("Saya masih perlukan jenis lampiran untuk {subject}.", "Saya masih perlukan jenis lampiran."),
    },
    "product": {
        "en": ("I still need which products to include for {subject}.", "I still need which products to include."),
        "zh": ("我仍需要知道 {subject} 要包括哪些产品。", "我仍需要知道要包括哪些产品。"),
        "ms": ("Saya masih perlu tahu produk mana yang disertakan untuk {subject}.",
               "Saya masih perlu tahu produk mana yang disertakan."),
    },
    "customer": {
        "en": ("I still need which customers to include for {subject}.", "I still need which customers to include."),
        "zh": ("我仍需要知道 {subject} 要包括哪些客户。", "我仍需要知道要包括哪些客户。"),
        "ms": ("Saya masih perlu tahu pelanggan mana yang disertakan untuk {subject}.",
               "Saya masih perlu tahu pelanggan mana yang disertakan."),
    },
    # A question about one item of a kind (an entity, which-held or did-you-mean pick, a spec): that kind's word.
    "which": {
        "en": ("I still need which {kind} you mean for {subject}.", "I still need which {kind} you mean."),
        "zh": ("我仍需要知道 {subject} 您指的是哪个{kind}。", "我仍需要知道您指的是哪个{kind}。"),
        "ms": ("Saya masih perlu tahu {kind} yang mana satu untuk {subject}.",
               "Saya masih perlu tahu {kind} yang mana satu."),
    },
    "measure": {
        "en": ("I still need the figure to rank by for {subject}.", "I still need the figure to rank by."),
        "zh": ("我仍需要 {subject} 的排名数据。", "我仍需要排名数据。"),
        "ms": ("Saya masih perlukan angka untuk disusun bagi {subject}.", "Saya masih perlukan angka untuk disusun."),
    },
    "breakdown": {
        "en": ("I still need which row to break down for {subject}.", "I still need which row to break down."),
        "zh": ("我仍需要知道 {subject} 要细分哪一行。", "我仍需要知道要细分哪一行。"),
        "ms": ("Saya masih perlu tahu baris mana yang hendak diperincikan untuk {subject}.",
               "Saya masih perlu tahu baris mana yang hendak diperincikan."),
    },
    "what": {
        "en": ("I still need what to check for {subject}.", "I still need what to check."),
        "zh": ("我仍需要知道 {subject} 要查什么。", "我仍需要知道要查什么。"),
        "ms": ("Saya masih perlu tahu apa yang hendak disemak untuk {subject}.",
               "Saya masih perlu tahu apa yang hendak disemak."),
    },
    "more": {
        "en": ("I still need more detail for {subject}.", "I still need more detail."),
        "zh": ("我仍需要 {subject} 的更多详情。", "我仍需要更多详情。"),
        "ms": ("Saya masih perlukan butiran lanjut untuk {subject}.", "Saya masih perlukan butiran lanjut."),
    },
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


def carry_scope(domain: str, kind: str, named: str, subject: str, language: str | None) -> tuple[str, list[Option]]:
    """RG-1 (#1570): "Delivery for <named>: only <subject> (from earlier) or all <kind>s?" and its two picks:
    keep the carried `kind`, or clear it."""
    code = lang(language)
    plural = _KIND_PLURAL[kind][code]
    asked = text("carry_scope", language, label=_CARRY_LABEL[domain][code], named=named, subject=subject, plural=plural)
    options = [
        Option(key="1", label=text("carry_only", language, subject=subject), payload={"kind": "carry_scope"}),
        Option(
            key="2", label=text("carry_all", language, plural=plural), payload={"kind": "carry_scope", "clear": kind}
        ),
    ]
    return asked, options


def still_need(missing: str, subject: str | None, language: str | None, kind: str | None = None) -> str:
    """RG-1b: v1's "I still need X for Y." (`missing` a `_STILL_NEED` key, else "more"; `kind` names the item
    a "which" line is about)."""
    with_subject, bare = _STILL_NEED.get(missing, _STILL_NEED["more"])[lang(language)]
    word = kind_word(kind, language)
    return with_subject.format(subject=subject, kind=word) if subject else bare.format(kind=word)


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
    if pending.options and pending.kind not in ("offer_doc_handover", "clarify_did_you_mean"):
        lines.append("")
        lines.extend(f"{i}. {safe_label(o.label, language)}" for i, o in enumerate(pending.options, start=1))
        if pending.more:
            lines.append(text("more", language, n=pending.more))
        lines.append("")
        lines.append(text("reply_number", language))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# v1's own localized sentences (label_catalog), for PV2's not-found / offer / did-you-mean lines
# --------------------------------------------------------------------------- #


def v1_line(english: str, language: str | None, **fields: Any) -> str:
    """`english` (a label_catalog key) filled with `fields`, in the turn language the way v1's
    Localizer prints it; English, or any sentence v1 does not translate, comes back as written."""
    from app.services.chatbot import label_catalog

    code = lang(language)
    return label_catalog.Localizer(code, label_catalog.defaults(code)).fill(english, **fields)


def not_found_line(names: str, language: str | None) -> str:
    """`Couldn't find: {names}.` (v1's not-found sentence)."""
    return v1_line("Couldn't find: {names}.", language, names=names)


def escalate_offer(team: str, language: str | None) -> str:
    """`Would you like me to escalate to {team} team?` (v1's escalation offer)."""
    return v1_line("Would you like me to escalate to {team} team?", language, team=team)


def did_you_mean_head(shown: str, language: str | None) -> str:
    """`Couldn't find {shown}. Did you mean:` (v1's did-you-mean heading)."""
    return v1_line("Couldn't find {shown}. Did you mean:", language, shown=shown)
