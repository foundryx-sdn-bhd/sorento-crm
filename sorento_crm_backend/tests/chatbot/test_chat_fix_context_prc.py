"""PR-C (chat fix, follow-up context) red tests: PLAN-chat-fix-context-16oct.md,
chat-fix-context-acceptance-criteria.md (AC-C1 to AC-C4, AC-C7).

Seam: the live path of engine.py (open question from `turn.question.open_question`, its
line from `parser.open_question_line`, the parser's user block from `turn.context.assemble`).
`head/parser.py::build_user_block` has no live caller and is not tested through.
Pure Python, no database.
"""
from __future__ import annotations

import json

from app.services.chatbot.head import parser
from app.services.chatbot.turn import context, pending as pending_mod, question

BRANDS = [
    {"brand_name": n, "brand_code": c}
    for n, c in [
        ("Sorento", "SRT"), ("Mocha", "MCH"), ("Browny", "BRW"), ("Kitchen Plus", "KCH"),
        ("Alpha Home", "ALH"), ("Bravo Living", "BRL"), ("Charlie Wares", "CHW"),
        ("Delta Cookware", "DLC"), ("Echo Select", "ECS"), ("Foxtrot Dining", "FXD"),
        ("Golf Homeware", "GLH"), ("Hotel Supplies", "HTS"),
    ]
]
PRODUCT_TYPES = [f"Product type number {i} category" for i in range(56)]
PRODUCT_DETAILS = [
    (f"detail_key_{i}", [f"choice a{i}", f"choice b{i}", f"choice c{i}", f"choice d{i}"])
    for i in range(14)
]
PROMO_KINDS = [
    {"type_code": c, "type_name": n}
    for c, n in [
        ("DISC", "Discount"), ("BUNDLE", "Bundle deal"), ("FOC", "Free of charge"),
        ("CLEAR", "Clearance"), ("SEASON", "Seasonal offer"),
    ]
]
SUBJECT = "domain inventory; product SRTWC286-SH."
SUBJECT_LINE = "Current subject: product SRTWC286-SH"


def _options() -> list[dict]:
    return [
        {"position": 1, "code": "SRTWC286-SH", "label": "SRTWC286-SH Sorento chair"},
        {"position": 2, "code": "SRTWC286-NV", "label": "SRTWC286-NV Sorento chair navy"},
        {"position": 3, "code": "SRTWC286-GR", "label": "SRTWC286-GR Sorento chair grey"},
    ]


def _pending(answered: list[int] | None = None) -> pending_mod.Pending:
    p = pending_mod.ask("product_pick", _options(), asked_at_turn=3)
    return pending_mod.with_answered_positions(p, answered) if answered else p


def _layers(*, vocabulary: bool = True, subject: str | None = "product SRTWC286-SH",
            pending: pending_mod.Pending | None = None, message: str = "and the navy one",
            previous: str = "Which one? 1. SRTWC286-SH 2. SRTWC286-NV 3. SRTWC286-GR",
            **over) -> context.ContextLayers:
    oq = question.open_question(pending, None, None)
    kwargs = dict(
        level="conversation",
        profile_facts=None, summaries=None, earlier_messages=None,
        previous_response=previous,
        current_subject=subject,
        # mirrors engine.py: no waiting line when the open question owes nothing
        pending_kind=pending.kind if pending and oq and oq["owed"] else None,
        pending_options=[o["label"] for o in pending.options] if pending else None,
        settings_profile_line=None,
        current_message=message, reply_to=None, media_line=None,
        task_lines=None,
        open_question_line=parser.open_question_line(oq),
        brands=BRANDS if vocabulary else None,
        promotion_kinds=PROMO_KINDS if vocabulary else None,
        product_types=PRODUCT_TYPES if vocabulary else None,
        product_details=PRODUCT_DETAILS if vocabulary else None,
    )
    kwargs.update(over)
    return context.ContextLayers(**kwargs)


def _block(**kw) -> str:
    return context.assemble(_layers(**kw))[0]


# ---- S1: AC-C1 ------------------------------------------------------------------ #

def test_vocabulary_is_realistically_sized() -> None:
    """Fixture sanity: the vocabulary alone exceeds the L2 cap, as on the live path."""
    lines = [
        context.known_brands_line(BRANDS), context.product_types_line(PRODUCT_TYPES),
        context.product_details_line(PRODUCT_DETAILS), context.promotion_kinds_line(PROMO_KINDS),
    ]
    assert context.est_tokens("\n".join(lines)) > context.CAPS["L2"]


def test_c1_short_subject_kept_whole_beside_full_vocabulary_and_pick() -> None:
    block = _block(pending=_pending())
    assert SUBJECT_LINE in block.splitlines()
    assert "Known brands: Sorento (SRT)" in block  # vocabulary still whole


def test_c1_subject_kept_with_vocabulary_on_the_default_level_too() -> None:
    block = _block(pending=None, level="full")
    assert SUBJECT_LINE in block.splitlines()


def test_c1_guard_subject_shortened_when_its_own_lines_exceed_cap() -> None:
    long_subject = "product " + ", ".join(f"SRTWC{n:03d}-SH Sorento chair model" for n in range(60))
    assert context.est_tokens(long_subject) > context.CAPS["L2"]
    block = _block(subject=long_subject, vocabulary=False, pending=_pending())
    subject_lines = [ln for ln in block.splitlines() if ln.startswith("Current subject: ")]
    assert len(subject_lines) == 1 and len(subject_lines[0]) < len("Current subject: " + long_subject)
    assert subject_lines[0].startswith("Current subject: product SRTWC000-SH")
    assert any(ln.startswith("Open question: ") for ln in block.splitlines())
    assert "Pending: the assistant is waiting for a product_pick reply." in block


# ---- S2: AC-C2 / AC-C3 ------------------------------------------------------------ #

def test_c2_answered_list_owes_nothing_and_names_the_picked_option() -> None:
    oq = question.open_question(_pending(answered=[2]), None, None)
    assert oq is not None and oq["owed"] == []
    assert "SRTWC286-NV" in json.dumps(oq["answered"], ensure_ascii=False)
    assert "SRTWC286-SH" not in json.dumps(oq["answered"], ensure_ascii=False)
    # the list and its options stay: "2 also" still picks number 2
    assert [o["position"] for o in oq["options"]] == [1, 2, 3]


def test_c2_answered_list_renders_no_waiting_line_but_keeps_options() -> None:
    block = _block(pending=_pending(answered=[2]), vocabulary=False)
    assert "waiting for" not in block
    line = next(ln for ln in block.splitlines() if ln.startswith("Open question: "))
    parsed = json.loads(line[len("Open question: "):])
    assert parsed["owed"] == [] and len(parsed["options"]) == 3
    assert "Open question options: " in block


def test_c3_unanswered_list_is_unchanged() -> None:
    oq = question.open_question(_pending(), None, None)
    assert oq["owed"] == ["pick"] and "answered" not in oq
    block = _block(pending=_pending(), vocabulary=False)
    assert "Pending: the assistant is waiting for a product_pick reply." in block


def test_c3_first_pick_of_two_still_open_is_answered_not_owed() -> None:
    oq = question.open_question(_pending(answered=[1, 3]), None, None)
    assert oq["owed"] == []
    answered = json.dumps(oq["answered"], ensure_ascii=False)
    assert "SRTWC286-SH" in answered and "SRTWC286-GR" in answered and "SRTWC286-NV" not in answered


# ---- AC-C4: a first message is byte-identical to main ------------------------------ #

GOLDEN_FIRST_MESSAGE = (
    "Known brands: Sorento (SRT), Mocha (MCH)\n"
    "Product types: Chair, Table\n"
    "Product details: colour (red, blue)\n"
    "Promotion kinds: DISC (Discount); FOC (Free of charge)\n"
    "Current user message: how many SRTWC286-SH do you have in stock?"
)


def test_c4_first_message_block_is_byte_identical() -> None:
    layers = _layers(
        subject=None, pending=None, previous="", message="how many SRTWC286-SH do you have in stock?",
        brands=BRANDS[:2], promotion_kinds=PROMO_KINDS[:1] + PROMO_KINDS[2:3],
        product_types=["Chair", "Table"], product_details=[("colour", ["red", "blue"])],
    )
    assert context.assemble(layers)[0] == GOLDEN_FIRST_MESSAGE
