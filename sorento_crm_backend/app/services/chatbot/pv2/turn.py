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
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.services.chatbot.pv2 import answer as answer_mod
from app.services.chatbot.pv2 import clarify_copy
from app.services.chatbot.pv2.apply import DEFAULT_POLICY, ClarifyPolicy, ResolverLike, apply_turn
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
    state_in = roll_session(load_state(ctx.raw_state, ctx.now), ctx.now)
    if state_in.pending is None and v1_pending is not None:
        state_in = mirror_v1_pending(state_in, v1_pending)
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

    state_out, actions = apply_turn(
        state_in,
        out,
        resolver=ctx.resolver,
        policy=ctx.policy,
        can_offer_handover=ctx.can_offer_handover,
        now=ctx.now,
    )
    state_out.last_turn_at = ctx.now
    refusal = _grant_refusal(ctx, actions, out)
    if refusal is not None:
        language = clarify_copy.lang(out.language)
        trace = {"acts": [a.act for a in out.asks], "actions": [a.type for a in actions], "grant_refused": True}
        return answer_mod.TurnV2Result(None, None, refusal[0], state_out, actions, refusal[1], language, trace)
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
PO_DOMAIN_SUBJECT = {"purchase_order": "purchase orders", "spo_allocation": "purchase order allocation"}


def _is_sales_dataset(spec: Any) -> bool:
    """The dataset as the report lane reads it: a null one takes the policy default."""
    from app.services.chatbot.pv2 import reports

    try:
        ds = reports._dataset_for(spec)
    except Exception:  # noqa: BLE001 - unreadable spec: the closed side
        return True
    return ds is None or ds.key == "sales"


def _grant_refusal(ctx: TurnV2Context, actions: list[Any], out: Any) -> tuple[str, str] | None:
    """(reply, route) when this turn asks for a domain the contact holds no grant for, else
    None. Refused before any lookup or report question, so nothing of the data is read."""
    granted = ctx.granted_reveals or frozenset()
    lookups = [a for a in actions if isinstance(a, Lookup)]
    last = lookups[-1] if lookups else None
    if PO_GRANT not in granted:
        # Any part of the turn in a PO domain refuses the whole turn (closed side).
        domains = [a.domain for a in lookups] + [getattr(a, "domain", None) for a in getattr(out, "asks", None) or []]
        hit = next((d for d in domains if d in PO_DOMAIN_SUBJECT), None)
        if hit is not None:
            from app.services.chatbot import copy as copy_mod
            from app.services.chatbot.lanes import canned as canned_lanes

            text = canned_lanes.field_grant_denied_text(copy_mod.fallback_copy(), PO_DOMAIN_SUBJECT[hit])
            return text, "clarify"
    if SALES_REPORT_GRANT in granted:
        return None
    for ask in getattr(out, "asks", None) or []:
        report = getattr(ask, "report", None)
        if report is not None and ask.domain in answer_mod.REPORT_DOMAINS and _is_sales_dataset(report):
            from app.services.chatbot.lanes.business import SALES_REPORT_NOT_ENABLED_MESSAGE

            return SALES_REPORT_NOT_ENABLED_MESSAGE, "report"
    if last is not None and answer_mod._is_report(last) and _is_sales_dataset(last.held.report):
        from app.services.chatbot.lanes.business import SALES_REPORT_NOT_ENABLED_MESSAGE

        return SALES_REPORT_NOT_ENABLED_MESSAGE, "report"
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
    elif st.pending is not None and st.pending.kind == "v1_open_question":
        st.pending = None
    if rows is not None:
        st.last_rows = list(rows)[:LAST_ROWS_CAP]
    return st


# --------------------------------------------------------------------------- #
# v1 question mirror
# --------------------------------------------------------------------------- #


def mirror_v1_pending(state: ConversationState, v1_pending: Any) -> ConversationState:
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
    labels = [str(o["label"]) for o in opts]
    keys = [str(o.get("position") or o.get("idx") or n) for n, o in enumerate(opts, start=1)]
    return after_v1(
        state,
        question_kind=str(kind),
        option_labels=labels,
        option_keys=keys,
        offer=kind in OFFER_KINDS,
        asked=str(getattr(v1_pending, "question", None) or getattr(v1_pending, "asked", None) or ""),
    )


# --------------------------------------------------------------------------- #
# CORE hook adapter
# --------------------------------------------------------------------------- #


def memory_profile(profile: Any, memory_intake: dict[str, Any] | None) -> MemoryProfile:
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
        audience="staff" if is_staff_profile(profile) else getattr(profile, "tier", None),
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
    """v1's `_reference_kinds(per_match_scope=False)`: PV2 has no per-match customer drop, so a
    contact held to customer links loses the customer-owned kinds."""
    from app.services.chatbot.contracts import visible_reference_kinds

    kinds = visible_reference_kinds(attributes, is_staff)
    if held_to_links:
        kinds = [k for k in kinds if k not in ("sales_order", "customer_po_ref")]
    return kinds


def scope_for_resolver(db: Any, contact_respond_id: Any, space_id: Any, profile: Any) -> dict[str, Any]:
    """Reference kinds, packing-list regions and customer scope for this contact, as v1 computes them."""
    from app.services.chatbot.head.access import _granted_field_reveal_keys
    from app.services.chatbot.lanes.business import services as business_services
    from app.services.chatbot.turn.state import is_staff_profile
    from app.services.eta_policy import UNRESOLVED, resolve_request_contact, rules_for_contact

    contact = str(contact_respond_id)
    staff = is_staff_profile(profile)
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

    contact = str(hctx.contact_respond_id)
    envelope = getattr(hctx, "envelope", None)
    # The engine's Envelope is a pydantic model (`contracts.Envelope`): read by attribute,
    # so a console turn reads console history in its own world.
    ingress = getattr(envelope, "ingress", None)
    is_test = bool(getattr(envelope, "is_test", False) or getattr(hctx, "dry_run", False))
    session_block = hctx.session_block if isinstance(hctx.session_block, dict) else {}
    session_vars = session_block.get("session_vars") if isinstance(session_block.get("session_vars"), dict) else session_block
    raw_state = session_vars.get("pv2")
    profile = hctx.profile
    # The contact's scope, read once before the parse (closed on any failure inside).
    with _session(factory) as sdb:
        scope = scope_for_resolver(sdb, contact, space_id, profile)
    granted = scope.pop("granted_reveals")

    def exchanges(since: datetime | None) -> list[tuple[str, str]]:
        with _session(factory) as edb:
            return load_exchanges(edb, contact_respond_id=contact, ingress=ingress, is_test=is_test, since=since)

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
            profile=memory_profile(profile, getattr(hctx, "memory_intake", None)),
            parse=None,
            parse_parts=parse_parts,
            resolver=_LazyResolver(lambda: DbResolver(db, pinned=_pinned(config.keys), **scope)),
            load_exchanges=exchanges,
            can_offer_handover=offers_escalation(profile),
            granted_reveals=granted,
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
