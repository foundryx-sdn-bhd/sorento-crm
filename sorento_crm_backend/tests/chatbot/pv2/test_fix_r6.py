"""PV2-FIX round 6 red tests, built from the crew's live traces (scratchpad r6-live-traces.md):
SO kinds for linked dealers, a bare quantity under a v1 product pick, a report breakdown pick and
a period refine. States mirror the trace MEMORY blocks (for example the held report has measures=none)."""
from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest

from app.models.order import SalesOrder
from app.services.chatbot.contracts import visible_reference_kinds
from app.services.chatbot.pv2 import answer as answer_mod
from app.services.chatbot.pv2 import bridge, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.resolve import DbResolver, Entity, Held
from app.services.chatbot.pv2.schema import Ask, Period, ReportSpec
from app.services.chatbot.pv2.state import Lookup, ResolvedRef, dump_state
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import (
    NOW, FakeResolver, ask as _base_ask, held as mk_held, mk_state, option, out, pending,
)
from tests.chatbot.pv2.test_fix_round2 import _Fake

SO = "sales_orders.outstanding"
TODAY = date(2026, 10, 14)
PROMPT = (Path(turn.__file__).parent / "prompt_v2.txt").read_text(encoding="utf-8").lower()


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


# ------------------------------------------------------------------ 1 SO number for a linked dealer


def test_a_linked_dealer_with_the_so_grant_probes_the_same_kinds_as_v1():
    assert turn.resolver_reference_kinds([SO], False, True) == visible_reference_kinds([SO], False)
    assert "sales_order" in turn.resolver_reference_kinds([SO], False, True)


def test_staff_without_the_so_grant_still_has_no_sales_order_kind():
    kinds = turn.resolver_reference_kinds([], True, False)
    assert kinds == visible_reference_kinds([], True)
    assert "sales_order" not in kinds


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _so(db, number, customer):
    row = SalesOrder(id=str(uuid.uuid4()), so_number=number, customer_id=customer.id,
                     debtor_name=customer.customer_name, company_id=DEFAULT_COMPANY_ID)
    db.add(row)
    db.flush()
    return row


def _resolve_so(db, links):
    ent = Entity.model_construct(kind="sales_order", key=None, phrase="SO422057", op="set",
                                 refers_to_held=False, doc_number=None, quantity=None)
    kinds = ["customer_order", "sales_order", "customer_po_ref"]
    return DbResolver(db, pinned={}, reference_kinds=kinds, reference_links=links).resolve_entities(
        [ent], held=Held())[0]


def test_a_linked_dealer_finds_its_own_sales_order_and_never_another_customers(db):
    mine = S.customer(db, "MINE", "My Dealer Sdn Bhd")
    other = S.customer(db, "OTHER", "Other Dealer Sdn Bhd")
    _so(db, "SO422057", other)
    assert _resolve_so(db, [mine.id]).status == "none"
    own = _resolve_so(db, [other.id])
    assert own.status == "exact" and own.refs[0].key == "SO422057"


def test_the_prompt_puts_a_sales_order_number_in_doc_number():
    assert "sales order number" in PROMPT


# ------------------------------------------------------------------ 2 a bare quantity under a v1 product pick

SH = ["SRTWC286-SH", "SRTWC286-SH-150", "SRTWC286-SH-200", "SRTWC286-SH-NEW", "SRTWC286-SH-NEW-150",
      "SRTWC286-SH-NEW-200", "SRTWC286-SH-NEW-P", "SRTWC286-SH-P", "SRTWC286-SH-PP", "SRTWC286-SH-UF"]


def _pick_state():
    h = mk_held("inventory", {"product": ["SRTWC286"]})
    h.refs["product"][0] = ResolvedRef(kind="product", key="SRTWC286", label="SRTWC286 (10 variants)")
    state = mk_state(h, pending_=pending("v1_open_question", [option(str(i), c) for i, c in enumerate(SH, 1)]))
    state.pending.asked = ""
    state.pending.v1_kind = "product_pick"
    return state


QTY_ENTITY = {"kind": "product", "key": None, "phrase": None, "op": "set", "refers_to_held": True,
              "quantity": {"number": 5, "unit": "unit"}, "doc_number": None}


@pytest.mark.parametrize("act,answer", [("refines_held", None),
                                        ("answers_bot_question", {"option_key": None, "free_value": None})])
def test_the_prompts_quantity_entity_shape_requantifies_the_held_family(act, answer):
    state, actions = apply_turn(
        _pick_state(), out(_ask(act=act, answer=answer, entities=[QTY_ENTITY])),
        resolver=_Fake({"product|None": ("exact", [("product", "SRTWC286")], False)}))
    assert isinstance(actions[0], Lookup)
    assert actions[0].requantify is True
    v = bridge.to_v1_verdict(state, actions[0], today=TODAY)
    assert v["demand_qty"] == 5


def test_the_prompt_says_a_bare_quantity_reply_is_only_a_quantity():
    assert "only a quantity" in PROMPT


# ------------------------------------------------------------------ 3 report breakdown pick and period refine

SEP = Period(preset=None, n=None, date_from=date(2026, 9, 1), date_to=date(2026, 9, 30))


def _delivered_total():
    return ReportSpec.model_validate(dict(
        dataset="sales", measures=[], group_by=[], top_n_per_group=None, filters=[], period=SEP,
        compare_to=None, compare_period=None, grain=None, threshold=None, sort=None, top_n=None,
        basis="delivered"))


def _breakdown_state():
    h = mk_held("sales")
    h.period = SEP
    h.report = _delivered_total()
    opts = [option(str(i), label, {"report": {"group_by": [value]}})
            for i, (label, value) in enumerate([("By customer", "customer"), ("By product", "product"),
                                                ("Delivery orders", "delivery_order")], 1)]
    state = mk_state(h, pending_=pending("clarify_report_breakdown", opts, asked="*Drill down:*\n1. By customer"))
    return state


class _Mcp:
    def __init__(self):
        self.calls = []

    def __call__(self, name, args):
        self.calls.append((name, args))
        return {"result_type": "sales_report", "response": "Delivered sales\nTotal RM 1.00", "has_result": True,
                "options": [{"idx": 1, "label": "By customer", "value": "customer", "aliases": []},
                            {"idx": 2, "label": "By product", "value": "product", "aliases": []},
                            {"idx": 3, "label": "Delivery orders", "value": "delivery_order", "aliases": []}]}


def _run(parsed, state, mcp):
    ctx = turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(state), profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: parsed, resolver=FakeResolver({}), load_exchanges=lambda s: [],
        report_answer=answer_mod.default_report_answer(mcp_call=mcp, contact_id="c1", space_id="s1", dealer=True),
        granted_reveals=frozenset({"sales_orders.sales_report"}))
    return turn._run(ctx)


def test_picking_1_runs_v1s_delivered_report_by_customer_and_keeps_the_total_held():
    mcp = _Mcp()
    res = _run(out(_ask(act="answers_bot_question", answer={"option_key": "1", "free_value": None})),
               _breakdown_state(), mcp)
    assert [c[0] for c in mcp.calls] == ["crm_sales_report"], res.direct_reply
    assert mcp.calls[0][1].get("group_by") == "customer"
    assert "How many" not in (res.direct_reply or "")
    assert not res.state.held.report.group_by, "the total stays held; v1's next 'only August' is a total"
    assert res.state.pending is not None and res.state.pending.kind == "clarify_report_breakdown"


def test_only_august_reruns_the_held_delivered_report_for_august_without_a_question():
    mcp = _Mcp()
    aug = {"preset": None, "n": None, "date_from": "2026-08-01", "date_to": "2026-08-31"}
    res = _run(out(_ask(act="refines_held", period=aug)), _breakdown_state(), mcp)
    assert [c[0] for c in mcp.calls] == ["crm_sales_report"], res.direct_reply
    args = mcp.calls[0][1]
    assert args.get("date_from") == "2026-08-01" and args.get("date_to") == "2026-08-31"
    assert "?" not in (res.direct_reply or "").splitlines()[-1] or "Drill down" in res.direct_reply


def test_the_prompt_says_a_period_named_under_a_pending_question_is_a_refine():
    assert "names a period" in PROMPT
