"""PV2-FIX round 4 red tests, each with a parity pin against v1: delivered sales report tool,
v1 pick lane, v1 stock requantify, product not-found wording, duplicate order."""
from __future__ import annotations

from datetime import date

import pytest

from app.services.chatbot.pv2 import bridge, turn
from app.services.chatbot.pv2 import reports as r
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.schema import Ask, Period, ReportSpec
from app.services.chatbot.pv2.state import ClarifyAct, HandOver, Lookup, ResolvedRef, dump_state

from tests.chatbot.pv2.carry_fakes import (
    NOW, FakeResolver, ask as _base_ask, held as mk_held, mk_state, option, out, pending,
)
from tests.chatbot.pv2.test_fix_round2 import _Fake, _e

TODAY = date(2026, 10, 15)
CONTACT, SPACE = "contact-1", "space-1"


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


# ------------------------------------------------------------------ 1 delivered sales report tool


def _spec(**kw):
    base = dict(dataset="sales", measures=["amount"], group_by=[], top_n_per_group=None, filters=[],
                period=Period(preset=None, n=None, date_from=date(2026, 9, 1), date_to=date(2026, 9, 30)),
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


V1_SALES = {"result_type": "sales_report", "response": "Delivered sales\nTotal RM 15.00", "has_result": True}


def _answer(spec, mcp, **kw):
    return r.answer_report(spec, refs={}, today=TODAY, mcp_call=mcp, contact_id=CONTACT, space_id=SPACE, **kw)


def _strip(args):
    return {k: v for k, v in args.items() if k not in ("contact_id", "space_id", "_diagnostics", "view")}


def test_a_delivered_total_goes_to_v1s_delivery_order_sales_report():
    from app.services.chatbot.lanes.business import fetch

    mcp = _Mcp(V1_SALES)
    reply = _answer(_spec(), mcp, dealer=True)
    assert len(mcp.calls) == 1
    name, args = mcp.calls[0]
    assert name == "crm_sales_report"
    v1 = fetch.entity_ids_transformer(
        {"tool": "crm_sales_report", "entities": [],
         "semantic_input": {"date_filter_start": "2026-09-01", "date_filter_end": "2026-09-30"}}, space_id=SPACE)
    assert v1["date_from"] == "2026-09-01" and v1["date_to"] == "2026-09-30"
    assert _strip(args) == _strip(v1)
    expected = fetch._sales_report_output(V1_SALES, {"tool": "crm_sales_report"})["response"]
    assert expected in reply.text


@pytest.mark.parametrize("kw", [dict(basis="ordered", top_n=10, group_by=["customer"]), dict(top_n=10, group_by=["product"])],
                         ids=["ordered", "ranking"])
def test_ordered_and_rankings_stay_on_report_ask(kw):
    mcp = _Mcp({"result_type": "report_ask", "response": "x", "has_result": True})
    _answer(_spec(**kw), mcp)
    assert [c[0] for c in mcp.calls] == ["crm_report_ask"]


# ------------------------------------------------------------------ 2 pick of a v1 question


def _pick(key, v1_kind):
    h = mk_held("inventory", {"product": ["SRTWC286"]})
    picked = option(key, "Option", {"kind": "choice", "key": key, "v1_kind": v1_kind})
    action = Lookup(ask_index=0, domain="inventory", held=h, picked=picked, pending_kind="v1_open_question")
    return bridge.to_v1_verdict(mk_state(h), action, today=TODAY)


def test_a_pick_of_a_v1_question_is_a_business_query_not_a_clarification():
    v = _pick("1", "product_pick")
    assert v["message_type"] == "business_query"
    assert v["open_question_answer"]["mode"] == "pick" and v["open_question_answer"]["picked"] == [1]
    assert v["entities"] and all(e["current_message"] is False for e in v["entities"])


def test_a_pick_of_a_v1_escalation_offer_confirms_it():
    v = _pick("1", "team_pick")
    assert v["is_affirmative"] is True
    assert v["escalation"]["is_escalation_confirmation"] is True


# ------------------------------------------------------------------ 3 quantity on a v1 stock pick


def test_a_quantity_alone_requantifies_the_held_family_on_v1s_pick_lane():
    h = mk_held("inventory", {"product": ["SRTWC286"]})
    state = mk_state(h, pending_=pending("v1_open_question", [option("1", "A"), option("2", "B")]))
    state.pending.v1_kind = "product_pick"
    ent = {"kind": "product", "key": None, "phrase": None, "op": "set", "refers_to_held": True,
           "quantity": {"number": 5, "unit": None}, "doc_number": None}
    state, actions = apply_turn(state, out(_ask(act="refines_held", domain="inventory", entities=[ent])),
                                resolver=_Fake({"product|None": ("exact", [("product", "SRTWC286")], False)}))
    assert isinstance(actions[0], Lookup)
    v = bridge.to_v1_verdict(state, actions[0], today=TODAY)
    assert v["demand_qty"] == 5
    prods = [e for e in v["entities"] if e["hint"] == "product"]
    assert prods and all(e["current_message"] is False and e["quantity"] is None for e in prods)
    assert v["open_question_answer"]["mode"] is None


# ------------------------------------------------------------------ 4 product not-found wording


def _ctx(parsed, state=None, **extra):
    return turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(state or mk_state()), profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: parsed, resolver=extra.pop("resolver", FakeResolver({})),
        load_exchanges=lambda s: [], **extra)


def _product_parsed():
    return out(_ask(domain="inventory", entities=[_e(phrase="SRTXYZ9")]))


def test_a_product_not_found_reads_like_v1_with_the_domains_team_offer():
    res = turn._run(_ctx(_product_parsed(), domain_teams={"inventory": "warehouse"}, offer_mode="offer"))
    assert ('Couldn\'t find: "SRTXYZ9" (product). Would you like me to escalate to warehouse team?'
            in res.direct_reply), res.direct_reply
    parsed = out(_ask(act="answers_bot_question", answer={"option_key": "yes", "free_value": None}))
    again = turn._run(_ctx(parsed, state=res.state, domain_teams={"inventory": "warehouse"}, offer_mode="offer"))
    assert any(isinstance(a, HandOver) and a.team_key == "warehouse" for a in again.actions)


def test_a_product_not_found_for_staff_is_the_sentence_alone():
    res = turn._run(_ctx(_product_parsed(), offer_mode="none"))
    assert res.direct_reply.strip() == 'Couldn\'t find: "SRTXYZ9" (product).', res.direct_reply


def test_a_did_you_mean_candidate_is_still_asked():
    table = {"product|SRTXYZ9": ("ambiguous", [("product", "SRTXYZ8")], True)}
    state, actions = apply_turn(mk_state(), _product_parsed(), resolver=_Fake(table))
    assert isinstance(actions[0], ClarifyAct) and actions[0].pending.options


# ------------------------------------------------------------------ 5 duplicate order


def test_two_lookups_on_one_domain_send_one_ask_to_v1():
    ent = {"kind": "order", "key": None, "phrase": None, "op": "set", "refers_to_held": False,
           "quantity": None, "doc_number": "202610-0151"}
    ent2 = {**ent, "doc_number": "202610-0152"}
    parsed = out(_ask(domain="order", entities=[ent]), _ask(domain="order", entities=[ent2]))
    table = {"order|202610-0151": ("exact", [("delivery_order", "202610-0151")], False),
             "order|202610-0152": ("exact", [("delivery_order", "202610-0152")], False)}
    res = turn._run(_ctx(parsed, resolver=_Fake(table)))
    asks = res.verdict.get("asks") if res.verdict else None
    assert not asks or [a["domain"] for a in asks].count("order") == 1, asks


def test_the_same_document_under_two_kinds_is_one_v1_entity():
    h = mk_held("order")
    h.refs["order"] = [ResolvedRef(kind="order", key="202610-0151", label="202610-0151")]
    h.refs["delivery_order"] = [ResolvedRef(kind="delivery_order", key="202610-0151", label="202610-0151")]
    v = bridge.to_v1_verdict(mk_state(h), Lookup(ask_index=0, domain="order", held=h), today=TODAY)
    assert [e["canonical_code"] for e in v["entities"]].count("202610-0151") == 1
