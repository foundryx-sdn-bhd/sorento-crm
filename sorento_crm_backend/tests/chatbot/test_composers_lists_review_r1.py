"""CHAT-COMPOSER Lists review round 1 red tests.

Spec: `documentation/plans/chatbot/chat-composer-seam-design-lists.md`, "Review round 1" (LD4 to LD6, S1, K2, K3).
B1/LD4 was replaced by LD7 (review round 2): a code list that needs more than one message never states a total,
whatever its shape (twins, a two-type token, few rows). LD3 amended: the narrow sentence states the codes listed. LD5: a bare number picks a card. K2: a gone
card returns the error fragment. LD6: `ref_staff` comes from the profile and `ref_page` only from the carry.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.services.chatbot.label_catalog import LANE_LABELS, Localizer, defaults, tokens_match
from tests.chatbot.test_composers_lists_l1 import FOOT, _card, _code, _code_of, _run, _uuid
from tests.chatbot.test_composers_stock_xd import _set_gate

NARROW_KEY = "Those are the first {n} matches. Send more of the code to narrow it."


def _ent(code, k=0, *, entity_type="product", raw="ZZQ"):
    return {
        "entity_type": entity_type,
        "uuid": _uuid(code, k + (50 if entity_type != "product" else 0)),
        "code": code,
        "canonical_code": code,
        "raw": raw,
    }


# --------------------------------------------------------------------------- #
# B1 / LD4: capped on product rows per token
# --------------------------------------------------------------------------- #


def _twin_items(n_codes, desc="x" * 160):
    return [_card(_code(i), desc, company=co) for i in range(n_codes) for co in ("Sorento", "Mocha")]


def _assert_total_less(out):
    assert "set_window" in out, "the list must be cut to one message"
    first = out["response"].splitlines()[0]
    assert first.startswith("Here are the matching products (showing 1 to "), first
    assert "capped" not in out["set_window"]["carry"]


def test_twinned_codes_never_state_a_total(monkeypatch):
    _set_gate(monkeypatch, True)
    entities = [_ent(_code(i), k) for i in range(10) for k in (0, 1)]
    out = _run(0, items=_twin_items(10), entities=entities)
    _assert_total_less(out)
    assert len(out["set_window"]["carry"]["code_ids"]) == 10


def test_a_two_type_token_never_states_a_total(monkeypatch):
    _set_gate(monkeypatch, True)
    entities = [_ent(_code(i)) for i in range(10)] + [
        _ent(f"CUST{i:02d}", entity_type="customer") for i in range(10)
    ]
    _assert_total_less(_run(10, desc="x" * 400, entities=entities))


def test_a_short_single_type_list_never_states_a_total_either(monkeypatch):
    _set_gate(monkeypatch, True)
    entities = [_ent(_code(i)) for i in range(9)]
    _assert_total_less(_run(9, desc="x" * 430, entities=entities))


def test_the_last_page_states_the_codes_listed(monkeypatch):
    _set_gate(monkeypatch, True)
    predicate = {
        "require": {}, "qualifying_total": 10, "truncated": False, "unrecognized_terms": [], "class_labels": [],
        "set_noun": "matching products", "code_list": True, "offset": 9,
    }
    out = _run(0, items=[_card(_code(9), "d")], predicate=predicate)
    lines = [ln for ln in out["response"].strip().split("\n") if ln]
    assert lines[-1] == FOOT
    assert lines[-2] == "Those are the first 10 matches. Send more of the code to narrow it.", lines[-3:]
    assert "That is all" not in out["response"]


# --------------------------------------------------------------------------- #
# LD3 amended: the sentence carries {n}
# --------------------------------------------------------------------------- #


def test_narrow_sentence_is_a_catalogued_template(monkeypatch):
    assert NARROW_KEY in LANE_LABELS
    for lang in ("ms", "zh"):
        assert "{n}" in LANE_LABELS[NARROW_KEY][lang]
        assert tokens_match(NARROW_KEY, LANE_LABELS[NARROW_KEY][lang])
    _set_gate(monkeypatch, True)
    for n in (10, 20):
        english = f"Those are the first {n} matches. Send more of the code to narrow it."
        for lang in ("ms", "zh"):
            out = Localizer(lang, defaults(lang)).reply(english)
            assert out != english and str(n) in out, out
    _set_gate(monkeypatch, False)
    english = "Those are the first 10 matches. Send more of the code to narrow it."
    assert Localizer("zh", defaults("zh")).reply(english) == english


# --------------------------------------------------------------------------- #
# LD5: a bare number picks a card
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", [{"code_list": True}, {"report_groups": True}], ids=["code_list", "report_groups"])
def test_a_bare_number_after_a_cut_list_is_a_pick_not_a_count(key):
    from app.services.chatbot.turn_runtime import with_set_count_from_text

    verdict = {"top_n": None, "reference_positions": [2], "entities": []}
    carried = {"set_key": {**key, "shown": 9, "total": 13}}
    assert with_set_count_from_text(verdict, "2", carried=carried) == verdict


# --------------------------------------------------------------------------- #
# K2 / LD6: the reference page turn
# --------------------------------------------------------------------------- #


def _run_fetch(db, *, ref_page, staff=True, attributes=("purchase_orders.placed",), po=()):
    from app.services.chatbot.lanes.business import run_fetch
    from app.services.chatbot.lanes.business.services import FetchServices

    calls: list[str] = []

    def _no_tool(name, args):
        calls.append(name)
        raise AssertionError("no tool may run for a reference page")

    out = {
        "domain_hint": "order", "intent_hint": "check_order", "message_type": "business_query", "entities": [],
        "po_numbers": list(po), "ref_staff": staff,
    }
    if ref_page is not None:
        out["ref_page"] = ref_page
    payload = {
        "_exit_kind": "continue",
        "gate": {"compatible_entities": []},
        "ctx": {
            "contact": {"id": str(uuid.uuid4())},
            "access": {"attributes": list(attributes)},
            "parse": {"output": out},
        },
    }
    return run_fetch(payload, services=FetchServices(mcp_call=_no_tool), db=db), calls


def _gone_carry():
    return {"ref_cards": [{"kind": "po", "id": str(uuid.uuid4()), "lines": 25}], "card": 0, "shown": 10, "total": 25}


def test_a_gone_card_returns_the_error_fragment_and_calls_no_tool(session_factory, monkeypatch):
    _set_gate(monkeypatch, True)
    fragment, calls = _run_fetch(session_factory(), ref_page=_gone_carry())
    assert fragment["kind"] == "error" and fragment["outcome"] == "not_found"
    assert calls == []


def test_a_ref_page_turn_with_no_db_returns_the_error_fragment(monkeypatch):
    _set_gate(monkeypatch, True)
    fragment, calls = _run_fetch(None, ref_page=_gone_carry())
    assert fragment["kind"] == "error" and fragment["outcome"] == "not_found"
    assert calls == []


def _runner_output(session_factory, monkeypatch, *, profile, verdict_extra, carry=None):
    from app.services.chatbot import turn_runtime
    from app.services.chatbot.lanes import business
    from app.services.chatbot.turn.plan import FetchSpec
    from app.services.chatbot.turn.state import Focus
    from tests.chatbot._turn_helpers import verdict

    seen: list[dict[str, Any]] = []

    def fake_run_fetch(payload, **kwargs):
        seen.append(payload["ctx"]["parse"]["output"])
        return {"fetch": {"has_result": True, "response": "stubbed", "answers": []}}

    monkeypatch.setattr(business, "run_fetch", fake_run_fetch)
    v = verdict(domain_hint="order", intent_hint="check_order", **verdict_extra)
    runner = turn_runtime.make_tool_runner(
        session_factory(),
        ctx={"parse": {"output": v}},
        verdict=v,
        focus=Focus(domains=["order"]),
        compatible_entities=[],
        predicate=None,
        unplaced={},
        space_id=None,
        dry_run=True,
        turn_trace=type("Trace", (), {"add": lambda self, *a, **k: None})(),
        profile=profile,
    )
    filters = {"set_page": {"set_key": carry}} if carry else {}
    runner("order", FetchSpec(domain="order", entities=[], filters=filters, date_window=None))
    assert seen
    return seen[0]


def test_ref_staff_comes_from_the_profile_not_the_verdict(session_factory, monkeypatch):
    from app.services.chatbot.turn.state import Profile

    _set_gate(monkeypatch, True)
    out = _runner_output(
        session_factory, monkeypatch, profile=Profile(tier="dealer"),
        verdict_extra={"ref_staff": True, "po_numbers": ["PO-2026/04-0011"]},
    )
    assert out.get("ref_staff") is not True, out.get("ref_staff")


def test_staff_profile_still_sets_ref_staff(session_factory, monkeypatch):
    from app.services.chatbot.turn.state import Profile

    _set_gate(monkeypatch, True)
    out = _runner_output(session_factory, monkeypatch, profile=Profile(tier="office"), verdict_extra={})
    assert out.get("ref_staff") is True


@pytest.mark.parametrize("tier", ["office", "dealer"])
def test_a_verdict_supplied_ref_page_is_dropped(session_factory, monkeypatch, tier):
    from app.services.chatbot.turn.state import Profile

    _set_gate(monkeypatch, True)
    out = _runner_output(
        session_factory, monkeypatch, profile=Profile(tier=tier),
        verdict_extra={"ref_page": {"ref_cards": [{"kind": "po", "id": "x", "lines": 25}], "card": 0, "shown": 10, "total": 25}},
    )
    assert "ref_page" not in out


def test_the_carry_still_sets_ref_page(session_factory, monkeypatch):
    from app.services.chatbot.turn.state import Profile

    _set_gate(monkeypatch, True)
    carry = {"ref_cards": [{"kind": "po", "id": "x", "lines": 25}], "card": 0, "shown": 10, "total": 25, "domain": "order"}
    out = _runner_output(session_factory, monkeypatch, profile=Profile(tier="office"), verdict_extra={}, carry=carry)
    assert out["ref_page"] == carry
