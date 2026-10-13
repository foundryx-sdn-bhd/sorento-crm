"""PV2-FIX round 5c red tests: quantity under a v1 menu, v1 did-you-mean lane, offer decline,
report breakdown and period, offer re-ask. Each names its v1 parity source."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.services.chatbot.pv2 import bridge, clarify_copy, turn
from app.services.chatbot.pv2 import reports as r
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.schema import Ask, Period, ReportSpec
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, SmallTalk, dump_state

from tests.chatbot.pv2.carry_fakes import (
    NOW, FakeResolver, ask as _base_ask, held as mk_held, mk_state, option, out, pending,
)
from tests.chatbot.pv2.test_fix_round2 import _Fake

TODAY = date(2026, 10, 16)


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


def _ent(phrase=None, key=None, refers=False, quantity=None, kind="product"):
    return {"kind": kind, "key": key, "phrase": phrase, "op": "set", "refers_to_held": refers,
            "quantity": quantity, "doc_number": None}


# ------------------------------------------------------------------ 3 quantity under a v1 family menu
# v1 parity: turn/apply.py:3162-3172 (requantify needs demand_qty and no current-message product)


def _menu_state():
    h = mk_held("inventory", {"product": ["SRTWC286"]})
    state = mk_state(h, pending_=pending("v1_open_question", [option("1", "A"), option("2", "B")]))
    state.pending.v1_kind = "product_pick"
    return state


FAMILY = {"product|SRTWC286": ("exact", [("product", "SRTWC286")], False),
          "product|None": ("exact", [("product", "SRTWC286")], False)}


def _verdict(**ask_kw):
    state = _menu_state()
    state, actions = apply_turn(state, out(_ask(**ask_kw)), resolver=_Fake(FAMILY))
    assert isinstance(actions[0], Lookup), actions[0]
    return bridge.to_v1_verdict(state, actions[0], today=TODAY)


def _requantified(v):
    assert v["demand_qty"] == 5
    prods = [e for e in v["entities"] if e["hint"] == "product"]
    assert prods and all(e["current_message"] is False for e in prods)
    assert v["open_question_answer"]["mode"] is None


def test_a_free_value_with_a_quantity_entity_requantifies():
    _requantified(_verdict(act="answers_bot_question", answer={"option_key": None, "free_value": "5 unit"},
                           entities=[_ent(quantity={"number": 5, "unit": "unit"})]))


def test_naming_the_family_again_with_a_quantity_requantifies():
    _requantified(_verdict(act="new_topic", domain="inventory",
                           entities=[_ent(phrase="SRTWC286", quantity={"number": 5, "unit": "unit"})]))


def test_a_quantity_only_entity_not_tied_to_the_held_requantifies():
    _requantified(_verdict(act="refines_held", domain="inventory",
                           entities=[_ent(quantity={"number": 5, "unit": "unit"})]))


def test_a_bare_option_pick_still_picks():
    v = _verdict(act="answers_bot_question", answer={"option_key": "1", "free_value": None})
    assert v["open_question_answer"]["mode"] == "pick" and v["open_question_answer"]["picked"] == [1]


# ------------------------------------------------------------------ 4 single did-you-mean = v1's miss lane
# v1 parity: lanes/business/answer.py:5265-5380 writes `Couldn't find "X" (product). Did you mean ...`


def _v1_pending(kind, options, expects, payload=None):
    from app.services.chatbot.turn import pending as v1p

    return v1p.ask(kind, options, expects=expects, payload=payload or {}, asked_at_turn=1)


def test_a_v1_product_pick_with_an_escalate_offer_mirrors_yes_no_too():
    p = _v1_pending("product_pick", [{"label": "SRTWB999", "position": 1}], None, {"escalate_offered": True})
    state = turn.mirror_v1_pending(mk_state(), p)
    keys = [o.key for o in state.pending.options]
    assert "yes" in keys and "no" in keys, keys


# ------------------------------------------------------------------ 5 decline of an offer
# v1 parity: turn/apply.py:956-967 (decline arm -> "Escalation declined.")


def _team_pick_state():
    p = _v1_pending("team_pick", [{"label": "Yes", "position": 1}], "yes_no")
    return turn.mirror_v1_pending(mk_state(mk_held("inventory", {"product": ["SRTWC286"]})), p)


def test_a_no_to_a_mirrored_v1_offer_is_a_clean_decline_verdict():
    state = _team_pick_state()
    state, actions = apply_turn(state, out(_ask(act="answers_bot_question",
                                                answer={"option_key": "no", "free_value": None})),
                                resolver=_Fake({}))
    assert isinstance(actions[0], Lookup)
    v = bridge.to_v1_verdict(state, actions[0], today=TODAY)
    assert v["entities"] == []
    assert v["is_affirmative"] is False
    assert v["escalation"]["is_escalation_confirmation"] is False


def test_a_no_to_pv2s_own_document_offer_is_an_escalation_decline_verdict():
    ent = {**_ent(kind="order"), "doc_number": "202610-0151"}
    parsed = out(_ask(domain="order", entities=[ent]))
    ctx = turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(mk_state()), profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: parsed, resolver=FakeResolver({}), load_exchanges=lambda s: [], offer_mode="offer")
    first = turn._run(ctx)
    no = out(_ask(act="answers_bot_question", answer={"option_key": "no", "free_value": None}))
    ctx2 = turn.TurnV2Context(
        text="no", now=NOW, raw_state=dump_state(first.state), profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: no, resolver=FakeResolver({}), load_exchanges=lambda s: [], offer_mode="offer")
    second = turn._run(ctx2)
    assert second.verdict is not None, "a casual SmallTalk reply, not v1's decline arm"
    assert second.verdict["escalation"].get("escalation_declined") is True
    assert second.verdict["entities"] == []


# ------------------------------------------------------------------ 6 sales report
# v1 parity: lanes/business/fetch.py:2533-2555 (breakdown offer), fetch.py:863-868 (group_by from the pick)

SEP = Period(preset=None, n=None, date_from=date(2026, 9, 1), date_to=date(2026, 9, 30))
AUG = Period(preset=None, n=None, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))


def _spec(**kw):
    base = dict(dataset="sales", measures=["amount"], group_by=[], top_n_per_group=None, filters=[], period=SEP,
                compare_to=None, compare_period=None, grain=None, threshold=None, sort=None, top_n=None,
                basis="delivered")
    base.update(kw)
    return ReportSpec.model_validate(base)


class _Mcp:
    def __init__(self, result):
        self.result, self.calls = result, []

    def __call__(self, name, args):
        self.calls.append((name, args))
        return self.result


OFFER_ENVELOPE = {
    "result_type": "sales_report", "response": "Delivered sales\nTotal RM 15.00", "has_result": True,
    "options": [{"idx": 1, "label": "By customer", "value": "customer", "aliases": []},
                {"idx": 2, "label": "By product", "value": "product", "aliases": []},
                {"idx": 3, "label": "Delivery orders", "value": "delivery_order", "aliases": []}],
}


def test_a_delivered_report_keeps_v1s_breakdown_offer_as_a_pending():
    reply = r.answer_report(_spec(), refs={}, today=TODAY, mcp_call=_Mcp(OFFER_ENVELOPE),
                            contact_id="c1", space_id="s1", dealer=True)
    assert reply.pending is not None
    options = reply.pending["options"]
    assert [o["key"] for o in options] == ["1", "2", "3"]
    assert [o["label"] for o in options] == ["By customer", "By product", "Delivery orders"]
    assert options[0]["payload"]["report"]["group_by"] == ["customer"]


def test_a_held_delivered_report_with_a_breakdown_asks_v1_with_its_detail_pick():
    from app.services.chatbot.lanes.business import fetch

    mcp = _Mcp(OFFER_ENVELOPE)
    r.answer_report(_spec(group_by=["customer"]), refs={}, today=TODAY, mcp_call=mcp, contact_id="c1", space_id="s1", dealer=True)
    assert [c[0] for c in mcp.calls] == ["crm_sales_report"]
    v1 = fetch.entity_ids_transformer(
        {"tool": "crm_sales_report", "entities": [],
         "semantic_input": {"date_filter_start": "2026-09-01", "date_filter_end": "2026-09-30",
                            "outstanding_detail_pick": "customer"}}, space_id="s1")
    assert v1["group_by"] == "customer"
    strip = lambda a: {k: v for k, v in a.items() if k not in ("contact_id", "_diagnostics", "view")}
    assert strip(mcp.calls[0][1]) == strip(v1)


def test_a_new_period_replaces_the_held_reports_period():
    h = mk_held("sales")
    h.report = _spec(group_by=["customer"], top_n=10)
    state, actions = apply_turn(mk_state(h), out(_ask(act="refines_held", period=AUG.model_dump(mode="json"))),
                                resolver=_Fake({}))
    assert state.held.report.period.date_from == date(2026, 8, 1)
    assert state.held.report.period.date_to == date(2026, 8, 31)


# ------------------------------------------------------------------ 7 mirrored offer shape and re-ask


def test_a_mirrored_yes_no_offer_shows_only_yes_and_no():
    state = _team_pick_state()
    assert [o.key for o in state.pending.options] == ["yes", "no"], [o.key for o in state.pending.options]
    text = clarify_copy.render(state.pending, "en")
    assert text.count("Yes") <= 1


def test_a_non_answer_act_replaces_a_mirrored_offer_instead_of_re_asking_it():
    state = _team_pick_state()
    state, actions = apply_turn(state, out(_ask(act="switch_back", switch_back_to="T9")), resolver=_Fake({}))
    act = actions[0]
    assert not (isinstance(act, ClarifyAct) and act.reask)
    text = clarify_copy.render(act.pending, "en") if isinstance(act, ClarifyAct) else ""
    assert "didn't catch" not in text.lower()
    assert state.pending is None or state.pending.kind != "v1_open_question"
