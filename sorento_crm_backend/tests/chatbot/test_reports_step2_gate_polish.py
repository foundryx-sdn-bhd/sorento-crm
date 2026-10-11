"""Phase 2 RED tests - REPORTS-STEP2 gate polish (11 Oct 2026).

(a) a ranking waiting on its period keeps the new values a restated ask carries and does not repeat
    the period question word for word, and the re-ask is bounded;
(b) a location word the parser typed as a warehouse is not reported as a product miss.

Driven through parser output fields only, never message text.
"""
from __future__ import annotations

from typing import Any

import pytest

from tests.chatbot import test_report_ask_lane as lane
from tests.chatbot.test_report_ask_lane import PERIOD_Q, SEP, _e, _rank, console  # noqa: F401
from tests.chatbot.test_reports_step2_lane import _s2_harness  # noqa: F401  (autouse fixture by name)

NO_PERIOD = {"date_filter_start": None, "date_filter_end": None}


# ================================================================================ (a)


def _restated(console, **overrides: Any) -> tuple[str, list[dict[str, Any]]]:
    return console.say(_rank(_e("Sorento", "brand"), **{**NO_PERIOD, **overrides}), "make it 20 for sorento")


def test_a_a_restated_ask_without_a_period_is_not_answered_word_for_word(console) -> None:
    text, calls = console.say(_rank(_e("Sorento", "brand"), top_n=10, **NO_PERIOD), "top 10 salesman for sorento")
    assert text.strip() == PERIOD_Q and calls == [], (text, calls)
    again, calls = _restated(console, top_n=20)
    assert calls == [], calls
    assert again.strip() != text.strip(), again
    assert "period" in again.lower(), again


def _pending(console) -> dict[str, Any]:
    from tests.chatbot.test_reports_step2_lane import _wire

    return _wire(console).get("required_ask") or {}


def test_a_b_the_new_value_is_kept_when_the_period_finally_comes(console) -> None:
    console.say(_rank(_e("Sorento", "brand"), top_n=10, **NO_PERIOD), "top 10 salesman for sorento")
    _restated(console, top_n=20)
    # The restated branch alone puts the new count on the open question.
    pending = _pending(console)
    assert (pending.get("extras") or {}).get("top_n") == 20, pending
    _text, calls = console.say(lane._reply(**SEP), "last month")
    (args,) = calls
    assert args["top_n"] == 20, args
    assert args["brand_ids"] == [console.ids["brand"]], args
    assert args["date_from"] == "2026-09-01", args


def test_a_c_the_re_ask_is_bounded(console) -> None:
    console.say(_rank(_e("Sorento", "brand"), top_n=10, **NO_PERIOD), "top 10 salesman for sorento")
    _restated(console, top_n=20)
    text, calls = _restated(console, top_n=30)
    assert calls == [], calls
    assert "Which period?" not in text, text


def test_p2_a_new_ranking_axis_starts_the_period_question_fresh(console) -> None:
    console.say(_rank(_e("Sorento", "brand"), top_n=10, **NO_PERIOD), "top 10 salesman for sorento")
    _restated(console, top_n=20)  # one miss is now on the open period question
    text, calls = console.say(
        _rank(group_by="customer", top_n=5, ranking_drill="global", **NO_PERIOD), "top 5 customers"
    )
    assert calls == [], calls
    assert text.strip() == PERIOD_Q, text


# ================================================================================ (b)


def _outstanding_with_location(sf, mp, word: str):
    """The generic resolver places the product only; the warehouse word is one the LANE resolves
    itself (`resolve_warehouse_token`), so the resolver echoes it back unplaced."""
    import uuid

    from app.models.inventory import Warehouse
    from tests.chatbot import test_reports_step2_s5a as s5a
    from tests.chatbot.test_outstanding_lane import REPORT_HIT, _qf, _run_turn, _seed_contact

    _seed_contact(sf, variables={})
    db = sf()
    try:
        db.add(Warehouse(id=str(uuid.uuid4()), warehouse_code="ZZT-SENTUL", warehouse_name="ZZT SENTUL", is_active=True))
        db.commit()
    finally:
        db.close()
    ents = [
        {"raw": s5a.PRODUCT_CODE, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
        {"raw": word, "hint": "warehouse", "canonical_code": None, "current_message": True, "confident": True},
    ]
    return _run_turn(
        sf, mp, qf=_qf(order_status="so_outstanding", entities=ents), text_body="outstanding at location",
        msg_id="ZZT-polish-b", attributes=s5a.OUT_ATTRS, matches=s5a.PRODUCT_MATCH, mcp_response=REPORT_HIT,
    )


@pytest.mark.parametrize("word", ["Sentul", "ZZT-SENTUL"])
def test_b_a_location_the_lane_placed_is_not_reported_as_not_found(session_factory, monkeypatch, word) -> None:
    from tests.chatbot.test_reports_step2_s5a import _harness  # noqa: F401

    result, _calls = _outstanding_with_location(session_factory, monkeypatch, word)
    text = (result.reply or {}).get("text") or ""
    assert "Location: all" not in text, ("the word was not placed as the location", text)
    assert "could not find" not in text.lower() and "couldn't find" not in text.lower(), text


def test_b_b_a_location_word_nothing_holds_is_still_said(session_factory, monkeypatch) -> None:
    """Guard (passes now): a word that is no warehouse at all keeps the not-found line."""
    from tests.chatbot.test_reports_step2_s5a import _harness  # noqa: F401

    result, _calls = _outstanding_with_location(session_factory, monkeypatch, "Gudang")
    text = (result.reply or {}).get("text") or ""
    assert "could not find Gudang" in text, text


def test_p1_guesses_for_a_placed_location_word_never_reach_the_outstanding_call(session_factory, monkeypatch) -> None:
    """The warehouse word is the only unplaced token and the generic resolver also guessed products
    for it: placing the location must not leave those guesses in the call."""
    import uuid

    from app.models.inventory import Warehouse
    from tests.chatbot import test_reports_step2_s5a as s5a
    from tests.chatbot.conftest import validating_resolve_entity
    from tests.chatbot.test_outstanding_lane import REPORT_HIT, ResolveGateServices, _qf, _run_turn, _seed_contact
    from tests.chatbot.test_reports_step2_s5a import _harness  # noqa: F401

    guesses = [
        {"uuid": str(uuid.uuid4()), "entity_type": "product", "canonical_code": f"ZZTGUESS{i}", "similarity": 0.3,
         "match_tier": "trgm"}
        for i in (1, 2)
    ]

    def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        asked = list(body.get("tokens") or [])
        resolutions = []
        for raw in asked:
            if raw == s5a.PRODUCT_CODE:
                resolutions.append({"raw": raw, "token": raw, "matches": [s5a.PRODUCT_MATCH[raw]]})
            else:
                resolutions.append({"raw": raw, "token": raw, "matches": guesses})
        return {
            "tokens": asked, "resolutions": resolutions,
            "unresolved_tokens": [t for t in asked if t != s5a.PRODUCT_CODE],
        }

    services = ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}],
        resolve_entity=validating_resolve_entity(_resolve_entity),
        probe=lambda **_: None,
    )
    _seed_contact(session_factory, variables={})
    db = session_factory()
    try:
        db.add(Warehouse(id=str(uuid.uuid4()), warehouse_code="ZZT-SENTUL", warehouse_name="ZZT SENTUL", is_active=True))
        db.commit()
    finally:
        db.close()
    ents = [
        {"raw": s5a.PRODUCT_CODE, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
        {"raw": "Sentul", "hint": "warehouse", "canonical_code": None, "current_message": True, "confident": True},
    ]
    result, calls = _run_turn(
        session_factory, monkeypatch, qf=_qf(order_status="so_outstanding", entities=ents),
        text_body="outstanding at location", msg_id="ZZT-polish-p1", attributes=s5a.OUT_ATTRS,
        resolve_services=services, mcp_response=REPORT_HIT,
    )
    sent = [a for n, a in calls if n in ("crm_report", "crm_outstanding_report")]
    assert sent, calls
    assert "ZZTGUESS" not in str(sent) and not any(g["uuid"] in str(sent) for g in guesses), sent
    # Only the typed product reaches the transformer: the two guesses are dropped.
    assert sent[0]["_diagnostics"]["entities_in"] == 1, sent[0]["_diagnostics"]
    text = (result.reply or {}).get("text") or ""
    assert "could not find" not in text.lower(), text


def test_p4_a_restate_without_group_by_keeps_the_held_ranking(console) -> None:
    """A real "top 20" restatement parses with no group_by: that is the held axis, not a new one."""
    console.say(_rank(_e("Sorento", "brand"), top_n=10, **NO_PERIOD), "top 10 salesman for sorento")
    text, calls = console.say(_rank(_e("Sorento", "brand"), group_by=None, top_n=20, **NO_PERIOD), "top 20")
    assert calls == [], calls
    assert text.startswith("I still need a period for that."), text
    pending = _pending(console)
    assert (pending.get("extras") or {}).get("top_n") == 20, pending
    assert int(pending.get("misses") or 0) == 1, pending


def test_p1_resolve_kinds_drops_guesses_before_it_places_the_location_word(session_factory, monkeypatch) -> None:
    """The seam itself: the warehouse word is the only unplaced token and the resolver guessed products
    for it. `_without_guesses` only acts while something is unplaced, so it must run BEFORE the lane's
    location word leaves that map. (Also the whole-message phrase case: a resolution whose token the
    resolver was never asked about.)"""
    import uuid

    from app.models.inventory import Warehouse
    from app.services.chatbot import turn_runtime
    from app.services.chatbot.lanes.business import resolve_gate
    from tests.chatbot.test_outstanding_lane import PRODUCT_CODE, PRODUCT_UUID

    db = session_factory()
    try:
        db.add(Warehouse(id=str(uuid.uuid4()), warehouse_code="ZZT-SENTUL", warehouse_name="ZZT SENTUL", is_active=True))
        db.commit()
        exact = {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE, "match_tier": "exact"}
        guesses = [
            {"uuid": str(uuid.uuid4()), "entity_type": "product", "canonical_code": f"ZZTGUESS{i}", "match_tier": "trgm"}
            for i in (1, 2)
        ]
        resolutions = [
            {"token": PRODUCT_CODE, "matches": [exact]},
            {"token": "Sentul", "matches": guesses},
            {"token": "outstanding at sentul", "matches": guesses},  # a token the resolver was never asked
        ]
        compatible = [{**m, "code": m["canonical_code"]} for m in (exact, *guesses)]

        def stub(ctx, entry, item, **kwargs):
            return {
                "resolved": {
                    "tokens": [PRODUCT_CODE, "Sentul"], "resolutions": resolutions, "unresolved_tokens": ["Sentul"],
                },
                "gate": {
                    "compatible_entities": compatible,
                    "gate_debug": {"domain": "order", "allowed_lookup": ["product"], "entities_count": 3},
                },
                "aggregate": None, "tier_gate": None, "_exit_kind": "continue",
            }

        monkeypatch.setattr(resolve_gate, "run", stub)
        entities = [
            {"raw": PRODUCT_CODE, "hint": "product", "canonical_code": None, "current_message": True},
            {"raw": "Sentul", "hint": "warehouse", "canonical_code": None, "current_message": True},
        ]
        ctx = {"parse": {"output": {"domain_hint": "order", "intent_hint": "check_order", "entities": entities}}}
        outcome = turn_runtime.resolve_kinds(
            db, ctx=ctx, branch_kind="check_order", space_id=None, dry_run=True
        )
    finally:
        db.close()
    codes = [e.get("canonical_code") or e.get("code") for e in outcome.compatible_entities]
    assert codes == [PRODUCT_CODE], codes
    assert "sentul" not in outcome.unplaced_tokens, outcome.unplaced_tokens
