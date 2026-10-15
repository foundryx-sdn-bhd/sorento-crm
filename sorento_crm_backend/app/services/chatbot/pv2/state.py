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
SCALAR_PAYLOAD_KINDS = frozenset({"attachment_type", "domain", "spec", "handover", "choice", "row", "carry_scope"})


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
    family: bool = False  # a family key (matched by family_code), not an exact product code
    # A variant a size narrowed from a held family (RG-2b): a follow-up size is checked against the family.
    family_code: str | None = None
    # A product set (`product_sets.id`; the key is its code): answered by its members in the set domains.
    set_id: str | None = None


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
    # Every attachment kind the subject asked for (`attachment_type` is the first of them).
    attachment_types: list[str] = Field(default_factory=list)
    period: Period | None = None
    report: ReportSpec | None = None
    idea_draft_id: str | None = None
    status: str | None = None
    portal_form: str | None = None
    # Stable subject id ("s<n>", from `ConversationState.next_sid`): kept when the subject moves to
    # topics and back. None only on state saved before R2.
    sid: str | None = None

    def is_empty(self) -> bool:
        return not (
            self.domain
            or any(self.refs.values())
            or self.specs
            or self.attachment_type
            or self.attachment_types
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
    more: int = 0  # matches beyond the printed options (a capped menu)
    # The original ask this question interrupted (domain, attachment kinds, quantity): the answer
    # completes it instead of re-running the held subject's own domain.
    deferred: dict | None = None
    # The full original ask (an `Ask` dump) this question interrupted: a pick completes it with the
    # picked entity. `deferred` is read only for state saved before R2.
    origin: dict | None = None
    # The entity (an `Entity` dump, no quantity) whose miss raised a which_held question; None when
    # no entity raised it (a flagged clarify, a switch_back) and for state saved before R-GAPS.
    raised_by: dict | None = None
    # RG-1c: the held kind this question's ask leaves unnamed beside a newly named other one; its carry
    # question follows once this question's pick lands the new item.
    carry: str | None = None


class RegisterEntry(BaseModel):
    """One thing a reply printed (redesign R1): `key` is the code / uuid / number to act on and is
    never rendered; `label` is the printed identifier."""

    id: str  # "R<n>"
    kind: str
    key: str
    label: str
    parent: str | None = None  # the "R<n>" of the entry this one is a child of
    subject: str | None = None  # the printing subject's sid ("s<n>"); `subject_label` gives its current S<n>
    turn: int = 0


class ConversationState(BaseModel):
    v: Literal[1] = 1
    session_started_at: datetime
    last_turn_at: datetime
    turn_no: int = 0
    held: Held = Field(default_factory=Held)
    pending: PendingQuestion | None = None
    topics: list[Held] = Field(default_factory=list)
    last_rows: list[Option] = Field(default_factory=list)
    # The console run (`envelope.test_run_id`) this state belongs to; a new run starts empty.
    run_id: str | None = None
    # What the last two replies printed, newest reply first (`pv2/register.py`); empty for old state.
    register: list[RegisterEntry] = Field(default_factory=list)
    # R5-2: the line product codes of a register document whose card printed none (a sales order), by its key:
    # beside the register, never numbered and never rendered.
    doc_lines: dict[str, list[str]] = Field(default_factory=dict)
    # Next subject id to hand out ("s<next_sid>").
    next_sid: int = 1
    # The ask the last PV2 Lookup answered (an `Ask` dump); a v1 picker that Lookup raised keeps it as origin.
    last_lookup_ask: dict | None = None
    # R4a: what the last reply did (MEMORY's LAST REPLY): "answered <domain>", "asked <question kind>",
    # "refused <what>" or "not found <words>"; None for small talk, a hand-over and state saved before it.
    last_reply: str | None = None
    # R6 B: the records the question the last pick answered offered, so "the 150 one" can name another of them;
    # empty when the last turn answered no question.
    last_menu: list[ResolvedRef] = Field(default_factory=list)


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
    free_kind: str | None = None  # R5-4: the v1 roster's entity kind a free value answers (bridged as typed)
    row: Option | None = None
    misses: list[Miss] = Field(default_factory=list)
    requantify: bool = False
    # Option keys of a v1 pick the reply chose in full ("all"): every position is passed to v1.
    pick_all: list[str] = Field(default_factory=list)
    pending_v1_kind: str | None = None  # the v1 question kind a pick answered


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
    # The loop cap's closing line (RG-1b): said once, no options, and the open question is cleared.
    closes: bool = False


class SmallTalk(BaseModel):
    type: Literal["small_talk"] = "small_talk"
    ask_index: int
    declined_offer: bool = False


class HandOver(BaseModel):
    type: Literal["hand_over"] = "hand_over"
    ask_index: int
    team_key: str | None = None
    agent_key: str | None = None  # the member (users.id) a staff roster pick named


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
    rolled.register = []
    rolled.last_lookup_ask = None
    rolled.last_reply = None
    rolled.last_menu = []
    rolled.session_started_at = now
    return rolled


def push_topic(topics: list[Held], held: Held) -> list[Held]:
    """`held` onto the front of `topics`, an earlier entry for the same subject dropped, cap 5. The same
    subject is compared without its sid; the earlier topic's sid stays, so register entries still name it."""
    if held.is_empty():
        return list(topics)
    bare = held.model_copy(update={"sid": None})
    same = next((t for t in topics if t.model_copy(update={"sid": None}) == bare), None)
    kept = [t for t in topics if t is not same]
    pushed = held.model_copy(update={"sid": same.sid}, deep=True) if same is not None else held.model_copy(deep=True)
    return [pushed, *kept][:TOPICS_CAP]


def subject_label(state: ConversationState, sid: str | None) -> str | None:
    """The stack position of a subject: S1 = held, S2..S5 = topics newest first; None when it left."""
    if not sid:
        return None
    if state.held.sid == sid:
        return "S1"
    for i, t in enumerate(state.topics):
        if t.sid == sid:
            return f"S{i + 2}"
    return None


def subject_at(state: ConversationState, label: str | None) -> Held | None:
    """The subject an "S<n>" label names, or None (exact compare, never a parse of the user's words)."""
    for n, subject in enumerate([state.held, *state.topics], start=1):
        if label == f"S{n}":
            return subject
    return None


def new_sid(state: ConversationState) -> str:
    """The next subject id; the counter moves on."""
    sid = f"s{state.next_sid}"
    state.next_sid += 1
    return sid


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
    elif kind == "carry_scope":
        if payload.get("clear"):
            out.refs = set_refs(out.refs, payload["clear"], [], "clear")  # "all <kind>s": that kind no longer narrows
    elif kind in SCALAR_PAYLOAD_KINDS:
        pass
    else:
        # A "keys" payload is one family of accounts: one ref per key, one label.
        keys = [str(k) for k in payload.get("keys") or []] or [str(payload.get("key"))]
        refs = [
            ResolvedRef(
                kind=kind,
                key=key,
                label=str(payload.get("label") or key),
                phrase=payload.get("phrase"),
                quantity=Quantity(**payload["quantity"]) if payload.get("quantity") else None,
                set_id=payload.get("set_id"),
            )
            for key in keys
        ]
        out.refs = set_refs(out.refs, kind, refs, payload.get("op") or "set")
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
