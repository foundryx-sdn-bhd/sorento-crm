"""PV2-FIX r7b: a staff document not-found offers v1's member picker (interface `roster_for`), a member
pick assigns that member, and the assignment key reaches v1's next-assignee body. No DB, no model."""
from __future__ import annotations

import pytest

from app.services.chatbot.pv2 import answer as answer_mod
from app.services.chatbot.pv2 import turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.schema import Ask
from app.services.chatbot.pv2.state import HandOver, dump_state
from app.services.chatbot.tail import member_offer

from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask as _base_ask, mk_state, out
from tests.chatbot.pv2.test_fix_round2 import _Fake, _e

SENTENCE = ('Couldn\'t find: "SO422057" (sales order). '
            "Would you like me to escalate to customer service team?")
ROSTER_TEXT = (f"{SENTENCE}\n\n{member_offer.PICKER_HEADER}\n1. Sandy Lim\n2. Lin\n\n{member_offer.PICKER_CLOSE}")
MEMBERS = [{"user_id": "u1", "name": "Sandy Lim"}, {"user_id": "u2", "name": "Lin"}]
UUID = "9f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5f"


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


class _Roster:
    def __init__(self):
        self.calls = []

    def __call__(self, team_key, sentence):
        self.calls.append((team_key, sentence))
        return {"text": f"{sentence}\n\n{member_offer.PICKER_HEADER}\n1. Sandy Lim\n2. Lin\n\n{member_offer.PICKER_CLOSE}",
                "members": MEMBERS}


def _ctx(parsed, state=None, resolver=None, **extra):
    return turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(state or mk_state()), profile=MemoryProfile(audience="staff"),
        parse=lambda m, u: parsed, resolver=resolver or FakeResolver({}), load_exchanges=lambda s: [],
        offer_mode="offer", domain_teams={"order": "customer_service"}, **extra)


def _so_parse():
    ent = {"kind": "sales_order", "key": None, "phrase": None, "op": "set", "refers_to_held": False,
           "quantity": None, "doc_number": "SO422057"}
    return out(_ask(domain="order", entities=[ent]))


def _first():
    roster = _Roster()
    res = turn._run(_ctx(_so_parse(), roster_for=roster))
    return roster, res


def _answer(res, key, roster):
    parsed = out(_ask(act="answers_bot_question", answer={"option_key": key, "free_value": None}))
    return turn._run(_ctx(parsed, state=res.state, roster_for=roster))


def test_a_staff_sales_order_not_found_reads_as_v1s_member_picker():
    roster, res = _first()
    assert res.direct_reply == ROSTER_TEXT, res.direct_reply
    assert roster.calls and roster.calls[0][0] == "customer_service" and roster.calls[0][1] == SENTENCE


def test_picking_a_member_hands_over_to_that_member():
    roster, res = _first()
    second = _answer(res, "2", roster)
    assert any(isinstance(a, HandOver) and a.team_key == "customer_service" and a.agent_key == "u2"
               for a in second.actions), second.actions


def test_yes_hands_over_with_no_member():
    roster, res = _first()
    second = _answer(res, "yes", roster)
    assert any(isinstance(a, HandOver) and a.team_key == "customer_service" and a.agent_key is None
               for a in second.actions), second.actions


def test_no_is_the_escalation_decline():
    roster, res = _first()
    second = _answer(res, "no", roster)
    assert second.verdict is not None
    assert second.verdict["escalation"].get("escalation_declined") is True


def test_a_staff_product_did_you_mean_has_no_roster():
    roster = _Roster()
    parsed = out(_ask(domain="inventory", entities=[_e(phrase="SRTXYZ999")]))
    table = {"product|SRTXYZ999": ("ambiguous", [("product", "SRTWB999")], True)}
    res = turn._run(_ctx(parsed, resolver=_Fake(table), roster_for=roster, domain_teams={"inventory": "warehouse"}))
    assert roster.calls == []
    assert member_offer.PICKER_HEADER not in res.direct_reply


# ------------------------------------------------------------------ assignment proof, no DB


def test_the_hand_over_verdict_carries_the_member_as_preferred_assignee():
    from app.services.chatbot import turn_runtime

    v = answer_mod._verdict_for("hand_over", team_key="customer_service", agent_key=UUID)
    assert v["escalation"]["preferred_assignee_id"] == UUID
    projected = turn_runtime.lane_parse_output(v)
    assert projected["escalation"]["preferred_assignee_id"] == UUID


def test_the_escalation_lane_body_names_the_member_and_the_team():
    from app.services.chatbot.lanes import escalation as esc

    v = answer_mod._verdict_for("hand_over", team_key="customer_service", agent_key=UUID)
    ctx = {"parse": {"output": v}, "contact": {"phone": "+60100000000"}}
    body = esc._next_assignee_body(ctx, {"team": "customer_service", "brand_code": None, "company_id": None})
    assert body["preferred_assignee_id"] == UUID
    assert body["team_code"] == "customer_service"
