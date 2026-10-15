"""PV2 R5 fix round 1: red tests, no LLM (UAC R5-2 re-scoped: only lines the user may see count; R5-7).

1  the SO half of R5-2 through the real chain: so_status.lookup -> business._register_rows -> the engine's register
   write (`register.document_lines` into state.doc_lines) -> a "this product" parse.
2  an SO line with nothing outstanding is not on the SO outstanding card: never "this product", never an option.
3  "this product" resolves from the LAST answer's document only.
4  the reply scrub: untouched when no id, fast on a long space run, no leftover "()" where an id was removed.
"""
from __future__ import annotations

import time
import uuid

import pytest

from app.models.order import SalesOrder, SalesOrderLine
from app.services.chatbot import engine as engine_mod
from app.services.chatbot import so_status
from app.services.chatbot.lanes import business
from app.services.chatbot.pv2 import register
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.state import ClarifyAct, Lookup
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot.pv2 import _register_envelopes as E
from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held as mk_held, mk_state, out

UID = "5d0c7a14-9b3e-4f28-8a61-0c4e2b7d9f13"


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _exact(key):
    return {"status": "exact", "refs": [{"kind": "product", "key": key, "label": key}]}


def _resolver(*codes):
    return FakeResolver({"entities": {f"product|{c}": _exact(c) for c in codes}})


def _so(db, number, lines):
    """lines: [(code, ordered, delivered)]"""
    so = SalesOrder(id=str(uuid.uuid4()), so_number=number, debtor_name="HANLIM TRADING SDN BHD",
                    company_id=DEFAULT_COMPANY_ID)
    db.add(so)
    db.flush()
    for code, ordered, delivered in lines:
        p = S.product(db, code)
        db.add(SalesOrderLine(id=str(uuid.uuid4()), sales_order_id=so.id, product_id=p.id,
                              qty_ordered=ordered, qty_delivered=delivered))
    db.flush()
    return so


def _so_answer_state(db, number, lines):
    so = _so(db, number, lines)
    got = so_status.lookup(db, [number], linked=None)
    rows = business._register_rows("sales_order", got.rows, got.lines)
    st = mk_state(mk_held("order", {"sales_order": [number]}), turn_no=3)
    engine_mod._pv2_write_register({"pv2_state": st}, [{"domain": "order", "rows": rows}])
    return st, so


def _this_product(domain="inventory"):
    return out(ask(act="refines_held", domain=domain, entities=[entity("product", refers_to_held=True)]))


def _held_products(st):
    return [x.key for x in st.held.refs.get("product", [])]


# ------------------------------------------------------------------ 1 the real SO chain

@pytest.mark.parametrize("domain", ["inventory", "master_products"])
def test_1_this_product_after_a_one_line_sales_order_answer_runs_through_the_real_chain(db, domain):
    st, _ = _so_answer_state(db, "SO730101", [("SRTWQ7301", 4, 0)])
    assert st.doc_lines, "the register write kept the card's line codes"
    s2, acts = apply_turn(st, _this_product(domain), resolver=_resolver("SRTWQ7301"), now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], Lookup), f"{type(acts[0]).__name__}: {getattr(acts[0], 'about', None)}"
    assert _held_products(s2) == ["SRTWQ7301"]


def test_1_without_the_document_lines_write_the_pick_does_not_resolve(db, monkeypatch):  # guard: passes today
    monkeypatch.setattr(register, "document_lines", lambda *a, **k: {})
    st, _ = _so_answer_state(db, "SO730102", [("SRTWQ7302", 4, 0)])
    _, acts = apply_turn(st, _this_product(), resolver=_resolver("SRTWQ7302"), now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], ClarifyAct)


# ------------------------------------------------------------------ 2 only lines the SO card prints

def test_2_a_fully_delivered_so_line_is_never_this_product(db):
    st, _ = _so_answer_state(db, "SO730201", [("SRTWQ7311", 6, 2), ("SRTWQ7312", 3, 3)])
    s2, acts = apply_turn(st, _this_product(), resolver=_resolver("SRTWQ7311", "SRTWQ7312"), now=NOW,
                          can_offer_handover=False)
    assert isinstance(acts[0], Lookup), f"{type(acts[0]).__name__}"
    assert _held_products(s2) == ["SRTWQ7311"], _held_products(s2)


def test_2_the_delivered_lines_code_is_nowhere_in_the_saved_state_or_a_clarify(db):
    st, _ = _so_answer_state(db, "SO730202", [("SRTWQ7321", 6, 0), ("SRTWQ7322", 3, 3), ("SRTWQ7323", 9, 1)])
    assert "SRTWQ7322" not in st.model_dump_json()
    s2, acts = apply_turn(st, _this_product(), resolver=_resolver("SRTWQ7321", "SRTWQ7323"), now=NOW,
                          can_offer_handover=False)
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__
    labels = sorted(o.label for o in acts[0].pending.options)
    assert labels == ["SRTWQ7321", "SRTWQ7323"], labels
    assert "SRTWQ7322" not in s2.model_dump_json()


# ------------------------------------------------------------------ 3 the last answer's document only

def test_3_this_product_does_not_resolve_from_an_earlier_answers_document():
    st = mk_state(mk_held("order", {"delivery_order": ["202609-2013"]}), turn_no=3)
    engine_mod._pv2_write_register({"pv2_state": st}, [E.envelope(E.ORDERS_TOOL, {"data": [E.ORDERS_RAW["data"][1]]})])
    st.turn_no = 4  # a later answer printed no document rows (a stock answer)
    st.held.refs = {}
    engine_mod._pv2_write_register({"pv2_state": st}, [{"domain": "inventory", "figures": []}])
    assert st.last_rows == []
    _, acts = apply_turn(st, _this_product(), resolver=_resolver("SRTW5000"), now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__


def test_3_guard_the_document_of_the_last_answer_still_resolves():  # guard: passes today
    st = mk_state(mk_held("order", {"delivery_order": ["202609-2013"]}), turn_no=3)
    engine_mod._pv2_write_register({"pv2_state": st}, [E.envelope(E.ORDERS_TOOL, {"data": [E.ORDERS_RAW["data"][1]]})])
    s2, acts = apply_turn(st, _this_product(), resolver=_resolver("SRTW5000"), now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], Lookup) and _held_products(s2) == ["SRTW5000"]


# ------------------------------------------------------------------ 4 the reply scrub

@pytest.mark.parametrize("text", ["Here is the list () for you", "Pick one (see below)\n1. SRTWC1\n2. SRTWC2 ()"])
def test_4_a_reply_with_no_id_comes_out_unchanged(text):
    assert register.scrub_ids(text) == text


def test_4_brackets_around_a_removed_id_go_and_other_brackets_stay():
    got = register.scrub_ids(f"SRTWC1 ({UID}) is ready (see below)")
    assert "()" not in got and UID not in got and "(see below)" in got, got
    assert "SRTWC1" in got


def test_4_a_known_label_replaces_the_id_inside_brackets():  # guard: passes today
    assert register.scrub_ids(f"Order ({UID})", {UID: "SO730301"}) == "Order (SO730301)"


def test_4_a_very_long_space_run_after_an_id_finishes_fast():
    text = f"Item {UID}" + " " * 65536 + "(note)"
    t0 = time.perf_counter()
    got = register.scrub_ids(text)
    assert time.perf_counter() - t0 < 0.5
    assert UID not in got and "(note)" in got


def test_4_only_the_brackets_an_id_removal_left_go_an_unrelated_empty_pair_stays():
    got = register.scrub_ids(f"Call us () later\nItem SRTWC1 ({UID})")
    assert UID not in got and "Call us () later" in got, got
    assert got.split("\n")[1].strip() == "Item SRTWC1", got
