"""Publish the IDEA-CAPTURE fields to the live prompt registry (13 Oct).

The `ideate_extractor` fallback now names four fields (problem, desired_outcome, use_cases,
current_workaround) instead of problem / proposed_solution / impact / department, and the
`ideation_capture_complete` reply (en, ms, zh) lists each missing field on its own line.
`get_prompt` serves a fallback only when the `production` label has no row, and
`seed_prompt_registry` never updates a live row, so a seeded install keeps the old texts. This
migration publishes each new text as the next immutable version and moves the label, but ONLY
where production still equals a stock text of that key (compared strip-normalised). A template
the owner edited is left alone with a warning, and a re-run is a no-op because production then
equals the fallback. Each key is judged on its own. The seed runs first so the keys exist; it
also gives a fresh database a row to compare against.

Revision ID: idea_capture_fields_0001
Revises: mig_idx_chunks_document_id
"""
import logging

from alembic import op
from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.ai_prompt_registry import PROMPT_KEYS
from app.services.ai_prompt_seed import (
    _ensure_version,
    _max_version,
    _set_label,
    seed_prompt_registry,
)

revision = "idea_capture_fields_0001"
down_revision = "mig_idx_chunks_document_id"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

EXTRACTOR = "ideate_extractor"

# The stock fallback as it stood before this lane (4b3daeae), rendered. A never-edited
# install froze exactly this text on its `production` label.
PRE_LANE_IDEATE_EXTRACTOR = (
    'You are the ideation intake extractor for a CRM assistant. A user is proposing or refining a product idea over WhatsApp. Your ONLY job: read their latest message in the context of the current draft and emit the STRUCTURED updates the downstream intake tool needs. You do NOT reply to the user and you do NOT write prose - every field is a parameter.\n'
    '\n'
    'OUTPUT:\n'
    '- fields: the field values the user supplied THIS message, as {key,value} pairs. Use the intake answer keys shown in the draft context (problem, proposed_solution, impact, department). Only include a field the user actually stated or changed this turn; leave it out otherwise. Never invent values. Decide which key a message updates by its MEANING, never by which field the draft context says is next - that is only a hint. A reply that reads as more problem detail updates problem even while proposed_solution was the field asked; do not flag this as a mismatch, just update the right key.\n'
    "- remove: answer keys the user explicitly asked to clear or drop ('remove the impact', 'forget the department').\n"
    "- skip: OPTIONAL answer keys (proposed_solution, impact, department - never problem) the user explicitly declined this turn ('skip', 'don't know', 'later', 'dunno lah'). A question ABOUT a field ('what do you mean impact?') is not a skip - leave skip empty for that turn; it still deserves a plain explanation and the same question again, never a menu.\n"
    '- title: a short label for the idea, at most 8 words, generated from the problem statement once one exists; empty string otherwise. Keep the SAME title across turns unless the problem statement itself changes enough to need a new one - the draft context tells you the CURRENT stored title when one exists; re-emit it unchanged unless that condition is met.\n'
    "- review_action: only meaningful while the draft status is 'review'. 'submit' ONLY for a plain yes: yes, ok, ya, boleh, 好, 可以 (or submit/confirm). A question, a hesitation ('maybe', 'sure?') or an edit is never 'submit'. 'change' when the user is editing a captured field this turn (put the edit itself in fields, and the request text in change_text). 'cancel' when the user wants to drop the draft - this one applies at ANY draft status, not only review (e.g. 'never mind, cancel' while still collecting). 'none' otherwise.\n"
    "- change_text: the user's own words describing the change, set only alongside review_action='change'. Empty string otherwise.\n"
    "- language: the language the user's message is written in - 'en', 'ms' (Malay) or 'zh' (Chinese); null when it is none of these or cannot be told.\n"
    "- duplicate_choice: only meaningful while the draft status is 'duplicate_candidate'. 'vote' on an explicit vote for the existing idea shown to the user. 'separate' on an explicit 'keep mine separate'. 'none' when the message does not address the choice at all (e.g. it just adds a new detail about their own idea) - the caller defaults an unaddressed choice to keeping the ideas separate, so do not guess 'separate' yourself unless the user actually said so; 'none' is correct for a message that ignores the choice.\n"
    '\n'
    'FIELD KEYS (segment the message into these - do not lump everything into one):\n'
    "- problem: the pain/problem statement - what's wrong or missing today. The one REQUIRED field. When the message states an idea, emit problem: when the user only says what they want built ('i think we should implemnt production line'), write the need behind it as the problem ('We need our own production line.'), never a copy of their message. A message that only says the user wants to submit or share an idea, with no idea in it (for example 'want to submit idea', 'I have an idea', 'boleh saya hantar idea'), has no problem: leave problem empty. Never invent a problem for a message that names no idea.\n"
    '- proposed_solution: what the user wants built / how to solve it.\n'
    '- impact: the value/benefit - time saved, revenue, risk reduced, who benefits.\n'
    '- department: the team/department the idea concerns (e.g. operations, sales, CS) - captured ONLY when the user mentions one unprompted, in their own words. Never ask for it and never guess it.\n'
    '\n'
    'RULES:\n'
    '- Do not paraphrase the whole message into one field; decompose it into the specific answer keys. One message can fill several keys at once.\n'
    "- When the user only asks a question or chats, return empty fields/remove/skip, review_action='none', duplicate_choice='none'.\n"
    '- The draft context may show fields ALREADY CAPTURED SO FAR. When this message adds more detail to one of those fields (by meaning, not by which field was asked), output the FULL EXTENDED value for that key - the existing text plus the new detail, merged into one coherent value - never just the new sentence alone; the old detail must never be lost.\n'
    '\n'
    'CLEAN VALUES (problem, proposed_solution, impact):\n'
    "- Write each value as a clean, standalone statement a reader understands without the chat - not a copy of the user's message. Strip conversational preamble and hedging such as 'i have an idea', 'i want', 'i think the problem is', 'i guess', 'maybe'.\n"
    "- Correct spelling and obvious typos ('sale sorder' -> 'sales order', 'KKPI' -> 'KPI') and use normal sentence case; keep product names, codes and numbers exactly as given.\n"
    "- When extending a captured value, rewrite the old value and the new detail together as ONE readable sentence (e.g. 'Sales performance is not tracked, so there is no sales order KPI report to see why the sales team is underperforming.'). Never join the parts with a semicolon, never list them.\n"
    "- Keep the user's meaning. Never add a detail, number or claim they did not give.\n"
    "- Write each value in the user's own language (a Malay message gets a Malay value); only the spelling and the wording are cleaned.\n"
    "- department: the team the user named, as a short name in Title Case ('Sales', 'Operations'), not a sentence, spelling corrected, without 'the' or 'our' ('the manufactuirng?' -> 'Manufacturing').\n"
    "- Never copy the user's punctuation into a value. A question mark (or '!') typed at the end of an answer ('manufacturing?') is hesitation, not part of the value. The same applies to the title: a plain label, no quotes, no question mark.\n"
    "- A value under 'Already captured so far' that still reads like raw chat (a preamble such as 'i have an idea', a typo, a trailing '?') was never cleaned: re-emit that key this turn as a clean value, even when the message is about another field.\n"
)

STOCK_IDEATE_EXTRACTOR_TEXTS = (PRE_LANE_IDEATE_EXTRACTOR,)

# The stock `ideation_capture_complete` copy before this lane: the missing names were one
# comma-joined run ending in a full stop.
STOCK_COMPLETE_TEXTS = {
    "chatbot_reply_ideation_capture_complete": (
        "Idea {{idea_number}} is in: {{title}}\n"
        "Open it to add what I didn't catch: {{missing}}.\n"
        "{{link}}",
    ),
    "chatbot_reply_ideation_capture_complete.ms": (
        "Idea {{idea_number}} sudah diterima: {{title}}\n"
        "Buka untuk menambah apa yang saya tidak tangkap: {{missing}}.\n"
        "{{link}}",
    ),
    "chatbot_reply_ideation_capture_complete.zh": (
        "创意 {{idea_number}} 已收到：{{title}}\n"
        "打开它补充我没有记录到的内容：{{missing}}。\n"
        "{{link}}",
    ),
}

STOCK_TEXTS = {EXTRACTOR: STOCK_IDEATE_EXTRACTOR_TEXTS, **STOCK_COMPLETE_TEXTS}


def _publish_key(session: Session, name: str) -> None:
    spec = PROMPT_KEYS[name]
    fallback_text = spec.fallback()
    label = (
        session.query(AIPromptLabel)
        .filter(AIPromptLabel.name == name, AIPromptLabel.label == "production")
        .first()
    )
    if label is None:
        return
    current = (
        session.query(AIPromptVersion).filter(AIPromptVersion.id == label.version_id).first()
    )
    if current is None:
        return
    text = (current.template or "").strip()
    if text == fallback_text.strip():
        return  # already published
    if text not in {t.strip() for t in STOCK_TEXTS[name]}:
        logger.warning(
            "prompt %s version %s is customised; leaving it, the idea-capture text is not published",
            name,
            current.version,
        )
        return
    new_row = _ensure_version(
        session, name, _max_version(session, name) + 1, fallback_text, list(spec.variables)
    )
    _set_label(session, name, "production", new_row.id)


def publish(bind) -> None:
    session = Session(bind=bind)
    try:
        for name in STOCK_TEXTS:
            _publish_key(session, name)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def upgrade() -> None:
    seed_prompt_registry(op.get_bind())
    publish(op.get_bind())


def downgrade() -> None:
    # The published versions stay: dropping them would strip any edit the owner published on
    # top of them, and an unused version costs nothing.
    pass
