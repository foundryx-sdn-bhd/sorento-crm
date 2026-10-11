"""Final nits: the parser prompt never reads a bare number as a promotion kind; the reply dedupe
keeps two different promotions that share a title. Fake names only."""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.main import app
from app.services.chatbot.lanes.business.services import ResolveGateServices
from tests.chatbot import test_outstanding_lane as lane_harness
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _seed_contact
from tests.chatbot.test_promo_owner_round2 import _turn


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_the_prompt_never_reads_a_bare_number_as_a_promotion_kind():
    """(1) The PROMOTION KIND section: a kind is set only when the message names it in words; a
    bare number, or a reply to a numbered list, is never a promotion_kind."""
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

    section = SEMANTIC_PARSER_PROMPT.split("== PROMOTION KIND ==", 1)[1]
    section = section.split("== ", 1)[0].split("━", 1)[0]  # this section only
    flat = " ".join(section.lower().split())
    assert "bare number" in flat, flat
    assert "numbered list" in flat, flat
    assert "never" in flat and "promotion_kind" in flat, flat
    assert "in words" in flat or "names the kind" in flat or "names it" in flat, flat


def test_two_promotions_sharing_a_title_but_not_dates_are_both_listed(session_factory, monkeypatch):
    """(2) same title + same dates under two companies is one line; the same title with different
    dates is another promotion and keeps its line."""
    _seed_contact(session_factory, variables={})
    pid = str(uuid.uuid4())

    def _call(name: str, args: dict[str, Any]) -> Any:
        def row(i, company, start, end):
            return {
                "id": f"00000000-0000-0000-0000-0000000000{i:02d}", "description": "ZZT MOCHA A3 FLYER", "company_name": company,
                "start_date": start, "end_date": end, "is_expired": False, "attachments": [],
            }

        data = [
            row(1, "Sorento", "2026-10-01", "2026-12-31"),
            row(2, "Mocha", "2026-10-01", "2026-12-31"),
            row(3, "Sorento", "2027-01-01", "2027-03-31"),
        ]
        body = {"data": data, "pagination": {"total": 3, "page": 1, "limit": 10}, "empty": False, "fallback_used": False}
        return lane_harness._present_response()(name, json.dumps(body))

    monkeypatch.setattr(lane_harness, "_capturing_mcp", lambda response=None: (_call, []))

    def _resolve(body: dict[str, Any]) -> dict[str, Any]:
        asked = list(body.get("tokens") or [])
        match = {"uuid": pid, "entity_type": "product", "canonical_code": "ZZTFLY1", "match_tier": "exact"}
        return {"tokens": asked, "resolutions": [{"raw": r, "token": r, "matches": [match]} for r in asked], "unresolved_tokens": []}

    services = ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}], resolve_entity=_resolve, probe=lambda **_: None
    )
    qf = _parser_output(
        domain_hint="promotion", intent_hint="check_promotion",
        entities=[{"raw": "ZZTFLY1", "hint": "product", "canonical_code": None, "current_message": True, "confident": True}],
    )
    reply = _turn(session_factory, monkeypatch, services, qf, "promo for ZZTFLY1", "zzt-fn-dedupe")
    assert reply.count("ZZT MOCHA A3 FLYER") == 2, reply
    assert reply.count("2026-12-31") == 1 and reply.count("2027-03-31") == 1, reply


def test_a_message_the_parser_marks_as_a_reference_never_filters_by_its_kind(session_factory, monkeypatch):
    """A parser output with reference_target set (or reference_positions) is a reference, not a new
    ask: its promotion_kind is ignored: no kind filter, never "There is no ... right now.".
    Live shape: bare "2" -> {promotion_kind "pp", reference_positions [], reference_target "dym"}."""
    from app.models.marketing import PromotionType
    from app.services.chatbot.lanes.business.services import _resolve_entity
    from tests.chatbot.test_outstanding_lane import _seed_contact  # noqa: F401
    from tests.chatbot.test_promo_kind_subject import PROMO_TOOL
    from tests.chatbot.test_promo_menu_digits import _ask_qf, _world
    from tests.chatbot.test_promo_owner_round2 import _mcp

    _world(session_factory, basin_promo=False)
    db = session_factory()
    db.add(PromotionType(type_code="pp", type_name="PP Promo", show_expired=True, match_markers=[], match_priority=60, is_default=False))
    db.commit()
    captured = _mcp(monkeypatch, session_factory)
    services = ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in ("Sorento Office", "Sorento Dealer", "End User")],
        resolve_entity=_resolve_entity(session_factory()), probe=lambda **_: None,
    )
    _turn(session_factory, monkeypatch, services, _ask_qf("cabinet", brand="Sorento"), "Pls send sorento cabinet promotion", "zzt-fn-ref-ask")
    _turn(
        session_factory, monkeypatch, services,
        _parser_output(
            domain_hint="promotion", intent_hint="check_promotion", entities=[], promotion_kind=None,
            entity_op="replace_combine", reference_positions=[3], reference_target="result",
            open_question_answer={"mode": "pick", "items": [], "picked": [3], "qty_for_all": None},
        ),
        "basin cabinet", "zzt-fn-ref-answer",
    )
    n = len(captured)
    bare = _parser_output(
        domain_hint="promotion", intent_hint="check_promotion", entities=[], promotion_kind="pp",
        reference_positions=[], reference_target="dym", open_question_answer={"mode": None, "items": [], "picked": [], "qty_for_all": None},
    )
    reply = _turn(session_factory, monkeypatch, services, bare, "2", "zzt-fn-ref-2")
    assert "There is no" not in reply, reply
    assert all(not a.get("promotion_types") for name, a in captured[n:] if name == PROMO_TOOL), captured[n:]


def test_control_a_real_kind_ask_still_filters_by_its_kind(session_factory, monkeypatch):
    """Control: "any sorento a3 flyer?" (promotion_kind a3_flyer, reference_target null) filters."""
    from tests.chatbot.test_promo_kind_paths import _continue_services, _seed as _seed_kind_world
    from tests.chatbot.test_promo_kind_subject import PROMO_TOOL, _qf
    from tests.chatbot.test_promo_owner_round2 import _mcp

    ids = _seed_kind_world(session_factory, sorento="none")
    captured = _mcp(monkeypatch, session_factory)
    qf = _qf(kind="a3_flyer")
    qf["reference_target"] = None
    _turn(session_factory, monkeypatch, _continue_services(ids["srt"]), qf, "any sorento a3 flyer?", "zzt-fn-kind")
    probes = [a for name, a in captured if name == PROMO_TOOL]
    assert probes and all(a.get("promotion_types") == ["a3_flyer"] for a in probes), captured
