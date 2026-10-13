"""PV2 apply_turn (CONTRACT-PV2-13oct.md section 3): ONE function for every domain.

Pure except through `resolver`. Code decides from structured fields only: no word or
fuzzy matching on the user's text (option keys and T-keys are compared EXACTLY; phrases
go to the resolver, which matches against data). Clarify, never guess: anything the
parser output and the data do not settle becomes a question with real options.

Per ask, in order (section 3):
  escalation.requested -> HandOver
  clarify.needed / unclear -> ClarifyAct
  small_talk -> state unchanged
  answers_bot_question -> exact option key of the pending question, its payload applied
  refines_held -> per-field ops against HELD
  new_topic -> held pushed onto topics, held = this ask
  switch_back -> a printed T-key swaps with held
Then the new items go to the resolver: ambiguity -> ClarifyAct, nothing -> truthful
ClarifyAct(not_found), else Lookup. The short-reply safety checks each count one MISS on
the pending question; two misses on the same question offer a hand-over.
"""
from __future__ import annotations

from typing import Any, Literal, Protocol

from app.services.chatbot.pv2 import clarify_copy
from app.services.chatbot.pv2.schema import Ask, Entity, ParserV2Output, Spec
from app.services.chatbot.pv2.state import (
    MISSES_BEFORE_HANDOVER,
    LAST_ROWS_CAP,
    Action,
    ClarifyAct,
    ConversationState,
    HandOver,
    Held,
    Lookup,
    OfferHandover,
    Option,
    PendingQuestion,
    ResolvedRef,
    ResolvedSpec,
    SmallTalk,
    apply_payload,
    push_topic,
    set_refs,
    topic_index,
)

#: The 11 Oct rule (carried + new subject -> which_held): on these domains one subject is
#: answered at a time, so a new entity of a held kind with no `refers_to_held` and op
#: `add` asks which one is meant instead of answering both.
SINGLE_SUBJECT_DOMAINS: frozenset[str] = frozenset({"product_attachment"})

#: Pending kinds whose answer is consumed outside held (ideation, a v1 open question):
#: the pick is passed on to the answer layer on the Lookup.
PASS_THROUGH_PENDING = frozenset({"idea_similar", "idea_missing_field", "idea_multi", "v1_open_question"})

#: Pending kinds whose question is an open one: a free value answers it.
FREE_VALUE_PENDING = frozenset({"idea_missing_field", "v1_open_question"})

#: `Ask.cleared` names (section 1).
CLEARABLE = frozenset({"attachment_type", "period", "report", "specs"})


class ResolverLike(Protocol):
    """The subset of PV2-RESOLVE's `Resolver` (section 4) apply_turn calls. Resolution /
    SpecResolution are read by attribute: `status` ("exact" | "ambiguous" | "none"),
    `refs` / `specs`, `options`, `phrase`."""

    def resolve_entities(self, ents: list[Entity], *, held: Held) -> list[Any]: ...

    def resolve_specs(self, specs: list[Spec], *, held: Held) -> list[Any]: ...

    def resolve_attachment_type(self, key: str | None, *, held: Held) -> Any: ...


class ClarifyPolicy:
    """Ask-or-default rows (PR #1570 shape). Tonight a constant table whose default is
    ask: a single candidate is still asked unless a row says default."""

    def __init__(self, rows: dict[tuple[str, str | None], Literal["ask", "default"]] | None = None):
        self.rows = dict(rows or {})

    def decide(self, about: str, domain: str | None) -> Literal["ask", "default"]:
        return self.rows.get((about, domain)) or self.rows.get((about, None)) or "ask"


DEFAULT_POLICY = ClarifyPolicy()


class _Miss(Exception):
    """A short-reply safety check failed: re-ask (one MISS)."""

    def __init__(self, about: str = "other", *, nothing_held: bool = False):
        super().__init__(about)
        self.about = about
        self.nothing_held = nothing_held


def apply_turn(
    state: ConversationState,
    out: ParserV2Output,
    *,
    resolver: ResolverLike,
    policy: ClarifyPolicy = DEFAULT_POLICY,
    can_offer_handover: bool = True,
) -> tuple[ConversationState, list[Action]]:
    """Apply one parser output to the state; one Action per ask (section 3).

    `can_offer_handover` is False for staff and for a contact whose escalation is barred
    (`turn/state.offers_escalation`, owner rulings #1262 slice 11 and ESCALATION-CONTROL):
    their second miss re-asks instead of offering a hand-over."""
    st = state.model_copy(deep=True)
    st.turn_no = state.turn_no + 1
    language = out.language
    actions: list[Action] = []

    if out.escalation is not None and out.escalation.requested:
        st.pending = None
        return st, [HandOver(ask_index=0, team_key=out.escalation.team_key)]

    if out.clarify.needed or not out.asks:
        about = out.clarify.about or "other"
        act = _clarify(st, 0, about, out.clarify.phrase, language, options=_options_for(st, about))
        st.pending = act.pending
        return st, [act]

    first_question: PendingQuestion | None = None
    pending_in = st.pending
    answered_pending = False
    for i, ask in enumerate(out.asks):
        held_before, topics_before = st.held.model_copy(deep=True), list(st.topics)
        try:
            action, answered = _one_ask(st, i, ask, pending_in, out, resolver, policy, language)
        except _Miss as miss:
            st.held, st.topics = held_before, topics_before
            action = _miss(st, i, pending_in, miss, language, can_offer_handover)
        else:
            answered_pending = answered_pending or answered
        actions.append(action)
        question = getattr(action, "pending", None)
        if question is not None and first_question is None:
            first_question = question
        if isinstance(action, HandOver):
            break

    if first_question is not None:
        st.pending = first_question
    elif answered_pending or any(isinstance(a, (Lookup, HandOver)) for a in actions):
        st.pending = None
    return st, actions


# --------------------------------------------------------------------------- #
# One ask
# --------------------------------------------------------------------------- #


def _one_ask(
    st: ConversationState,
    i: int,
    ask: Ask,
    pending: PendingQuestion | None,
    out: ParserV2Output,
    resolver: ResolverLike,
    policy: ClarifyPolicy,
    language: str,
) -> tuple[Action, bool]:
    """(action, answered_pending). Mutates `st` (held/topics) only on success."""
    if ask.act == "unclear":
        about = out.clarify.about or "other"
        return _clarify(st, i, about, out.clarify.phrase, language, options=_options_for(st, about)), False

    if ask.act == "small_talk":
        return SmallTalk(ask_index=i), False

    if ask.act == "answers_bot_question":
        return _answer(st, i, ask, pending, resolver, policy, language)

    if ask.act == "switch_back":
        idx = topic_index(ask.switch_back_to, st.topics)
        if idx is None:
            raise _Miss("which_held")
        back = st.topics[idx]
        rest = [t for j, t in enumerate(st.topics) if j != idx]
        st.topics = push_topic(rest, st.held)
        st.held = back.model_copy(deep=True)
        qs: list[ClarifyAct] = []
        held = _apply_fields(st, st.held, ask, resolver, policy, language, i, qs, refine=True)
        return _commit(st, i, held, ask, qs), False

    if ask.act == "new_topic":
        if i > 0 or not st.held.is_empty():
            st.topics = push_topic(st.topics, st.held)
        qs = []
        held = _apply_fields(st, Held(), ask, resolver, policy, language, i, qs, refine=False)
        return _commit(st, i, held, ask, qs), False

    # refines_held
    if st.held.is_empty() and not ask.domain and ask.row_pick is None:
        raise _Miss("which_held", nothing_held=True)
    if i > 0:
        st.topics = push_topic(st.topics, st.held)
    row = None
    if ask.row_pick is not None:
        if not (1 <= ask.row_pick <= len(st.last_rows)):
            raise _Miss("other")
        row = st.last_rows[ask.row_pick - 1]
    qs = []
    held = _apply_fields(st, st.held, ask, resolver, policy, language, i, qs, refine=True)
    if row is not None:
        held = apply_payload(held, row.payload)
    action = _commit(st, i, held, ask, qs)
    if isinstance(action, Lookup):
        action.row = row
    return action, False


def _commit(st: ConversationState, i: int, held: Held, ask: Ask, qs: list[ClarifyAct]) -> Action:
    """Held becomes this ask's subject; the first open question (if any) is the action,
    else a Lookup."""
    st.held = held
    if qs:
        return qs[0]
    return Lookup(ask_index=i, domain=held.domain, held=held.model_copy(deep=True), ask=ask)


def _answer(
    st: ConversationState,
    i: int,
    ask: Ask,
    pending: PendingQuestion | None,
    resolver: ResolverLike,
    policy: ClarifyPolicy,
    language: str,
) -> tuple[Action, bool]:
    if pending is None:
        raise _Miss("other")
    key = ask.answer.option_key if ask.answer is not None else None
    free = ask.answer.free_value if ask.answer is not None else None
    picked = next((o for o in pending.options if o.key == key), None) if key is not None else None
    if picked is None:
        if free is not None and pending.kind in FREE_VALUE_PENDING:
            return (
                Lookup(
                    ask_index=i,
                    domain=st.held.domain,
                    held=st.held.model_copy(deep=True),
                    ask=ask,
                    pending_kind=pending.kind,
                    free_value=free,
                ),
                True,
            )
        raise _Miss("other")

    payload_kind = picked.payload.get("kind")
    if pending.kind == "offer_handover" or payload_kind == "handover":
        if picked.payload.get("value") is True:
            return HandOver(ask_index=i, team_key=None), True
        return SmallTalk(ask_index=i), True

    if pending.kind in PASS_THROUGH_PENDING or payload_kind == "choice":
        return (
            Lookup(
                ask_index=i,
                domain=st.held.domain,
                held=st.held.model_copy(deep=True),
                ask=ask,
                picked=picked,
                pending_kind=pending.kind,
                free_value=free,
            ),
            True,
        )

    held = apply_payload(st.held, picked.payload)
    qs: list[ClarifyAct] = []
    held = _apply_fields(st, held, ask, resolver, policy, language, i, qs, refine=True)
    action = _commit(st, i, held, ask, qs)
    if isinstance(action, Lookup):
        action.picked = picked
        action.pending_kind = pending.kind
    return action, True


# --------------------------------------------------------------------------- #
# Fields: entities, specs, scalars
# --------------------------------------------------------------------------- #


def _apply_fields(
    st: ConversationState,
    base: Held,
    ask: Ask,
    resolver: ResolverLike,
    policy: ClarifyPolicy,
    language: str,
    i: int,
    qs: list[ClarifyAct],
    *,
    refine: bool,
) -> Held:
    """`base` with this ask's fields applied. An item that needs the user appends its
    question to `qs` and is left out; every other item still applies (section 3: held =
    this ask, then the unresolved items go to the resolver and are asked)."""
    held = base.model_copy(deep=True)
    if ask.domain:
        held.domain = ask.domain

    for name in ask.cleared:
        if name in CLEARABLE:
            setattr(held, name, [] if name == "specs" else None)

    held = _apply_entities(st, held, base, ask, resolver, policy, language, i, qs, refine=refine)
    held = _apply_specs(st, held, ask.specs, resolver, policy, language, i, qs)

    if ask.attachment_type is not None:
        res = resolver.resolve_attachment_type(ask.attachment_type, held=held)
        status = getattr(res, "status", "none")
        refs = list(getattr(res, "refs", None) or [])
        if status == "exact" and refs:
            held.attachment_type = refs[0].key
        else:
            qs.append(
                _clarify(
                    st, i, "attachment_type", ask.attachment_type, language,
                    options=list(getattr(res, "options", None) or []),
                )
            )
    if ask.period is not None:
        held.period = ask.period
    if ask.report is not None:
        held.report = _merge_report(held.report, ask.report) if refine else ask.report
        _resolve_report(st, held, resolver, language, i, qs)
    return held


def _resolve_report(
    st: ConversationState, held: Held, resolver: Any, language: str, i: int, qs: list[ClarifyAct]
) -> None:
    """Section 4 REPORTS: the held spec through `resolve_report_spec` when the resolver
    has one. A spec the policy table cannot run yet (a missing measure / basis, asked
    together in ONE question) becomes this ask's question with the lane's own copy and
    options; a resolved spec replaces the held one."""
    resolve = getattr(resolver, "resolve_report_spec", None)
    if resolve is None:
        return
    res = resolve(held.report, held=held, profile=None)
    if res is None:
        return
    spec = getattr(res, "spec", None)
    if spec is not None:
        held.report = spec
    if getattr(res, "status", None) in ("ask", "ambiguous", "none"):
        qs.append(
            _clarify(
                st, i, getattr(res, "about", None) or "report_measure", None, language,
                options=list(getattr(res, "options", None) or []),
                asked=getattr(res, "asked", None) or getattr(res, "reply", None),
            )
        )


def _apply_entities(
    st: ConversationState,
    held: Held,
    base: Held,
    ask: Ask,
    resolver: ResolverLike,
    policy: ClarifyPolicy,
    language: str,
    i: int,
    qs: list[ClarifyAct],
    *,
    refine: bool,
) -> Held:
    to_resolve: list[Entity] = []
    for ent in ask.entities:
        has_held = bool(base.refs.get(ent.kind)) if refine else False
        if ent.refers_to_held and not has_held:
            raise _Miss("which_held", nothing_held=True)
        if ent.op == "clear":
            held.refs = set_refs(held.refs, ent.kind, [], "clear")
            continue
        if ent.refers_to_held and ent.key is None and ent.phrase is None:
            continue  # "this one": the held subject stays as it is
        if ent.op == "remove" and not has_held:
            raise _Miss("which_held", nothing_held=True)
        to_resolve.append(ent)
    if not to_resolve:
        return held

    results = resolver.resolve_entities(to_resolve, held=held)
    for ent, res in zip(to_resolve, results):
        status = getattr(res, "status", "none")
        refs = list(getattr(res, "refs", None) or [])
        options = list(getattr(res, "options", None) or [])
        if status == "ambiguous" and len(options) == 1 and policy.decide("entity", held.domain) == "default":
            held = apply_payload(held, {**options[0].payload, "op": ent.op})
            continue
        if status == "ambiguous" or (status == "exact" and not refs and options):
            qs.append(_clarify(st, i, "entity", ent.phrase or ent.key, language, options=options, kind=ent.kind))
            continue
        if status != "exact" or not refs:
            qs.append(
                _clarify(
                    st, i, "entity", ent.phrase or ent.key, language,
                    options=options, kind=ent.kind, not_found=not options,
                )
            )
            continue
        if ent.op == "remove":
            current = {r.key for r in held.refs.get(ent.kind, [])}
            if not current.intersection(r.key for r in refs):
                raise _Miss("which_held")
            held.refs = set_refs(held.refs, ent.kind, refs, "remove")
            continue
        carried = base.refs.get(ent.kind, []) if refine else []
        if (
            refine
            and ent.op == "add"
            and not ent.refers_to_held
            and carried
            and held.domain in SINGLE_SUBJECT_DOMAINS
            and not {r.key for r in carried}.issuperset(r.key for r in refs)
        ):
            options = _which_held_options([*carried, *refs])
            qs.append(_clarify(st, i, "which_held", None, language, options=options, kind=ent.kind))
            continue
        held.refs = set_refs(held.refs, ent.kind, refs, ent.op)
    return held


def _apply_specs(
    st: ConversationState,
    held: Held,
    specs: list[Spec],
    resolver: ResolverLike,
    policy: ClarifyPolicy,
    language: str,
    i: int,
    qs: list[ClarifyAct],
) -> Held:
    clears = [s for s in specs if s.op == "clear"]
    for s in clears:
        held.specs = [h for h in held.specs if s.attribute is not None and h.attribute != s.attribute]
    live = [s for s in specs if s.op != "clear"]
    if not live:
        return held
    results = resolver.resolve_specs(live, held=held)
    for spec, res in zip(live, results):
        status = getattr(res, "status", "none")
        resolved: list[ResolvedSpec] = list(getattr(res, "specs", None) or [])
        options = list(getattr(res, "options", None) or [])
        if status == "ambiguous" and len(options) == 1 and policy.decide("spec_attribute", held.domain) == "default":
            held = apply_payload(held, options[0].payload)
            continue
        if (
            status == "exact"
            and options
            and getattr(res, "inferred", False)
            and policy.decide("spec_attribute", held.domain) == "ask"
        ):
            # Code placed the number on the one attribute whose real range holds it: the
            # policy decides, and tonight's policy asks with the resolver's labelled option
            # (section 4). Without an option there is nothing truthful to print: taken.
            qs.append(_clarify(st, i, "spec_attribute", spec.phrase, language, options=options))
            continue
        if status == "none" and not options:
            closest = list(getattr(res, "closest", None) or [])
            if closest:
                qs.append(_clarify(st, i, "spec_attribute", spec.phrase, language, options=closest, not_found=True))
                continue
        if status != "exact" or not resolved:
            qs.append(
                _clarify(
                    st, i, "spec_attribute", spec.phrase, language,
                    options=options, not_found=status == "none" and not options,
                )
            )
            continue
        for r in resolved:
            if spec.op == "remove":
                held.specs = [h for h in held.specs if h.attribute != r.attribute]
            elif spec.op == "add":
                held.specs = [*held.specs, r]
            else:
                held.specs = [h for h in held.specs if h.attribute != r.attribute] + [r]
    return held


def _merge_report(held: Any, new: Any) -> Any:
    """Section 5: a follow-up edits the held report spec field by field: a field the
    new spec states replaces the held one, a field it leaves null/empty keeps it."""
    if held is None:
        return new
    merged = held.model_copy(deep=True)
    for name, value in new:
        if value is None or value == []:
            continue
        if name == "filters":
            dims = {f.dimension for f in value}
            merged.filters = [f for f in merged.filters if f.dimension not in dims] + list(value)
        else:
            setattr(merged, name, value)
    return merged


# --------------------------------------------------------------------------- #
# Questions
# --------------------------------------------------------------------------- #


def _which_held_options(refs: list[ResolvedRef]) -> list[Option]:
    return [
        Option(
            key=str(n),
            label=r.label,
            payload={"kind": r.kind, "key": r.key, "label": r.label, "op": "set"},
        )
        for n, r in enumerate(refs, start=1)
    ]


def _options_for(st: ConversationState, about: str) -> list[Option]:
    """Real options for a model-raised clarify: the held subjects for which_held, else
    none (an open question; never a guessed list)."""
    if about == "which_held":
        return _which_held_options([r for refs in st.held.refs.values() for r in refs])
    return []


def _clarify(
    st: ConversationState,
    i: int,
    about: str,
    phrase: str | None,
    language: str,
    *,
    options: list[Option],
    kind: str | None = None,
    not_found: bool = False,
    asked: str | None = None,
) -> ClarifyAct:
    asked = asked or clarify_copy.question_for(about, phrase, language, kind=kind, not_found=not_found)
    pending = PendingQuestion(
        kind=f"clarify_{about}",
        asked=asked,
        options=list(options),
        asked_turn=st.turn_no,
        misses=0,
    )
    return ClarifyAct(ask_index=i, about=about, phrase=phrase, pending=pending, not_found=not_found)


def _miss(
    st: ConversationState,
    i: int,
    pending: PendingQuestion | None,
    miss: _Miss,
    language: str,
    can_offer_handover: bool = True,
) -> Action:
    """One MISS: re-ask the pending question with its real options; the second miss on
    the same question offers a hand-over instead."""
    if pending is None:
        asked = clarify_copy.question_for(miss.about, None, language, nothing_held=miss.nothing_held)
        question = PendingQuestion(
            kind=f"clarify_{miss.about}",
            asked=asked,
            options=_options_for(st, miss.about),
            asked_turn=st.turn_no,
            misses=1,
        )
        return ClarifyAct(ask_index=i, about=miss.about, pending=question, reask=True)
    misses = pending.misses + 1
    if can_offer_handover and misses >= MISSES_BEFORE_HANDOVER and pending.kind != "offer_handover":
        offer = PendingQuestion(
            kind="offer_handover",
            asked=clarify_copy.text("offer_handover", language),
            options=clarify_copy.handover_options(language),
            asked_turn=st.turn_no,
            misses=0,
        )
        return OfferHandover(ask_index=i, pending=offer)
    again = pending.model_copy(deep=True)
    again.misses = misses
    return ClarifyAct(
        ask_index=i,
        about=pending.kind.removeprefix("clarify_"),
        pending=again,
        reask=True,
    )


def record_rows(state: ConversationState, rows: list[Option]) -> ConversationState:
    """The rows the answer just printed, for the next turn's `row_pick` (cap 20)."""
    st = state.model_copy(deep=True)
    st.last_rows = list(rows)[:LAST_ROWS_CAP]
    return st
