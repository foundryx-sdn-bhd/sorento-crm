"""The history backfill: chat facts and topic labels for messages before the traces (CA-4).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca4-backfill-09oct.md, rules B1 to B10.

**What it reads.** Incoming `chat_histories` rows of one era (`HISTORY_ERAS`, Malaysia dates;
`sent_at` is naive UTC) that no non-test engine turn processed (same contact + `message_id`,
the link `chat_history_turn_link` uses). Exact duplicates collapse to the lowest id. A message
that already has a fact is not read again, so a second run adds nothing.

**The reply** an ask got is found by explicit links only, never by text: the outgoing rows
carrying the same n8n execution id (`chat_histories.turn_id`), else the outgoing rows of the
same contact after the ask and before that contact's next incoming row (crew ruling Q2).
`sender_source = user` rows are staff, never the bot.

**HYBRID (owner ruling).** Saved parser output (`state_trace.parser_raw`, a JSON object whose
domain is a taxonomy subject) fixes the subject, as a turn's domain does; otherwise the model
picks the subject. Leaf, ask split, language and product words are the CA-2 labeller's rules
(`labeller.parse_labels`, `labeller._write_items`), reused as they are. Only this era needs the
model to name the bot's OUTCOME too, from the reply, in a closed list; with no bot reply the
outcome is `no_reply` (crew ruling Q3) or, staff only, `handover`.

**Nothing anyone wrote is stored.** Message and reply are read into the prompt for one call;
facts get codes, ids and the label audit; the run table gets counts and tokens.

The model and the resolver are passed in (`chat`, `resolve`), as in the labeller, so the tests
need no provider. `chat` may return the provider's result object (its usage lands on the run
row) or plain content.
"""
from __future__ import annotations

import json
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable, Iterable

from sqlalchemy import and_, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from app.models.chat_facts import HISTORY_ERAS, ChatAskFact, ChatAskItem, ChatBackfillRun
from app.models.chat_history import ChatHistory
from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot.analytics import facts as facts_mod
from app.services.chatbot.analytics import labeller as lab

logger = logging.getLogger(__name__)

#: Recorded on every row and run; bump when the prompt or the rules change.
PROMPT_VERSION = "ca4-1"

#: What the model may call the bot's reply. `missed`, `phantom` and `technical` are facts only a
#: trace holds and are never guessed from text.
MODEL_OUTCOMES: tuple[str, ...] = (
    "answered",
    "answered_empty",
    "clarified",
    "refused_policy",
    "cannot_do",
    "handover",
    "noise",
)

#: Set by the rules, never by the model.
NO_REPLY = "no_reply"
HANDOVER = "handover"

#: The outcomes that are failures in this era (the others are trace-only).
_FAILURE_OUTCOMES = ("cannot_do",)

#: The saved parser output's fields (crew data, 9 Oct 2026).
PARSER_DOMAIN_KEY = "domain_hint"
PARSER_INTENT_KEY = "intent_hint"

#: A staff member typing in the thread (Respond's own sender vocabulary).
STAFF_SENDER = "user"

#: Malaysia is UTC+8 all year; `sent_at` is stored as naive UTC.
_MYT_OFFSET = timedelta(hours=8)

#: Token sizes per ask for `estimate` (the card's measured sizes): message + hint + reply +
#: neighbour, the cached instructions shared out over a batch, and the JSON answer.
_TOKENS_PER_ASK_IN = 130
_TOKENS_INSTRUCTIONS = 3000
_TOKENS_PER_ASK_OUT = 50

_H = ChatHistory.__table__
_TURNS = ChatbotTurn.__table__
_FACTS = ChatAskFact.__table__
_ITEMS = ChatAskItem.__table__

ChatFn = Callable[[list[dict], dict], Any]


# --------------------------------------------------------------------------- eras


def era_window(era: str) -> tuple[datetime, datetime]:
    """`[start, end)` of an era as naive UTC, the way `sent_at` is stored."""
    if era not in HISTORY_ERAS:
        raise ValueError(f"unknown era {era!r}; one of {', '.join(HISTORY_ERAS)}")
    first, after = HISTORY_ERAS[era]
    return (datetime.combine(first, time()) - _MYT_OFFSET, datetime.combine(after, time()) - _MYT_OFFSET)


# --------------------------------------------------------------------------- selection


def _eligible(era: str):
    """The WHERE of an era's eligible messages (B1, B2), before "already has a fact"."""
    start, end = era_window(era)
    earlier = aliased(ChatHistory)
    traced = select(_TURNS.c.id).where(
        _TURNS.c.contact_respond_id == _H.c.contact_id,
        _TURNS.c.message_id == _H.c.message_id,
        _TURNS.c.is_test.is_(False),
    )
    duplicate = select(earlier.id).where(
        earlier.type == "incoming",
        earlier.contact_id == _H.c.contact_id,
        earlier.id < _H.c.id,
        or_(
            and_(_H.c.message_id.isnot(None), earlier.message_id == _H.c.message_id),
            and_(
                _H.c.message_id.is_(None),
                earlier.message_id.is_(None),
                earlier.message == _H.c.message,
                func.date_trunc("second", earlier.sent_at) == func.date_trunc("second", _H.c.sent_at),
            ),
        ),
    )
    return [
        _H.c.type == "incoming",
        _H.c.sent_at >= start,
        _H.c.sent_at < end,
        ~traced.exists(),
        ~duplicate.exists(),
    ]


def _has_fact(*, stale_below: int | None = None):
    """A fact exists for the message (optionally: one labelled under the current version)."""
    query = select(_FACTS.c.id).where(_FACTS.c.history_message_id == _H.c.id, _FACTS.c.ask_seq == 1)
    if stale_below is not None:
        query = query.where(_FACTS.c.taxonomy_version >= stale_below)
    return query.exists()


def count_eligible(db: Session, era: str) -> int:
    return int(db.execute(select(func.count()).select_from(_H).where(*_eligible(era))).scalar() or 0)


def pending(db: Session, era: str, *, relabel: bool = False, limit: int | None = None) -> list[int]:
    """The ids still to read, grouped by contact then time (the order batches are cut in).

    `relabel` also returns messages whose fact was labelled under an older taxonomy version.
    """
    version = lab.load_taxonomy(db).version if relabel else None
    query = (
        select(_H.c.id)
        .where(*_eligible(era), ~_has_fact(stale_below=version))
        .order_by(_H.c.contact_id, _H.c.sent_at, _H.c.id)
    )
    if limit is not None:
        query = query.limit(limit)
    return [int(r[0]) for r in db.execute(query)]


# --------------------------------------------------------------------------- one message


@dataclass(frozen=True)
class Message:
    """One incoming message and what the rules know about it. Lives for one batch."""

    id: int
    contact: str
    channel: str
    sent_at: datetime
    text: str
    is_media: bool
    parser_domain: str | None
    parser_intent: str | None
    reply: str
    forced_outcome: str | None
    shown_ids: tuple[str, ...]


def parser_hint(state_trace: Any, subjects: Iterable[str]) -> tuple[str | None, str | None]:
    """`(domain, intent)` of the saved parser output, when it is a JSON object whose domain is
    a taxonomy subject; `(None, None)` otherwise."""
    raw = state_trace.get("parser_raw") if isinstance(state_trace, dict) else None
    if not isinstance(raw, dict):
        return None, None
    domain = raw.get(PARSER_DOMAIN_KEY)
    if not isinstance(domain, str) or domain not in set(subjects):
        return None, None
    intent = raw.get(PARSER_INTENT_KEY)
    return domain, intent if isinstance(intent, str) and intent else None


def _reply_rows(db: Session, row: Any) -> list[Any]:
    """The outgoing rows answering `row` (B3): same execution id, else the contact's outgoing
    rows after it and before its next incoming row."""
    columns = (_H.c.message, _H.c.result, _H.c.sender_source)
    if row.turn_id:
        linked = db.execute(
            select(*columns)
            .where(_H.c.type == "outgoing", _H.c.contact_id == row.contact_id, _H.c.turn_id == row.turn_id)
            .order_by(_H.c.sent_at, _H.c.id)
        ).all()
        if linked:
            return linked
    after = tuple_(_H.c.sent_at, _H.c.id) > tuple_(row.sent_at, row.id)
    next_incoming = db.execute(
        select(_H.c.sent_at, _H.c.id)
        .where(_H.c.type == "incoming", _H.c.contact_id == row.contact_id, after)
        .order_by(_H.c.sent_at, _H.c.id)
        .limit(1)
    ).first()
    query = select(*columns).where(_H.c.type == "outgoing", _H.c.contact_id == row.contact_id, after)
    if next_incoming is not None:
        query = query.where(tuple_(_H.c.sent_at, _H.c.id) < tuple_(next_incoming.sent_at, next_incoming.id))
    return db.execute(query.order_by(_H.c.sent_at, _H.c.id)).all()


def _shown_ids(rows: list[Any]) -> tuple[str, ...]:
    """Product ids the bot's reply rows offered (`result` items, B6). Only the item's own
    `uuid`, and only for `entity_type = product`; checked against `products` when written."""
    ids: list[str] = []
    for r in rows:
        for item in r.result if isinstance(r.result, list) else []:
            if not isinstance(item, dict) or item.get("entity_type") != "product":
                continue
            try:
                ids.append(str(uuid.UUID(str(item.get("uuid")))))
            except ValueError:
                continue
    return tuple(dict.fromkeys(ids))


def _load(db: Session, ids: list[int], subjects: set[str]) -> list[Message]:
    rows = db.execute(select(_H).where(_H.c.id.in_(ids))).all()
    by_id = {int(r.id): r for r in rows}
    out: list[Message] = []
    for history_id in ids:
        row = by_id.get(history_id)
        if row is None:
            continue
        replies = _reply_rows(db, row)
        bot = [r for r in replies if r.sender_source != STAFF_SENDER]
        if bot:
            forced = None
        elif replies:
            forced = HANDOVER
        else:
            forced = NO_REPLY
        domain, intent = parser_hint(row.state_trace, subjects)
        text = row.message or ""
        out.append(
            Message(
                id=history_id,
                contact=row.contact_id,
                channel=row.channel or "whatsapp",
                sent_at=row.sent_at,
                text=text,
                is_media=bool(row.media_url) or not text.strip(),
                parser_domain=domain,
                parser_intent=intent,
                reply="\n".join(r.message or "" for r in bot)[: lab.REPLY_CHARS],
                forced_outcome=forced,
                shown_ids=_shown_ids(bot),
            )
        )
    return out


# --------------------------------------------------------------------------- prompt


_INSTRUCTIONS = """You label old customer messages sent to a WhatsApp sales bot of a \
building-supplies distributor, and say how the bot answered each one. Each input row is one \
message, with the start of the bot's reply to it (empty when the bot did not reply).

For every row return one label with the row's key:
- asks: the distinct questions in the message, in order. When the row has a subject, the FIRST \
ask keeps it; when its subject is null, give the first ask the subject it belongs to (a subject \
code below). Pick each ask's leaf from its subject's codes below. Add a further ask only when the \
message plainly asks a second, different thing. If no leaf fits, use the "none of the above" \
code of that subject.
- confidence: 0 to 1, how sure you are of the leaf.
- product_words: the words in the message that name one product (a code or a product's name), \
each with how sure you are that it names a product. Never invent one.
- language: en, ms, zh, mixed (two languages in one message) or other.
- outcome: how the bot's reply answered the message: answered (it gave what was asked), \
answered_empty (it looked and found nothing), clarified (it asked the customer something back), \
refused_policy (it said the customer may not see this), cannot_do (it said it cannot do this, or \
offered to pass it to a person because it could not), handover (a person took over), noise (the \
message asks nothing: a greeting, thanks, an emoji).
Messages from the same customer are grouped and in time order; use the earlier ones to read a \
short follow-up.

Taxonomy (subject: leaves):"""


def json_schema() -> dict[str, Any]:
    """The labeller's strict schema plus the outcome of each message."""
    schema = json.loads(json.dumps(lab.json_schema()))
    label = schema["properties"]["labels"]["items"]
    label["properties"]["outcome"] = {"type": "string", "enum": list(MODEL_OUTCOMES)}
    label["required"] = [*label["required"], "outcome"]
    return schema


def build_messages(batch: list[Message], taxonomy: lab.Taxonomy) -> list[dict]:
    rows = [
        {
            "key": str(m.id),
            "subject": m.parser_domain,
            "intent": m.parser_intent,
            "message": m.text,
            "bot_reply_start": m.reply,
        }
        for m in batch
    ]
    return [
        {"role": "system", "content": f"{_INSTRUCTIONS}\n{lab._taxonomy_block(taxonomy)}"},
        {"role": "user", "content": json.dumps({"rows": rows}, ensure_ascii=False)},
    ]


def _entries(content: str) -> dict[str, dict]:
    """The model's label entries by key; the first entry for a key wins."""
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return {}
    labels = data.get("labels") if isinstance(data, dict) else None
    out: dict[str, dict] = {}
    for entry in labels if isinstance(labels, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("key"), str):
            out.setdefault(entry["key"], entry)
    return out


def parse(content: str, batch: list[Message], taxonomy: lab.Taxonomy) -> dict[int, tuple[lab.Label, str]]:
    """`{history id: (label, outcome)}` for the rows the answer settles. A row is left out when
    its outcome is outside `MODEL_OUTCOMES` (unless the rules set it), its subject is free and
    the model's is not a known subject, or the labeller's rules reject it."""
    entries = _entries(content)
    inputs: list[lab.AskInput] = []
    for m in batch:
        entry = entries.get(str(m.id))
        if entry is None:
            continue
        subject = m.parser_domain
        if subject is None:
            asks = entry.get("asks")
            first = asks[0] if isinstance(asks, list) and asks and isinstance(asks[0], dict) else {}
            subject = first.get("subject")
            if subject not in taxonomy.subjects:
                continue
        inputs.append(
            lab.AskInput(
                key=str(m.id), subject=subject, forced_leaf=None, text="", domain=subject,
                intent=m.parser_intent, outcome=None, reply="",
            )
        )
    labels = lab.parse_labels(content, inputs, taxonomy)
    out: dict[int, tuple[lab.Label, str]] = {}
    for m in batch:
        label = labels.get(str(m.id))
        if label is None:
            continue
        outcome = m.forced_outcome or entries[str(m.id)].get("outcome")
        if outcome not in MODEL_OUTCOMES and outcome not in (NO_REPLY, HANDOVER):
            continue
        out[m.id] = (label, outcome)
    return out


def _noise_label(taxonomy: lab.Taxonomy) -> lab.Label:
    leaf = lab.NOISE if lab.NOISE in taxonomy.leaf_codes(lab.GENERAL) else lab._fallback(lab.GENERAL, taxonomy)
    return lab.Label("other", (lab.AskLabel(lab.GENERAL, leaf, 1.0),))


# --------------------------------------------------------------------------- writing


def _domain(subject: str) -> str | None:
    return None if subject == lab.GENERAL else subject


def _write(
    db: Session,
    m: Message,
    label: lab.Label,
    outcome: str,
    taxonomy: lab.Taxonomy,
    *,
    model: str,
    resolve: lab.ResolveFn,
    now: datetime,
) -> int:
    """The message's fact rows (one per ask) and items. Replaces an older label of the same
    message (`--relabel`). Returns the asks written. Does not commit."""
    db.execute(_FACTS.delete().where(_FACTS.c.history_message_id == m.id))
    who = facts_mod._who(db, m.contact)
    common = {
        "source": "history_backfill",
        "turn_id": None,
        "history_message_id": m.id,
        "contact_respond_id": m.contact,
        "contact_id": who["contact_id"],
        "customer_id": who["customer_id"],
        "company_id": who["company_id"],
        "audience": who["audience"],
        "contact_type": who["contact_type"],
        "asked_at": m.sent_at.replace(tzinfo=timezone.utc),
        "channel": m.channel,
        "outcome": outcome,
        "failure_reason": outcome if outcome in _FAILURE_OUTCOMES else None,
        "tools": [],
        "is_media": m.is_media,
        "taxonomy_version": taxonomy.version,
        "label_model": model,
        "label_prompt_version": PROMPT_VERSION,
        "labelled_at": now,
        "language": label.language,
    }
    shown = []
    if m.shown_ids:
        shown = [
            str(r[0])
            for r in db.execute(select(facts_mod._PRODUCTS.c.id).where(facts_mod._PRODUCTS.c.id.in_(m.shown_ids)))
        ]
    for seq, ask in enumerate(label.asks, start=1):
        fact_id = str(uuid.uuid4())
        db.execute(
            pg_insert(_FACTS).values(
                id=fact_id,
                ask_seq=seq,
                domain=_domain(ask.subject),
                intent=m.parser_intent if seq == 1 else None,
                topic_code=ask.leaf,
                label_confidence=ask.confidence,
                **common,
            )
        )
        lab._write_items(db, fact_id, ask, m.contact, resolve)
        if seq == 1 and shown:
            db.execute(
                pg_insert(_ITEMS).on_conflict_do_nothing(),
                [
                    {
                        "id": str(uuid.uuid4()), "ask_id": fact_id, "kind": "product", "ref_id": ref,
                        "role": "shown", "resolved_by": "history_result",
                    }
                    for ref in shown
                ],
            )
    return len(label.asks)


# --------------------------------------------------------------------------- the run


def estimate(db: Session, era: str, *, batch_size: int = lab.BATCH_SIZE, relabel: bool = False) -> dict[str, Any]:
    """Counts and projected tokens for an era. No model call, no write."""
    eligible = count_eligible(db, era)
    todo = len(pending(db, era, relabel=relabel))
    calls = math.ceil(todo / batch_size) if todo else 0
    return {
        "era": era,
        "eligible": eligible,
        "pending": todo,
        "calls": calls,
        "tokens_in": todo * _TOKENS_PER_ASK_IN + calls * _TOKENS_INSTRUCTIONS,
        "tokens_out": todo * _TOKENS_PER_ASK_OUT,
    }


def _usage(result: Any) -> tuple[str, int, int, int]:
    content = getattr(result, "content", result)
    return (
        content if isinstance(content, str) else "",
        int(getattr(result, "prompt_tokens", 0) or 0),
        int(getattr(result, "completion_tokens", 0) or 0),
        int(getattr(result, "cached_tokens", 0) or 0),
    )


def run(
    db: Session,
    *,
    era: str,
    chat: ChatFn,
    resolve: lab.ResolveFn,
    model: str,
    limit: int | None = None,
    batch_size: int = lab.BATCH_SIZE,
    relabel: bool = False,
) -> dict[str, Any]:
    """Backfill one era: one model call and one commit per batch, the run row updated in the
    same commit, so a stop loses at most one batch and a rerun picks up what is left."""
    era_window(era)
    taxonomy = lab.load_taxonomy(db)
    if not taxonomy.has_leaves:
        return {"skipped": "no_leaves", "messages_read": 0, "messages_skipped": 0, "asks_written": 0}
    subjects = set(taxonomy.subjects)

    record = ChatBackfillRun(
        id=str(uuid.uuid4()),
        era=era,
        status="running",
        messages_eligible=count_eligible(db, era),
        model=model,
        prompt_version=PROMPT_VERSION,
        taxonomy_version=taxonomy.version,
        row_limit=limit,
    )
    db.add(record)
    db.commit()

    ids = pending(db, era, relabel=relabel, limit=limit)
    try:
        for start in range(0, len(ids), batch_size):
            batch = _load(db, ids[start : start + batch_size], subjects)
            now = datetime.now(timezone.utc)
            settled: dict[int, tuple[lab.Label, str]] = {
                m.id: (_noise_label(taxonomy), "noise") for m in batch if not m.text.strip()
            }
            asked = [m for m in batch if m.text.strip()]
            if asked:
                record.calls += 1
                try:
                    result = chat(build_messages(asked, taxonomy), json_schema())
                except Exception:  # noqa: BLE001 - one failed call skips its batch, not the run
                    logger.warning("chat backfill: a model call failed; its batch waits for the next run", exc_info=True)
                    record.failed_calls += 1
                    asked = []
                    result = ""
                content, tokens_in, tokens_out, tokens_cached = _usage(result)
                record.tokens_in += tokens_in
                record.tokens_out += tokens_out
                record.tokens_cached += tokens_cached
                if asked:
                    settled.update(parse(content, asked, taxonomy))
            for m in batch:
                if m.id not in settled:
                    record.messages_skipped += 1 if m in asked else 0
                    continue
                label, outcome = settled[m.id]
                record.asks_written += _write(
                    db, m, label, outcome, taxonomy, model=model, resolve=resolve, now=now
                )
                record.messages_read += 1
            record.last_history_id = max(m.id for m in batch) if batch else record.last_history_id
            db.commit()
    except Exception as exc:
        db.rollback()
        record.status = "failed"
        record.error = type(exc).__name__
        record.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise
    record.status = "done"
    record.finished_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "skipped": None,
        "run_id": record.id,
        "messages_read": record.messages_read,
        "messages_skipped": record.messages_skipped,
        "asks_written": record.asks_written,
        "calls": record.calls,
        "failed_calls": record.failed_calls,
    }
