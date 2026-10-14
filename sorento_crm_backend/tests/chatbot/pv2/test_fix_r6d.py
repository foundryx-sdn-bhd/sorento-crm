"""PV2-FIX r6d: an unscoped staff contact's delivered-report drill pick, including sales_agent
(contracts.SALES_REPORT_GROUP_BYS, which v1's crm_sales_report offers to unscoped staff)."""
from __future__ import annotations

import pytest

from tests.chatbot.pv2.carry_fakes import mk_state, option, out, pending, held as mk_held
from app.services.chatbot.pv2 import answer as answer_mod
from app.services.chatbot.pv2 import turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.state import dump_state
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver
from tests.chatbot.pv2.test_fix_r6 import SEP, _ask, _delivered_total


def _run(parsed, state, mcp):
    """An UNSCOPED staff contact (dealer=False)."""
    ctx = turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(state), profile=MemoryProfile(audience="staff"),
        parse=lambda m, u: parsed, resolver=FakeResolver({}), load_exchanges=lambda s: [],
        report_answer=answer_mod.default_report_answer(mcp_call=mcp, contact_id="c1", space_id="s1", dealer=False),
        granted_reveals=frozenset({"sales_orders.sales_report"}))
    return turn._run(ctx)


class _StaffMcp:
    def __init__(self):
        self.calls = []

    def __call__(self, name, args):
        self.calls.append((name, args))
        return {"result_type": "sales_report", "response": "Delivered sales\nTotal RM 1.00", "has_result": True,
                "options": [{"idx": i, "label": label, "value": value, "aliases": []}
                            for i, (label, value) in enumerate(
                                [("By customer", "customer"), ("By product", "product"),
                                 ("Delivery orders", "delivery_order"), ("By sales agent", "sales_agent")], 1)]}


def _staff_state():
    h = mk_held("sales")
    h.period = SEP
    h.report = _delivered_total()
    labels = [("By customer", "customer"), ("By product", "product"), ("Delivery orders", "delivery_order"),
              ("By sales agent", "sales_agent")]
    opts = [option(str(i), label, {"report": {"group_by": [value]}}) for i, (label, value) in enumerate(labels, 1)]
    return mk_state(h, pending_=pending("clarify_report_breakdown", opts, asked="*Drill down:*"))


def _pick_sales_agent():
    mcp = _StaffMcp()
    res = _run(out(_ask(act="answers_bot_question", answer={"option_key": "4", "free_value": None})),
               _staff_state(), mcp)
    return mcp, res


def test_a_staff_sales_agent_drill_runs_v1s_report_by_sales_agent():
    mcp, res = _pick_sales_agent()
    assert [c[0] for c in mcp.calls] == ["crm_sales_report"], res.direct_reply
    assert mcp.calls[0][1].get("group_by") == "sales_agent"
    assert "How many" not in (res.direct_reply or "")
    assert not res.state.held.report.group_by, "the total stays held"
    assert res.state.pending is not None and res.state.pending.kind == "clarify_report_breakdown"


def test_a_drill_pick_that_does_not_run_never_leaves_a_total_beside_a_measure_question():
    mcp, res = _pick_sales_agent()
    if mcp.calls:
        return
    report = res.state.held.report
    pend = res.state.pending
    assert report.group_by == ["sales_agent"], "held total + question about the breakdown is inconsistent"
    assert pend is not None and pend.kind == "clarify_report_measure"


def test_a_new_topic_with_a_clarify_flag_replaces_an_open_did_you_mean():
    """cand-p9ob step 2 (r6 dev run): a new_topic report ask that also sets clarify.needed must start
    its own topic, not re-ask the open did-you-mean as a miss."""
    from app.services.chatbot.pv2.state import ClarifyAct
    from tests.chatbot.pv2.carry_fakes import mk_state

    codes = ["CB34003", "CB44003", "CB48003", "CB52003", "CB58003"]
    opts = [option(str(i), c, {"kind": "product", "key": c, "label": c}) for i, c in enumerate(codes, 1)]
    state = mk_state(mk_held("inventory"), pending_=pending(
        "clarify_did_you_mean",
        opts, asked='Couldn\'t find "CBDR4003" (product). Did you mean:\n1. CB34003\n2. CB44003'))
    this_month = {"preset": "this_month", "n": None, "date_from": None, "date_to": None}
    report = {"dataset": "sales", "measures": [], "group_by": [], "top_n_per_group": None, "filters": [],
              "period": this_month, "compare_to": None, "compare_period": None, "grain": None,
              "threshold": None, "sort": "desc", "top_n": None, "basis": None}
    ents = [{"kind": "brand", "key": "SORENTO", "phrase": "Sorento", "op": "set", "refers_to_held": False,
             "quantity": None, "doc_number": None},
            {"kind": "brand", "key": "MOCHA", "phrase": "Mocha", "op": "set", "refers_to_held": False,
             "quantity": None, "doc_number": None},
            {"kind": "product_class", "key": "water_closet", "phrase": "water closet", "op": "set",
             "refers_to_held": False, "quantity": None, "doc_number": None}]
    parsed = out(_ask(act="new_topic", domain="sales", period=this_month, report=report, entities=ents),
                 clarify={"needed": True, "phrase": "Top sales", "about": "report_measure"})
    from tests.chatbot.pv2.test_fix_round2 import _Fake

    table = {"brand|SORENTO": ("exact", [("brand", "SORENTO")], False),
             "brand|MOCHA": ("exact", [("brand", "MOCHA")], False),
             "product_class|water_closet": ("exact", [("product_class", "water_closet")], False)}
    from app.services.chatbot.pv2.apply import apply_turn

    new_state, actions = apply_turn(state, parsed, resolver=_Fake(table))
    act = actions[0]
    assert not (isinstance(act, ClarifyAct) and act.reask), "re-asked the open did-you-mean as a miss"
    assert new_state.held.domain == "sales"
    assert new_state.pending is None or new_state.pending.kind != "clarify_did_you_mean"


def test_a_dealer_delivered_report_by_sales_agent_gets_the_refusal_line():
    from app.services.chatbot.lanes.business import report_ask
    from app.services.chatbot.pv2 import answer as answer_mod
    from app.services.chatbot.pv2 import turn
    from app.services.chatbot.pv2.history import MemoryProfile
    from app.services.chatbot.pv2.state import dump_state
    from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, mk_state

    mcp = _StaffMcp()
    sep = {"preset": None, "n": None, "date_from": "2026-09-01", "date_to": "2026-09-30"}
    report = {"dataset": "sales", "measures": [], "group_by": ["sales_agent"], "top_n_per_group": None,
              "filters": [], "period": sep, "compare_to": None, "compare_period": None, "grain": None,
              "threshold": None, "sort": None, "top_n": None, "basis": "delivered"}
    parsed = out(_ask(act="new_topic", domain="sales", period=sep, report=report))
    ctx = turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(mk_state()), profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: parsed, resolver=FakeResolver({}), load_exchanges=lambda s: [],
        report_answer=answer_mod.default_report_answer(mcp_call=mcp, contact_id="c1", space_id="s1", dealer=True),
        granted_reveals=frozenset({"sales_orders.sales_report"}))
    res = turn._run(ctx)
    assert mcp.calls == [], "a dealer must never reach crm_sales_report by sales agent"
    assert res.direct_reply is not None and report_ask.DEALER_REFUSAL in res.direct_reply, res.direct_reply
