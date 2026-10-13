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

import pytest

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


def _seed_gratings(session_factory, rows):
    """Floor gratings known only as a registry product type (no Category row names them),
    one stock row each: the dev shape behind the crew's "250mm floor grating" ask."""
    from app.models.product_spec import ProductSpecifications
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._mc_lookup_seed import product, stock, warehouse
    from tests.chatbot.test_lane_require import _seed_registry
    from tests.chatbot.test_outstanding_lane import _seed_contact

    _seed_contact(session_factory, variables={})
    db = session_factory()
    _seed_registry(db)
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    codes = []
    for tag, mm in rows:
        p = product(db, company_id=DEFAULT_COMPANY_ID, code=f"ZZT-FG-{tag}-{uuid.uuid4().hex[:4]}")
        values = {"product_type": {"value": "floor_grating"}, **{k: {"value": v, "unit": "mm"} for k, v in mm.items()}}
        db.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=p.id, values=values))
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=5)
        codes.append(p.product_code)
    db.commit()
    return codes


@pytest.mark.parametrize("key", ["size", "dim_width"])
def test_p12_a_size_on_a_product_type_subject_lists_it(session_factory, monkeypatch, _clear_overrides, key):
    """Crew live check on v87 (bec7d329): "250mm floor grating got stock?" answered "Couldn't
    find: floor grating (category)" with no tool call. The subject is a registry product type
    (no Category row); the route filters it, so it is no miss."""
    codes = _seed_gratings(session_factory, (("A", {"dim_length": 250}), ("B", {"dim_width": 250}), ("C", {"dim_length": 300})))
    call, captured = _route_backed_stock(monkeypatch, session_factory)
    monkeypatch.setattr(harness, "_capturing_mcp", lambda response=None: (call, captured))
    qf = _parser_output(
        domain_hint="inventory", intent_hint="check_stock",
        entities=[_spec_entity("250mm", key, 250), _category("floor grating", "floor grating")],
    )
    reply = _say(session_factory, monkeypatch, qf, "250mm floor grating got stock?")
    calls = _stock_calls(captured)
    assert calls and calls[-1].get("spec_constraints"), (captured, reply)
    assert {"key": "product_type", "op": "eq", "value": "floor_grating"} in calls[-1]["spec_constraints"], calls
    assert "Couldn't find" not in reply, reply
    assert set(_shown(reply, codes)) == set(codes[:2]), reply


def test_p13_a_size_whose_subject_maps_to_nothing_keeps_the_resolvers_set(session_factory, monkeypatch, _clear_overrides):
    """Crew live check on v87: "Couldn't find: floor grating (category)", no tool call. When the
    subject maps to no single registry value (`fetch._subject_constraint` None, as for a phrase
    two product types claim), the lane sends no constraints; the resolver's counted set must
    then stay, or the subject is placed by nothing."""
    from app.services.chatbot.lanes.business import fetch

    codes = _seed_gratings(session_factory, (("A", {"dim_length": 250}), ("B", {"dim_width": 250})))
    call, captured = _route_backed_stock(monkeypatch, session_factory)
    monkeypatch.setattr(harness, "_capturing_mcp", lambda response=None: (call, captured))
    monkeypatch.setattr(fetch, "_subject_constraint", lambda db, raw: None)
    qf = _parser_output(
        domain_hint="inventory", intent_hint="check_stock",
        entities=[_spec_entity("250mm", "size", 250), _category("floor grating", "floor grating")],
    )
    reply = _say(session_factory, monkeypatch, qf, "250mm floor grating got stock?")
    assert "Couldn't find" not in reply, reply
    assert _stock_calls(captured), (captured, reply)
    assert set(_shown(reply, codes)) == set(codes), reply


PROMO_PRODUCTS = "crm_marketing_promotion_products_list"


def _promo_world(session_factory, monkeypatch, lines=7):
    """A single-tier contact (no menu) and one live promotion holding `lines` basins; the
    promotion-products tool pages like the route (`spec_page` on a constrained call) and the
    real MCP presenter renders it."""
    import json

    from tests.chatbot.test_outstanding_lane import _present_response
    from tests.chatbot.test_promo_subject_filter import _seed, _use_mcp

    _seed(session_factory)
    present = _present_response()
    rows = [
        {
            "selling_price": 100 + i,
            "product": {"product_code": f"ZZPB{i}", "product_name": f"ZZPB{i}", "list_price": 200},
            "promotion": {"id": "p1", "description": "ZZT Raya", "start_date": "2026-10-01", "end_date": "2026-10-31"},
        }
        for i in range(lines)
    ]

    def behaviour(name, args):
        if name != PROMO_PRODUCTS:
            return {"has_result": True, "items": [{"description": "ZZT Raya"}]}
        page, limit = int(args.get("page") or 1), int(args.get("limit") or 50)
        body = {"data": rows[(page - 1) * limit: page * limit], "pagination": {"total": lines, "page": page, "limit": limit}}
        if args.get("spec_constraints"):
            body["spec_page"] = {"page": page, "limit": limit, "total": lines}
        return present(name, json.dumps(body))

    return [r["product"]["product_code"] for r in rows], (lambda: _use_mcp(monkeypatch, behaviour))


def _promo_say(session_factory, monkeypatch, use, qf, text):
    from tests.chatbot.test_promo_subject_filter import TWO_TIERS, _services

    captured = use()
    result, _ = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=text,
        msg_id=f"zzt-pp-{uuid.uuid4().hex[:6]}", resolve_services=_services(TWO_TIERS),
    )
    return (result.reply or {}).get("text") or "", [a for n, a in captured if n == PROMO_PRODUCTS]


def test_p14_a_promotion_list_shows_a_few_one_line_each_then_pages(session_factory, monkeypatch, _clear_overrides):
    """Crew live check on bec7d329: "which basins are on promotion?" listed 50 lines, 4-5 lines
    each, no paging. Expected: a few lines, one line per product, "Reply to see more", and the
    parser's continuation brings the next page with the same filters."""
    from tests.chatbot.test_promo_subject_filter import _ask_qf, _pick_qf

    codes, use = _promo_world(session_factory, monkeypatch)
    _promo_say(session_factory, monkeypatch, use, _ask_qf("basin"), "which basins are on promotion?")
    first, calls = _promo_say(session_factory, monkeypatch, use, _pick_qf(1), "1")
    assert calls and calls[-1].get("limit") == 5 and calls[-1].get("page") == 1, calls
    assert SEE_MORE in first, first
    assert f"{codes[0]}: MYR 100.00" in first and "List Price" not in first, first
    assert f"1. {codes[0]}: MYR 100.00\n2. {codes[1]}: MYR 101.00" in first, "one line each, no blank line between"
    assert len(_shown(first, codes)) == 5, first

    # The live parser read the "more" turn with date_mode "overlap" (crew, 045a0fea).
    more = _parser_output(
        domain_hint="promotion", intent_hint="check_promotion", entities=[], continuation=True, date_mode="overlap"
    )
    second, calls2 = _promo_say(session_factory, monkeypatch, use, more, "more")
    assert calls2 and calls2[-1].get("page") == 2, calls2
    assert calls2[-1].get("spec_constraints") == calls[-1].get("spec_constraints"), (calls, calls2)
    assert calls2[-1].get("access_levels") == calls[-1].get("access_levels") == ["Sorento Office"], (calls, calls2)
    # Crew live check on 045a0fea: page 2 switched date_mode null -> overlap. The next page is
    # asked with exactly the first page's arguments, only the page moves.
    scoped = lambda a: {k: v for k, v in a.items() if k not in ("page", "_diagnostics")}  # noqa: E731
    assert scoped(calls2[-1]) == scoped(calls[-1]), (calls[-1], calls2[-1])
    assert set(_shown(second, codes)) == set(codes[5:]), second
    assert SEE_MORE not in second, second


def test_p15_a_size_inside_the_category_words_leaves_the_type_named(session_factory, monkeypatch, _clear_overrides):
    """Crew live trace a92941c7 (v87, adf7b727): ONE entity, category raw "250mm floor grating"
    with spec size 250. Grounding split the size off but the category kept "250mm" in its words,
    so they named no product type, no tool ran and the reply was "Couldn't find". The measure
    leaves the category's words; the type is asked with the size, and a size no grating holds
    is answered from what the data holds (the values on file), never "Couldn't find"."""
    codes = _seed_gratings(session_factory, (("A", {"dim_length": 150}), ("B", {"dim_width": 130})))
    call, captured = _route_backed_stock(monkeypatch, session_factory)
    monkeypatch.setattr(harness, "_capturing_mcp", lambda response=None: (call, captured))
    entity = {**_category("250mm floor grating", None), "spec_key": "size", "spec_value": 250}
    qf = _parser_output(domain_hint="inventory", intent_hint="check_stock", entities=[entity])
    reply = _say(session_factory, monkeypatch, qf, "250mm floor grating got stock?")
    calls = _stock_calls(captured)
    assert calls, (captured, reply)
    assert {"key": "product_type", "op": "eq", "value": "floor_grating"} in calls[-1]["spec_constraints"], calls
    assert "Couldn't find" not in reply, reply
    assert "150" in reply and "130" in reply, reply


def test_p17_a_class_said_in_chinese_with_its_english_canonical_is_filtered(session_factory, monkeypatch, _clear_overrides):
    """The shape the published "A SIZE AND A CLASS WORD" rule asks of the parser for
    "600mm 的洗手盆有货吗？": the size its own entity, the class raw in Chinese with its English
    canonical_code. Code reads the canonical (grounding's own swap), never a word list."""
    codes, captured = _world(session_factory, monkeypatch)
    qf = _parser_output(
        domain_hint="inventory", intent_hint="check_stock",
        entities=[_spec_entity("600mm", "size", 600), _category("洗手盆", "basin")],
    )
    reply = _say(session_factory, monkeypatch, qf, "600mm 的洗手盆有货吗？")
    calls = _stock_calls(captured)
    assert calls and calls[-1].get("spec_constraints"), (captured, reply)
    assert {"key": "size", "op": "eq", "value": 600} in calls[-1]["spec_constraints"], calls
    assert any(c.get("key") == "class" for c in calls[-1]["spec_constraints"]), calls
    assert "洗手盆" not in reply.split("\n")[0] or len(_shown(reply, codes)) == 5, reply
    assert len(_shown(reply, codes)) == 5, reply
