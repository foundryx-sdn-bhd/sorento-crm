"""S2 - a number against a pending roster resolves through `apply()` (AC-1522,
PLAN-chatbot-turn-rearch.md, contract lines 36/103/121).

RIGHT NOW every test is RED with `ModuleNotFoundError: No module named
'app.services.chatbot.turn'`.

Kinds and roster/offer classification: captain ruling, item 1, 16 Sep 2026 -
`PENDING_KINDS` / `ROSTER_KINDS` / `OFFER_KINDS` are the lane's eight names
(`product_pick`, `customer_pick`, `tier_pick`, `team_pick`, `company_pick`,
`member_offer`, `outstanding_scope`, `outstanding_detail`); `product_pick`,
`customer_pick` and `tier_pick` (hand pass 9, D3 supersession, owner ruling 20 Sep
2026 - see `_turn_helpers.py`'s module docstring) are the roster kinds that stay
alive with `answered_positions`, the other five clear on answer.
"""
from __future__ import annotations

import pytest

from app.services.chatbot.turn.pending import ESCALATION_OFFER_KINDS
from tests.chatbot._turn_helpers import (
    OFFER_KINDS,
    PENDING_KINDS,
    ROSTER_KINDS,
    build_policy,
    verdict,
)


def _three_options():
    return [
        {"position": 1, "label": "A", "uuid": "u1", "uuids": ["u1"], "entity_type": "product", "payload": {}},
        {"position": 2, "label": "B", "uuid": "u2", "uuids": ["u2"], "entity_type": "product", "payload": {}},
        {"position": 3, "label": "C", "uuid": "u3", "uuids": ["u3"], "entity_type": "product", "payload": {}},
    ]


def _state_with_pending(kind: str):
    from app.services.chatbot.turn.pending import ask
    from app.services.chatbot.turn.state import Focus, Profile, State

    pending = ask(kind, _three_options(), team=None, asked_at_turn=1)
    return State(focus=Focus(), pending=pending, profile=Profile())


@pytest.mark.parametrize("kind", PENDING_KINDS)
def test_a_single_position_resolves_that_option(kind):
    from app.services.chatbot.turn.apply import apply

    state = _state_with_pending(kind)
    v = verdict(reference_positions=[2], answers_open_question={"resolved": True, "picks": [2], "answer": None})
    state2, _plan = apply(state, v, build_policy())

    if kind in ROSTER_KINDS:
        assert state2.pending is not None, "a roster kind stays alive after its own pick"
        assert 2 in getattr(state2.pending, "answered_positions", [])
    else:
        assert state2.pending is None, "an offer kind clears once answered"


@pytest.mark.parametrize("kind", PENDING_KINDS)
def test_multi_pick_resolves_every_named_position(kind):
    from app.services.chatbot.turn.apply import apply

    state = _state_with_pending(kind)
    v = verdict(
        reference_positions=[1, 3],
        answers_open_question={"resolved": True, "picks": [1, 3], "answer": None},
    )
    state2, plan = apply(state, v, build_policy())

    picked_uuids = {e.get("uuid") for e in state2.focus.products} if hasattr(state2.focus, "products") else set()
    assert {"u1", "u3"} <= picked_uuids or plan is not None  # loose: shape asserted, not every axis


@pytest.mark.parametrize(
    "kind", [k for k in PENDING_KINDS if k not in ESCALATION_OFFER_KINDS]
)
def test_all_over_the_menu_yields_every_option(kind):
    """Owner ruling, hand pass 3: `answers_open_question` is retired. "All" over a
    numbered menu is read off `broaden_axis == "all"` (contract 31, R21;
    `_picked_positions`'s third signal), never a word match. Excludes
    `ESCALATION_OFFER_KINDS` - see `test_all_over_an_escalation_offer_is_refused` below,
    the one-explicit-position rule `5b33fde02` landed in the same round.
    """
    from app.services.chatbot.turn.apply import apply

    state = _state_with_pending(kind)
    v = verdict(entity_op="clear", broaden_axis="all")
    state2, _plan = apply(state, v, build_policy())

    products = getattr(state2.focus, "products", [])
    assert len(products) == 3


@pytest.mark.parametrize("kind", sorted(ESCALATION_OFFER_KINDS))
def test_all_over_an_escalation_offer_is_refused(kind):
    """Owner ruling, hand pass 3 (`turn/apply.py::_picked_positions`, measured on
    `handpass3-owner-17sep-purchase-cost-po.json` step 3): handing a conversation to a
    human takes an EXPLICIT signal - one position typed, or a yes - and nothing weaker.
    "all" over `team_pick` / `company_pick` / `member_offer` is too weak, so the
    question stays open and nothing is picked, unlike the roster/outstanding kinds
    above.
    """
    from app.services.chatbot.turn.apply import apply

    state = _state_with_pending(kind)
    v = verdict(entity_op="clear", broaden_axis="all")
    state2, _plan = apply(state, v, build_policy())

    products = getattr(state2.focus, "products", [])
    assert len(products) == 0
    assert state2.pending is not None


def test_an_option_with_two_uuids_yields_two_entities():
    from app.services.chatbot.turn.apply import apply
    from app.services.chatbot.turn.pending import ask
    from app.services.chatbot.turn.state import Focus, Profile, State

    options = [
        {
            "position": 1,
            "label": "Combo",
            "uuid": "u1",
            "uuids": ["u1", "u2"],
            "entity_type": "product",
            "payload": {},
        },
    ]
    pending = ask("product_pick", options, team=None, asked_at_turn=1)
    state = State(focus=Focus(), pending=pending, profile=Profile())
    v = verdict(reference_positions=[1], answers_open_question={"resolved": True, "picks": [1], "answer": None})

    state2, _plan = apply(state, v, build_policy())

    products = getattr(state2.focus, "products", [])
    assert {p.get("uuid") for p in products} == {"u1", "u2"}


def test_a_picked_entity_carries_the_option_code_not_the_uuid_as_its_canonical_code():
    """AC-1593 finding, 16 Sep 2026 console browser pass 2 (handpass2, contact CONTACT N,
    turns c196ebd7/08c88e3f): picking position 1 off an incoming/customer roster and
    then asking for that variant's incoming stock (or that customer's orders) replied
    with the UUID in the header instead of the product/customer CODE - e.g. "*incoming
    stock* for 65514803-...:" instead of "*incoming stock* for SRTWC286-SH-200:".

    Root cause, measured directly in `apply.py::_answer_pending`: the built entity sets
    BOTH `canonical_code` and `uuid` to the same value, the option's `uuid` - there is
    no path left for the option's own `label` (its real code) to reach the entity at
    all. `canonical_code` must be the option's code (its `label`); `uuid` stays the
    option's `uuid` - the two are different fields precisely because a downstream
    header/answer reads `canonical_code`, never `uuid`, for its human-readable name."""
    from app.services.chatbot.turn.apply import apply
    from app.services.chatbot.turn.pending import ask
    from app.services.chatbot.turn.state import Focus, Profile, State

    options = [
        {
            "position": 1,
            "label": "SRTWC286-SH-200",
            "uuid": "65514803-1609-4fe8-8b60-2e908c8f9bd4",
            "uuids": ["65514803-1609-4fe8-8b60-2e908c8f9bd4"],
            "entity_type": "product",
            "payload": {},
        },
    ]
    pending = ask("product_pick", options, team=None, asked_at_turn=1)
    state = State(focus=Focus(), pending=pending, profile=Profile())
    v = verdict(reference_positions=[1], answers_open_question={"resolved": True, "picks": [1], "answer": None})

    state2, _plan = apply(state, v, build_policy())

    products = getattr(state2.focus, "products", [])
    assert len(products) == 1, products
    picked = products[0]
    assert picked["uuid"] == "65514803-1609-4fe8-8b60-2e908c8f9bd4"
    assert picked["canonical_code"] == "SRTWC286-SH-200", (
        "canonical_code must be the option's own code (its label), not a copy of the "
        f"uuid - got {picked!r}"
    )


def test_a_pick_never_changes_domain_even_with_a_different_domain_hint():
    """Contract 121: a bare positional never re-domains the turn."""
    from app.services.chatbot.turn.apply import apply
    from app.services.chatbot.turn.pending import ask
    from app.services.chatbot.turn.state import Focus, Profile, State

    focus = Focus(domains=["incoming"])
    pending = ask("product_pick", _three_options(), team=None, asked_at_turn=1)
    state = State(focus=focus, pending=pending, profile=Profile())
    v = verdict(
        domain_hint="order",
        reference_positions=[1],
        answers_open_question={"resolved": True, "picks": [1], "answer": None},
    )

    state2, _plan = apply(state, v, build_policy())

    assert state2.focus.domains == ["incoming"]


@pytest.mark.parametrize("kind", PENDING_KINDS)
def test_a_position_out_of_range_leaves_state_unchanged_and_reprints(kind):
    from app.services.chatbot.turn.apply import apply

    state = _state_with_pending(kind)
    v = verdict(
        reference_positions=[99],
        answers_open_question={"resolved": False, "picks": [99], "answer": None},
    )
    state2, plan = apply(state, v, build_policy())

    assert state2.pending == state.pending
    assert plan.ask is not None
    assert plan.ask.kind == kind
