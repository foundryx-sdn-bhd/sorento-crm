"""PV2-FIX r7: the crew's r5 and r6 live checks (dev DB turns, 87 v2 turns) as a deterministic replay,
plus red tests for the r6 regressions. Rows come from `fixtures/livecheck_r5r6.jsonl`: recorded
parser outputs and recorded state-in (with the v1 open question mirrored as the engine does);
resolver answers are canned from the live reply. Regression rows carry the CORRECTED expectation
(crew TSV notes). No model call."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.services.chatbot.pv2 import bridge, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.schema import Ask, ParserV2Output
from app.services.chatbot.pv2.state import ClarifyAct, ConversationState, Held, Lookup, ResolvedRef
from tests.chatbot.pv2 import replay
from tests.chatbot.pv2.carry_fakes import ask as _base_ask, held as mk_held, mk_state, out

FIXTURE = Path(__file__).parent / "fixtures" / "livecheck_r5r6.jsonl"
ROWS = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
BY_CASE = {r["case"]: r for r in ROWS}
TODAY = date(2026, 10, 14)


def _run_row(case):
    row = dict(BY_CASE[case], _where=case)
    (result,) = replay.run_case([row])
    return result


@pytest.mark.parametrize("case", list(BY_CASE), ids=list(BY_CASE))
def test_the_live_turn_replays_to_its_expected_outcome(case):
    result = _run_row(case)
    assert not result.failures, result.failures


def test_every_recorded_parse_is_a_valid_parser_output():
    for r in ROWS:
        ParserV2Output.model_validate(r["parser_out"])
    assert len(ROWS) == 87


# ------------------------------------------------------------------ B1 a different product is a new topic


def test_a_different_product_after_an_order_is_a_new_topic_with_the_order_saved():
    result = _run_row("live-S6-8-t2")
    held = result.state_out.held
    assert held.domain == "inventory"
    assert list(held.refs) == ["product"], held.refs            # no delivery_order carried into the stock ask
    assert [t.domain for t in result.state_out.topics] == ["order"]
    assert [r.key for r in result.state_out.topics[0].refs["delivery_order"]] == ["202610-0151"]


# ------------------------------------------------------------------ B2 switch back with no key


def test_a_switch_back_with_no_key_goes_to_the_one_saved_topic():
    result = _run_row("live-S6-8-t3")
    action = result.actions[0]
    assert isinstance(action, Lookup), action
    assert result.state_out.held.domain == "order"
    assert [r.key for r in result.state_out.held.refs["delivery_order"]] == ["202610-0151"]


def test_a_clarify_about_an_entity_is_never_emitted_without_options():
    for case in ("live-S6-8-t3", "live-R6-S6-8a-t3", "live-R6-S6-8b-t3"):
        for action in _run_row(case).actions:
            if isinstance(action, ClarifyAct) and action.about == "entity" and not action.not_found:
                assert action.pending.options, f"{case}: option-less entity question"


# ------------------------------------------------------------------ B3 a quantity under a v1 yes/no offer


def test_a_bare_quantity_under_a_v1_offer_requantifies_instead_of_missing():
    result = _run_row("live-S10-11-t2")
    action = result.actions[0]
    assert isinstance(action, Lookup) and action.requantify is True, action
    v = bridge.to_v1_verdict(result.state_out, action, today=TODAY)
    assert v["demand_qty"] == 5


# ------------------------------------------------------------------ B4 portal link for a purchase request


def test_a_portal_link_ask_sends_v1_no_product_entities_and_no_carried_products():
    from app.services.chatbot.pv2.state import dump_state

    result = _run_row("live-X-zh-order4-t1")
    action = result.actions[0]
    assert isinstance(action, Lookup) and action.domain == "portal_link"
    v = bridge.to_v1_verdict(result.state_out, action, today=TODAY)
    assert v["portal_form"] == "purchase_request"
    assert [e for e in v["entities"] if e["hint"] == "product"] == [], v["entities"]
    focus = bridge.focus_from_v2(result.state_out.held)
    assert focus.products == [], "the typed codes were never a form the user filled in"
