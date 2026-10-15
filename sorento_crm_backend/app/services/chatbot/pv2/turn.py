"""PV2 turn orchestration (CONTRACT-PV2-13oct.md section 6): what the ONE engine hook calls
when `flag.parser_mode` says v2.

    run_turn_v2(ctx) -> TurnV2Result(verdict, focus, direct_reply | None, state)

load + roll state -> history + MEMORY -> parser v2 call -> apply_turn -> answer. The hook then
continues the normal stages with `verdict` + `focus`, or returns `direct_reply` through the
same tail, and persists `dump_state(result.state)` as `session_vars["pv2"]`
(`turn/tail.session_payload` writes `ctx.pv2`). After the v1 stages ran, `after_v1` records a
question the v1 lane asked as the PV2 pending question (`v1_open_question`) so the next
message can answer it by option key.

Everything I/O-shaped arrives on the context as a callable (the parser call, the exchange
loader, the resolver, the report / idea handlers), so this module runs in tests with no DB
and no model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

import logging

from app.services.chatbot.pv2 import answer as answer_mod
from app.services.chatbot.pv2 import clarify_copy, register
from app.services.chatbot.pv2.apply import (
    DEFAULT_POLICY,
    ClarifyPolicy,
    ResolverLike,
    apply_turn,
    shown_row,
    valid_origin,
)
from app.services.chatbot.pv2.history import (
    TIMEZONE,
    MemoryProfile,
    compact_exchanges,
    history_messages,
    load_exchanges,
    render_memory,
    render_user_message,
)
from app.services.chatbot.pv2.schema import ParserV2Output
from app.services.chatbot.pv2.state import (
    Lookup,
    LAST_ROWS_CAP,
    ConversationState,
    Option,
    PendingQuestion,
    dump_state,
    load_state,
    roll_session,
)

#: (history messages, final user message) -> the validated parser output (`pv2/call.py`).
ParseFn = Callable[[list[dict[str, str]], str], ParserV2Output]
#: (session start) -> this session's last exchanges, oldest first (`history.load_exchanges`).
ExchangesFn = Callable[[datetime | None], list[tuple[str, str]]]
#: (exchanges, memory block, text) -> `call.V2Result` (output, usage, dropped): CORE's
#: `call.parse_v2`, which builds the messages itself. When set it is used instead of `parse`.
ParsePartsFn = Callable[[list[tuple[str, str]], str, str], Any]

logger = logging.getLogger(__name__)


@dataclass
class TurnV2Context:
    text: str
    now: datetime
    raw_state: Any
    profile: MemoryProfile
    parse: ParseFn | None
    resolver: ResolverLike
    load_exchanges: ExchangesFn
    parse_parts: ParsePartsFn | None = None
    can_offer_handover: bool = True
    policy: ClarifyPolicy = DEFAULT_POLICY
    base_focus: Any = None
    report_answer: answer_mod.ReportAnswer | None = None
    idea_answer: answer_mod.IdeaAnswer | None = None
    trace: dict[str, Any] = field(default_factory=dict)
    # The contact's granted field-reveal keys; the closed default grants nothing.
    granted_reveals: frozenset[str] = frozenset()
    # Live policy: domain -> escalation team code; and how a miss may end (offer / refer / none).
    domain_teams: dict[str, str] | None = None
    offer_mode: Literal["offer", "refer", "none"] = "offer"
    # Staff only: (team, v1 offer sentence) -> v1's member picker (`staff_roster_for`).
    roster_for: Callable[[str, str], dict[str, Any] | None] | None = None
    # (domain, codes) -> {code: has the thing}, from v1's did-you-mean probe; None = untagged.
    dym_tags: Callable[[str, list[str]], dict[str, bool]] | None = None
    # The console run this turn belongs to; state and history never cross runs.
    test_run_id: str | None = None
    # The previous reply's text (session_vars.user_response): v1's pending keeps no question text.
    last_reply: str | None = None
    # An availability-only stock dealer: v1 asks which one for a partial, never lists them.
    stock_availability_only: bool = False
    # R5-6: the conversation's reply language (session_vars.reply_language, what the engine carries).
    conversation_language: str | None = None


def turn_language(ctx: TurnV2Context, out: Any) -> str:
    """R5-6: this turn's reply language by the engine's one rule (`engine._turn_language`): the language typed this
    turn, else the parse when it reads ms or zh off a line that reads as none, else the conversation's. The words
    the parse holds as data (codes, names) never decide it."""
    from app.services.chatbot.engine import _turn_language

    strip = [str(v) for a in out.asks for e in a.entities for v in (e.phrase, e.key, e.doc_number) if v]
    carried = {"reply_language": ctx.conversation_language}
    return _turn_language(ctx.text, carried, ctx.profile, strip=strip, parsed=out.language)


def local_today(now: datetime) -> date:
    return now.astimezone(ZoneInfo(TIMEZONE)).date() if now.tzinfo else now.date()


def run_turn_v2(ctx: Any) -> answer_mod.TurnV2Result:
    """The engine hook's entry. `ctx` is this module's TurnV2Context, or CORE's
    `hook.TurnV2Context`, which `from_hook` turns into one (DB reads, the pinned v2
    config, RESOLVE's resolver and the report / idea handlers)."""
    if not isinstance(ctx, TurnV2Context):
        return _run_from_hook(ctx)
    return _run(ctx)


def _run(ctx: TurnV2Context, *, v1_pending: Any = None) -> answer_mod.TurnV2Result:
    raw_state = ctx.raw_state
    if ctx.test_run_id and not (isinstance(raw_state, dict) and raw_state.get("run_id") == ctx.test_run_id):
        raw_state = None  # a new console run starts empty
    state_in = roll_session(load_state(raw_state, ctx.now), ctx.now)
    if state_in.pending is None and v1_pending is not None:
        # The question text is the last reply the contact was SHOWN (the loaded exchanges), not
        # `session_vars.user_response`, which can hold an older reply.
        shown = ctx.load_exchanges(state_in.session_started_at)
        reply = shown[-1][1] if shown else ctx.last_reply
        state_in = mirror_v1_pending(state_in, v1_pending, last_reply=reply)
    # Capped here, so CORE's `call.build_messages` and the injected `parse` both send the
    # compact form (a report table in a past reply is what blew the prompt budget).
    exchanges = compact_exchanges(ctx.load_exchanges(state_in.session_started_at))
    messages = history_messages(exchanges)
    user_message = render_user_message(state_in, ctx.profile, ctx.now, ctx.text)
    usage: dict[str, Any] = {}
    if ctx.parse_parts is not None:
        parsed = ctx.parse_parts(exchanges, render_memory(state_in, ctx.profile, ctx.now), ctx.text)
        out = parsed.output
        usage = dict(getattr(parsed, "usage", None) or {})
        dropped = getattr(parsed, "dropped", None) or []
        ctx.trace["dropped_keys"] = [getattr(d, "__dict__", d) for d in dropped]
    else:
        out = ctx.parse(messages, user_message)
    language = turn_language(ctx, out)

    early = _pre_grant_refusal(ctx, out, state_in.pending, state_in.held, register.shown(state_in))
    if early is not None:
        # Nothing was looked up and nothing is kept: the turn changes no state but the last reply (R4a).
        trace = {"acts": [a.act for a in out.asks], "actions": [], "grant_refused": True}
        refused = state_in.model_copy(update={"last_reply": f"refused {early[2]}"})
        return answer_mod.TurnV2Result(None, None, early[0], refused, [], early[1], language, trace)
    extra: dict[str, Any] = {"dym_tags": ctx.dym_tags} if ctx.dym_tags is not None else {}
    state_out, actions = apply_turn(
        state_in,
        out,
        resolver=ctx.resolver,
        policy=ctx.policy,
        can_offer_handover=ctx.can_offer_handover,
        domain_teams=ctx.domain_teams,
        offer_mode=ctx.offer_mode,
        roster_for=ctx.roster_for,
        stock_availability_only=ctx.stock_availability_only,
        now=ctx.now,
        language=language,
        **extra,
    )
    state_out.last_turn_at = ctx.now
    state_out.run_id = ctx.test_run_id
    refusal = _grant_refusal(ctx, actions, out, state_in.held, state_in.pending)
    if refusal is not None:
        trace = {"acts": [a.act for a in out.asks], "actions": [a.type for a in actions], "grant_refused": True}
        # A refused ask leaves no pending, origin or held behind; only the session clock moves, so repeated
        # refusals never roll the session.
        kept = state_in.model_copy(update={"last_turn_at": ctx.now, "last_reply": f"refused {refusal[2]}"})
        return answer_mod.TurnV2Result(None, None, refusal[0], kept, actions, refusal[1], language, trace)
    result = answer_mod.build_result(
        state_out,
        actions,
        out,
        today=local_today(ctx.now),
        pending_in=state_in.pending,
        report_answer=ctx.report_answer,
        idea_answer=ctx.idea_answer,
        base_focus=ctx.base_focus,
        message=ctx.text,
        language=language,
        so_granted=SO_GRANT in (ctx.granted_reveals or frozenset()),
    )
    result.trace.update(
        {
            "pv2_turn": {
                "turn_no": result.state.turn_no,
                "history_exchanges": len(exchanges),
                "language": out.language,
                "route": result.route,
                "pending_kind": result.state.pending.kind if result.state.pending else None,
                "held_domain": result.state.held.domain,
            },
            "memory": user_message,
            "parser_out": out.model_dump(mode="json"),
            **ctx.trace,
        }
    )
    result.usage = usage
    return result


#: Whole-domain grants (v1 `turn_runtime.domain_denial_text`, `engine` sales-report seam).
PO_GRANT = "purchase_orders.placed"
SALES_REPORT_GRANT = "sales_orders.sales_report"
SO_GRANT = "sales_orders.outstanding"
SALES_ORDER_KINDS = frozenset({"sales_order", "customer_po_ref"})
PO_DOMAIN_SUBJECT = {"purchase_order": "purchase orders", "spo_allocation": "purchase order allocation"}


def _is_sales_dataset(spec: Any) -> bool:
    """The dataset as the report lane reads it: a null one takes the policy default."""
    from app.services.chatbot.pv2 import reports

    try:
        ds = reports._dataset_for(spec)
    except Exception:  # noqa: BLE001 - unreadable spec: the closed side
        return True
    return ds is None or ds.key == "sales"


REPORT_QUESTION_KINDS = frozenset({"clarify_report_measure", "clarify_report_period"})


def _judged_asks(out: Any, pending_in: Any, held: Any = None, rows: list[Any] | None = None) -> list[Any]:
    """The asks a grant check judges: this turn's and, when the turn answers the open question, the held
    subject (its domain and report) and the question's original ask: a pick continues both, so a grant
    removed since either was asked still binds, however many questions deep. A pick of a printed row (`rows`,
    the shown rows) is judged as the entity apply makes of it (R4a), so a grant removed since it was printed
    binds too."""
    asks = []
    for ask in getattr(out, "asks", None) or []:
        picked = shown_row(rows or [], ask)
        asks.append(ask if picked is None else ask.model_copy(update={"entities": [picked, *ask.entities]}))
    if pending_in is None or not any(getattr(a, "act", None) == "answers_bot_question" for a in asks):
        return asks
    origin = valid_origin(getattr(pending_in, "origin", None))
    return [*([held] if held is not None else []), *([origin] if origin is not None else []), *asks]


def _pre_grant_refusal(
    ctx: TurnV2Context, out: Any, pending_in: Any, held: Any = None, rows: list[Any] | None = None
) -> tuple[str, str, str] | None:
    """The permission checks that read only the parse and the open question, run BEFORE any
    lookup so a refusal never reveals whether a record exists: a sales order without the SO
    grant, a sales ask (or the answer to a report question) without the sales report grant."""
    granted = ctx.granted_reveals or frozenset()
    asks = _judged_asks(out, pending_in, held, rows)
    # Audience-split as v1: a dealer is refused; staff keep probing the kinds they may see (not-found + roster offer).
    staff = getattr(ctx.profile, "audience", None) == "staff"
    if not staff and SO_GRANT not in granted and any(
        getattr(e, "kind", None) in SALES_ORDER_KINDS for ask in asks for e in getattr(ask, "entities", None) or []
    ):
        from app.services.chatbot.lanes.business.fetch import SO_NOT_ENABLED_MESSAGE

        return SO_NOT_ENABLED_MESSAGE, "clarify", "sales_order"
    if SALES_REPORT_GRANT not in granted and (
        any(getattr(ask, "domain", None) == "sales" for ask in asks)
        or (pending_in is not None and getattr(pending_in, "kind", None) in REPORT_QUESTION_KINDS)
    ):
        from app.services.chatbot.lanes.business import SALES_REPORT_NOT_ENABLED_MESSAGE

        return SALES_REPORT_NOT_ENABLED_MESSAGE, "report", "sales"
    return None


def _grant_refusal(
    ctx: TurnV2Context, actions: list[Any], out: Any, held: Any = None, pending_in: Any = None
) -> tuple[str, str, str] | None:
    """(reply, route, what was refused) when this turn asks for a domain the contact holds no grant for, else
    None. Refused before any lookup or report question, so nothing of the data is read."""
    granted = ctx.granted_reveals or frozenset()
    lookups = [a for a in actions if isinstance(a, Lookup)]
    last = lookups[-1] if lookups else None
    # v1 has no PO refusal for a non-staff contact (its PO list is limited by what the contact may see),
    # so only staff are held to the placed grant here.
    if PO_GRANT not in granted and getattr(ctx.profile, "audience", None) == "staff":
        # Any part of the turn in a PO domain refuses the whole turn (closed side).
        judged = _judged_asks(out, pending_in, held)
        domains = [a.domain for a in lookups] + [getattr(a, "domain", None) for a in judged]
        hit = next((d for d in domains if d in PO_DOMAIN_SUBJECT), None)
        if hit is not None:
            from app.services.chatbot import copy as copy_mod
            from app.services.chatbot.lanes import canned as canned_lanes

            text = canned_lanes.field_grant_denied_text(copy_mod.fallback_copy(), PO_DOMAIN_SUBJECT[hit])
            return text, "clarify", hit
    if SALES_REPORT_GRANT in granted:
        return None
    from app.services.chatbot.pv2 import reports

    running = getattr(held, "report", None)
    for ask in _judged_asks(out, pending_in, held):
        report = getattr(ask, "report", None)
        # Mirror apply: only a refine merges onto the held report, a new topic starts bare.
        base = running if getattr(ask, "act", None) in ("refines_held", "switch_back") else None
        if report is None:
            running = base
            continue
        # Judge the spec this ask will run (held + this refine), never a partial refine.
        merged = reports.edit_report(base, report, cleared=getattr(ask, "cleared", None) or ())
        running = merged
        if ask.domain in answer_mod.REPORT_DOMAINS and merged is not None and _is_sales_dataset(merged):
            from app.services.chatbot.lanes.business import SALES_REPORT_NOT_ENABLED_MESSAGE

            return SALES_REPORT_NOT_ENABLED_MESSAGE, "report", "sales"
    if last is not None and answer_mod._is_report(last) and _is_sales_dataset(last.held.report):
        from app.services.chatbot.lanes.business import SALES_REPORT_NOT_ENABLED_MESSAGE

        return SALES_REPORT_NOT_ENABLED_MESSAGE, "report", "sales"
    return None


def wire(result: answer_mod.TurnV2Result) -> dict[str, Any]:
    """The `session_vars["pv2"]` value to persist."""
    return dump_state(result.state)


def after_v1(
    state: ConversationState,
    *,
    question_kind: str | None,
    option_labels: list[str],
    offer: bool = False,
    asked: str | None = None,
    language: str | None = None,
    rows: list[Option] | None = None,
    option_keys: list[str] | None = None,
) -> ConversationState:
    """After the v1 stages answered a PV2 lookup: a question the v1 lane asked (its pending
    kind and option labels, `contracts.PENDING_KINDS`) becomes the PV2 pending question
    `v1_open_question`, options keyed by position ("1", "2", ...) plus "yes" / "no" for an
    offer, so the model answers it by exact key and the bridge passes the pick back as
    `open_question_answer`. No question -> pending clears. `rows` are the answer's
    printed rows for the next `row_pick`."""
    st = state.model_copy(deep=True)
    if question_kind:
        keys = option_keys or [str(n) for n in range(1, len(option_labels) + 1)]
        options = [
            Option(key=key, label=label, payload={"kind": "choice", "key": key, "v1_kind": question_kind})
            for key, label in zip(keys, option_labels)
        ]
        if offer:
            options += [
                Option(
                    key=key,
                    label=clarify_copy.text(key, language),
                    payload={"kind": "choice", "key": key, "v1_kind": question_kind},
                )
                for key in ("yes", "no")
            ]
        st.pending = PendingQuestion(
            kind="v1_open_question",
            asked=asked or "",
            options=options,
            asked_turn=st.turn_no,
            misses=0,
            v1_kind=question_kind,
        )
        st.last_reply = "asked v1_open_question"  # R4a: the reply asked v1's question, whatever it answered
    elif st.pending is not None and st.pending.kind == "v1_open_question":
        st.pending = None
    if rows is not None:
        st.last_rows = list(rows)[:LAST_ROWS_CAP]
    return st


# --------------------------------------------------------------------------- #
# v1 question mirror
# --------------------------------------------------------------------------- #


def _question_text(reply: str | None, yes_no: bool, labels: list[str] | None = None) -> str:
    """The question v1 printed, read off its own previous reply (v1's Pending keeps no text):
    an offer is the reply's last line; a menu is the last line above its first numbered option,
    and only when that menu IS this pending's (every option label is printed in it) - an older
    reply is never mirrored as the question. Bounded, never the whole reply."""
    from app.services.chatbot.pv2.history import compact_reply

    lines = [ln.strip() for ln in str(reply or "").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return ""
    if yes_no:
        return compact_reply(lines[-1])
    head: list[str] = []
    menu: list[str] = []
    for ln in lines:
        if ln.partition(". ")[0].isdigit():  # one of v1's numbered options
            menu.append(ln)
        elif not menu:
            head.append(ln)
    if not menu:
        # A single-option offer prints no menu: its question is the last line. A pending with
        # several options but a reply that printed none is not the reply that asked it.
        return compact_reply(lines[-1]) if len(labels or []) <= 1 else ""
    if labels and len(menu) != len(labels):
        return ""  # a menu of a different size is not this pending's: stale
    return compact_reply(head[-1]) if head else ""


def _offer_sentence(v1_pending: Any, yes_no: bool) -> str:
    """v1's own escalation offer, worded from the pending's own team (never a reply's)."""
    team = str(getattr(v1_pending, "team", None) or "").replace("_", " ").strip()
    if not (yes_no and team):
        return ""
    return f"Would you like me to escalate to {team} team?"


#: R5-4: v1 rosters whose every position is another copy of the same answer (the promotion's dealer and end-user
#: copy): a pick leaves the other copies pickable, so the roster is mirrored while a position is unanswered.
COPY_ROSTER_KINDS = frozenset({"tier_pick"})


def mirror_v1_pending(
    state: ConversationState, v1_pending: Any, last_reply: str | None = None
) -> ConversationState:
    """The v1 lane's open question (the v1 `Pending` the engine loaded: `kind`, `options`
    dicts with `label`) as the PV2 `v1_open_question`, so MEMORY shows it and the model
    answers it by exact key (`after_v1`'s keys). The v1 engine still holds its own pending
    and consumes the pick through `open_question_answer`."""
    from app.services.chatbot.turn.pending import OFFER_KINDS

    kind = getattr(v1_pending, "kind", None)
    if not kind:
        return state
    # Keyed by v1's own `position` (what v1 `_answer_pending` accepts), legacy `idx` else.
    opts = [o for o in (getattr(v1_pending, "options", None) or []) if isinstance(o, dict) and o.get("label")]
    answered = set((getattr(v1_pending, "payload", None) or {}).get("answered_positions") or [])
    if answered and (kind not in COPY_ROSTER_KINDS or {o.get("position") for o in opts} <= answered):
        return state  # a picker a pick already answered is not asked again (a copy roster: once every copy went)
    yes_no = getattr(v1_pending, "expects", None) == "yes_no"
    if yes_no:
        opts = []  # a yes / no question mirrors only yes and no
    labels = [str(o["label"]) for o in opts]
    keys = [str(o.get("position") or o.get("idx") or n) for n, o in enumerate(opts, start=1)]
    payload = getattr(v1_pending, "payload", None) or {}
    mirrored = after_v1(
        state,
        question_kind=str(kind),
        option_labels=labels,
        option_keys=keys,
        offer=kind in OFFER_KINDS or yes_no or payload.get("escalate_offered") is True,
        asked=str(getattr(v1_pending, "question", None) or getattr(v1_pending, "asked", None) or "")
        or _offer_sentence(v1_pending, yes_no)
        or _question_text(last_reply, yes_no, labels),
    )
    if mirrored.pending is not None and mirrored.pending.origin is None:
        # The ask the Lookup behind this picker answered (an unreadable one is not adopted).
        mirrored.pending.origin = state.last_lookup_ask if valid_origin(state.last_lookup_ask) else None
    if mirrored.pending is not None:
        for option, source in zip(mirrored.pending.options, opts):
            if source.get("entity_type"):
                option.payload["entity_type"] = source["entity_type"]  # R5-4: what a typed answer to it is
    return mirrored


# --------------------------------------------------------------------------- #
# CORE hook adapter
# --------------------------------------------------------------------------- #


def memory_profile(profile: Any, memory_intake: dict[str, Any] | None, *, staff: bool | None = None) -> MemoryProfile:
    """The CONTACT / EARLIER SESSIONS lines from the v1 profile (`turn/state.Profile`) and
    the memory intake (profile facts, closed-session summaries)."""
    from app.services.chatbot.turn.state import is_staff_profile

    intake = memory_intake or {}
    facts = {
        str(f.get("key")): f.get("value")
        for f in (intake.get("profile_facts") or [])
        if isinstance(f, dict) and f.get("key")
    }
    brands = facts.get("usual_brands") or []
    summaries = [s for s in (intake.get("summaries") or []) if isinstance(s, str)]
    return MemoryProfile(
        audience="staff" if (is_staff_profile(profile) if staff is None else staff) else getattr(profile, "tier", None),
        role=facts.get("role") if isinstance(facts.get("role"), str) else None,
        company=None,
        language=getattr(profile, "language", None),
        brands=[str(b) for b in brands] if isinstance(brands, list) else [str(brands)],
        earlier_sessions=summaries[:3],
    )


def _pinned(keys: Any) -> dict[str, Any]:
    """The pinned index keys exactly as `DbResolver.from_index_keys` reads them."""
    pinned: dict[str, Any] = {k: set(v) for k, v in (getattr(keys, "entity_keys", None) or {}).items()}
    pinned["attachment_type"] = set(getattr(keys, "attachment_types", None) or [])
    pinned["spec_attribute"] = set(getattr(keys, "spec_attributes", None) or [])
    pinned["spec_value"] = {
        f"{attr}.{value}"
        for attr, values in (getattr(keys, "spec_values", None) or {}).items()
        for value in values
    }
    return pinned


def resolver_reference_kinds(attributes: list[str] | None, is_staff: bool, held_to_links: bool) -> list[str]:
    """v1's `_reference_kinds(per_match_scope=True)`: the kinds the asker may see. A contact held
    to customer links keeps them all; `DbResolver._within_links` drops each match whose customer
    is outside the links, so no other customer's document is ever shown."""
    from app.services.chatbot.contracts import visible_reference_kinds

    return visible_reference_kinds(attributes, is_staff)


def is_staff_contact(db: Any, contact_respond_id: Any, space_id: Any, profile: Any) -> bool:
    """Staff by tier "office" OR by an active office access type (v1's two signals)."""
    from app.services.chatbot.turn.state import is_staff_profile

    if is_staff_profile(profile):
        return True
    try:
        from app.services import contact_customer_scope as scope_mod
        from app.services.field_access import resolve_contact_with_null_workspace_fallback

        if not contact_respond_id or not str(contact_respond_id).strip():
            return False
        internal = resolve_contact_with_null_workspace_fallback(
            db,
            contact_id=str(contact_respond_id).strip(),
            space_id=(str(space_id).strip() or None) if space_id is not None else None,
        )
        return bool(internal) and bool(scope_mod.is_office_staff(db, internal))
    except Exception:  # noqa: BLE001 - unknown: not staff (the closed side)
        return False


def scope_for_resolver(db: Any, contact_respond_id: Any, space_id: Any, profile: Any) -> dict[str, Any]:
    """Reference kinds, packing-list regions and customer scope for this contact, as v1 computes them."""
    from app.services.chatbot.head.access import _granted_field_reveal_keys
    from app.services.chatbot.lanes.business import services as business_services
    from app.services.chatbot.turn.state import is_staff_profile
    from app.services.eta_policy import UNRESOLVED, resolve_request_contact, rules_for_contact

    contact = str(contact_respond_id)
    staff = is_staff_contact(db, contact, space_id, profile)
    try:
        attrs = _granted_field_reveal_keys(db, contact_id=contact, space_id=space_id)
    except Exception:  # noqa: BLE001 - unknown grants fall to the closed default
        attrs = []
    try:
        scope = business_services.customer_scope(db, contact, space_id)
    except Exception:  # noqa: BLE001 - unknown scope: held to links, no customer names at all
        scope = {"enforced": True, "ids": [], "linked": []}
    held = (not staff) or bool((scope or {}).get("enforced"))
    try:
        regions = rules_for_contact(db, resolve_request_contact(db, contact, space_id)).regions
    except Exception:  # noqa: BLE001 - v1's closed value for an unknown contact
        regions = UNRESOLVED.regions
    # v1 `engine._reference_scope`: enforced -> the linked ids; staff -> no restriction;
    # any other contact -> its linked ids (none when unlinked).
    links: list[str] | None = None
    if (scope or {}).get("enforced") or not staff:
        links = [str(row[0]) for row in ((scope or {}).get("linked") or [])]
    return {
        "reference_links": links,
        "reference_kinds": resolver_reference_kinds(attrs, staff, held),
        "regions": regions,
        "customer_scope": scope,
        "granted_reveals": frozenset(attrs),
        "staff": staff,
    }


def _run_from_hook(hctx: Any) -> answer_mod.TurnV2Result:
    from app.services.chatbot.head.access import default_space_id
    from app.services.chatbot.lanes.business.services import _mcp_call
    from app.services.chatbot.pv2 import call as pv2_call
    from app.services.chatbot.pv2.resolve import DbResolver
    from app.services.chatbot.turn.state import offers_escalation

    factory = hctx.session_factory
    with _session(factory) as db:
        config = pv2_call.resolve_v2_config(db, override_version_id=getattr(hctx, "override_version_id", None))
        space_id = default_space_id(db)
        from app.services.chatbot.turn.policy import load_policy

        try:
            domain_teams: dict[str, str] | None = {
                d.name: d.escalation_team_code for d in load_policy(db).domains if d.escalation_team_code
            }
        except Exception:  # noqa: BLE001 - a policy read failure never fails the turn
            logger.warning("pv2: policy teams unavailable, falling back to the seed", exc_info=True)
            domain_teams = None

    contact = str(hctx.contact_respond_id)
    envelope = getattr(hctx, "envelope", None)
    # The engine's Envelope is a pydantic model (`contracts.Envelope`): read by attribute,
    # so a console turn reads console history in its own world.
    ingress = getattr(envelope, "ingress", None)
    is_test = bool(getattr(envelope, "is_test", False) or getattr(hctx, "dry_run", False))
    run_id = (getattr(envelope, "test_run_id", None) or None) if getattr(envelope, "is_test", False) else None
    session_block = hctx.session_block if isinstance(hctx.session_block, dict) else {}
    session_vars = session_block.get("session_vars") if isinstance(session_block.get("session_vars"), dict) else session_block
    raw_state = session_vars.get("pv2")
    profile = hctx.profile
    # The contact's scope, read once before the parse (closed on any failure inside).
    with _session(factory) as sdb:
        scope = scope_for_resolver(sdb, contact, space_id, profile)
    granted = scope.pop("granted_reveals")
    staff = scope.pop("staff")

    def exchanges(since: datetime | None) -> list[tuple[str, str]]:
        with _session(factory) as edb:
            return load_exchanges(
                edb, contact_respond_id=contact, ingress=ingress, is_test=is_test, since=since, test_run_id=run_id
            )

    def parse_parts(history: list[tuple[str, str]], memory_block: str, text: str) -> Any:
        return pv2_call.parse_v2(config, history=history, memory_block=memory_block, text=text)

    # One session for the resolver and the lanes. It is OPEN across the parser call, but
    # idle: nothing touches `db` until the parse returned (`_LazyResolver` builds the
    # resolver on first use, the idea handler runs after apply), and a lazily-connecting
    # SQLAlchemy session checks out no connection before its first query.
    with _session(factory) as db:
        ctx = TurnV2Context(
            text=hctx.text,
            now=hctx.now,
            raw_state=raw_state,
            profile=memory_profile(profile, getattr(hctx, "memory_intake", None), staff=staff),
            parse=None,
            parse_parts=parse_parts,
            resolver=_LazyResolver(lambda: DbResolver(db, pinned=_pinned(config.keys), **scope)),
            load_exchanges=exchanges,
            can_offer_handover=offers_escalation(profile) and not staff,
            granted_reveals=granted,
            domain_teams=domain_teams,
            offer_mode="none" if staff else ("offer" if offers_escalation(profile) else "refer"),
            roster_for=staff_roster_for(factory, contact) if staff else None,
            dym_tags=dym_tags_for(factory, contact, space_id),
            test_run_id=run_id,
            last_reply=session_vars.get("user_response") if isinstance(session_vars.get("user_response"), str) else None,
            conversation_language=session_vars.get("reply_language") if isinstance(session_vars.get("reply_language"), str) else None,
            stock_availability_only=bool(getattr(profile, "stock_availability_only", False)),
            report_answer=answer_mod.default_report_answer(
                mcp_call=_mcp_call(None),
                contact_id=contact,
                space_id=space_id,
                dealer=bool(getattr(profile, "stock_availability_only", False)),
            ),
            idea_answer=answer_mod.default_idea_answer(
                db,
                respond_io_id=contact,
                message_text=hctx.text,
                session_vars=session_vars,
                is_test=is_test,
                submitter_name=_first_name(envelope),
            ),
            trace={
                "index_sha": config.index_sha,
                "version": config.prompt_version,
                "version_id": config.version_id,
                "cache_key": config.cache_key,
            },
        )
        state_in = getattr(hctx, "state_in", None)
        ctx.base_focus = getattr(state_in, "focus", None)
        return _run(ctx, v1_pending=getattr(state_in, "pending", None))


def staff_roster_for(factory: Any, contact: str) -> Callable[[str, str], dict[str, Any] | None]:
    """team code, v1 offer sentence -> v1's own member offer (`tail/member_offer`): its text
    (the sentence, the picker header, the numbered members, the close) and the members
    (`user_id`, `name`) in printed order; None when the team has no roster."""

    def build(team: str, sentence: str) -> dict[str, Any] | None:
        from app.services.chatbot.tail import member_offer as member_mod

        try:
            plan = member_mod.cs_roster_plan({})
            from app.services.chatbot.contracts import DEFAULT_SUGGESTED_AGENT

            # The same routing v1 reads for a CS offer: the roster read needs the agent code too.
            routing = {"suggested_team": team, "suggested_agent": DEFAULT_SUGGESTED_AGENT}
            ctx = {"parse": {"output": {"routing": routing}}, "contact": {"id": contact}}
            with _session(factory) as db:
                rosters = member_mod.fetch_rosters(db, plan, ctx)
            offer = member_mod.build_cs_member_offer({"response": sentence}, plan, rosters)
            if not offer.get("member_offer"):
                logger.warning("pv2: staff roster is empty for team %s (rosters=%s)", team, rosters)
                return None
            members = [
                {"user_id": str(r.get("uuid")), "name": str(r.get("label"))}
                for r in offer.get("cs_last_result_set") or []
            ]
            return {"text": offer["response"], "members": members}
        except Exception:  # noqa: BLE001 - a roster failure leaves the plain not-found line
            logger.warning("pv2: staff roster unavailable", exc_info=True)
            return None

    return build


def dym_tags_for(factory: Any, contact: str, space_id: Any) -> Callable[[str, list[str]], dict[str, bool]] | None:
    """(domain, codes) -> {code: has stock}: v1's own did-you-mean tag probe (`miss_suggest`'s
    `DOMAIN_PROBE` entry and `dym_annotate`), for the domains it tags. {} = untagged (v1 fails
    open the same way). None when v1 would stock-deny this contact: the tags are a stock
    answer, so the did-you-mean stays untagged."""
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot import turn_runtime

    db = factory()
    try:
        denied = bool(engine_mod._stock_denial_enabled(db)) and (
            turn_runtime.load_profile(db, contact)[0].stock_allowed is not True
        )
    except Exception:  # noqa: BLE001 - unknown: the closed side, untagged
        denied = True
    finally:
        db.close()
    if denied:
        return None

    def tags(domain: str, codes: list[str]) -> dict[str, bool]:
        from app.models.product import Product
        from app.services.chatbot.lanes.business import miss_suggest as ms
        from app.services.chatbot.lanes.business.services import _mcp_probe

        row = ms.DOMAIN_PROBE.get(domain)
        if row is None or row.get("predicate") != "qty_gt_zero":
            return {}
        codes = list(codes)[: int(row.get("probe_cap") or 3)]
        try:
            with _session(factory) as db:
                ids = dict(db.query(Product.product_code, Product.id).filter(Product.product_code.in_(codes)).all())
            cands = [{"uuid": str(ids[c]), "entity_type": "product", "code": c} for c in codes if c in ids]
            if not cands:
                return {}
            probe = _mcp_probe(None)(
                row["tool"], ms._probe_args(cands, parser={"domain_hint": domain}, contact_id=contact, space_id=space_id)
            )
            ann = ms.dym_annotate(
                probe, payload={}, transform={
                    "probe_predicate": row["predicate"], "probe_tool": row["tool"], "probe_noun": row.get("noun"),
                    "dym_candidate_codes": [c["code"] for c in cands],
                },
            )
        except Exception:  # noqa: BLE001 - an untagged offer is today's bare did-you-mean
            logger.warning("pv2: did-you-mean tags unavailable", exc_info=True)
            return {}
        if not (ann.get("dym_probe_meta") or {}).get("ok"):
            return {}
        has = {ms._norm(c) for c in ann.get("dym_available_codes") or []}
        return {c["code"]: ms._norm(c["code"]) in has for c in cands}

    return tags


def _first_name(envelope: Any) -> str | None:
    """The submitter name v1's ideate lane sends (`lanes/ideate.build_arguments`): the
    respond.io contact's `firstName`, when it has one."""
    contact = getattr(envelope, "contact", None)
    name = contact.get("firstName") if isinstance(contact, dict) else None
    return str(name) if name else None


class _LazyResolver:
    """The DB resolver, built on first use (a turn that only small-talks reads nothing)."""

    def __init__(self, build: Callable[[], Any]):
        self._build = build
        self._inner: Any = None

    def __getattr__(self, name: str) -> Any:
        if self._inner is None:
            self._inner = self._build()
        return getattr(self._inner, name)


class _session:
    """`with _session(factory) as db`: the engine's own open/close discipline."""

    def __init__(self, factory: Callable[[], Any]):
        self.factory = factory

    def __enter__(self) -> Any:
        self.db = self.factory()
        return self.db

    def __exit__(self, *exc: Any) -> None:
        self.db.close()
