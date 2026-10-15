"""PV2 R5 fix round 5, red tests, no LLM, no DB: the grant pre-check must judge the domain apply will run.

When a numbered reply also names an entity, apply falls through to a refine in the parser's domain (the open
question is a confirm or a digit-keyed v1 menu), so that domain must be judged, not dropped. A bare number is still
the open question's (its own ask's domain runs; a mis-tagged domain is ignored).
"""
from __future__ import annotations

import pytest

from app.services.chatbot.lanes.business import SALES_REPORT_NOT_ENABLED_MESSAGE
from app.services.chatbot.pv2 import turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.state import dump_state
from app.services.chatbot.turn import pending as v1p
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held as mk_held, mk_state, option, out, pending
from tests.chatbot.pv2.test_r5_fix1_picks import _lim_confirm
from tests.chatbot.pv2.test_r5_fix2_picks import _which_line_menu
from tests.chatbot.pv2.test_r5_d_doc_lines import _resolver

NO_SALES = frozenset({"delivery_orders.outstanding"})


def _run(state, parsed, resolver, granted=NO_SALES):
    ctx = turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(state), profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: parsed, resolver=resolver, load_exchanges=lambda s: [], granted_reveals=granted)
    return turn._run(ctx)


def _v1_digit_menu():
    """A v1 customer menu mirrored with digit keys."""
    opts = [{"code": f"C{i}", "label": lab, "position": i, "entity_type": "customer"}
            for i, lab in enumerate(["ABC TRADING", "ABD TRADING"], 1)]
    v1 = v1p.ask("customer_pick", opts, payload={"domain": "order"}, asked_at_turn=3)
    st = turn.mirror_v1_pending(mk_state(mk_held("order")), v1, last_reply="Which customer?\n1. ABC TRADING\n2. ABD TRADING")
    assert st.pending is not None
    return st


def _one_ask(domain, act="answers_bot_question"):
    """'1, sales for ABC': one ask, a number, a named customer, and the domain the parser tagged."""
    return out(ask(act=act, domain=domain, answer={"option_key": None, "free_value": "1"},
                   entities=[entity("customer", phrase="ABC")]))


@pytest.mark.parametrize("state_of", [_lim_confirm, _v1_digit_menu], ids=["confirm", "v1-digit-menu"])
@pytest.mark.parametrize("act", ["answers_bot_question", "refines_held"])
def test_a_number_beside_a_named_customer_in_an_ungranted_domain_is_refused_before_any_lookup(state_of, act):
    """RED: apply runs the ask as a refine in `sales`, but the pre-check dropped the domain for the number."""
    r = FakeResolver({})
    res = _run(state_of(), _one_ask("sales", act), r)
    assert res.direct_reply == SALES_REPORT_NOT_ENABLED_MESSAGE, (res.direct_reply, res.verdict)
    assert res.actions == [], "refused before apply ran"
    assert r.calls == [], r.calls


def test_an_option_key_answer_tagged_with_an_ungranted_domain_is_judged_in_that_domain():
    """An option key keeps the parser's domain through apply (the answer's refine fields), so it is judged."""
    r = FakeResolver({})
    parsed = out(ask(act="answers_bot_question", domain="sales", answer={"option_key": "yes", "free_value": None}))
    res = _run(_lim_confirm(), parsed, r)
    assert res.direct_reply == SALES_REPORT_NOT_ENABLED_MESSAGE, res.direct_reply
    assert res.actions == [] and r.calls == []


def test_a_bare_number_tagged_with_an_ungranted_domain_still_runs_the_menus_stock_lookup():  # guard: passes today
    st = _which_line_menu()
    menu = [o.label for o in st.pending.options]
    parsed = out(ask(act="new_topic", domain="sales", row_pick=2))
    res = _run(st, parsed, _resolver())
    assert res.direct_reply != SALES_REPORT_NOT_ENABLED_MESSAGE, res.direct_reply
    assert res.verdict is not None and res.verdict["domain_hint"] == "inventory"
    assert [e["canonical_code"] for e in res.verdict["entities"] if e["hint"] == "product"] == [menu[1]]


# ------------------------------------------------------------------ fix round 6: a refine-answer's shown row is judged


def _so_row_state(open_question):
    st = _lim_confirm() if open_question else mk_state(mk_held("order"))
    st.last_rows = [option("1", "SO410001", {"kind": "row", "row_kind": "sales_order", "of": 1})]
    return st


def test_a_refine_answer_with_a_row_pick_on_an_so_row_is_refused_before_any_lookup_without_the_so_grant():
    """RED: the reply names a customer beside the number, so apply reads it as a refine and applies the shown SO row;
    the pre-check skipped `shown_row` for every answer, so only v1's fetch gate would catch the lost grant."""
    from app.services.chatbot.lanes.business.fetch import SO_NOT_ENABLED_MESSAGE

    r = FakeResolver({})
    parsed = out(ask(act="answers_bot_question", row_pick=1, answer={"option_key": None, "free_value": None},
                     entities=[entity("customer", phrase="ABC")]))
    res = _run(_so_row_state(True), parsed, r)
    assert res.direct_reply == SO_NOT_ENABLED_MESSAGE, (res.direct_reply, res.verdict)
    assert res.actions == [] and r.calls == [], r.calls


def test_guard_the_same_row_pick_with_no_open_question_is_refused_as_before():  # guard: passes today
    from app.services.chatbot.lanes.business.fetch import SO_NOT_ENABLED_MESSAGE

    r = FakeResolver({})
    res = _run(_so_row_state(False), out(ask(act="refines_held", domain="order", row_pick=1)), r)
    assert res.direct_reply == SO_NOT_ENABLED_MESSAGE, res.direct_reply
    assert res.actions == [] and r.calls == []


# ------------------------------------------------------------------ keyed pick beside a number keeps the parser's domain


@pytest.mark.parametrize("extra", [{"row_pick": 1}, {"answer": {"option_key": "yes", "free_value": "1"}}],
                         ids=["row-pick", "free-value"])
def test_guard_an_option_key_beside_a_number_is_still_judged_in_the_tagged_domain(extra):  # guard: passes today
    """'yes' picks by key, so the number does not run the question's own ask and the parser's domain (sales) stays
    judged: refused for a contact without the sales grant, before any resolver call."""
    r = FakeResolver({})
    answer = extra.get("answer") or {"option_key": "yes", "free_value": None}
    parsed = out(ask(act="answers_bot_question", domain="sales", answer=answer, row_pick=extra.get("row_pick")))
    res = _run(_lim_confirm(), parsed, r)
    assert res.direct_reply == SALES_REPORT_NOT_ENABLED_MESSAGE, res.direct_reply
    assert res.actions == [] and r.calls == []
