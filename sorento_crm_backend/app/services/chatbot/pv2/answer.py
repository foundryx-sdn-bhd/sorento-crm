"""PV2 answer contract (CONTRACT-PV2-13oct.md section 5): reuse, never new formatters.

One turn's actions become ONE of:
- a direct reply PV2 writes itself: a clarify / hand-over offer question (`clarify_copy`),
  a report (`pv2/reports.answer_report`, PV2-REPORTS), an idea turn (`pv2/ideas.run_idea_turn`,
  PV2-IDEAS);
- a v1 verdict + focus the engine's existing stages answer (`bridge`): a data lookup, and
  small talk / hand-over, which are v1 verdicts too (`message_type` casual /
  request_for_help), so `lanes/casual.py` and `run_escalation_lane` answer them unchanged.

Clarify first: when any ask needs the user, the turn asks and answers nothing else
(never guess). Reports and ideas are wired through injectable callables so this module
stays testable without the REPORTS / IDEAS lanes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from app.services.chatbot.pv2 import bridge, clarify_copy
from app.services.chatbot.pv2.schema import ParserV2Output
from app.services.chatbot.pv2.state import (
    Action,
    ClarifyAct,
    ConversationState,
    HandOver,
    Lookup,
    OfferHandover,
    Option,
    PendingQuestion,
    SmallTalk,
)
from app.services.chatbot.turn.state import Focus

IDEA_PENDING_KINDS = frozenset({"idea_similar", "idea_missing_field", "idea_multi"})
IDEA_DOMAIN = "ideate"
#: Domains whose held report spec is answered by the report engine (PV2-REPORTS).
REPORT_DOMAINS = frozenset({None, "sales", "chat_analytics"})

Route = str  # "lookup" | "clarify" | "small_talk" | "hand_over" | "report" | "idea"


@dataclass
class TurnV2Result:
    """What the engine hook continues with (section 6): `verdict` + `focus` for the
    normal stages, or `direct_reply` returned through the same tail. `state` is the
    ConversationState to persist (`session_vars["pv2"]`)."""

    verdict: dict[str, Any] | None
    focus: Focus | None
    direct_reply: str | None
    state: ConversationState
    actions: list[Action] = field(default_factory=list)
    route: Route = "lookup"
    language: str = "en"
    trace: dict[str, Any] = field(default_factory=dict)


#: (spec, refs, today, row, incoming) -> object with text / pending / rows / plan
#: (ReportReply). `row` is the picked LAST ANSWER ROWS option, `incoming` this ask's spec.
ReportAnswer = Callable[..., Any]
#: (lookup, pending_in, language, turn_no) -> object with reply_text / pending /
#: idea_draft_id (IdeaTurnResult), or None when it is not an idea turn.
IdeaAnswer = Callable[..., Any]


def reply_for(action: Action, language: str | None) -> str | None:
    """The text PV2 itself writes for a question action, else None."""
    if isinstance(action, ClarifyAct):
        return clarify_copy.render(action.pending, language, reask=action.reask)
    if isinstance(action, OfferHandover):
        return clarify_copy.render(action.pending, language)
    return None


def replay_reply(state: ConversationState, actions: list[Action]) -> str:
    """The PV2-written part of a turn's reply, for replay checks (CORE `replay.py`): the
    question copy of every clarify / offer action. A lookup's reply comes from the v1
    formatters, so it contributes nothing here."""
    language = None
    parts = [reply_for(a, language) for a in actions]
    return "\n\n".join(p for p in parts if p)


def _lookup_refs(lookup: Lookup) -> dict[str, list[str]]:
    return {kind: [r.key for r in refs] for kind, refs in lookup.held.refs.items()}


def _pending_from(raw: Any, turn_no: int) -> PendingQuestion | None:
    """A lane's pending (a ReportReply / IdeaTurnResult `pending` dict) as a question."""
    if raw is None:
        return None
    data = raw if isinstance(raw, dict) else getattr(raw, "__dict__", {})
    options = [
        o if isinstance(o, Option) else Option(
            key=str(_attr(o, "key")), label=str(_attr(o, "label")), payload=dict(_attr(o, "payload") or {})
        )
        for o in (data.get("options") or [])
    ]
    return PendingQuestion(
        kind=str(data.get("kind") or "clarify_other"),
        asked=str(data.get("asked") or ""),
        options=options,
        asked_turn=int(data.get("asked_turn") or turn_no),
        misses=int(data.get("misses") or 0),
        v1_kind=data.get("v1_kind"),
    )


def _attr(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _rows_from(rows: Any) -> list[Option]:
    out: list[Option] = []
    for r in rows or []:
        if isinstance(r, Option):
            out.append(r)
        else:
            out.append(Option(key=str(_attr(r, "key")), label=str(_attr(r, "label")), payload=dict(_attr(r, "payload") or {})))
    return out


def _is_report(lookup: Lookup) -> bool:
    return lookup.held.report is not None and lookup.domain in REPORT_DOMAINS


def _is_idea(lookup: Lookup) -> bool:
    ask = lookup.ask
    return (
        lookup.pending_kind in IDEA_PENDING_KINDS
        or lookup.domain == IDEA_DOMAIN
        or (ask is not None and ask.ideation is not None)
    )


def _verdict_for(kind: str, *, team_key: str | None = None) -> dict[str, Any]:
    verdict = bridge.blank_verdict()
    verdict["entity_op"] = "reuse"
    if kind == "small_talk":
        verdict["message_type"] = "casual"
    elif kind == "hand_over":
        verdict["message_type"] = "request_for_help"
        verdict["routing"] = {"suggested_team": team_key, "suggested_agent": None}
    return verdict


def build_result(
    state: ConversationState,
    actions: list[Action],
    out: ParserV2Output,
    *,
    today: date,
    pending_in: PendingQuestion | None = None,
    report_answer: ReportAnswer | None = None,
    idea_answer: IdeaAnswer | None = None,
) -> TurnV2Result:
    """One turn's actions as the engine's next step (see the module docstring)."""
    language = clarify_copy.lang(out.language)
    lookups = [a for a in actions if isinstance(a, Lookup)]
    domains = [a.domain for a in lookups]
    trace: dict[str, Any] = {
        "acts": [a.act for a in out.asks],
        "actions": [a.type for a in actions],
        "domains": domains,
        "pv2_ungated_domain": any(d is not None and not bridge.is_gated(d) for d in domains),
    }

    questions = [a for a in actions if isinstance(a, (ClarifyAct, OfferHandover))]
    if questions:
        reply = reply_for(questions[0], language)
        return TurnV2Result(None, None, reply, state, actions, "clarify", language, trace)

    handover = next((a for a in actions if isinstance(a, HandOver)), None)
    if handover is not None:
        verdict = _verdict_for("hand_over", team_key=handover.team_key)
        return TurnV2Result(verdict, bridge.focus_from_v2(state.held), None, state, actions, "hand_over", language, trace)

    if lookups:
        last = lookups[-1]
        if idea_answer is not None and _is_idea(last):
            idea = idea_answer(last, pending_in, language, state.turn_no)
            if idea is not None:
                st = state.model_copy(deep=True)
                st.pending = _pending_from(_attr(idea, "pending"), st.turn_no)
                draft = _attr(idea, "idea_draft_id")
                if draft:
                    st.held.idea_draft_id = str(draft)
                trace["route_detail"] = _attr(idea, "status")
                return TurnV2Result(None, None, str(_attr(idea, "reply_text") or ""), st, actions, "idea", language, trace)
        if report_answer is not None and _is_report(last):
            incoming = last.ask.report if last.ask is not None else None
            rep = report_answer(last.held.report, _lookup_refs(last), today, last.row, incoming)
            st = state.model_copy(deep=True)
            plan = _attr(rep, "plan")
            spec = _attr(plan, "spec") if plan is not None else None
            if spec is not None:
                st.held.report = spec
            st.pending = _pending_from(_attr(rep, "pending"), st.turn_no)
            rows = _rows_from(_attr(rep, "rows"))
            if rows:
                st.last_rows = rows[:20]
            trace["route_detail"] = _attr(plan, "status") if plan is not None else None
            return TurnV2Result(None, None, str(_attr(rep, "text") or ""), st, actions, "report", language, trace)
        verdict = bridge.to_v1_verdict(state, last, today=today)
        if len(lookups) > 1:
            verdict["asks"] = [
                {"domain": a.domain, "intent": bridge.INTENT_BY_DOMAIN.get(a.domain or "")}
                for a in lookups
                if a.domain
            ]
        return TurnV2Result(verdict, bridge.focus_from_v2(last.held), None, state, actions, "lookup", language, trace)

    verdict = _verdict_for("small_talk")
    return TurnV2Result(verdict, bridge.focus_from_v2(state.held), None, state, actions, "small_talk", language, trace)


# --------------------------------------------------------------------------- #
# Default lane handlers (lazy: the lanes land at integration)
# --------------------------------------------------------------------------- #


def default_report_answer(*, mcp_call: Callable, contact_id: str | None, space_id: str | None, dealer: bool) -> ReportAnswer:
    """`pv2/reports.answer_report` bound to this turn's egress and grants (PV2-REPORTS);
    a picked report row drills in first (`reports.drill_row`)."""

    def run(spec: Any, refs: dict[str, list[str]], today: date, row: Option | None, incoming: Any) -> Any:
        from app.services.chatbot.pv2 import reports

        if row is not None and reports.ROW_PAYLOAD in row.payload:
            spec, extra = reports.drill_row(spec, row, incoming=incoming)
            refs = {**refs, **{k: [str(_attr(r, "key")) for r in v] for k, v in (extra or {}).items()}}
        return reports.answer_report(
            spec,
            refs=refs,
            today=today,
            mcp_call=mcp_call,
            contact_id=contact_id,
            space_id=space_id,
            dealer=dealer,
        )

    return run


def default_idea_answer(
    db: Any, *, respond_io_id: str, message_text: str, session_vars: dict[str, Any], is_test: bool,
    submitter_name: str | None,
) -> IdeaAnswer:
    """`pv2/ideas.run_idea_turn` bound to this turn (PV2-IDEAS)."""

    def run(lookup: Lookup, pending_in: PendingQuestion | None, language: str, turn_no: int) -> Any:
        from app.services.chatbot.pv2 import ideas

        return ideas.run_idea_turn(
            db,
            ask=lookup.ask,
            language=language,
            respond_io_id=respond_io_id,
            message_text=message_text,
            session_vars=session_vars,
            pending=pending_in,
            turn_no=turn_no,
            is_test=is_test,
            submitter_name=submitter_name,
        )

    return run
