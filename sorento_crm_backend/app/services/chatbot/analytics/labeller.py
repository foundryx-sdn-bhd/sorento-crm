"""The nightly topic labeller: leaf topic, ask split, language and product words (CA-2).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca2-taxonomy-09oct.md, "Behaviour card".

**HYBRID (owner ruling, 9 Oct 2026).** The SUBJECT of an ask is free: it is the domain the bot
acted on, already on the CA-1 fact row (`outcome._domain`), and a follow-up with no domain of
its own has the domain the engine carried from the previous turn there, which is the explicit
turn link (card answer 3). This module only fills what the parser does not say: the leaf
under that subject, further asks in the same message, the language, and the product words.
It never changes the subject of ask 1, the parser, or a live turn.

**Nothing runs before the owner approves taxonomy v1.** With no leaf in `chatbot.chat_topics`
(only the subjects the first migration seeds), `run` returns without calling the model.

**The customer's words are read, never stored.** The text comes off `chatbot.turns.envelope`
into the prompt for one call; the fact row gets codes, a version, a model name and a score.

**Products resolve exactly or not at all.** A product word goes through the bot's own
resolver, inside the asking contact's scope, and only a match on the `exact` tier counts: the
resolver also answers by prefix, substring, trigram and embedding, which are guesses (owner
rule: never matched by string similarity). One product left, and the model at least
`PRODUCT_CONFIDENCE_FLOOR` sure of the word: one `resolver_pass` item. Anything else: none.

The model and the resolver are passed in (`chat`, `resolve`), so the tests drive the whole job
without a provider; `production_chat` and `production_resolve` build the real ones.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.chat_facts import ChatAskFact, ChatAskItem, ChatTopic
from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot.analytics import outcome as outcome_mod

logger = logging.getLogger(__name__)

#: Recorded on every row the labeller writes; bump when the prompt or the rules change.
PROMPT_VERSION = "ca2-1"

#: Asks per model call (owner ruling: batches of 40).
BATCH_SIZE = 40

#: The model's own score a product word needs before the resolver is asked (card answer 5).
PRODUCT_CONFIDENCE_FLOOR = 0.8

#: The languages an ask can be in; `mixed` for a message in two (card answer 4).
LANGUAGES: tuple[str, ...] = ("en", "ms", "zh", "mixed", "other")

#: The subject for an ask the bot gave no domain, and its noise leaf.
GENERAL = "general"
NOISE = "general.noise"

#: The bot reply the model sees, cut to this many characters (design 3.4).
REPLY_CHARS = 300

#: The only resolver tier that is a match rather than a guess.
EXACT_TIER = "exact"

ChatFn = Callable[[list[dict], dict], str]
ResolveFn = Callable[[str, str], list[dict]]


# --------------------------------------------------------------------------- shapes


@dataclass(frozen=True)
class Taxonomy:
    """The active taxonomy: its version, subjects `{code: label}` and leaves per subject
    `{subject: [(code, label, definition)]}`."""

    version: int
    subjects: dict[str, str]
    leaves: dict[str, list[tuple[str, str, str]]]

    @property
    def has_leaves(self) -> bool:
        return any(self.leaves.values())

    def leaf_codes(self, subject: str) -> set[str]:
        return {code for code, _label, _definition in self.leaves.get(subject, [])}


@dataclass(frozen=True)
class AskInput:
    """One fact row as the model sees it. `key` is the fact id."""

    key: str
    subject: str
    forced_leaf: str | None
    text: str
    domain: str | None
    intent: str | None
    outcome: str | None
    reply: str


@dataclass(frozen=True)
class ProductWord:
    word: str
    confidence: float


@dataclass(frozen=True)
class AskLabel:
    subject: str
    leaf: str
    confidence: float
    product_words: tuple[ProductWord, ...] = ()


@dataclass(frozen=True)
class Label:
    language: str
    asks: tuple[AskLabel, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------- pure rules


def subject_for(domain: str | None, message_type: str | None) -> tuple[str, str | None]:
    """`(subject, forced_leaf)` of ask 1. The fact's domain when there is one; otherwise
    `general`, and a casual message with no domain is noise outright (owner ruling)."""
    if domain:
        return domain, None
    if message_type == "casual":
        return GENERAL, NOISE
    return GENERAL, None


def _other(subject: str) -> str:
    return f"{subject}.other"


def _fallback(subject: str, taxonomy: "Taxonomy") -> str:
    """Where an ask lands when no leaf fits: `<subject>.other`, or the subject itself when it
    has no `.other` leaf (a one-leaf subject, or `sales`, which has none in v1), so every
    `topic_code` is a row of `chatbot.chat_topics`."""
    other = _other(subject)
    return other if other in taxonomy.leaf_codes(subject) else subject


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _leaf(subject: str, leaf: Any, taxonomy: Taxonomy) -> str:
    return leaf if isinstance(leaf, str) and leaf in taxonomy.leaf_codes(subject) else _fallback(subject, taxonomy)


def _product_words(value: Any) -> tuple[ProductWord, ...]:
    words: list[ProductWord] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict) and isinstance(item.get("word"), str) and item["word"].strip():
            words.append(ProductWord(item["word"].strip(), _confidence(item.get("confidence"))))
    return tuple(words)


#: Extra asks one message may add after ask 1 (ask_seq at most 4).
MAX_EXTRA_ASKS = 3


def parse_labels(content: str, batch: list[AskInput], taxonomy: Taxonomy) -> dict[str, Label]:
    """The model's answer, held to the rules. Anything malformed is left out (that row stays
    unlabelled and is picked again next night); nothing here raises.

    - ask 1 keeps the row's subject; a leaf outside that subject is `<subject>.other`;
    - a further ask keeps the model's subject only when it is a known subject;
    - a forced leaf (casual with no domain) wins over the model;
    - a language outside `LANGUAGES` is `other`;
    - the first entry for a key wins, and at most `MAX_EXTRA_ASKS` asks follow ask 1.
    """
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return {}
    labels = data.get("labels") if isinstance(data, dict) else None
    if not isinstance(labels, list):
        return {}
    rows = {row.key: row for row in batch}
    out: dict[str, Label] = {}
    seen: set[str] = set()
    for entry in labels:
        if not isinstance(entry, dict):
            continue
        row = rows.get(entry.get("key"))
        if row is not None:
            if row.key in seen:
                continue
            seen.add(row.key)
        raw_asks = entry.get("asks")
        if row is None or not isinstance(raw_asks, list) or not raw_asks:
            continue
        asks: list[AskLabel] = []
        for index, raw in enumerate(raw_asks):
            if not isinstance(raw, dict):
                continue
            if index == 0:
                subject = row.subject
                leaf = row.forced_leaf or _leaf(subject, raw.get("leaf"), taxonomy)
            else:
                subject = raw.get("subject")
                if subject not in taxonomy.subjects:
                    continue
                leaf = _leaf(subject, raw.get("leaf"), taxonomy)
            asks.append(
                AskLabel(subject, leaf, _confidence(raw.get("confidence")), _product_words(raw.get("product_words")))
            )
        asks = asks[: 1 + MAX_EXTRA_ASKS]
        if not asks or asks[0].subject != row.subject:
            continue
        language = entry.get("language")
        out[row.key] = Label(language if language in LANGUAGES else "other", tuple(asks))
    return out


def json_schema() -> dict[str, Any]:
    """The strict output schema the provider is held to."""
    word = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"word": {"type": "string"}, "confidence": {"type": "number"}},
        "required": ["word", "confidence"],
    }
    one_ask = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "subject": {"type": "string"},
            "leaf": {"type": "string"},
            "confidence": {"type": "number"},
            "product_words": {"type": "array", "items": word},
        },
        "required": ["subject", "leaf", "confidence", "product_words"],
    }
    label = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "key": {"type": "string"},
            "language": {"type": "string", "enum": list(LANGUAGES)},
            "asks": {"type": "array", "items": one_ask},
        },
        "required": ["key", "language", "asks"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"labels": {"type": "array", "items": label}},
        "required": ["labels"],
    }


_INSTRUCTIONS = """You label customer messages sent to a WhatsApp sales bot of a building-supplies \
distributor. Each input row is one message, with the bot's subject for it, what the bot did, and \
the start of the bot's reply.

For every row return one label with the row's key:
- asks: the distinct questions in the message, in order. The FIRST ask keeps the row's subject; \
pick its leaf from that subject's codes below. Add a further ask only when the message plainly \
asks a second, different thing; give it the subject it belongs to (a subject code below) and a \
leaf under that subject. If no leaf fits, use the "none of the above" code of that subject.
- confidence: 0 to 1, how sure you are of the leaf.
- product_words: the words in the message that name one product (a code or a product's name), \
each with how sure you are that it names a product. Never invent one.
- language: en, ms, zh, mixed (two languages in one message) or other.
Messages in a row group from the same customer are in time order; use the earlier ones to read \
a short follow-up.

Taxonomy (subject: leaves):"""


def _taxonomy_block(taxonomy: Taxonomy) -> str:
    lines: list[str] = []
    for subject, label in taxonomy.subjects.items():
        lines.append(f"{subject} ({label}):")
        for code, leaf_label, definition in taxonomy.leaves.get(subject, []):
            lines.append(f"  - {code}: {definition or leaf_label}")
        if _fallback(subject, taxonomy) == subject:
            lines.append(f"  - {subject}: none of the above")
    return "\n".join(lines)


def build_messages(batch: list[AskInput], taxonomy: Taxonomy) -> list[dict]:
    """System: the instructions and the taxonomy (the same for every call, so the provider
    can cache it). User: the rows as JSON."""
    rows = [
        {
            "key": row.key,
            "subject": row.subject,
            "intent": row.intent,
            "bot_outcome": row.outcome,
            "message": row.text,
            "bot_reply_start": (row.reply or "")[:REPLY_CHARS],
        }
        for row in batch
    ]
    return [
        {"role": "system", "content": f"{_INSTRUCTIONS}\n{_taxonomy_block(taxonomy)}"},
        {"role": "user", "content": json.dumps({"rows": rows}, ensure_ascii=False)},
    ]


def batch_keys(messages: list[dict]) -> list[str]:
    """The keys a built prompt carries, in order."""
    for message in messages:
        if message.get("role") == "user":
            try:
                return [row["key"] for row in json.loads(message["content"])["rows"]]
            except (KeyError, TypeError, ValueError):
                return []
    return []


# --------------------------------------------------------------------------- database


def load_taxonomy(db: Session) -> Taxonomy:
    rows = (
        db.query(ChatTopic)
        .filter(ChatTopic.status == "active")
        .order_by(ChatTopic.sort_order.asc(), ChatTopic.code.asc())
        .all()
    )
    subjects = {row.code: row.label for row in rows if row.parent_code is None}
    leaves: dict[str, list[tuple[str, str, str]]] = {code: [] for code in subjects}
    for row in rows:
        if row.parent_code in leaves:
            leaves[row.parent_code].append((row.code, row.label, row.definition))
    return Taxonomy(version=max((row.version for row in rows), default=0), subjects=subjects, leaves=leaves)


def _message_text(turn: ChatbotTurn) -> str:
    from app.services.chatbot.turn.memory import _turn_message_text

    return _turn_message_text(turn)


def _reply_text(turn: ChatbotTurn) -> str:
    response = turn.response if isinstance(turn.response, dict) else {}
    reply = response.get("reply") if isinstance(response.get("reply"), dict) else {}
    return str(reply.get("text") or "")


def _pick(db: Session, taxonomy: Taxonomy, limit: int | None) -> list[tuple[ChatAskFact, ChatbotTurn]]:
    """Ask-1 rows with no topic or an older taxonomy version, newest first."""
    query = (
        db.query(ChatAskFact, ChatbotTurn)
        .join(ChatbotTurn, ChatbotTurn.id == ChatAskFact.turn_id)
        .filter(
            ChatAskFact.ask_seq == 1,
            or_(ChatAskFact.topic_code.is_(None), ChatAskFact.taxonomy_version < taxonomy.version),
        )
        .order_by(ChatAskFact.asked_at.desc(), ChatAskFact.id.asc())
    )
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def _input(fact: ChatAskFact, turn: ChatbotTurn) -> AskInput:
    turn_dict = {"trace": turn.trace if isinstance(turn.trace, list) else []}
    subject, forced = subject_for(fact.domain, outcome_mod.message_type(turn_dict))
    return AskInput(
        key=str(fact.id),
        subject=subject,
        forced_leaf=forced,
        text=_message_text(turn),
        domain=fact.domain,
        intent=fact.intent,
        outcome=fact.outcome,
        reply=_reply_text(turn),
    )


def _batches(rows: list[Any], size: int) -> Iterable[list[Any]]:
    """Grouped by contact, then time, so a contact's neighbouring asks share a call."""
    ordered = sorted(rows, key=lambda pair: (pair[0].contact_respond_id, pair[0].asked_at))
    for start in range(0, len(ordered), size):
        yield ordered[start : start + size]


def _resolved_product(resolve: ResolveFn, word: ProductWord, contact: str) -> str | None:
    if word.confidence < PRODUCT_CONFIDENCE_FLOOR:
        return None
    try:
        matches = resolve(word.word, contact) or []
    except Exception:  # noqa: BLE001 - a resolver fault costs the item, never the label
        logger.warning("chat labeller: resolver failed on a product word", exc_info=True)
        return None
    ids = {
        str(m.get("uuid"))
        for m in matches
        if isinstance(m, dict)
        and m.get("entity_type") == "product"
        and m.get("match_tier") == EXACT_TIER
        and m.get("uuid")
    }
    return next(iter(ids)) if len(ids) == 1 else None


def _write_items(
    db: Session, ask_id: str, ask: AskLabel, contact: str, resolve: ResolveFn
) -> None:
    for word in ask.product_words:
        product_id = _resolved_product(resolve, word, contact)
        if product_id is None:
            continue
        db.execute(
            pg_insert(ChatAskItem.__table__)
            .values(
                id=_new_id(),
                ask_id=ask_id,
                kind="product",
                ref_id=product_id,
                role="named",
                resolved_by="resolver_pass",
                confidence=word.confidence,
            )
            .on_conflict_do_nothing(constraint="uq_chat_ask_items_ask_kind_ref_role")
        )


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())


def _apply(
    db: Session,
    fact: ChatAskFact,
    label: Label,
    taxonomy: Taxonomy,
    *,
    model: str,
    resolve: ResolveFn,
    now: datetime,
) -> None:
    """Write one message's label: ask 1 in place, further asks as new rows (the previous
    split of the same turn is replaced), and each ask's resolver items."""
    audit = {
        "taxonomy_version": taxonomy.version,
        "label_model": model,
        "label_prompt_version": PROMPT_VERSION,
        "labelled_at": now,
        "language": label.language,
    }
    first = label.asks[0]
    fact.topic_code = first.leaf
    fact.label_confidence = first.confidence
    for key, value in audit.items():
        setattr(fact, key, value)

    db.query(ChatAskFact).filter(ChatAskFact.turn_id == fact.turn_id, ChatAskFact.ask_seq > 1).delete(
        synchronize_session=False
    )
    db.query(ChatAskItem).filter(
        ChatAskItem.ask_id == fact.id, ChatAskItem.resolved_by == "resolver_pass"
    ).delete(synchronize_session=False)
    db.flush()
    _write_items(db, fact.id, first, fact.contact_respond_id, resolve)

    copied = {
        column.name: getattr(fact, column.name)
        for column in ChatAskFact.__table__.columns
        if column.name not in ("id", "ask_seq", "domain", "intent", "topic_code", "label_confidence", "created_at")
    }
    for seq, ask in enumerate(label.asks[1:], start=2):
        extra = ChatAskFact(
            **{**copied, **audit},
            id=_new_id(),
            ask_seq=seq,
            domain=None if ask.subject == GENERAL else ask.subject,
            intent=None,
            topic_code=ask.leaf,
            label_confidence=ask.confidence,
        )
        db.add(extra)
        db.flush()
        _write_items(db, extra.id, ask, fact.contact_respond_id, resolve)


def run(
    db: Session,
    *,
    chat: ChatFn,
    resolve: ResolveFn,
    model: str,
    limit: int | None = None,
    batch_size: int = BATCH_SIZE,
) -> dict[str, Any]:
    """Label every ask-1 row with no topic or a stale taxonomy version. One model call and
    one commit per batch, so a stop loses at most one batch. Returns counts."""
    taxonomy = load_taxonomy(db)
    if not taxonomy.has_leaves:
        return {"skipped": "no_leaves", "labelled": 0, "batches": 0}
    rows = _pick(db, taxonomy, limit)
    labelled = batches = 0
    for batch in _batches(rows, batch_size):
        inputs = [_input(fact, turn) for fact, turn in batch]
        try:
            content = chat(build_messages(inputs, taxonomy), json_schema())
        except Exception:  # noqa: BLE001 - one failed call skips its batch, not the night
            logger.warning("chat labeller: a model call failed; its batch waits for tomorrow", exc_info=True)
            continue
        batches += 1
        labels = parse_labels(content, inputs, taxonomy)
        now = datetime.now(timezone.utc)
        for fact, _turn in batch:
            label = labels.get(str(fact.id))
            if label is not None:
                _apply(db, fact, label, taxonomy, model=model, resolve=resolve, now=now)
                labelled += 1
        db.commit()
    return {"skipped": None, "labelled": labelled, "batches": batches}


# --------------------------------------------------------------------------- production


def production_chat(db: Session) -> tuple[ChatFn, str]:
    """The configured provider and model (System Settings, the parser's default), as a
    `chat` callable, plus the model name recorded on every row."""
    from app.services.ai_assistant_service import AIAssistantConfigService
    from app.services.llm_provider import get_provider, resolve_api_key

    config = AIAssistantConfigService(db).get()
    if config is None:
        raise RuntimeError("AI assistant configuration is not set")
    api_key = resolve_api_key(config, config.provider)
    if not api_key:
        raise RuntimeError(f"no API key configured for provider {config.provider!r}")
    provider = get_provider(config.provider, api_key, config.model)

    def chat(messages: list[dict], schema: dict) -> str:
        return provider.chat(
            messages,
            temperature=0.0,
            model=config.model,
            json_schema=schema,
            json_schema_name="chat_topic_labels",
            prompt_cache_key="chat-topic-labeller",
        ).content

    return chat, str(config.model)


def production_resolve(session_factory: Callable[[], Session]) -> ResolveFn:
    """The bot's own resolver, inside the asking contact's company and brand scope (as the
    engine scopes a turn), spec search off. Returns the token's matches."""
    from app.services.chatbot import engine
    from app.services.chatbot.lanes.business.services import _resolve_entity

    def resolve(word: str, contact_respond_id: str) -> list[dict]:
        scope = engine._contact_company_scope(session_factory, contact_respond_id)
        brand_scope = engine._contact_brand_scope(session_factory, contact_respond_id)
        db = engine._scoped_factory(session_factory, scope, brand_scope)()
        try:
            result = _resolve_entity(db)(
                {"query": word, "tokens": [word], "allowed_entity_types": ["product"]}
            )
        finally:
            db.close()
        resolutions = result.get("resolutions") if isinstance(result, dict) else None
        if not isinstance(resolutions, list) or not resolutions:
            return []
        first = resolutions[0] if isinstance(resolutions[0], dict) else {}
        return list(first.get("matches") or [])

    return resolve
