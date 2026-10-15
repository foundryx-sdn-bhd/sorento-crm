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

from app.services.chatbot.pv2 import bridge, clarify_copy, register
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
    #: The parser v2 provider usage (the hook records it as the turn's parser spend).
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def branch_kind(self) -> str | None:
        """The `contracts.BRANCH_KINDS` value the hook closes a direct reply with."""
        return _BRANCH_BY_ROUTE.get(self.route)


#: A direct reply's route as the branch kind its turn row records.
_BRANCH_BY_ROUTE = {
    "clarify": "clarify_menu",
    "report": "business_query",
    "idea": "ideate",
}


#: (spec, refs, today, row, incoming) -> object with text / pending / rows / plan
#: (ReportReply). `row` is the picked LAST ANSWER ROWS option, `incoming` this ask's spec.
ReportAnswer = Callable[..., Any]
#: (lookup, pending_in, language, turn_no) -> object with reply_text / pending /
#: idea_draft_id (IdeaTurnResult), or None when it is not an idea turn.
IdeaAnswer = Callable[..., Any]


def reply_for(action: Action, language: str | None) -> str | None:
    """The text PV2 itself writes for a question action, else None."""
    if isinstance(action, ClarifyAct):
        shown = clarify_copy.render(action.pending, language, reask=action.reask)
        return f"{action.note}\n\n{shown}" if action.note else shown
    if isinstance(action, OfferHandover):
        return clarify_copy.render(action.pending, language)
    return None


def replay_reply(state: ConversationState, actions: list[Action]) -> str:
    """The PV2-written part of a turn's reply, for replay checks (CORE `replay.py`): the
    question copy of every clarify / offer action. A lookup's reply comes from the v1
    formatters, so it contributes nothing here."""
    language = None
    parts = [reply_for(a, language) for a in actions]
    for a in actions:
        if isinstance(a, Lookup) and _is_report(a):
            parts.append(_report_preview(a, state.last_turn_at.date()))
    return "\n\n".join(p for p in parts if p)


def _report_preview(lookup: Lookup, today: date) -> str:
    """A report lookup's own lines without the data call: the `*Read as:*` header, the
    "not available yet" notes, or the one line it says instead (`reports.plan_report`)."""
    from app.services.chatbot.pv2 import reports

    plan = reports.plan_report(lookup.held.report, refs=_lookup_refs(lookup), today=today)
    return "\n".join(x for x in [plan.header, *plan.notes, plan.reply or ""] if x)


def _lookup_refs(lookup: Lookup) -> dict[str, list[dict[str, str]]]:
    from app.services.chatbot.pv2.apply import report_refs

    return report_refs(lookup.held)


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


def _verdict_for(kind: str, *, team_key: str | None = None, agent_key: str | None = None) -> dict[str, Any]:
    verdict = bridge.blank_verdict()
    verdict["entity_op"] = "reuse"
    if kind == "small_talk":
        verdict["message_type"] = "casual"
    elif kind == "hand_over":
        verdict["message_type"] = "request_for_help"
        verdict["routing"] = {"suggested_team": team_key, "suggested_agent": None}
        if agent_key:
            # v1's member pick: the escalation lane assigns `escalation.preferred_assignee_id`.
            verdict["escalation"] = {**verdict["escalation"], "preferred_assignee_id": agent_key}
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
    base_focus: Focus | None = None,
    message: str | None = None,
    language: str | None = None,
    so_granted: bool = False,
) -> TurnV2Result:
    """One turn's actions as the engine's next step (see the module docstring). `message`
    is the customer's own text: every verdict carries it as `user_goal`, as v1's casual
    lane does, so a v1 template that prints it never prints null."""
    result = _build_result(
        state, actions, out, today, pending_in, report_answer, idea_answer, base_focus, language, so_granted
    )
    if message and result.verdict is not None and not result.verdict.get("user_goal"):
        result.verdict["user_goal"] = message
    return result


def _build_result(
    state, actions, out, today, pending_in, report_answer, idea_answer, base_focus, language=None, so_granted=False
) -> TurnV2Result:
    # R5-6: the turn's language (`turn.turn_language`), else the parse's reading of the message.
    language = clarify_copy.lang(language or out.language)
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
        verdict = _verdict_for("hand_over", team_key=handover.team_key, agent_key=handover.agent_key)
        return TurnV2Result(verdict, bridge.focus_from_v2(state.held, base_focus), None, state, actions, "hand_over", language, trace)

    if lookups:
        last = lookups[-1]
        if idea_answer is not None and _is_idea(last):
            idea = idea_answer(last, pending_in, language, state.turn_no)
            if idea is not None:
                st = state.model_copy(deep=True)
                st.pending = _pending_from(_attr(idea, "pending"), st.turn_no)
                if st.pending is not None:
                    st.last_reply = f"asked {st.pending.kind}"  # R4a: the lane asked, not answered
                draft = _attr(idea, "idea_draft_id")
                if draft:
                    st.held.idea_draft_id = str(draft)
                # The capture service's own pointer (`ideation`), for the hook to keep.
                trace["ideation_session_vars"] = _attr(idea, "session_vars")
                trace["route_detail"] = _attr(idea, "status")
                return TurnV2Result(None, None, str(_attr(idea, "reply_text") or ""), st, actions, "idea", language, trace)
        if report_answer is not None and _is_report(last):
            incoming = last.ask.report if last.ask is not None else None
            run_spec = last.held.report
            picked_group = None
            if last.pending_kind == "clarify_report_breakdown" and last.picked is not None:
                picked_group = (last.picked.payload.get("report") or {}).get("group_by")
            if picked_group:
                # v1's drill-down pick: run the held report by it, keep the held report as it was.
                run_spec = run_spec.model_copy(update={"group_by": list(picked_group), "top_n": None}, deep=True)
            rep = report_answer(run_spec, _lookup_refs(last), today, last.row, incoming)
            st = state.model_copy(deep=True)
            plan = _attr(rep, "plan")
            spec = _attr(plan, "spec") if plan is not None else None
            if picked_group and _attr(rep, "v1_sales"):
                spec = last.held.report  # the drill ran v1's report: the total stays held
            if spec is not None:
                st.held.report = spec
            st.pending = _pending_from(_attr(rep, "pending"), st.turn_no)
            if st.pending is not None:
                st.last_reply = f"asked {st.pending.kind}"  # R4a: the lane asked, not answered
            rows = _rows_from(_attr(rep, "rows"))
            # R5-7: a row's id in the report text prints as the row's name, else nothing; a row named by an id
            # is "(unnamed)" in the pick list, so numbers still match the printed rows.
            from app.services.chatbot.pv2.reports import ROW_PAYLOAD

            names = {str(k): r.label for r in rows for k in ((r.payload.get(ROW_PAYLOAD) or {}).get("keys") or [])}
            text = register.scrub_ids(str(_attr(rep, "text") or ""), names)
            rows = [r.model_copy(update={"label": clarify_copy.safe_label(r.label, language)}) for r in rows]
            if rows:
                # R4a: a list cut to the cap says how long it was (MEMORY's LAST ANSWER ROWS).
                st.last_rows = [r.model_copy(update={"payload": {**r.payload, "of": len(rows)}}) for r in rows[:20]]
                fresh = register.entries_from_report_rows(st.last_rows, st.turn_no, st.held.sid)
                st.register = register.roll(st.register, fresh)
            elif st.pending is None:
                st.last_rows = []  # R4a: an answer that printed no rows leaves none to pick
            trace["route_detail"] = _attr(plan, "status") if plan is not None else None
            return TurnV2Result(None, None, text, st, actions, "report", language, trace)
        verdict = bridge.to_v1_verdict(state, last, today=today, so_granted=so_granted)
        if len(lookups) > 1:
            verdict["asks"] = [
                {"domain": d, "intent": bridge.INTENT_BY_DOMAIN.get(d or "")}
                for d in dict.fromkeys(a.domain for a in lookups if a.domain)
            ]
        return TurnV2Result(verdict, bridge.focus_from_v2(last.held, base_focus), None, state, actions, "lookup", language, trace)

    verdict = _verdict_for("small_talk")
    if any(isinstance(a, SmallTalk) and a.declined_offer for a in actions):
        # A "no" to PV2's own offer is v1's escalation-declined lane, not casual chat.
        verdict["escalation"] = {**verdict["escalation"], "escalation_declined": True}
    return TurnV2Result(verdict, bridge.focus_from_v2(state.held, base_focus), None, state, actions, "small_talk", language, trace)


# --------------------------------------------------------------------------- #
# Default lane handlers (lazy: the lanes land at integration)
# --------------------------------------------------------------------------- #


def default_report_answer(*, mcp_call: Callable, contact_id: str | None, space_id: str | None, dealer: bool) -> ReportAnswer:
    """`pv2/reports.answer_report` bound to this turn's egress and grants (PV2-REPORTS). A
    picked report row was already drilled into the held spec by `apply_turn`."""

    def run(spec: Any, refs: dict[str, list[dict[str, str]]], today: date, row: Option | None, incoming: Any) -> Any:
        from app.services.chatbot.pv2 import reports

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
