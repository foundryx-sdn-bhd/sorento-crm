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
