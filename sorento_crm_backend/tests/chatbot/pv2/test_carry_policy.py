"""ClarifyPolicy behaviour for a spec with attribute null (contract sections 3 and 4)."""
from __future__ import annotations

from app.services.chatbot.pv2.apply import DEFAULT_POLICY, ClarifyPolicy, apply_turn
from app.services.chatbot.pv2.state import ClarifyAct, Lookup
from tests.chatbot.pv2.carry_fakes import FakeResolver, ask, held, mk_state, option, out, spec

_LENGTH = option(
    "dim_length",
    "Length",
    {"kind": "spec", "attribute": "dim_length", "comparator": "eq", "value": 600, "unit": "mm"},
)
_CFG = {"specs": {"|600": {"status": "ambiguous", "specs": [], "options": [_LENGTH.model_dump()]}}}


def _turn(policy):
    state = mk_state(held("product_attachment", {"product": ["SRTWC286"]}))
    o = out(ask(act="refines_held", specs=[spec(number=600, unit="mm")]))
    return apply_turn(state, o, resolver=FakeResolver(_CFG), policy=policy)


def test_default_policy_still_asks_with_one_candidate():
    assert DEFAULT_POLICY.decide("spec_attribute", None) == "ask"
    new, actions = _turn(DEFAULT_POLICY)
    assert isinstance(actions[0], ClarifyAct)
    assert actions[0].about == "spec_attribute"
    assert new.held.specs == []


def test_default_row_applies_the_single_candidate():
    policy = ClarifyPolicy({("spec_attribute", None): "default"})
    new, actions = _turn(policy)
    assert isinstance(actions[0], Lookup)
    assert [(s.attribute, s.value, s.unit) for s in new.held.specs] == [("dim_length", 600, "mm")]
    assert new.pending is None
