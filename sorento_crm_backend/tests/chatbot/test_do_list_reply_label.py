"""DO-LIST-REPLY-LABEL (owner, 6 Oct 2026, prod, "quite frustrating"): the quick reply
labelled "Delivery order list" under the outstanding summary must answer the detail offer
exactly as "1" does - never re-run the summary.

Prod trace (contact 551644114, turn 11): the open question is `outstanding_detail`,
expects `pick`, one option `{position 1, label "Delivery order list", payload {value:
"do"}}`, filters `{scope: "do", ...}`. respond.io sends the tapped quick reply as the
TEXT "Delivery order list".

Measured cause (`PLAN-do-list-reply-label.md`): the parser is shown the option's label
(`turn/question.py::_options`) and returns a pick of position 1 for it, but the prompt
also fills `document: ["DO"]` for the words "delivery order" even when they are the
whole message. `turn/decide.py`'s outstanding-kinds arm reads the named document BEFORE
the picked position (`NEW_ASK "named_document"`), and `turn/apply.py::_answer_outstanding`
then re-runs the REPORT for that scope (`detail: None`) - the summary already on screen.
`so_status.carried_status_verdict`'s `pick_under_offer` cannot help: it keeps the
parser's document whenever the text itself names one, which a label reply does.

The rule under test: a declared pick whose named document is the SAME scope as the option
it picked IS the pick. "Sales order" typed after the DO list (named "so", picked option
"do") keeps its new-ask reading (hand pass 3 row 5, `test_rearch_handpass3_owner_17sep`).
Both halves are the parser's own fields (`reference_positions`, `document`) read against
the pending's own stored option payload - no word of the message is read here.
"""
from __future__ import annotations

from typing import Any

import pytest

from tests.chatbot._turn_helpers import build_policy, verdict

PICK_ONE = {"mode": "pick", "picked": [1], "items": [], "qty_for_all": None}
PICK_TWO = {"mode": "pick", "picked": [2], "items": [], "qty_for_all": None}


def _do_offer(asked_at_turn: int = 6):
    """The prod-shaped single-scope detail offer: the DO summary ran, one list on offer."""
    from app.services.chatbot.turn.pending import ask

    return ask(
        "outstanding_detail",
        [
            {
                "uuid": "do",
                "label": "Delivery order list",
                "payload": {"value": "do"},
                "position": 1,
                "entity_type": "outstanding_detail",
            }
        ],
        expects="pick",
        asked_at_turn=asked_at_turn,
        payload={
            "domain": "order",
            "filters": {
                "scope": "do",
                "offer_text": "Reply 1 for the delivery order list.",
                "customer_ids": ["c8a1d2e4-0000-4000-8000-000000000001"],
            },
        },
    )


def _both_offer():
    """The two-scope offer: `1. Sales order list`, `2. Delivery order list`, `3. Both lists`."""
    from app.services.chatbot.turn.pending import ask

    rows = [("so", "Sales order list"), ("do", "Delivery order list"), ("both", "Both lists")]
    return ask(
        "outstanding_detail",
        [
            {"uuid": value, "label": label, "payload": {"value": value}, "position": i, "entity_type": "outstanding_detail"}
            for i, (value, label) in enumerate(rows, start=1)
        ],
        expects="pick",
        asked_at_turn=6,
        payload={"domain": "order", "filters": {"scope": "both", "offer_text": "Reply with a number for detail:"}},
    )


def _summary_focus():
    from app.services.chatbot.turn.state import Focus

    return Focus(
        domains=["order"],
        document=["DO"],
        status="outstanding",
        customers=[
            {
                "raw": "hanlim",
                "hint": "customer",
                "canonical_code": "300-H030",
                "uuid": "c8a1d2e4-0000-4000-8000-000000000001",
                "current_message": False,
            }
        ],
    )


def _apply(v: dict[str, Any], *, pending, focus=None):
    from app.services.chatbot.turn.apply import apply
    from app.services.chatbot.turn.state import Profile, State

    state = State(focus=focus or _summary_focus(), pending=pending, profile=Profile())
    return apply(state, v, build_policy())


#: Turn C, the control: "1". `so_status.carried_status_verdict`'s `pick_under_offer` has
#: already blanked the document the parser carried off the conversation.
BARE_ONE = dict(
    message_type="casual",
    reference_positions=[1],
    open_question_answer=PICK_ONE,
    domain_in_message=False,
)

#: Turn B: the tapped quick reply, "Delivery order list". The parser's own worked example
#: (`chatbot_parser_prompt.py`: options "1. Sales order list, 2. Delivery order list" +
#: "DO list" -> reference_positions [2]) gives the position; its DOCUMENT rule ("delivery
#: order" fills `document` even when it is the whole message) gives `["DO"]`; the words
#: "delivery order" are a domain word (`domain_in_message` true, `domain_hint` order).
LABEL_REPLY = dict(
    message_type="business_query",
    reference_positions=[1],
    open_question_answer=PICK_ONE,
    document=["DO"],
    domain_in_message=True,
    domain_hint="order",
    intent_hint="check_order",
)


class TestDecideReadsTheLabelReplyAsThePick:
    def test_the_label_reply_decides_exactly_as_the_bare_number_does(self) -> None:
        """AC-DLRL-1 at the decide() seam: B and C are one Decision."""
        from app.services.chatbot.turn.decide import ANSWER, decide

        pending = _do_offer()
        control = decide(verdict(**BARE_ONE), _summary_focus(), pending)
        label = decide(verdict(**LABEL_REPLY), _summary_focus(), pending)
        assert (control.kind, control.why, control.scope, control.positions) == (ANSWER, "picked_scope", "do", (1,))
        assert (label.kind, label.why, label.scope, label.positions) == (
            control.kind,
            control.why,
            control.scope,
            control.positions,
        ), (
            "the quick-reply label must read as the SAME pick the bare number is, not as a "
            f"named document - got {label.as_trace()!r} scope={label.scope!r}"
        )

    @pytest.mark.parametrize("carried_status", ["do_outstanding", "outstanding"])
    def test_a_report_status_the_parser_carries_does_not_change_the_pick(self, carried_status: str) -> None:
        """The live parser carries the summary's status onto the reply (PR #1435 cloud
        pass); with a document in the text `pick_under_offer` leaves it in place, so the
        pick must hold with the status beside it."""
        from app.services.chatbot.turn.decide import ANSWER, decide

        label = decide(verdict(**LABEL_REPLY, order_status=carried_status), _summary_focus(), _do_offer())
        assert (label.kind, label.why, label.scope) == (ANSWER, "picked_scope", "do"), label.as_trace()

    def test_the_label_over_the_two_scope_offer_picks_the_do_list(self) -> None:
        """AC-DLRL-2: `1. Sales order list, 2. Delivery order list, 3. Both lists` +
        "Delivery order list" -> position 2 with `document: ["DO"]` picks the DO list."""
        from app.services.chatbot.turn.decide import ANSWER, decide

        v = verdict(**{**LABEL_REPLY, "reference_positions": [2], "open_question_answer": PICK_TWO})
        label = decide(v, _summary_focus(), _both_offer())
        assert (label.kind, label.why, label.scope, label.positions) == (ANSWER, "picked_scope", "do", (2,)), (
            label.as_trace()
        )

    def test_a_document_that_is_not_the_picked_option_stays_a_new_ask(self) -> None:
        """AC-DLRL-3, the guard: "Sales order" after the DO list arrives as `document:
        ["SO"]` AND `reference_positions: [1]` (hand pass 3 row 5, turn bb233665); the
        only option on offer is "Delivery order list", so the named document wins and
        the report re-runs for the SO scope."""
        from app.services.chatbot.turn.decide import NEW_ASK, decide

        v = verdict(
            message_type="business_query",
            reference_positions=[1],
            open_question_answer=PICK_ONE,
            document=["SO"],
            domain_in_message=True,
            domain_hint="order",
        )
        named = decide(v, _summary_focus(), _do_offer())
        assert (named.kind, named.why, named.scope) == (NEW_ASK, "named_document", "so"), named.as_trace()

    def test_a_named_document_with_no_pick_stays_a_new_ask(self) -> None:
        """A document named with NO position (`reference_positions: []`) is still the new
        scope it always was - the rule only joins a declared pick to its own document."""
        from app.services.chatbot.turn.decide import NEW_ASK, decide

        v = verdict(message_type="business_query", document=["DO"], domain_in_message=True, domain_hint="order")
        named = decide(v, _summary_focus(), _do_offer())
        assert (named.kind, named.why, named.scope) == (NEW_ASK, "named_document", "do"), named.as_trace()


class TestApplyRunsTheListNotTheSummary:
    def test_the_label_reply_runs_the_delivery_order_list_and_keeps_the_offer(self) -> None:
        """AC-DLRL-1 at the apply() seam: `trace.outstanding.detail == "do"` is what makes
        the fetch the LIST (the same tool call with `detail=`); `detail: None` is the
        summary. The detail offer survives its own pick (contract 39)."""
        state_b, plan_b = _apply(verdict(**LABEL_REPLY), pending=_do_offer())
        state_c, plan_c = _apply(verdict(**BARE_ONE), pending=_do_offer())

        assert plan_c.trace.outstanding == {"kind": "outstanding_detail", "scope": "do", "detail": "do"}
        assert plan_b.trace.outstanding == plan_c.trace.outstanding, (
            "the label reply must run the delivery order LIST exactly as '1' does - "
            f"got {plan_b.trace.outstanding!r}, rules {plan_b.trace.rules_fired!r}"
        )
        assert "outstanding_pending_dropped" not in plan_b.trace.rules_fired, plan_b.trace.rules_fired
        assert "answer_outstanding" in plan_b.trace.rules_fired, plan_b.trace.rules_fired
        assert state_b.pending is not None and state_b.pending.kind == "outstanding_detail", (
            f"the detail offer stays open across its own pick - got {state_b.pending!r}"
        )
        assert state_b.focus.document == state_c.focus.document == ["DO"]
        assert state_b.focus.status == state_c.focus.status == "outstanding"
        assert state_b.focus.domains == state_c.focus.domains == ["order"]

    def test_the_summary_is_never_re_run_by_the_label_reply(self) -> None:
        """The symptom itself: a re-run of the REPORT is `trace.outstanding` with
        `detail: None` (or the offer dropped). Neither may happen on the label reply."""
        state_b, plan_b = _apply(verdict(**LABEL_REPLY), pending=_do_offer())
        answered = plan_b.trace.outstanding or {}
        assert answered.get("detail") == "do", (
            f"summary re-run instead of the list: trace.outstanding={plan_b.trace.outstanding!r}, "
            f"rules={plan_b.trace.rules_fired!r}, pending={state_b.pending!r}"
        )
