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

from collections import Counter
from datetime import date, datetime
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

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
    Miss,
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

def local_date(now: datetime) -> date:
    return now.astimezone(ZoneInfo("Asia/Kuala_Lumpur")).date() if now.tzinfo else now.date()


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
    now: datetime | None = None,
) -> tuple[ConversationState, list[Action]]:
    """Apply one parser output to the state; one Action per ask (section 3).

    `can_offer_handover` is False for staff and for a contact whose escalation is barred
    (`turn/state.offers_escalation`, owner rulings #1262 slice 11 and ESCALATION-CONTROL):
    their second miss re-asks instead of offering a hand-over."""
    st = state.model_copy(deep=True)
    st.turn_no = state.turn_no + 1
    language = out.language
    today = local_date(now) if now is not None else None
    actions: list[Action] = []

    if out.escalation is not None and out.escalation.requested:
        st.pending = None
        return st, [HandOver(ask_index=0, team_key=out.escalation.team_key)]

    if out.clarify.needed or not out.asks:
        about = out.clarify.about or "other"
        if st.pending is not None:
            # A garbled answer to the open question re-asks it with its real options and
            # counts a MISS (two -> the hand-over offer), never an option-less new question.
            act = _miss(st, 0, st.pending, _Miss(about), language, can_offer_handover)
        else:
            act = _clarify(st, 0, about, out.clarify.phrase, language, options=_options_for(st, about))
        st.pending = act.pending
        return st, [act]

    first_question: PendingQuestion | None = None
    pending_in = st.pending
    answered_pending = False
    # T-keys name the topics MEMORY printed, not ones an earlier ask of this message pushed.
    printed_topics = [t.model_copy(deep=True) for t in st.topics]
    for i, ask in enumerate(out.asks):
        held_before, topics_before = st.held.model_copy(deep=True), list(st.topics)
        try:
            action, answered = _one_ask(
                st, i, ask, pending_in, out, resolver, policy, language, today, printed_topics
            )
        except _Miss as miss:
            st.held, st.topics = held_before, topics_before
            action = _miss(st, i, pending_in, miss, language, can_offer_handover)
        else:
            answered_pending = answered_pending or answered
            action = _repeat_is_a_miss(st, action, pending_in, language, can_offer_handover)
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
    today: date | None,
    printed_topics: list[Held],
) -> tuple[Action, bool]:
    """(action, answered_pending). Mutates `st` (held/topics) only on success."""
    if ask.act == "unclear":
        about = out.clarify.about or "other"
        if pending is not None:
            raise _Miss(about)
        return _clarify(st, i, about, out.clarify.phrase, language, options=_options_for(st, about)), False

    if ask.act == "small_talk":
        return SmallTalk(ask_index=i), False

    if ask.act == "answers_bot_question":
        return _answer(st, i, ask, pending, resolver, policy, language, today)

    if ask.act == "switch_back":
        printed = topic_index(ask.switch_back_to, printed_topics)
        if printed is None:
            raise _Miss("which_held")
        back = printed_topics[printed]
        idx = next((j for j, t in enumerate(st.topics) if t == back), None)
        rest = [t for j, t in enumerate(st.topics) if j != idx]
        st.topics = push_topic(rest, st.held)
        st.held = back.model_copy(deep=True)
        qs = _Qs()
        held = _apply_fields(st, st.held, ask, resolver, policy, language, i, qs, refine=True, today=today)
        return _commit(st, i, held, ask, qs), False

    if ask.act == "new_topic":
        if i > 0 or not st.held.is_empty():
            st.topics = push_topic(st.topics, st.held)
        qs = _Qs()
        held = _apply_fields(st, Held(), ask, resolver, policy, language, i, qs, refine=False, today=today)
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
    qs = _Qs()
    base = st.held
    drilled = False
    if row is not None and base.report is not None and _is_report_row(row):
        base, drilled = _drill(st, base, row, ask, language, i, qs)
    held = _apply_fields(st, base, ask, resolver, policy, language, i, qs, refine=True, today=today)
    if row is not None and not drilled:
        held = apply_payload(held, row.payload)
    if drilled and ask.report is None and not qs:
        _plan_report(st, held, resolver, language, i, qs, today)
    action = _commit(st, i, held, ask, qs)
    if isinstance(action, Lookup):
        action.row = row
    return action, False


def _is_report_row(row: Option) -> bool:
    from app.services.chatbot.pv2 import reports

    return reports.ROW_PAYLOAD in row.payload


def _drill(
    st: ConversationState, held: Held, row: Option, ask: Ask, language: str, i: int, qs: list[ClarifyAct]
) -> tuple[Held, bool]:
    """Section 5: "the 2nd one" on a report drills into that row (`reports.drill_row`): the
    held spec narrows to the row, its filter refs ride on `held.refs`. A row the report
    cannot narrow by says v1's own line instead (`DrillRefused`), held unchanged."""
    from app.services.chatbot.pv2 import reports

    try:
        spec, extra = reports.drill_row(held.report, row, incoming=ask.report)
    except reports.DrillRefused as exc:
        qs.append(_clarify(st, i, "other", None, language, options=[], asked=exc.line))
        return held, False
    out = held.model_copy(deep=True)
    out.report = spec
    for dim, refs in (extra or {}).items():
        out.refs[dim] = [
            ResolvedRef(kind=str(_get(r, "kind") or dim), key=str(_get(r, "key")), label=str(_get(r, "label")))
            for r in refs
        ]
    return out, True


def _commit(st: ConversationState, i: int, held: Held, ask: Ask, qs: list[ClarifyAct]) -> Action:
    """Held becomes this ask's subject; the first open question (if any) is the action,
    else a Lookup."""
    st.held = held
    if qs:
        return qs[0]
    return Lookup(
        ask_index=i, domain=held.domain, held=held.model_copy(deep=True), ask=ask,
        misses=list(getattr(qs, "misses", [])),
    )


class _Qs(list):
    """The questions an ask raised, plus the list items that found nothing."""

    def __init__(self) -> None:
        super().__init__()
        self.misses: list[Miss] = []


#: A reference found under a different kind than the parser named answers in the domain of
#: the data actually found (a bare number the parser called a container that is a delivery).
DOMAIN_BY_FOUND_KIND: dict[str, str] = {
    "delivery_order": "order", "sales_order": "order", "customer_po_ref": "order",
    "inbound_shipment": "incoming",
    "purchase_order": "purchase_order", "purchase_request": "purchase_order",
}


def _answer(
    st: ConversationState,
    i: int,
    ask: Ask,
    pending: PendingQuestion | None,
    resolver: ResolverLike,
    policy: ClarifyPolicy,
    language: str,
    today: date | None,
) -> tuple[Action, bool]:
    if pending is None:
        raise _Miss("other")
    key = ask.answer.option_key if ask.answer is not None else None
    free = ask.answer.free_value if ask.answer is not None else None
    picked = next((o for o in pending.options if o.key == key), None) if key is not None else None
    if picked is None and key is not None and pending.kind == "clarify_entity":
        # A candidate question is keyed by position; its code (the payload key) still picks.
        picked = next((o for o in pending.options if o.payload.get("key") == key), None)
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
    qs = _Qs()
    if "report" in picked.payload and held.report is not None:
        _plan_report(st, held, resolver, language, i, qs, today)
    held = _apply_fields(st, held, ask, resolver, policy, language, i, qs, refine=True, today=today)
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
    today: date | None = None,
) -> Held:
    """`base` with this ask's fields applied. An item that needs the user appends its
    question to `qs` and is left out; every other item still applies (section 3: held =
    this ask, then the unresolved items go to the resolver and are asked)."""
    held = base.model_copy(deep=True)
    if ask.domain:
        held.domain = ask.domain
    if ask.portal_form:
        held.domain = "portal_link"  # the field names a portal form
        held.portal_form = ask.portal_form
    if ask.status is not None:
        held.status = ask.status

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
            options = list(getattr(res, "options", None) or [])
            # No candidates: the held product carries no files to pick from, said truly.
            qs.append(
                _clarify(
                    st, i, "attachment_type", None, language,
                    options=options, kind="attachment_type", not_found=not options,
                )
            )
    if ask.period is not None:
        held.period = ask.period
    if ask.report is not None or (held.report is not None and ({"report", "period"} & set(ask.cleared))):
        from app.services.chatbot.pv2 import reports

        held.report = reports.edit_report(held.report if refine else None, ask.report, cleared=ask.cleared)
        if held.report is not None:
            _plan_report(st, held, resolver, language, i, qs, today)
    return held


def _plan_report(
    st: ConversationState,
    held: Held,
    resolver: Any,
    language: str,
    i: int,
    qs: list[ClarifyAct],
    today: date | None,
) -> None:
    """Section 4/5 REPORTS: the held spec's filters through `resolve_report_spec` (when
    the resolver has one), then `reports.plan_report`. Ambiguous filter names, and the ONE
    combined policy question for missing fields (owner ruling 4 Oct: a ranking never
    defaults the metric or the basis), become this ask's question with the lane's own copy
    and options. Settled filter refs ride on `held.refs` under their dimension."""
    from app.services.chatbot.pv2 import reports

    resolve = getattr(resolver, "resolve_report_spec", None)
    try:
        res = resolve(held.report, held=held, profile=None) if resolve is not None else None
    except NotImplementedError:
        res = None
    except AttributeError as exc:
        # RESOLVE forwards to `reports.resolve_report_spec`, not on this build yet: the
        # filters stay unresolved and are said below. Any other AttributeError is a bug.
        if "resolve_report_spec" not in str(exc):
            raise
        res = None
    if res is not None:
        options = [_option(o) for o in (getattr(res, "options", None) or [])]
        if getattr(res, "status", None) == "ambiguous" and options:
            qs.append(_clarify(st, i, "entity", getattr(res, "phrase", None), language, options=options))
            return
        for dim, refs in (getattr(res, "refs", None) or {}).items():
            held.refs[dim] = [
                ResolvedRef(kind=dim, key=str(_get(r, "key")), label=str(_get(r, "label") or _get(r, "key")))
                for r in refs
            ]
    if today is None:
        return
    plan = reports.plan_report(held.report, refs=report_refs(held), today=today)
    if plan.status == "unresolved":
        # A filter no resolver settled to live rows on this build: said truthfully by its
        # dimension's label (never a key), never run unfiltered, never an empty reply, and
        # dropped from the held spec so the line is said once (REPORTS' own `_say` rule).
        from app.services.chatbot.pv2 import report_measures as rm

        labels = ", ".join(dict.fromkeys(rm.dimension_label(d) for d in plan.unresolved))
        held.report = held.report.model_copy(
            update={"filters": [f for f in held.report.filters if f.dimension not in plan.unresolved]}, deep=True
        )
        qs.append(
            _clarify(
                st, i, "other", None, language, options=[],
                asked=clarify_copy.text("filter_unavailable", language, labels=labels),
            )
        )
        return
    if plan.status == "ask":
        held.report = plan.spec
        qs.append(
            _clarify(
                st, i, plan.about or "report_measure", None, language,
                options=[_option(o) for o in plan.options],
                asked=plan.reply,
            )
        )


def report_refs(held: Held) -> dict[str, list[dict[str, str]]]:
    """`held.refs` in the shape `reports.plan_report` / `answer_report` read: dimension ->
    [{kind, key, label}]."""
    out: dict[str, list[dict[str, str]]] = {}
    for kind, refs in held.refs.items():
        dim = REPORT_DIMENSION_OF_KIND.get(kind, kind)
        out.setdefault(dim, []).extend({"kind": r.kind, "key": r.key, "label": r.label} for r in refs)
    return out


#: Entity kinds whose report filter dimension has another name (`report_measures`).
REPORT_DIMENSION_OF_KIND = {"warehouse": "location", "product_type": "category"}


def _get(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _option(o: Any) -> Option:
    if isinstance(o, Option):
        return o
    return Option(key=str(_get(o, "key")), label=str(_get(o, "label")), payload=dict(_get(o, "payload") or {}))


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
    for ent in _fold_quantity_only(ask.entities):
        if ask.portal_form and ent.kind == "form":
            continue  # the portal form is the subject; a form lookup would only block it
        has_held = bool(base.refs.get(ent.kind)) if refine else False
        if ent.refers_to_held and not refine:
            # A new ask about "this one": the subject held before this message.
            carried_refs = st.held.refs.get(ent.kind) or []
            if not carried_refs:
                raise _Miss("which_held", nothing_held=True)
            held.refs = set_refs(held.refs, ent.kind, carried_refs, "set")
            if ent.key is None and ent.phrase is None:
                _set_quantity(held, ent)
                continue
            has_held = True
        if ent.refers_to_held and not has_held:
            raise _Miss("which_held", nothing_held=True)
        if ent.op == "clear":
            held.refs = set_refs(held.refs, ent.kind, [], "clear")
            continue
        if ent.refers_to_held and ent.key is None and ent.phrase is None:
            _set_quantity(held, ent)  # "this one": the held subject stays; a restated count replaces
            continue
        if ent.op == "remove" and not has_held:
            raise _Miss("which_held", nothing_held=True)
        to_resolve.append(ent)
    if not to_resolve:
        return held

    results = resolver.resolve_entities(to_resolve, held=held)
    set_kinds: set[str] = set()
    listed = Counter(e.kind for e in to_resolve)
    found = {
        e.kind for e, r in zip(to_resolve, results)
        if getattr(r, "status", "none") == "exact" and getattr(r, "refs", None)
    }
    for ent, res in zip(to_resolve, results):
        status = getattr(res, "status", "none")
        refs = list(getattr(res, "refs", None) or [])
        options = list(getattr(res, "options", None) or [])
        if (
            listed[ent.kind] >= 2
            and ent.kind in found
            and (status == "none" or (status == "ambiguous" and getattr(res, "did_you_mean", False)))
            and isinstance(qs, _Qs)
        ):
            words = ent.phrase or ent.doc_number
            if words:
                qs.misses.append(Miss(kind=ent.kind, phrase=words))
            continue
        if (
            status == "ambiguous"
            and len(options) == 1
            and not getattr(res, "did_you_mean", False)
            and policy.decide("entity", held.domain) == "default"
        ):
            held = apply_payload(held, {**options[0].payload, "op": ent.op, **_qty_payload(ent)})
            continue
        if status == "ambiguous" or (status == "exact" and not refs and options):
            if ent.quantity is not None:
                options = [o.model_copy(update={"payload": {**o.payload, **_qty_payload(ent)}}) for o in options]
            qs.append(_clarify(st, i, "entity", ent.phrase, language, options=options, kind=ent.kind))
            continue
        if status != "exact" or not refs:
            qs.append(
                _clarify(
                    st, i, "entity", ent.phrase or ent.doc_number, language,
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
        if refs[0].kind != ent.kind and refs[0].kind in DOMAIN_BY_FOUND_KIND:
            held.domain = DOMAIN_BY_FOUND_KIND[refs[0].kind]
        if ent.quantity is not None:
            refs = [r.model_copy(update={"quantity": ent.quantity}) for r in refs]
        # Several entities of one kind in one ask are one list: the first "set" replaces
        # what was held, the later ones join it.
        kind = refs[0].kind  # filed under the kind FOUND
        op = "add" if ent.op == "set" and kind in set_kinds else ent.op
        set_kinds.add(kind)
        held.refs = set_refs(held.refs, kind, refs, op)
    return held


def _fold_quantity_only(ents: list[Entity]) -> list[Entity]:
    """A count that names no item, beside exactly one named item of its kind, is that
    item's quantity."""
    out = list(ents)
    for j, e in enumerate(ents):
        if e.quantity is None or e.key is not None or e.phrase is not None or e.refers_to_held:
            continue
        named = [k for k, o in enumerate(out) if k != j and o is not None and o.kind == e.kind
                 and (o.key is not None or o.phrase is not None) and o.quantity is None]
        if len(named) == 1:
            out[named[0]] = out[named[0]].model_copy(update={"quantity": e.quantity})
            out[j] = None  # type: ignore[call-overload]
    return [e for e in out if e is not None]


def _qty_payload(ent: Entity) -> dict[str, Any]:
    return {"quantity": ent.quantity.model_dump()} if ent.quantity is not None else {}


def _set_quantity(held: Held, ent: Entity) -> None:
    """A count with no product named ("14 ada stock") restates the held refs' quantity."""
    if ent.quantity is None:
        return
    held.refs[ent.kind] = [r.model_copy(update={"quantity": ent.quantity}) for r in held.refs.get(ent.kind, [])]


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
                qs.append(
                    _clarify(st, i, "spec_attribute", spec.phrase, language, options=closest, kind="spec", not_found=True)
                )
                continue
        if status != "exact" or not resolved:
            qs.append(
                _clarify(
                    st, i, "spec_attribute", spec.phrase, language,
                    options=options, kind="spec", not_found=status == "none" and not options,
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
    if not_found and not phrase and kind != "attachment_type":
        # Nothing the user typed to name: ask openly rather than print an internal key.
        asked = clarify_copy.question_for(about, None, language, kind=kind)
    if about == "entity":
        # Keyed by position, like a v1 question: the printed number IS the key, and the
        # payload keeps the code it picks.
        options = [
            o.model_copy(update={"key": str(n), "payload": {"key": o.key, **o.payload}})
            for n, o in enumerate(options, start=1)
        ]
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


def _repeat_is_a_miss(
    st: ConversationState,
    action: Action,
    pending_in: PendingQuestion | None,
    language: str,
    can_offer_handover: bool,
) -> Action:
    """The same question asked again (same kind, same option keys) counts as a MISS on
    it, so a repeated which_held (the 11 Oct single-subject rule) reaches the hand-over
    offer like every other short-reply check (section 3)."""
    if not isinstance(action, ClarifyAct) or pending_in is None or action.reask:
        return action
    q = action.pending
    if (
        q.kind != "clarify_which_held"
        or pending_in.kind != q.kind
        or not q.options
        or q.asked != pending_in.asked
        or [o.key for o in q.options] != [o.key for o in pending_in.options]
    ):
        return action
    return _miss(st, action.ask_index, pending_in, _Miss(action.about), language, can_offer_handover)


def record_rows(state: ConversationState, rows: list[Option]) -> ConversationState:
    """The rows the answer just printed, for the next turn's `row_pick` (cap 20)."""
    st = state.model_copy(deep=True)
    st.last_rows = list(rows)[:LAST_ROWS_CAP]
    return st
