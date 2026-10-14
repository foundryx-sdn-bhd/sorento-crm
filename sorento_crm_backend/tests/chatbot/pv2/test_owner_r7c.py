"""PV2-FIX r7c: the owner's turns as Jayden (staff) on the dev stack, plus the reviewer's guards.
Rows: `fixtures/livecheck_owner_r7.jsonl` (recorded state-in and parse; no model call)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.services.chatbot.pv2 import bridge, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.schema import Ask
from app.services.chatbot.pv2.state import ClarifyAct, HandOver, Lookup, SmallTalk
from tests.chatbot.pv2 import replay
from tests.chatbot.pv2.carry_fakes import ask as _base_ask, mk_state, out
from tests.chatbot.pv2.test_fix_round2 import _Fake, _e

FIXTURE = Path(__file__).parent / "fixtures" / "livecheck_owner_r7.jsonl"
BY_CASE = {json.loads(l)["case"]: json.loads(l) for l in FIXTURE.read_text(encoding="utf-8").splitlines() if l.strip()}
PROMPT = (Path(turn.__file__).parent / "prompt_v2.txt").read_text(encoding="utf-8").lower()
TODAY = date(2026, 10, 14)


def _run_row(case):
    (result,) = replay.run_case([dict(BY_CASE[case], _where=case)])
    return result


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


@pytest.mark.parametrize("case", list(BY_CASE), ids=list(BY_CASE))
def test_the_owner_turn_replays_to_its_expected_outcome(case):
    result = _run_row(case)
    assert not result.failures, result.failures


# ------------------------------------------------------------------ A eta: the held family is carried, not retyped


def test_a_refine_without_entities_sends_the_held_product_as_carried():
    result = _run_row("owner-A-eta-fbbe484e")
    action = result.actions[0]
    assert isinstance(action, Lookup)
    v = bridge.to_v1_verdict(result.state_out, action, today=TODAY)
    prods = [e for e in v["entities"] if e["hint"] == "product"]
    assert prods and all(e["current_message"] is False for e in prods), prods


# ------------------------------------------------------------------ B "all" under a v1 product picker


def test_free_value_all_alone_is_a_miss():
    action = _run_row("owner-B-all-19cc5117").actions[0]
    assert isinstance(action, ClarifyAct) and action.reask is True


def test_option_key_all_picks_every_option_of_the_v1_picker():
    result = _run_row("owner-B2-pick-all-structured")
    action = result.actions[0]
    assert isinstance(action, Lookup)
    v = bridge.to_v1_verdict(result.state_out, action, today=TODAY)
    answer = v["open_question_answer"]
    assert answer["mode"] == "pick" and answer["picked"] == list(range(1, 11)), answer
    assert v["message_type"] == "business_query"


def test_the_prompt_says_a_reply_choosing_every_option_is_option_key_all():
    assert 'option_key "all"' in PROMPT and "every option" in PROMPT


# ------------------------------------------------------------------ C attachment words are never a product


def test_the_prompt_says_attachment_words_are_never_a_product():
    assert "attachment words are never a product" in PROMPT


def test_a_product_that_resolves_to_nothing_never_discards_the_held_product():
    result = _run_row("owner-C-photo-and-drawing-6797d985")
    held = result.state_out.held
    assert [r.key for r in held.refs.get("product", [])] == ["SRTWC286"], held.refs


# ------------------------------------------------------------------ D a new ask under an offer


def test_an_option_less_answer_with_a_new_entity_is_a_new_ask_not_the_offer_answer():
    result = _run_row("owner-D-photo-strwc286-46d0a9b5")
    action = result.actions[0]
    assert isinstance(action, Lookup) and action.pending_kind is None and action.picked is None
    held = result.state_out.held
    assert held.domain == "product_attachment" and held.attachment_type == "product_photos"
    assert [r.key for r in held.refs["product"]] == ["strwc286"]


# ------------------------------------------------------------------ E photo for this product


def test_a_photo_refine_with_the_held_product_uses_it():
    result = _run_row("owner-E2-photo-this-product-held-product")
    held = result.state_out.held
    assert [r.key for r in held.refs["product"]] == ["SRTWC286"] and held.attachment_type == "product_photos"


def test_a_photo_refine_without_a_held_product_uses_the_saved_topic_or_asks():
    result = _run_row("owner-E-photo-this-product-7c08f094")
    action = result.actions[0]
    if isinstance(action, Lookup):
        v = bridge.to_v1_verdict(result.state_out, action, today=TODAY)
        assert [e["canonical_code"] for e in v["entities"] if e["hint"] == "product"] == ["SRTWC286"], v["entities"]
    else:
        assert isinstance(action, ClarifyAct) and not action.not_found


# ------------------------------------------------------------------ reviewer guards


def test_a_stale_portal_form_never_drops_the_product_on_the_next_stock_question():
    table = {"product|SRTWC286": ("exact", [("product", "SRTWC286")], False)}
    first = out(_ask(domain="portal_link", portal_form="stock_inquiry", entities=[_e(phrase="SRTWC286")]))
    state, _ = apply_turn(mk_state(), first, resolver=_Fake(table))
    second = out(_ask(act="refines_held", domain="inventory"))
    state, actions = apply_turn(state, second, resolver=_Fake(table))
    v = bridge.to_v1_verdict(state, actions[0], today=TODAY)
    assert v["domain_hint"] == "inventory" and v["portal_form"] is None
    assert [e["canonical_code"] for e in v["entities"] if e["hint"] == "product"] == ["SRTWC286"], v["entities"]


def test_an_entity_clarify_with_nothing_held_never_has_zero_options():
    parsed = out(_ask(act="unclear"), clarify={"needed": True, "phrase": "xyz", "about": "entity"})
    state, actions = apply_turn(mk_state(), parsed, resolver=_Fake({}))
    for action in actions:
        assert not (isinstance(action, ClarifyAct) and action.about == "entity" and not action.pending.options), action
