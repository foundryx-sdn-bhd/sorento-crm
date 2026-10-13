"""PV2 ConversationState (CONTRACT-PV2-13oct.md section 3), its actions, and the pure
helpers around it: load/dump off `respond_contacts.session_vars["pv2"]`, the 4 h session
roll, and the one place an `Option.payload` is applied to `Held`.

No I/O and no text inspection here: every decision reads structured fields only.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal, Union

from pydantic import BaseModel, Field, ValidationError

from app.services.chatbot.pv2.schema import Ask, Comparator, Period, Quantity, ReportSpec

#: Contract section 3: topics newest first, cap 5; last answer rows cap 20.
TOPICS_CAP = 5
LAST_ROWS_CAP = 20
#: A gap longer than this after `last_turn_at` starts a new session.
SESSION_GAP = timedelta(hours=4)
#: After this many misses on the same pending question the bot offers a hand-over.
MISSES_BEFORE_HANDOVER = 2

#: The payload kinds that set a held scalar rather than a ref list.
SCALAR_PAYLOAD_KINDS = frozenset({"attachment_type", "domain", "spec", "handover", "choice", "row"})


class Option(BaseModel):
    """One numbered choice. `key` is what the model must echo back EXACTLY; `label` is
    the only thing a reply prints; `payload` is what picking it sets (section 3):
    `{"kind": <entity kind>, "key": <live code/id>, "label": ..., "op": "set"|"add"}` for a
    ref, `{"kind": "attachment_type"|"domain", "key": ...}`, `{"kind": "spec", "attribute",
    "comparator", "value", "value_to", "unit"}`, `{"kind": "handover", "value": bool}`, or
    `{"kind": "choice", "key": ...}` (an idea / v1 pick with no held effect)."""

    key: str
    label: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ResolvedRef(BaseModel):
    kind: str
    key: str
    label: str
    phrase: str | None = None
    quantity: Quantity | None = None


class ResolvedSpec(BaseModel):
    attribute: str
    comparator: Comparator
    value: str | float | bool
    value_to: float | None = None
    unit: str | None = None


class Held(BaseModel):
    domain: str | None = None
    refs: dict[str, list[ResolvedRef]] = Field(default_factory=dict)
    specs: list[ResolvedSpec] = Field(default_factory=list)
    attachment_type: str | None = None
    period: Period | None = None
    report: ReportSpec | None = None
    idea_draft_id: str | None = None
    status: str | None = None
    portal_form: str | None = None

    def is_empty(self) -> bool:
        return not (
            self.domain
            or any(self.refs.values())
            or self.specs
            or self.attachment_type
            or self.period
            or self.report
            or self.idea_draft_id
        )


class PendingQuestion(BaseModel):
    kind: str
    asked: str
    options: list[Option] = Field(default_factory=list)
    asked_turn: int = 0
    misses: int = 0
    v1_kind: str | None = None


class ConversationState(BaseModel):
    v: Literal[1] = 1
    session_started_at: datetime
    last_turn_at: datetime
    turn_no: int = 0
    held: Held = Field(default_factory=Held)
    pending: PendingQuestion | None = None
    topics: list[Held] = Field(default_factory=list)
    last_rows: list[Option] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Actions: one per ask (section 3). `ask_index` ties an action to its ask.
# --------------------------------------------------------------------------- #


class Miss(BaseModel):
    """An item of a list that found nothing (or only a did-you-mean) while its siblings
    resolved: v1 says it is not found beside the answer for the rest."""

    kind: str
    phrase: str | None = None


class Lookup(BaseModel):
    """Answer this ask from data. `held` is the held subject AFTER this ask applied
    (resolved codes only). `picked` / `free_value` carry an answer to a pending
    question whose kind is answered outside held (idea_*, v1_open_question)."""

    type: Literal["lookup"] = "lookup"
    ask_index: int
    domain: str | None
    held: Held
    ask: Ask | None = None
    picked: Option | None = None
    pending_kind: str | None = None
    free_value: str | None = None
    row: Option | None = None
    misses: list[Miss] = Field(default_factory=list)


class ClarifyAct(BaseModel):
    """Ask the user. `pending` is the question recorded for next turn; `not_found` marks
    a truthful "nothing named <phrase>" (no options to offer); `reask` marks a MISS."""

    type: Literal["clarify"] = "clarify"
    ask_index: int
    about: str
    phrase: str | None = None
    pending: PendingQuestion
    not_found: bool = False
    reask: bool = False
    note: str | None = None


class SmallTalk(BaseModel):
    type: Literal["small_talk"] = "small_talk"
    ask_index: int


class HandOver(BaseModel):
    type: Literal["hand_over"] = "hand_over"
    ask_index: int
    team_key: str | None = None


class OfferHandover(BaseModel):
    type: Literal["offer_handover"] = "offer_handover"
    ask_index: int
    pending: PendingQuestion


Action = Union[Lookup, ClarifyAct, SmallTalk, HandOver, OfferHandover]


# --------------------------------------------------------------------------- #
# Persistence and session roll
# --------------------------------------------------------------------------- #


def new_state(now: datetime) -> ConversationState:
    return ConversationState(session_started_at=now, last_turn_at=now, turn_no=0)


def load_state(raw: Any, now: datetime) -> ConversationState:
    """`session_vars["pv2"]` as a state; anything unreadable (absent, an older shape,
    a hand edit) is a fresh state rather than a failed turn."""
    if isinstance(raw, dict):
        try:
            return ConversationState.model_validate(raw)
        except ValidationError:
            pass
    return new_state(now)


def dump_state(state: ConversationState) -> dict[str, Any]:
    return state.model_dump(mode="json")


def roll_session(state: ConversationState, now: datetime) -> ConversationState:
    """Section 3: a gap over 4 h starts a new session: held moves to `topics[0]` (still
    reachable by switch_back), pending and last_rows clear."""
    if now - state.last_turn_at <= SESSION_GAP:
        return state
    rolled = state.model_copy(deep=True)
    if not rolled.held.is_empty():
        rolled.topics = push_topic(rolled.topics, rolled.held)
    rolled.held = Held()
    rolled.pending = None
    rolled.last_rows = []
    rolled.session_started_at = now
    return rolled


def push_topic(topics: list[Held], held: Held) -> list[Held]:
    """`held` onto the front of `topics`, an identical earlier entry dropped, cap 5."""
    if held.is_empty():
        return list(topics)
    kept = [t for t in topics if t != held]
    return [held.model_copy(deep=True), *kept][:TOPICS_CAP]


def topic_key(index: int) -> str:
    """The printed switch_back key of `topics[index]`: T1 is the newest."""
    return f"T{index + 1}"


def topic_index(key: str | None, topics: list[Held]) -> int | None:
    """The `topics` position a printed T-key names, or None when that key was never
    printed (exact key compare, no parsing of the user's words)."""
    for i in range(len(topics)):
        if key == topic_key(i):
            return i
    return None


# --------------------------------------------------------------------------- #
# Payloads
# --------------------------------------------------------------------------- #


def apply_payload(held: Held, payload: dict[str, Any]) -> Held:
    """What picking an option sets, applied to a copy of `held`."""
    out = held.model_copy(deep=True)
    if isinstance(payload.get("report"), dict) and out.report is not None:
        # A report policy pick (`reports._question`): the fields it names, onto the held spec.
        out.report = out.report.model_copy(update=payload["report"], deep=True)
    kind = payload.get("kind")
    if not kind:
        return out
    if kind == "attachment_type":
        out.attachment_type = payload.get("key")
    elif kind == "domain":
        out.domain = payload.get("key")
    elif kind == "spec":
        spec = ResolvedSpec(
            attribute=payload["attribute"],
            comparator=payload.get("comparator") or "eq",
            value=payload.get("value"),
            value_to=payload.get("value_to"),
            unit=payload.get("unit"),
        )
        out.specs = [s for s in out.specs if s.attribute != spec.attribute] + [spec]
    elif kind in SCALAR_PAYLOAD_KINDS:
        pass
    else:
        ref = ResolvedRef(
            kind=kind,
            key=str(payload.get("key")),
            label=str(payload.get("label") or payload.get("key")),
            phrase=payload.get("phrase"),
            quantity=Quantity(**payload["quantity"]) if payload.get("quantity") else None,
        )
        out.refs = set_refs(out.refs, kind, [ref], payload.get("op") or "set")
    return out


def set_refs(
    refs: dict[str, list[ResolvedRef]], kind: str, new: list[ResolvedRef], op: str
) -> dict[str, list[ResolvedRef]]:
    """Per-kind op (section 3): set = replace the kind, add = append (no duplicates by
    key), remove = drop these keys, clear = drop the kind."""
    out = {k: list(v) for k, v in refs.items()}
    current = out.get(kind, [])
    if op == "set":
        out[kind] = _dedupe(new)
    elif op == "add":
        out[kind] = _dedupe([*current, *new])
    elif op == "remove":
        drop = {r.key for r in new}
        out[kind] = [r for r in current if r.key not in drop]
    elif op == "clear":
        out[kind] = []
    if not out.get(kind):
        out.pop(kind, None)
    return out


def _dedupe(refs: list[ResolvedRef]) -> list[ResolvedRef]:
    seen: set[str] = set()
    out: list[ResolvedRef] = []
    for r in refs:
        if r.key in seen:
            continue
        seen.add(r.key)
        out.append(r)
    return out
