"""A held similar-list (`ideation.status == "similar_offered"`, no `draft_id`) keeps a bare
"2" or "NEW" in the ideate lane exactly as an open draft does (`open_ideation_draft` /
`_continues_open_draft`, `app/services/chatbot/turn/apply.py`). Without it the reply lands on
the domain menu or `low_signal` and never reaches the capture turn.

Pure seam, like `test_ideation_draft_keeps_lane.py`: `apply()` + `route()` over a `State`.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot.turn.apply import apply, open_ideation_draft
from app.services.chatbot.turn.route import route
from app.services.chatbot.turn.state import Focus, State
from tests.chatbot._turn_helpers import build_policy, verdict
from tests.chatbot.test_ideation_draft_keeps_lane import (
    OPEN_DRAFT,
    RULE,
    _confirm_verdict,
    _question_verdict,
)

HELD: dict[str, Any] = {
    "status": "similar_offered",
    "message_text": "price tag should show promo price in red",
    "title": "Promo price in red",
    "similar": [
        {"idea_id": "11111111-1111-4111-8111-111111111111", "idea_number": "IDEA-0151", "title": "A"},
        {"idea_id": "22222222-2222-4222-8222-222222222222", "idea_number": "IDEA-0097", "title": "B"},
    ],
    "updated_at": "2026-10-02T09:00:00+00:00",
    "is_test": False,
}


def _branch(ideation: Any, v: dict[str, Any]) -> tuple[str, Any]:
    state = State(focus=Focus(domains=["ideate"]), ideation=ideation)
    _, plan = apply(state, v, build_policy())
    return route(plan), plan


def test_a_held_list_counts_as_an_open_ideation_pointer() -> None:
    assert open_ideation_draft(HELD) is True


@pytest.mark.parametrize("ideation", [{"status": "zzz_unknown"}, {"status": "collecting"}, None, {}])
def test_an_unknown_status_without_a_draft_id_does_not(ideation: Any) -> None:
    assert open_ideation_draft(ideation) is False


@pytest.mark.parametrize(
    "make",
    [
        lambda: _confirm_verdict(),
        lambda: _question_verdict(),
        lambda: _question_verdict(reference_positions=[]),
    ],
    ids=["confirmation", "clarification", "clarification_no_positions"],
)
def test_a_bare_reply_over_a_held_list_routes_exactly_as_over_an_open_draft(make) -> None:
    v = make()
    held_branch, held_plan = _branch(dict(HELD), v)
    draft_branch, draft_plan = _branch(dict(OPEN_DRAFT), v)
    assert held_branch == draft_branch == "ideate"
    assert RULE in held_plan.trace.rules_fired


def test_an_unknown_status_pointer_does_not_hold_the_lane() -> None:
    v = _confirm_verdict()
    branch, plan = _branch({"status": "zzz_unknown"}, v)
    assert branch == "low_signal"
    assert RULE not in plan.trace.rules_fired


def test_idle_chat_over_a_held_list_is_not_absorbed_either() -> None:
    v = verdict(message_type="casual")
    branch, plan = _branch(dict(HELD), v)
    assert branch == "low_signal"
    assert RULE not in plan.trace.rules_fired
