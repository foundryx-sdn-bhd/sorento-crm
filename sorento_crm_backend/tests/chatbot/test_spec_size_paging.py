"""SPEC-PROMO-PARAMS, owner ruling 12 Oct 2026 (after hand test step 1): a size ask shows a few
matches first, then tells the customer they can reply to see more. The follow-up asking for more,
in any phrasing or language, is the PARSER's call (`continuation: true`, no new subject) and
returns the next page with the same filters. No word rule on the customer's text.

Reused, not rebuilt: the carry slot `focus.set_page` and its `set_page` fetch filter
(`turn/apply.py`, `turn_runtime.make_tool_runner`). The "another N" text reader
(`turn_runtime.set_continue_count`) is NOT used. Made-up codes only (ZZT-SOC-*).
"""
from __future__ import annotations

import uuid

from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _run_turn
from tests.chatbot.test_spec_on_category_entity import (  # noqa: F401
    STOCK_TOOL,
    _category,
    _clear_overrides,
    _route_backed_stock,
    _seed_basins,
    _spec_entity,
)
from tests.chatbot import test_outstanding_lane as harness

SEVEN = tuple((f"L{i}", {"dim_length": 600}) for i in range(5)) + (("W0", {"dim_width": 600}), ("W1", {"dim_width": 600}), ("N0", {}))
SEE_MORE = "Reply to see more"


def _world(session_factory, monkeypatch, rows=SEVEN):
    codes = _seed_basins(session_factory, rows)
    call, captured = _route_backed_stock(monkeypatch, session_factory)
    monkeypatch.setattr(harness, "_capturing_mcp", lambda response=None: (call, captured))
    return codes, captured


def _size_ask(raw="600mm", key="dim_width"):
    return _parser_output(
        domain_hint="inventory", intent_hint="check_stock",
        entities=[_category("basin", "basin"), _spec_entity(raw, key, 600)],
    )


def _say(session_factory, monkeypatch, qf, text):
    result, _ = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=text,
        msg_id=f"zzt-pg-{uuid.uuid4().hex[:6]}", real_resolver=True,
    )
    return (result.reply or {}).get("text") or ""


def _shown(reply, codes):
    return [c for c in codes if c in reply]


def _stock_calls(captured):
    return [a for n, a in captured if n == STOCK_TOOL]


def test_p1_first_page_shows_a_few_and_says_reply_to_see_more(session_factory, monkeypatch, _clear_overrides):
    """RED: every match is listed at once and nothing says more exist."""
    codes, captured = _world(session_factory, monkeypatch)
    reply = _say(session_factory, monkeypatch, _size_ask(), "600mm basin got stock?")
    sized = codes[:7]
    assert len(_shown(reply, sized)) == 5, reply
    assert codes[7] not in reply, "the basin with no size is not a match"
    assert SEE_MORE in reply, reply
    assert "of 7" in reply, reply
    calls = _stock_calls(captured)
    assert calls and calls[-1].get("limit") == 5 and calls[-1].get("page") == 1, calls


def test_p2_continuation_returns_the_next_page_with_the_same_filters(session_factory, monkeypatch, _clear_overrides):
    """RED: a parser `continuation` has no reader; the next page is never asked for."""
    codes, captured = _world(session_factory, monkeypatch)
    first = _say(session_factory, monkeypatch, _size_ask(), "600mm basin got stock?")
    first_shown = set(_shown(first, codes[:7]))
    first_constraints = _stock_calls(captured)[-1]["spec_constraints"]
    captured.clear()

    more = _parser_output(domain_hint="inventory", intent_hint="check_stock", entities=[], continuation=True)
    reply = _say(session_factory, monkeypatch, more, "lagi")
    calls = _stock_calls(captured)
    assert calls, f"the next page must be fetched: {captured}"
    assert calls[-1]["spec_constraints"] == first_constraints, calls
    assert calls[-1].get("page") == 2 and calls[-1].get("limit") == 5, calls
    second_shown = set(_shown(reply, codes[:7]))
    assert len(second_shown) == 2 and not (second_shown & first_shown), reply
    assert SEE_MORE not in reply, "the last page offers no more"


def test_p3_a_new_question_closes_the_list(session_factory, monkeypatch, _clear_overrides):
    """Guard: a message that is not a continuation does not page the old list."""
    codes, captured = _world(session_factory, monkeypatch)
    _say(session_factory, monkeypatch, _size_ask(), "600mm basin got stock?")
    captured.clear()
    other = _parser_output(domain_hint="inventory", intent_hint="check_stock", entities=[_category("basin", "basin")], continuation=False)
    _say(session_factory, monkeypatch, other, "basin got stock?")
    assert not [a for a in _stock_calls(captured) if a.get("page") == 2], captured


def test_p4_a_short_list_offers_no_more(session_factory, monkeypatch, _clear_overrides):
    """Guard: three matches fit the first page; nothing to offer, nothing carried."""
    rows = (("L0", {"dim_length": 600}), ("L1", {"dim_length": 600}), ("W0", {"dim_width": 600}))
    codes, captured = _world(session_factory, monkeypatch, rows)
    reply = _say(session_factory, monkeypatch, _size_ask(), "600mm basin got stock?")
    assert len(_shown(reply, codes)) == 3, reply
    assert SEE_MORE not in reply, reply


def test_p5_an_axis_word_narrows_to_that_axis(session_factory, monkeypatch, _clear_overrides):
    """Owner ruling: "600mm length basin" uses Length only (guard on the axis rule)."""
    rows = (("L0", {"dim_length": 600}), ("W0", {"dim_width": 600}))
    codes, captured = _world(session_factory, monkeypatch, rows)
    reply = _say(session_factory, monkeypatch, _size_ask("600mm length", "dim_length"), "600mm length basin got stock?")
    assert codes[0] in reply and codes[1] not in reply, reply
    sent = [c for a in _stock_calls(captured) for c in a.get("spec_constraints") or []]
    assert {"key": "dim_length", "op": "eq", "value": 600} in sent, sent


def test_p6_a_thickness_is_no_size_only_the_envelope_axes_are():
    """The ruling is about the product's envelope (length, width, height). A thickness is
    measured in mm too, but its ask keeps the established path (round 8/9: every brand's
    1.2mm sinks, through the resolver's counted set)."""
    from app.services.chatbot.lanes.business import fetch

    assert fetch.carries_size([{"key": "dim_length", "op": "eq", "value": 600}])
    assert fetch.carries_size([{"key": "size", "op": "eq", "value": 600}])
    assert not fetch.carries_size([{"key": "thickness", "op": "eq", "value": 1.2}])


def test_p7_a_continuation_that_repeats_the_category_keeps_the_lists_filters(
    session_factory, monkeypatch, _clear_overrides
):
    """Reviewer B1: the live parser often repeats the earlier subject on a follow-up. The
    saved list's filters must still be the ones sent; never a stock call with no filter."""
    codes, captured = _world(session_factory, monkeypatch)
    _say(session_factory, monkeypatch, _size_ask(), "600mm basin got stock?")
    first_constraints = _stock_calls(captured)[-1]["spec_constraints"]
    captured.clear()

    basin = {**_category("basin", "basin"), "current_message": False}
    more = _parser_output(
        domain_hint="inventory", intent_hint="check_stock", entities=[basin],
        entity_op="reuse", continuation=True,
    )
    reply = _say(session_factory, monkeypatch, more, "more")
    calls = _stock_calls(captured)
    assert calls, captured
    assert calls[-1].get("spec_constraints") == first_constraints, calls
    assert calls[-1].get("page") == 2, calls
    assert len(_shown(reply, codes[:7])) == 2, reply


def test_p8_the_gate_passes_a_saved_page_only_when_its_filters_will_be_sent():
    """Reviewer B1: an empty turn passes on a saved page only when that page holds a size the
    lane sends; a page without one, or on promotion, fails closed."""
    from app.services.chatbot.lanes.business import gate as gate_mod

    sized = [{"key": "class", "op": "eq", "value": "Wash Basin"}, {"key": "size", "op": "eq", "value": 600}]
    unsized = [{"key": "class", "op": "eq", "value": "Wash Basin"}]

    def passes(domain, constraints):
        parser = {
            "domain_hint": domain, "intent_hint": "check_stock", "message_type": "business_query",
            "entities": [], "spec_page": {"domain": domain, "spec_constraints": constraints, "page": 1},
        }
        return gate_mod.run_gate({}, parser=parser, resolver={"tokens": [], "resolutions": []}).get("gate_passed")

    assert passes("inventory", sized) is True
    assert passes("inventory", unsized) is not True
    assert passes("promotion", sized) is not True


def test_p9_a_spec_page_the_parser_emits_is_never_trusted(session_factory, monkeypatch, _clear_overrides):
    """Reviewer S1: only a page this bot saved may scope a turn. A `spec_page` key in the
    verdict itself (a provider without strict schemas) is dropped."""
    _codes, captured = _world(session_factory, monkeypatch)
    forged = {
        "domain": "inventory", "page": 3, "total": 99,
        "spec_constraints": [{"key": "size", "op": "eq", "value": 600}],
    }
    qf = _parser_output(
        domain_hint="inventory", intent_hint="check_stock", entities=[], continuation=True, spec_page=forged,
    )
    _say(session_factory, monkeypatch, qf, "more")
    assert not [c for c in _stock_calls(captured) if c.get("spec_constraints") or c.get("page") == 4], captured


def test_p10_the_counted_set_text_readers_never_see_a_size_list(session_factory, monkeypatch, _clear_overrides):
    """Reviewer S2: after a size list, "another 3" or a bare "2" is the parser's to read. The
    counted set's text readers (`with_set_count_from_text`, `with_brand_from_offer`) get no
    carry, so a position pick stays a position pick."""
    from app.services.chatbot import turn_runtime

    seen: list[object] = []
    for name in ("with_set_count_from_text", "with_brand_from_offer"):
        real = getattr(turn_runtime, name)

        def spy(verdict, message, *, carried=None, _real=real):
            seen.append(carried)
            return _real(verdict, message, carried=carried)

        monkeypatch.setattr(turn_runtime, name, spy)
    _world(session_factory, monkeypatch)
    _say(session_factory, monkeypatch, _size_ask(), "600mm basin got stock?")
    seen.clear()
    qf = _parser_output(domain_hint="inventory", intent_hint="check_stock", entities=[], reference_positions=[2])
    _say(session_factory, monkeypatch, qf, "2")
    assert seen and all(c is None for c in seen), seen


def test_p11_the_see_more_line_is_in_the_catalog():
    """Reviewer S4: the line goes through the contact's localizer like the miss line beside it."""
    from app.services.chatbot.label_catalog import Localizer, defaults

    line = "Showing 1 to 5 of 7. Reply to see more."
    for lang in ("ms", "zh"):
        done = Localizer(lang, defaults(lang)).sentence(line)
        assert done != line and "1" in done and "5" in done and "7" in done, (lang, done)
