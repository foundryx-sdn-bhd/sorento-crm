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
from app.services.chatbot.pv2.history import TIMEZONE, MemoryProfile, history_messages, render_user_message
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


@dataclass
class TurnV2Context:
    text: str
    now: datetime
    raw_state: Any
    profile: MemoryProfile
    parse: ParseFn
    resolver: ResolverLike
    load_exchanges: ExchangesFn
    can_offer_handover: bool = True
    policy: ClarifyPolicy = DEFAULT_POLICY
    report_answer: answer_mod.ReportAnswer | None = None
    idea_answer: answer_mod.IdeaAnswer | None = None
    trace: dict[str, Any] = field(default_factory=dict)


def local_today(now: datetime) -> date:
    return now.astimezone(ZoneInfo(TIMEZONE)).date() if now.tzinfo else now.date()


def run_turn_v2(ctx: TurnV2Context) -> answer_mod.TurnV2Result:
    state_in = roll_session(load_state(ctx.raw_state, ctx.now), ctx.now)
    exchanges = ctx.load_exchanges(state_in.session_started_at)
    messages = history_messages(exchanges)
    user_message = render_user_message(state_in, ctx.profile, ctx.now, ctx.text)
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
