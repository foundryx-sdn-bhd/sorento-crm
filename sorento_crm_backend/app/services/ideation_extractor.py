"""Ideation brain extractor (D-CONFIRM) - the sorento-side NLU for `ideate` turns.

shared-service `create_idea` runs NO LLM: it composes the echo/summary and owns the
durable draft, but it needs sorento to hand it STRUCTURED updates, never free text
to parse. So each `ideate` turn we run one small, schema-forced LLM step that reads
the user's message in the context of the current draft (its status, still-missing
required fields, the next optional field to ask, and any duplicate candidate) and
emits:

    { fields, remove, skip, title, review_action, change_text, duplicate_choice }

- ``fields`` - answer-key -> value updates the user just supplied. R17 (owner ruling,
  24 Sep 2026): the field a message updates is decided by the MEANING of the whole
  draft so far, never by which field ``next_field`` happened to ask - the hint is
  only a hint. A message that reads as more problem detail updates ``problem`` even
  while ``proposed_solution`` was the one asked (AC-1219).
- ``remove`` - answer keys the user asked to clear ("remove who", "forget the module").
- ``skip`` - OPTIONAL answer keys the user explicitly declined ("skip", "don't know",
  "later", "dunno lah"). Never ``problem`` - the one required field can't be skipped
  (deterministic guard below, AC-1205).
- ``title`` - a short label (at most 8 words) generated from the idea text, cut
  deterministically below if the model runs long (AC-1202). Empty string when the
  draft has no problem statement yet.
- ``review_action`` - ``"submit" | "change" | "cancel" | "none"``, meaningful only
  while the draft is in ``review``: ``submit`` on an explicit "yes/ok/boleh/submit/
  confirm"; ``change`` when the user is editing a captured field (the edit itself
  goes in ``fields``, the request text in ``change_text``); ``cancel`` when the user
  wants to drop the draft - honoured at ANY step, not only during review (AC-1211).
- ``change_text`` - the user's own words describing the change, set only alongside
  ``review_action == "change"``.
- ``duplicate_choice`` - ``"vote" | "separate" | "none"``, meaningful only while the
  draft is ``duplicate_candidate``: ``vote`` on an explicit vote for the existing
  idea, ``separate`` on an explicit "keep mine separate", ``none`` when the message
  does not address the choice at all (the caller defaults ``none`` to ``separate``
  per R4 - AC-1214).

``confirm`` is no longer read from the model (AC-1201): it is DERIVED here from
``review_action`` and the draft's status, so there is exactly one place (this
function) that decides it - the old draft flow read ``.confirm`` off the result.

Reuses the same provider plumbing as before (``get_provider`` + ``json_schema``
forced output) and the prompt registry (``ideate_extractor`` key). On any failure
(no api key, provider/parse error) it degrades to an EMPTY extraction so the turn
still calls ``create_idea`` with ``message_text`` - never raises.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.services import ai_prompt_registry
from app.services.ai_assistant_service import AIAssistantConfigService
from app.services.llm_provider import get_provider

logger = logging.getLogger(__name__)


IDEATE_EXTRACTION_SCHEMA_NAME = "ideate_extraction"

# Only these answer keys can ever be skipped (AC-1205) - the one required field,
# `problem`, cannot appear in `skip` no matter what the model emits.
_SKIPPABLE_KEYS = {"proposed_solution", "impact", "department"}

_REVIEW_ACTIONS = {"submit", "change", "cancel", "none"}
_DUPLICATE_CHOICES = {"vote", "separate", "none"}
_LANGUAGES = {"en", "ms", "zh"}

# OpenAI strict-mode json_schema: every property required, additionalProperties
# false, no open-ended object maps (``fields`` is an array of {key,value} pairs
# so dynamic answer keys stay strict-compliant).
IDEATE_EXTRACTION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "fields": {
            "type": "array",
            "description": (
                "Field updates the user supplied this turn, as {key,value} pairs. "
                "key is the intake answer key (one of: problem, proposed_solution, "
                "impact, department); value is the user's answer as plain text. "
                "Decide which key a message updates by its MEANING, never by which "
                "field was just asked (next_field is only a hint) - e.g. a reply "
                "that reads as more problem detail updates problem even while "
                "proposed_solution was the one asked. Empty when the turn adds "
                "nothing."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
            },
        },
        "remove": {
            "type": "array",
            "description": "Answer keys the user asked to clear/remove this turn.",
            "items": {"type": "string"},
        },
        "skip": {
            "type": "array",
            "description": (
                "OPTIONAL answer keys the user explicitly declined this turn "
                "('skip', 'don't know', 'later', 'dunno lah'). Never include "
                "problem - it is the one required field and cannot be skipped. "
                "A question ABOUT a field ('what do you mean impact?') is not a "
                "skip - leave skip empty for that turn."
            ),
            "items": {"type": "string"},
        },
        "title": {
            "type": "string",
            "description": (
                "A short label for the idea, at most 8 words, generated from the "
                "problem statement. Empty string when the draft has no problem "
                "statement yet."
            ),
        },
        "review_action": {
            "type": "string",
            "enum": sorted(_REVIEW_ACTIONS),
            "description": (
                "Only meaningful while the draft status is 'review'. 'submit' ONLY "
                "for a plain yes: yes, ok, ya, boleh, 好, 可以 (or submit/confirm). "
                "A question, a hesitation or an edit is never submit. 'change' when the user "
                "is editing a captured field this turn (put the edit in fields and "
                "the request in change_text). 'cancel' when the user wants to drop "
                "the draft - this one is honoured at ANY draft status, not only "
                "review. 'none' otherwise."
            ),
        },
        "change_text": {
            "type": "string",
            "description": (
                "The user's own words describing the change, set only alongside "
                "review_action == 'change'. Empty string otherwise."
            ),
        },
        "duplicate_choice": {
            "type": "string",
            "enum": sorted(_DUPLICATE_CHOICES),
            "description": (
                "Only meaningful while the draft status is 'duplicate_candidate'. "
                "'vote' on an explicit vote for the existing idea. 'separate' on "
                "an explicit 'keep mine separate'. 'none' when the message does "
                "not address the choice at all (e.g. it just adds a new detail)."
            ),
        },
        "has_idea": {
            "type": "boolean",
            "description": (
                "false when the message only says the user wants to submit or share an "
                "idea without stating one (for example 'I have an idea', 'want to submit "
                "idea', 'boleh saya hantar idea'); true when it states an idea, a need or "
                "a change."
            ),
        },
        "language": {
            "type": ["string", "null"],
            "enum": ["en", "ms", "zh", None],
            "description": (
                "The language the user's message is written in: 'en' (English), 'ms' "
                "(Malay) or 'zh' (Chinese). null when it is none of these or cannot be "
                "told (a bare number or 'new')."
            ),
        },
    },
    "required": [
        "fields",
        "remove",
        "skip",
        "title",
        "review_action",
        "change_text",
        "duplicate_choice",
        "has_idea",
        "language",
    ],
}


@dataclass
class IdeateExtraction:
    fields: dict[str, str] = field(default_factory=dict)
    remove: list[str] = field(default_factory=list)
    skip: list[str] = field(default_factory=list)
    title: str = ""
    review_action: str = "none"
    change_text: str = ""
    duplicate_choice: str = "none"
    confirm: bool = False
    #: The language of the message: en / ms / zh, None when unknown (IDEATION-CAPTURE).
    language: str | None = None
    #: False when the message only announces an idea without stating one; None when unknown.
    has_idea: bool | None = None


# #1279 round 2 (owner ruling, 26 Sep 2026): "only a yes creates the idea", in the
# owner's languages. `submit` / `confirm` stay accepted (R3, AC-1208).
_YES_WORDS = {"yes", "ok", "okay", "ya", "yup", "yeah", "boleh", "submit", "confirm", "好", "好的", "可以"}
# Particles that may ride along a bare yes ("ok lah", "yes please") without making it
# anything more than a yes.
_YES_FILLERS = {"lah", "la", "please", "pls"}
_WORD_RE = re.compile(r"[a-z]+|[\u4e00-\u9fff]+")

# W2: punctuation a user types at either end of an answer ("manufacturing?") is
# never part of the value. Sentence-final "." and "。" are kept on a sentence field.
_EDGE_PUNCT = "?？!！,，;；:："
_QUOTES = "\"'“”‘’「」『』"
_DEPARTMENT_ARTICLES = ("the ", "our ", "my ")


def _yes_tokens(message_text: str) -> list[str]:
    return _WORD_RE.findall((message_text or "").lower())


def derive_confirm(
    status: str | None,
    message_text: str,
    *,
    fields: dict[str, str],
    remove: list[str],
    review_action: str,
) -> bool:
    """The one place that decides ``confirm`` (AC-1201), per the owner's ruling of
    26 Sep 2026: only a yes, while the draft is in ``review``, creates the idea.

    - never outside ``review`` (AC-1211), never alongside a field edit or removal
      (that is a change request and re-enters the recap), never on change/cancel;
    - a bare yes (``yes``, ``ok``, ``ya``, ``boleh``, ``好``, ``可以``, optionally with
      ``lah`` / ``please``) confirms on its own, so an extractor outage cannot
      strand a draft at the confirm question;
    - a longer message confirms only when the model read it as ``submit`` AND it
      carries a yes word ("ok that's correct", "can you just submit it already").
    """
    if status != "review" or fields or remove or review_action in ("change", "cancel"):
        return False
    tokens = _yes_tokens(message_text)
    if not tokens:
        return False
    if all(t in _YES_WORDS or t in _YES_FILLERS for t in tokens) and any(t in _YES_WORDS for t in tokens):
        return True
    return review_action == "submit" and any(t in _YES_WORDS for t in tokens)


def _strip_edges(value: str) -> str:
    text = (value or "").strip()
    previous = None
    while text != previous:
        previous = text
        text = text.strip().strip(_QUOTES).strip().strip(_EDGE_PUNCT).strip()
    return text


def _title_case_word(word: str) -> str:
    # Keep an acronym ("IT", "HR") as typed; otherwise capitalise the first letter.
    if len(word) > 1 and word.isupper():
        return word
    return word[:1].upper() + word[1:].lower()


def normalise_field_value(key: str, value: str) -> str:
    """W2 (#1279 round 2): the deterministic clean-up every extracted value goes
    through before it reaches the intake. The prompt's CLEAN VALUES rule does the
    wording and the spelling; this guarantees the mechanical part whatever the
    model emitted: no quotes or typed ``?``/``!`` at either end, a first capital,
    and a department as a short Title Case name without a leading "the"/"our"."""
    text = _strip_edges(value)
    if not text:
        return ""
    if key == "department":
        lowered = text.lower()
        for article in _DEPARTMENT_ARTICLES:
            if lowered.startswith(article):
                text = text[len(article):].strip()
                break
        return " ".join(_title_case_word(w) for w in text.split())
    return text[:1].upper() + text[1:]


def normalise_title(title: str) -> str:
    """W2: the title is a label - no quotes, no trailing punctuation, first
    letter capitalised, at most 8 words (AC-1202)."""
    text = _strip_edges(title).rstrip(".。").strip()
    return _cut_title(text[:1].upper() + text[1:])


def _cut_title(title: str) -> str:
    """At most 8 words (AC-1202) - a longer model output is cut to its first 8."""
    words = (title or "").split()
    return " ".join(words[:8])


def extract_ideate_turn(
    db: Session,
    *,
    message_text: str,
    status: str | None = None,
    missing: list[str] | None = None,
    next_field: str | None = None,
    duplicate_candidate_title: str | None = None,
    field_labels: dict[str, str] | None = None,
    captured: dict[str, str] | None = None,
    prior_title: str | None = None,
) -> IdeateExtraction:
    """Extract the ideate NLU output from ``message_text`` given the draft context.

    Never raises - degrades to an empty extraction on any failure. ``confirm`` is
    derived here (AC-1201) by ``derive_confirm``: only a yes while ``status ==
    "review"`` (D-CONFIRM / AC-1208 / AC-1211, owner ruling 26 Sep 2026) - a
    confirmation only means anything once the draft is being reviewed. The old draft flow
    derived it again after its own normalisation, so an empty (failed) extraction
    still let a plain yes submit. ``cancel`` has no such gate:
    the caller reads ``review_action == "cancel"`` directly and honours it at any
    status (AC-1211).
    """
    raw = (message_text or "").strip()
    if not raw:
        return IdeateExtraction()

    try:
        config = AIAssistantConfigService(db).get()
    except Exception:  # noqa: BLE001 - never break the turn on a config read
        logger.warning("ideate_extractor: config read failed; empty extraction", exc_info=True)
        return IdeateExtraction()

    api_key = config.api_key_ciphertext or settings.openai_api_key
    if not api_key:
        return IdeateExtraction()

    try:
        system, _version = ai_prompt_registry.render(db, "ideate_extractor")
    except Exception:  # noqa: BLE001
        logger.warning("ideate_extractor: prompt render failed; empty extraction", exc_info=True)
        return IdeateExtraction()

    context_lines: list[str] = []
    context_lines.append(f"Current draft status: {status or 'new'}")
    if missing:
        context_lines.append(f"Fields still missing: {', '.join(missing)}")
    if next_field:
        context_lines.append(
            f"Next field the bot would ask (a HINT only, not a routing key): {next_field}"
        )
    if duplicate_candidate_title:
        context_lines.append(f"Duplicate candidate title: {duplicate_candidate_title}")
    if field_labels:
        labels = ", ".join(f"{k} ({v})" for k, v in field_labels.items())
        context_lines.append(f"Known field keys: {labels}")
    # Blocking 2 (reviewer, round 1, PR #1222 at 720bb8f5): without the draft's
    # CURRENT captured answers and its stored title, the model can only ever
    # emit a fresh, standalone value for a field - it has no way to EXTEND the
    # existing one, and no way to know a title already exists to keep stable
    # (AC-1219's "extended, not overwritten"; title stability).
    if captured:
        # #1277: one field per line. A "; "-joined list here was the same glue the
        # owner then saw inside a merged Problem value.
        captured_lines = "\n".join(f"- {k}: {v}" for k, v in captured.items())
        context_lines.append(
            "Already captured so far (EXTEND these when the message adds more "
            "detail to one of them - output the FULL merged value, rewritten as one "
            "clean statement, never just the new sentence alone):\n" + captured_lines
        )
    if prior_title:
        context_lines.append(
            f"Current stored title (keep the SAME title unless the problem "
            f"statement itself changes enough to need a new one): {prior_title}"
        )
    user_block = "\n".join(context_lines) + f"\n\nUser message:\n{raw}"

    messages_in = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_block},
    ]

    try:
        provider = get_provider(config.provider, api_key, config.model)
        result = provider.chat(
            messages_in,
            temperature=0.0,
            model=config.model,
            max_tokens=512,
            json_schema=IDEATE_EXTRACTION_JSON_SCHEMA,
            json_schema_name=IDEATE_EXTRACTION_SCHEMA_NAME,
        )
        data = json.loads((result.content or "").strip() or "{}")
    except Exception:  # noqa: BLE001 - provider/parse error → empty extraction
        logger.warning("ideate_extractor: LLM call failed; empty extraction", exc_info=True)
        return IdeateExtraction()

    fields: dict[str, str] = {}
    for pair in data.get("fields") or []:
        if isinstance(pair, dict) and pair.get("key"):
            fields[str(pair["key"])] = str(pair.get("value", ""))
    remove = [str(k) for k in (data.get("remove") or []) if k]

    # AC-1205: only optional keys can ever be skipped - `problem` is dropped from
    # `skip` no matter what the model emitted.
    skip = [str(k) for k in (data.get("skip") or []) if k and str(k) in _SKIPPABLE_KEYS]

    title = _cut_title(str(data.get("title") or ""))

    review_action = str(data.get("review_action") or "none")
    if review_action not in _REVIEW_ACTIONS:
        review_action = "none"
    change_text = str(data.get("change_text") or "")

    duplicate_choice = str(data.get("duplicate_choice") or "none")
    if duplicate_choice not in _DUPLICATE_CHOICES:
        duplicate_choice = "none"

    # AC-1208/AC-1211, narrowed by the owner's ruling of 26 Sep 2026 (#1279 round
    # 2): only a yes while the draft is under review confirms.
    confirm = derive_confirm(
        status, raw, fields=fields, remove=remove, review_action=review_action
    )

    language = data.get("language")
    if language not in _LANGUAGES:
        language = None

    has_idea = data.get("has_idea")
    if not isinstance(has_idea, bool):
        has_idea = None

    return IdeateExtraction(
        fields=fields,
        language=language,
        has_idea=has_idea,
        remove=remove,
        skip=skip,
        title=title,
        review_action=review_action,
        change_text=change_text,
        duplicate_choice=duplicate_choice,
        confirm=confirm,
    )
