"""Owner Q4 option (a), 12 Oct 2026: `crm_marketing_promotion_products_list` is the ONE promotion
path for an ask with a described subject (card section 8).

R4: the stamped tier miss ("No <Level> promotion for <subject>.") is answered on this path.
R5: when the tool returns no lines for the subject, the reply states the data's miss only: no
resolver "Couldn't find: <subject> (category)" and no escalate offer for the word the tool
already answered.

MCP doubles answer by tool name and tier, so the tests hold whichever tool the stamp probes use.
"""
from __future__ import annotations

from tests.chatbot.test_promo_kind_subject import PRODUCTS_TOOL, PROMO_TOOL
from tests.chatbot.test_promo_subject_filter import (
    HAS,
    NONE,
    TWO_TIERS,
    _ask_qf,
    _pick_qf,
    _say,
    _seed,
)

LINES = {
    "has_result": True,
    "items": [{"title": "ZZT-SK-1", "fields": [{"label": "Product Code", "value": "ZZT-SK-1"}]}],
}


def _office_only(name, args):
    """Lines for the Office tier only, on either promotion tool."""
    if name not in (PRODUCTS_TOOL, PROMO_TOOL):
        return NONE
    return LINES if args.get("access_levels") == ["Sorento Office"] else NONE


def test_r4_a_stamped_no_promotion_level_is_one_line_on_the_promotion_products_path(session_factory, monkeypatch):
    """RED today: the pick turn calls `crm_marketing_promotions_list` with scope_terms and
    prints the generic miss, not the one-liner from the promotion-products call."""
    _seed(session_factory)
    menu, _probes, _ = _say(
        session_factory, monkeypatch, _ask_qf("bathroom furniture"),
        "any bathroom furniture promo", "zzt-pp-r4-ask", _office_only,
    )
    lines = menu.splitlines()
    assert "1. Office - has promotion" in lines and "2. Dealer - no promotion" in lines, menu

    reply, captured, _ = _say(session_factory, monkeypatch, _pick_qf(2), "2", "zzt-pp-r4-pick", _office_only)
    assert reply.strip() == "No Dealer promotion for bathroom furniture.", reply
    pick_calls = [(n, a) for n, a in captured if n == PRODUCTS_TOOL]
    assert pick_calls, f"the pick must run the promotion-products tool: {captured}"
    assert all(a.get("access_levels") == ["Sorento Dealer"] for _n, a in pick_calls), captured
    assert all(isinstance(a.get("spec_constraints"), list) and a["spec_constraints"] for _n, a in pick_calls), captured
    assert not [1 for n, _a in captured if n == PROMO_TOOL], captured


def test_r4_the_level_that_has_lines_prints_them_not_the_one_liner(session_factory, monkeypatch):
    """Guard for the other half of the menu: Office has lines on the same path."""
    _seed(session_factory)
    _say(
        session_factory, monkeypatch, _ask_qf("bathroom furniture"),
        "any bathroom furniture promo", "zzt-pp-r4b-ask", _office_only,
    )
    reply, captured, _ = _say(session_factory, monkeypatch, _pick_qf(1), "1", "zzt-pp-r4b-pick", _office_only)
    assert "ZZT-SK-1" in reply, reply
    assert "No Office promotion" not in reply, reply
    assert [1 for n, _a in captured if n == PRODUCTS_TOOL], captured


def test_r5_an_empty_promotion_products_answer_prints_no_resolver_miss_or_escalation(session_factory, monkeypatch):
    """RED today: the reply is `Couldn't find: "bathroom furniture" (category). Would you like me
    to escalate...`, the resolver miss for a word the tool already answered. The menu is stamped
    with lines on both tiers, then the pick's tool call comes back empty."""
    _seed(session_factory)
    state = {"armed": False}

    def behaviour(name, args):
        return NONE if state["armed"] else HAS

    menu, _probes, _ = _say(
        session_factory, monkeypatch, _ask_qf("bathroom furniture"),
        "any bathroom furniture promo", "zzt-pp-r5-ask", behaviour,
    )
    assert "1. Office - has promotion" in menu.splitlines(), menu
    state["armed"] = True
    reply, captured, _ = _say(session_factory, monkeypatch, _pick_qf(1), "1", "zzt-pp-r5-pick", behaviour)
    assert [1 for n, _a in captured if n == PRODUCTS_TOOL], f"the tool must have been asked: {captured}"
    low = reply.lower()
    assert "couldn't find" not in low and "could not find" not in low, reply
    assert "escalate" not in low, reply
    assert "(category)" not in low, reply
    # the data's miss: names the subject and says there is no promotion for it
    assert "bathroom furniture" in low and "promotion" in low, reply
