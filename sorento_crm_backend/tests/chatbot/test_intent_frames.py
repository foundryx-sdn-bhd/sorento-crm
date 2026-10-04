"""INTENT-FRAMES (owner, 4 Oct 2026): each question gets its own frame, owned by its intent.

The focus IS the frame and `focus.intent` (STUCK-QTY-LOOP) names its owner. A new intent
starts a clean frame: the old ask's own filters never move over, and its subject moves
over only when the parser says the message is about the same subject (`continuation`,
`anaphora.backward_reference`; never `entity_op: "reuse"` alone, measured live). A
refinement of the same intent narrows the frame as before. Nothing expires by time or turn count (owner ruling Q1): a
frame ends by context, a reset, or an access change.

Card: documentation/plans/chatbot/intent-frames-behaviour-card.md. The engine replays of
the owner's sequences are `test_intent_frames_engine.py`.
"""
from __future__ import annotations

import dataclasses
import pytest

from app.services.chatbot import contracts
from app.services.chatbot.turn import frame, held
from app.services.chatbot.turn.apply import apply
from app.services.chatbot.turn.state import Focus, Profile, State, focus_from_wire, focus_to_wire

from tests.chatbot._turn_helpers import build_policy, entity, verdict

AGENT = {"raw": "W*******", "hint": "sales_agent", "canonical_code": "W*******", "uuid": "agent-1"}
BASIN = {"raw": "SRTWB9GM0", "hint": "product", "canonical_code": "SRTWB9GM0", "uuid": "p-basin"}
HANLIM = {"raw": "hanlim", "hint": "customer", "canonical_code": "HANLIM", "uuid": "c-hanlim"}
GUNMETAL = {"raw": "gunmetal", "hint": "specification", "spec_key": "finish", "spec_value": "Gunmetal"}


def _ranking_frame(**over) -> Focus:
    """A top selling ranking by sales agent W******* (crew report 2), with a date window
    and a carried product and customer: every axis a stale frame can hold."""
    base = dict(
        intent="check_order",
        products=[dict(BASIN)],
        customers=[dict(HANLIM)],
        domains=["order"],
        status="top_selling",
        document=["SO"],
        sales_channel="dealer",
        date_window={"start": "2026-09-01", "end": "2026-09-30"},
        sort={"by": "date", "dir": "desc"},
        extra={"sales_agent": [dict(AGENT)], "specification": [dict(GUNMETAL)]},
        tier=["dealer"],
        brands=["SRT"],
    )
    base.update(over)
    return Focus(**base)


def _state(focus: Focus, *, turn_no: int = 7, access_fp: str | None = "fp-a") -> State:
    return State(focus=focus, profile=Profile(access_fp=access_fp), turn_no=turn_no)


def _turn(focus: Focus, v: dict) -> Focus:
    """What the engine does with one verdict: the held rule (which marks a new intent),
    then APPLY."""
    state, _why = held.consume(_state(focus), v)
    out, _plan = apply(state, v, build_policy())
    return out.focus


def _codes(rows) -> list[str]:
    return [row.get("canonical_code") or row.get("raw") for row in rows or []]


# --------------------------------------------------------------------------- #
# Contract: every Focus field belongs to exactly one class                     #
# --------------------------------------------------------------------------- #


def test_every_focus_field_is_classified_once():
    """A new Focus field must say whether it is the frame's subject, owned by the intent,
    a held question (`held.py`), the contact's, or a stamp. Unclassified fails here."""
    classes = [frame.SUBJECT_AXES, frame.OWNED_AXES, held.FOCUS_HELD, frame.CONTACT_AXES, frame.STAMPS]
    named = [name for group in classes for name in group]
    assert sorted(named) == sorted({f.name for f in dataclasses.fields(Focus)}), named
    assert len(named) == len(set(named)), "a field is in two classes"


def test_the_extra_kinds_are_split_into_subject_and_owned():
    assert "sales_agent" not in frame.SUBJECT_EXTRA
    assert "specification" in frame.SUBJECT_EXTRA


# --------------------------------------------------------------------------- #
# A new intent starts a clean frame                                            #
# --------------------------------------------------------------------------- #


def test_a_new_intent_drops_the_old_asks_filters_and_subject():
    """Crew report 2: "taiyang only" read as a stock ask after a ranking by agent
    W*******. Nothing of the ranking rides into the stock lookup."""
    v = verdict(
        intent_hint="check_stock",
        domain_hint="inventory",
        entity_op="replace_combine",
        domain_in_message=False,
        entities=[entity("taiyang", hint="customer")],
    )
    focus = _turn(_ranking_frame(), v)
    assert focus.intent == "check_stock"
    assert focus.extra.get("sales_agent") in (None, [])
    assert focus.extra.get("specification") in (None, [])
    assert _codes(focus.products) == []
    assert _codes(focus.customers) == ["taiyang"]
    assert focus.status is None
    assert focus.document == []
    assert focus.sales_channel is None
    assert focus.date_window is None
    assert focus.sort is None


def test_a_new_intent_keeps_the_contacts_own_axes():
    v = verdict(intent_hint="check_stock", domain_hint="inventory", entities=[entity("SRTWC286-SH-150")])
    focus = _turn(_ranking_frame(), v)
    assert focus.tier == ["dealer"]
    assert focus.brands == ["SRT"]


def test_a_new_intent_with_no_carry_signal_and_no_entity_starts_empty():
    """"low stock report" after a stock check: the products of the stock check never
    become the report's filter."""
    v = verdict(intent_hint="low_stock_report", domain_hint="inventory", entity_op="replace_combine")
    focus = _turn(_ranking_frame(intent="check_stock", domains=["inventory"]), v)
    assert _codes(focus.products) == []
    assert _codes(focus.customers) == []


@pytest.mark.parametrize(
    "signal",
    [
        {"continuation": True, "entity_op": "reuse"},
        {"anaphora": {"backward_reference": True}},
        {"continuation": True},
    ],
    ids=["live_cert_reading", "backward_reference", "continuation"],
)
def test_a_carry_signal_carries_the_subject_but_never_the_old_filters(signal):
    """"cert?" after "any gunmetal basin has incoming?" (#833; the live parser reads it
    `continuation: true`, `entity_op: reuse`): the basins and their finish carry; the old
    ask's own filters do not."""
    v = verdict(intent_hint="check_product_attachment", domain_hint="product_attachment", **signal)
    focus = _turn(_ranking_frame(), v)
    assert _codes(focus.products) == ["SRTWB9GM0"]
    assert _codes(focus.customers) == ["HANLIM"]
    assert focus.extra.get("specification") == [GUNMETAL]
    assert focus.extra.get("sales_agent") in (None, [])
    assert focus.status is None
    assert focus.date_window is None
    assert focus.sort is None
    assert focus.sales_channel is None


def test_entity_op_reuse_alone_is_not_a_carry_signal():
    """Measured live (gpt-5.4-mini, 4 Oct 2026): "low stock report for sorento water tap"
    right after a stock check comes back `entity_op: reuse`, `continuation: false`. The
    stock check's products must not ride into the report."""
    v = verdict(intent_hint="low_stock_report", domain_hint="inventory", entity_op="reuse", continuation=False)
    focus = _turn(_ranking_frame(intent="check_stock", domains=["inventory"]), v)
    assert _codes(focus.products) == [] and _codes(focus.customers) == []


def test_a_carry_signal_never_carries_an_axis_the_message_named_itself():
    v = verdict(
        intent_hint="check_stock",
        domain_hint="inventory",
        continuation=True,
        entities=[entity("SRTWC286-SH-150")],
    )
    focus = _turn(_ranking_frame(), v)
    assert _codes(focus.products) == ["SRTWC286-SH-150"]
    assert _codes(focus.customers) == ["HANLIM"]


def test_the_frame_rule_is_traced():
    v = verdict(intent_hint="check_stock", domain_hint="inventory", entities=[entity("SRTWC286-SH-150")])
    state, _ = held.consume(_state(_ranking_frame()), v)
    _out, plan = apply(state, v, build_policy())
    assert "frame_new_intent" in plan.trace.rules_fired


# --------------------------------------------------------------------------- #
# The same intent keeps today's reading                                        #
# --------------------------------------------------------------------------- #


def test_a_refinement_of_the_same_intent_narrows_the_frame():
    """"orders for hanlim" then "for SRTWB9GM0": the same intent, hanlim AND the basin."""
    focus = Focus(intent="check_order", customers=[dict(HANLIM)], domains=["order"])
    v = verdict(
        intent_hint="check_order",
        domain_hint="order",
        domain_in_message=False,
        entity_op="replace_combine",
        entities=[entity("SRTWB9GM0")],
    )
    out = _turn(focus, v)
    assert _codes(out.customers) == ["HANLIM"]
    assert _codes(out.products) == ["SRTWB9GM0"]


@pytest.mark.parametrize("message_type", ["request_for_help", "clarification", "casual"])
def test_only_a_business_question_starts_a_new_frame(message_type):
    """"please escalate to marketing team" after a stock answer carries the escalation's
    subject; the parser may carry another intent onto it, but it asks nothing new."""
    v = verdict(message_type=message_type, intent_hint="check_product", domain_hint="master_products")
    out = _turn(_ranking_frame(), v)
    assert _codes(out.products) == ["SRTWB9GM0"]
    assert out.extra.get("sales_agent") == [AGENT]


def test_a_message_with_no_intent_keeps_the_frame():
    focus = _ranking_frame()
    out = _turn(focus, verdict(entities=[], is_affirmative=True))
    assert out.extra.get("sales_agent") == [AGENT]
    assert out.status == "top_selling"


def test_an_intent_outside_the_routing_table_is_not_a_new_frame():
    out = _turn(_ranking_frame(), verdict(intent_hint="check_ranking_by_agent", entities=[]))
    assert out.extra.get("sales_agent") == [AGENT]


def test_a_reset_still_drops_the_frame_but_keeps_the_contacts_axes():
    out = _turn(_ranking_frame(), verdict(topic_reset=True))
    assert _codes(out.products) == [] and out.extra.get("sales_agent") in (None, [])
    assert out.tier == ["dealer"] and out.brands == ["SRT"]


# --------------------------------------------------------------------------- #
# Expiry: by context only (owner ruling Q1), never by time or turn count       #
# --------------------------------------------------------------------------- #


def _stamped(*, access: str | None = "fp-a", **over) -> Focus:
    focus = _ranking_frame(**over)
    focus.frame_access = access
    return focus


def test_a_frame_built_under_other_access_is_dropped_at_load():
    state, why = frame.expire(_state(_stamped(access="fp-old"), access_fp="fp-new"))
    assert why == "access_changed"
    assert state.focus.intent is None
    assert _codes(state.focus.products) == []
    assert state.focus.extra.get("sales_agent") in (None, [])
    assert state.focus.tier == ["dealer"] and state.focus.brands == ["SRT"]


def test_the_access_drop_takes_the_held_questions_and_the_open_question():
    from app.services.chatbot.turn import pending as pending_mod

    focus = _stamped(access="fp-old", required_ask={"ask": "low_stock_report", "asking": "category"})
    roster = pending_mod.ask("product_pick", [{"position": 1, "label": "A", "code": "A"}], asked_at_turn=1)
    state = dataclasses.replace(_state(focus, access_fp="fp-new"), pending=roster)
    state, _why = frame.expire(state)
    assert state.focus.required_ask is None
    assert state.pending is None


def test_a_frame_never_expires_by_turn_count():
    """Owner ruling Q1: a hundred turns later the same frame, under the same access, is
    still the frame. Nothing counts turns."""
    state, why = frame.expire(_state(_stamped(), turn_no=10_000))
    assert why is None
    assert state.focus.intent == "check_order"


def test_no_ttl_constant_survives():
    from app.services.chatbot.turn import pending as pending_mod

    for module, name in ((held, "HELD_TTL_TURNS"), (frame, "FRAME_TTL_MINUTES"), (pending_mod, "OFFER_TTL"), (pending_mod, "tick")):
        assert not hasattr(module, name), f"{module.__name__}.{name}"


def test_an_unknown_access_never_counts_as_a_change():
    state, why = frame.expire(_state(_stamped(access="fp-old"), access_fp=None))
    assert why is None


def test_an_unstamped_frame_is_kept_and_stamped_on_write():
    """A row written before this ships: never dropped wholesale on deploy."""
    state, why = frame.expire(_state(_ranking_frame()))
    assert why is None and state.focus.intent == "check_order"
    assert frame.stamp(state).focus.frame_access == "fp-a"


def test_a_failed_access_read_keeps_the_old_access_stamp():
    stamped = frame.stamp(_state(_stamped(access="fp-a"), access_fp=None))
    assert stamped.focus.frame_access == "fp-a"


def test_an_empty_frame_carries_no_stamp():
    assert frame.stamp(_state(Focus(tier=["dealer"]))).focus.frame_access is None


# --------------------------------------------------------------------------- #
# The wire                                                                     #
# --------------------------------------------------------------------------- #


def test_the_stamp_survives_the_wire_and_the_session_contract():
    wire = focus_to_wire(_stamped())
    assert focus_from_wire(wire).frame_access == "fp-a"
    contracts.Focus.model_validate(wire)


def test_an_old_row_reads_as_unstamped():
    assert focus_from_wire({"products": [], "intent": "check_stock"}).frame_access is None


def test_a_row_stored_by_the_stuck_qty_loop_release_still_reads():
    """#1471 wrote `held_turn` / `held_access`; a row stored in between still validates."""
    contracts.Focus.model_validate({"intent": "check_stock", "held_turn": 4, "held_access": "fp"})


# --------------------------------------------------------------------------- #
# The lane's own reply outranks the generic miss text                          #
# --------------------------------------------------------------------------- #


def _envelope(**fetch) -> dict:
    return {"raw_fragment": {"kind": "result", "fetch": dict(fetch)}}


@pytest.mark.parametrize(
    "fetch",
    [{"has_result": False, "so_status": True}, {"has_result": False, "low_stock_report": True}],
    ids=["so_status_reply", "low_stock_reply"],
)
def test_no_miss_trigger_replaces_a_lanes_declared_reply(fetch):
    from app.services.chatbot import answer_bridge

    assert answer_bridge._miss_triggers({"_exit_kind": "not_found"}, _envelope(**fetch))[2] == (False, False, False)


def test_a_lanes_own_question_outranks_every_miss_trigger():
    from app.services.chatbot import answer_bridge

    envelope = {**_envelope(has_result=False), "required_ask": {"ask": "low_stock_report"}}
    assert answer_bridge._miss_triggers({"_exit_kind": "not_found"}, envelope)[2] == (False, False, False)


@pytest.mark.parametrize("fetch", [{"has_result": False}, {"has_result": True}], ids=["nothing", "a_denial_line"])
def test_a_resolver_not_found_still_speaks_when_the_lane_declared_nothing(fetch):
    """A lane line that only says what the contact may not see ("Sales order figures are
    not enabled", `has_result` true) is not an answer to the word: the miss speaks
    (ESCALATION-CONTROL's "Couldn't find SO999001")."""
    from app.services.chatbot import answer_bridge

    _raw, _item, (via_exit, _err, _empty) = answer_bridge._miss_triggers({"_exit_kind": "not_found"}, _envelope(**fetch))
    assert via_exit is True


def test_there_is_one_lane_reply_rule():
    from app.services.chatbot import answer_bridge

    assert not hasattr(answer_bridge, "_lane_asked")
    assert answer_bridge.LANE_OWN_REPLY_KEYS == ("so_status", "low_stock_report")


# --------------------------------------------------------------------------- #
# Security review B1: an escalation offer lives as long as the next message    #
# --------------------------------------------------------------------------- #


def _offer(kind: str = "team_pick", **payload):
    from app.services.chatbot.turn import pending as pending_mod

    return pending_mod.ask(
        kind, [{"position": 1, "label": "Warehouse", "code": "warehouse"}], asked_at_turn=3,
        payload={"domain": "inventory", **payload},
    )


def _apply_turn(state, v):
    state, why = held.consume(state, v)
    out, plan = apply(state, v, build_policy())
    return out, plan, why


@pytest.mark.parametrize("kind", ["team_pick", "member_offer", "company_pick"])
def test_an_escalation_offer_the_next_message_ignores_is_closed(kind):
    """Offer, then an unrelated turn: the offer is gone, so no later "ok thanks" can
    accept it (the AC-816 defect without its 3-turn clock)."""
    state = dataclasses.replace(_state(_ranking_frame(intent="check_stock")), pending=_offer(kind))
    out, plan, why = _apply_turn(state, verdict(intent_hint="check_stock", entities=[entity("SRTWC286-SH-150")]))
    assert out.pending is None and why == "offer_unanswered"


def test_an_escalation_offer_is_closed_on_a_new_intent_too():
    state = dataclasses.replace(_state(_ranking_frame(intent="check_stock")), pending=_offer())
    out, _plan, why = _apply_turn(state, verdict(intent_hint="check_promotion", entities=[entity("X1")]))
    assert out.pending is None and why == "new_intent"


def test_an_accepted_escalation_offer_is_not_marked():
    state = dataclasses.replace(_state(_ranking_frame(intent="check_stock")), pending=_offer())
    v = verdict(escalation={"is_escalation_confirmation": True, "company_pick": None})
    out, why = held.consume(state, v)
    assert out.pending is not None and why is None and held.CLOSE_OFFER not in v


def test_a_roster_keeps_its_pick_but_loses_the_escalate_sentence_when_ignored():
    from app.services.chatbot.turn import pending as pending_mod

    roster = pending_mod.ask(
        "product_pick", [{"position": 1, "label": "A", "code": "A"}], asked_at_turn=3,
        payload={"domain": "incoming", "escalate_offered": True},
    )
    after = held.without_unanswered_offer(roster, roster)
    assert after is not None and after.kind == "product_pick"
    assert "escalate_offered" not in after.payload


def test_a_question_asked_anew_this_turn_is_never_closed():
    from app.services.chatbot.turn import pending as pending_mod

    new = pending_mod.ask("team_pick", [{"position": 1, "label": "Sales", "code": "sales"}], asked_at_turn=4)
    assert held.without_unanswered_offer(_offer(), new) is new


def test_the_stamp_covers_the_question_the_composer_writes():
    """Security review S2/S1: a turn whose only held thing is the question it asks now
    is stamped, so an access change drops that question next turn."""
    stamped = frame.stamp(_state(Focus()), question=_offer())
    assert stamped.focus.frame_access == "fp-a"
    state, why = frame.expire(dataclasses.replace(_state(stamped.focus, access_fp="fp-b"), pending=_offer()))
    assert why == "access_changed" and state.pending is None


def test_a_held_access_stamp_from_the_stuck_qty_loop_release_is_read_as_the_frames():
    focus = focus_from_wire({"intent": "check_stock", "products": [{"raw": "A"}], "held_access": "fp-old"})
    assert focus.frame_access == "fp-old"
    _state_out, why = frame.expire(_state(focus, access_fp="fp-new"))
    assert why == "access_changed"


# --------------------------------------------------------------------------- #
# Reviewer B2 / S1                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("carry", [False, True], ids=["echo_only", "continuation"])
def test_an_echoed_old_entity_carries_its_axis_as_the_settled_row(carry):
    """The parser sometimes copies the carried product as an entity marked
    `current_message: false` ("hav incoming?" in replay case-025, live "check stock").
    The echo itself never lands - as a raw code the resolver widened it to its family
    (live pass, 4 Oct 2026) - but it says which axis the message is about, so that axis's
    settled row carries; nothing else does."""
    echoed = entity("SRTWB9GM0", current_message=False)
    v = verdict(intent_hint="check_stock", domain_hint="inventory", continuation=carry, entities=[echoed])
    focus = _turn(_ranking_frame(), v)
    assert focus.products == [BASIN]
    assert (focus.customers == [HANLIM]) is carry
    assert focus.extra.get("sales_agent") in (None, [])


def test_a_typed_entity_still_lands_on_a_new_intent():
    v = verdict(intent_hint="check_stock", domain_hint="inventory", entities=[entity("SRTWC286-SH-150")])
    assert _codes(_turn(_ranking_frame(), v).products) == ["SRTWC286-SH-150"]


@pytest.mark.parametrize(
    "v",
    [
        verdict(message_type="casual", intent_hint="check_stock"),
        verdict(message_type="request_for_help", intent_hint="check_product"),
    ],
    ids=["casual", "escalation_request"],
)
def test_a_turn_that_starts_no_frame_never_relabels_the_frame(v):
    """Reviewer S1: "thanks" read casual with a stale hint must not make the next
    refinement of the ranking look like a new intent."""
    state, _why = held.consume(_state(_ranking_frame()), v)
    assert state.focus.intent == "check_order"
    refine = verdict(intent_hint="check_order", domain_hint="order", domain_in_message=False,
                     entity_op="replace_combine", entities=[entity("hanlim", hint="customer")])
    consumed, _why = held.consume(state, refine)
    out, plan = apply(consumed, refine, build_policy())
    assert "frame_new_intent" not in plan.trace.rules_fired
    assert out.focus.extra.get("sales_agent") == [AGENT] and out.focus.status == "top_selling"


def test_an_answer_with_a_stale_hint_never_relabels_the_frame():
    from app.services.chatbot.turn import pending as pending_mod

    roster = pending_mod.ask("tier_pick", [{"position": 1, "label": "Dealer", "code": "Dealer"}], asked_at_turn=3)
    state = dataclasses.replace(_state(_ranking_frame()), pending=roster)
    v = verdict(intent_hint="check_product", reference_positions=[1], domain_in_message=False)
    out, _why = held.consume(state, v)
    assert out.focus.intent == "check_order" and held.NEW_INTENT not in v
