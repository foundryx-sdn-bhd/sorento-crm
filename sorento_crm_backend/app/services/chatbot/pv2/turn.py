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
    history_messages,
    load_exchanges,
    render_memory,
    render_user_message,
)
from app.services.chatbot.pv2.schema import ParserV2Output
from app.services.chatbot.pv2.state import (
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
    report_answer: answer_mod.ReportAnswer | None = None
    idea_answer: answer_mod.IdeaAnswer | None = None
    trace: dict[str, Any] = field(default_factory=dict)


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
    exchanges = ctx.load_exchanges(state_in.session_started_at)
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
    )
    state_out.last_turn_at = ctx.now
    result = answer_mod.build_result(
        state_out,
        actions,
        out,
        today=local_today(ctx.now),
        pending_in=state_in.pending,
        report_answer=ctx.report_answer,
        idea_answer=ctx.idea_answer,
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
) -> ConversationState:
    """After the v1 stages answered a PV2 lookup: a question the v1 lane asked (its pending
    kind and option labels, `contracts.PENDING_KINDS`) becomes the PV2 pending question
    `v1_open_question`, options keyed by position ("1", "2", ...) plus "yes" / "no" for an
    offer, so the model answers it by exact key and the bridge passes the pick back as
    `open_question_answer`. No question -> pending clears. `rows` are the answer's
    printed rows for the next `row_pick`."""
    st = state.model_copy(deep=True)
    if question_kind:
        options = [
            Option(key=str(n), label=label, payload={"kind": "choice", "key": str(n)})
            for n, label in enumerate(option_labels, start=1)
        ]
        if offer:
            options += [
                Option(key="yes", label=clarify_copy.text("yes", language), payload={"kind": "choice", "key": "yes"}),
                Option(key="no", label=clarify_copy.text("no", language), payload={"kind": "choice", "key": "no"}),
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
    labels = [
        str(o.get("label"))
        for o in (getattr(v1_pending, "options", None) or [])
        if isinstance(o, dict) and o.get("label")
    ]
    return after_v1(
        state,
        question_kind=str(kind),
        option_labels=labels,
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
    pinned: dict[str, Any] = {k: set(v) for k, v in (getattr(keys, "entity_keys", None) or {}).items()}
    pinned["attachment_type"] = set(getattr(keys, "attachment_types", None) or [])
    return pinned


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
    ingress = (getattr(hctx, "envelope", None) or {}).get("ingress") if isinstance(hctx.envelope, dict) else None
    is_test = bool(getattr(hctx, "dry_run", False))
    session_block = hctx.session_block if isinstance(hctx.session_block, dict) else {}
    session_vars = session_block.get("session_vars") if isinstance(session_block.get("session_vars"), dict) else session_block
    raw_state = session_vars.get("pv2")
    profile = hctx.profile

    def exchanges(since: datetime | None) -> list[tuple[str, str]]:
        with _session(factory) as edb:
            return load_exchanges(edb, contact_respond_id=contact, ingress=ingress, is_test=is_test, since=since)

    def parse_parts(history: list[tuple[str, str]], memory_block: str, text: str) -> Any:
        return pv2_call.parse_v2(config, history=history, memory_block=memory_block, text=text)

    # The resolver and the lanes read the DB after the parser call: one session for the
    # rest of the turn, never held across the model call.
    with _session(factory) as db:
        ctx = TurnV2Context(
            text=hctx.text,
            now=hctx.now,
            raw_state=raw_state,
            profile=memory_profile(profile, getattr(hctx, "memory_intake", None)),
            parse=None,
            parse_parts=parse_parts,
            resolver=_LazyResolver(lambda: DbResolver(db, pinned=_pinned(config.keys))),
            load_exchanges=exchanges,
            can_offer_handover=offers_escalation(profile),
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
                submitter_name=None,
            ),
            trace={
                "index_sha": config.index_sha,
                "version": config.prompt_version,
                "version_id": config.version_id,
                "cache_key": config.cache_key,
            },
        )
        v1_pending = getattr(getattr(hctx, "state_in", None), "pending", None)
        return _run(ctx, v1_pending=v1_pending)


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
