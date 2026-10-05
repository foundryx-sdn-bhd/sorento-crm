"""Top X hot selling (#1171): the ranked list is a sticky pick list, the same as the
customer picker and the product picker (owner, PR #1258, 26 Sep 2026 05:32Z: "make sure
the list of choice behave similarly to our other picker like customer picker, product
picker, that it will be sticky to a certain extent").

Reuses the pickers' OWN mechanism, no second one:

* `turn/pending.py::ROSTER_KINDS` - `top_selling_pick` sits beside `product_pick` and
  `customer_pick`, so it survives its own pick (`with_answered_positions`) and has no
  turn clock (`tick` only expires the escalation offers).
* `turn/decide.py::picked_positions` - a bare "2" arrives as `reference_positions`, a
  typed code or category name matches the option's label exactly.
* `turn/apply.py` - the same close rules: a list whose every row was picked closes on
  entry (pinned below), and a new ask about something else closes it
  (`new_ask_closes_stale_roster`, which reads no kind, so it is inherited as is and pinned
  by the pickers' own tests rather than again here).

The rows come from the MCP envelope's `result_set` (`presenters._top_selling_envelope`),
one per printed line, `idx` = the printed rank.
"""
from __future__ import annotations

import pytest

from tests.chatbot._turn_helpers import build_policy, entity, verdict

_ITEMS_RESULT_SET = [
    {"idx": 1, "label": "SRTWT7445", "code": "SRTWT7445", "entity_type": "product"},
    {"idx": 2, "label": "SRTKT39SS", "code": "SRTKT39SS", "entity_type": "product"},
    {"idx": 3, "label": "SRTBS1020", "code": "SRTBS1020", "entity_type": "product"},
    {"idx": 4, "label": "SRTSH2201", "code": "SRTSH2201", "entity_type": "product"},
    {"idx": 5, "label": "SRTWC5501", "code": "SRTWC5501", "entity_type": "product"},
]
_CATEGORY_RESULT_SET = [
    {"idx": 1, "label": "KS", "code": "KS", "entity_type": "category"},
    {"idx": 2, "label": "WC", "code": "WC", "entity_type": "category"},
]
_FILTERS = {"tool": "crm_top_selling_report", "rank_by": "quantity", "basis": "delivered"}


def _decide(v: dict, *, pending):
    from app.services.chatbot.turn.apply import apply
    from app.services.chatbot.turn.state import Focus, Profile, State

    state = State(focus=Focus(), pending=pending, profile=Profile())
    state2, _plan = apply(state, v, build_policy())
    return state2


def _armed(result_set=_ITEMS_RESULT_SET):
    from app.services.chatbot.turn.pending import top_selling_pick

    return top_selling_pick(result_set, asked_at_turn=4, filters=_FILTERS)


def test_top_selling_pick_is_a_roster_kind_beside_the_customer_and_product_pickers():
    from app.services.chatbot.turn.pending import PENDING_KINDS, ROSTER_KINDS

    assert {"product_pick", "customer_pick", "top_selling_pick"} <= ROSTER_KINDS
    assert "top_selling_pick" in PENDING_KINDS


def test_arms_one_option_per_printed_row_numbered_by_rank():
    pending = _armed()
    assert pending is not None
    assert pending.kind == "top_selling_pick"
    assert pending.asked_at_turn == 4
    assert [(o["position"], o["label"], o["code"]) for o in pending.options] == [
        (1, "SRTWT7445", "SRTWT7445"),
        (2, "SRTKT39SS", "SRTKT39SS"),
        (3, "SRTBS1020", "SRTBS1020"),
        (4, "SRTSH2201", "SRTSH2201"),
        (5, "SRTWC5501", "SRTWC5501"),
    ]
    assert pending.options[1]["name"] is None, "code only: the pick row carries no name"
    assert pending.options[1]["entity_type"] == "product"
    # A pick goes back to the ask that printed the list (contract 121 / AC-1704's
    # `status` carry), and the stored filters re-run it.
    assert pending.payload["domain"] == "sales"  # R7 (PLAN-prompt-dynamic-30sep D9)
    assert pending.payload["status"] == "top_selling"
    assert pending.payload["filters"] == _FILTERS


@pytest.mark.parametrize("result_set", [[], None])
def test_how_many_and_miss_arm_nothing(result_set):
    """The how-many reply and a miss print no list, so there is nothing to pick from."""
    assert _armed(result_set) is None


def test_no_turn_clock_same_as_the_customer_picker():
    from app.services.chatbot.turn.pending import ask, tick

    top = _armed()
    customer = ask("customer_pick", options=[{"position": 1, "label": "HANLIM", "payload": {}}])
    for _ in range(10):
        top, customer = tick(top), tick(customer)
    assert top is not None and top.kind == "top_selling_pick"
    assert customer is not None


def test_survives_the_session_store_round_trip():
    from app.services.chatbot.turn.pending import from_wire, to_wire

    pending = _armed()
    assert from_wire(to_wire(pending)) == pending


def test_a_bare_number_picks_that_row_and_the_list_stays_open():
    state = _decide(verdict(message_type="casual", reference_positions=[2]), pending=_armed())
    assert state.pending is not None and state.pending.kind == "top_selling_pick"
    assert state.pending.answered_positions == [2]
    assert state.focus.status == "top_selling"

    # a LATER reply resolves against the same list, exactly as a second pick over a
    # customer or product roster does
    state = _decide(verdict(message_type="casual", reference_positions=[4]), pending=state.pending)
    assert state.pending is not None and state.pending.kind == "top_selling_pick"
    assert state.pending.answered_positions == [2, 4]


def test_a_typed_code_picks_that_row():
    v = verdict(message_type="business_query", entities=[entity("srtkt39ss")])
    state = _decide(v, pending=_armed())
    assert state.pending is not None and state.pending.answered_positions == [2]


def test_a_typed_category_code_picks_that_row():
    """The printed label is the category CODE (owner ruling 26 Sep ~07:40Z, code only),
    so the code the line printed is what a typed answer matches (UAC AC-1966 as
    amended in S4). A category NAME no longer picks: the list never printed it."""
    v = verdict(
        message_type="business_query",
        entities=[entity("WC", hint="category", canonical_code="")],
    )
    state = _decide(v, pending=_armed(_CATEGORY_RESULT_SET))
    assert state.pending is not None and state.pending.answered_positions == [2]


def test_an_aside_keeps_the_list_open():
    pending = _armed()
    state = _decide(verdict(message_type="casual"), pending=pending)
    assert state.pending == pending


def test_the_list_closes_once_every_row_was_picked():
    """`apply`'s `stale_roster_closed` rule, the same one the pickers live under."""
    state = _decide(
        verdict(message_type="casual", reference_positions=[1, 2]),
        pending=_armed(_CATEGORY_RESULT_SET),
    )
    assert state.pending is not None and state.pending.answered_positions == [1, 2]
    state = _decide(verdict(message_type="casual"), pending=state.pending)
    assert state.pending is None
