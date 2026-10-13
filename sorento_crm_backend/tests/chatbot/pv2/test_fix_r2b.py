"""PV2-FIX r2 batch B: PO/SPO grant, sales-report grant, hand-over text, customer families,
catalogue. Red tests from the crew brief (14 Oct)."""
from __future__ import annotations

import re
from datetime import date
from types import SimpleNamespace

import pytest

from app.services.chatbot.head.parser import assert_emission
from app.services.chatbot.pv2 import bridge, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.resolve import DbResolver, Entity, Held
from app.services.chatbot.pv2.schema import Ask
from app.services.chatbot.pv2.state import Lookup, dump_state

from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask as _base_ask, held as mk_held, mk_state, out

SRT = {"entities": {"product|SRTWC286": {"status": "exact", "refs": [
    {"kind": "product", "key": "SRTWC286", "label": "SRTWC286"}]}}}
PO_GRANT = "purchase_orders.placed"


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


def _ctx(parsed, **extra):
    return turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(mk_state()), profile=MemoryProfile(audience="staff"),
        parse=lambda messages, user_message: parsed, resolver=FakeResolver(SRT),
        load_exchanges=lambda since: [], **extra,
    )


def _product_ask(domain):
    ent = {"kind": "product", "key": None, "phrase": "SRTWC286", "op": "set", "refers_to_held": False,
           "quantity": None, "doc_number": None}
    return out(_ask(domain=domain, entities=[ent]))


# ------------------------------------------------------------------ 1 PO / SPO grant


@pytest.mark.parametrize("domain", ["purchase_order", "spo_allocation"])
def test_po_domains_without_the_grant_are_refused_not_answered(domain):
    r = turn._run(_ctx(_product_ask(domain), granted_reveals=frozenset()))
    assert r.verdict is None, "a v1 verdict would leak PO data"
    assert r.direct_reply and r.direct_reply.strip()
    assert not re.search(r"\d", r.direct_reply), r.direct_reply
    assert r.route != "lookup"


@pytest.mark.parametrize("domain", ["purchase_order", "spo_allocation"])
def test_unset_granted_reveals_is_the_closed_side(domain):
    r = turn._run(_ctx(_product_ask(domain)))
    assert r.verdict is None and r.direct_reply


def test_po_domain_with_the_grant_reaches_v1():
    r = turn._run(_ctx(_product_ask("purchase_order"), granted_reveals=frozenset({PO_GRANT})))
    assert r.verdict is not None and r.verdict["domain_hint"] == "purchase_order"


# ------------------------------------------------------------------ 2 sales report grant

SALES_REPORT = {
    "dataset": "sales", "measures": ["amount"], "group_by": ["product"], "top_n_per_group": None, "filters": [],
    "period": None, "compare_to": None, "compare_period": None, "grain": None, "threshold": None,
    "sort": None, "top_n": None, "basis": None,
}


def _report_ctx(**extra):
    calls = []

    def report_answer(spec, refs, today, row, incoming):
        calls.append(spec)
        return SimpleNamespace(text="For which period?", pending=None, rows=[], plan=None, frame=None, error=False)

    parsed = out(_ask(domain="sales", report=SALES_REPORT))
    ctx = turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(mk_state()), profile=MemoryProfile(audience="staff"),
        parse=lambda m, u: parsed, resolver=FakeResolver({}), load_exchanges=lambda s: [],
        report_answer=report_answer, **extra,
    )
    return ctx, calls


def test_sales_report_without_the_grant_is_refused_before_the_period_question():
    from app.services.chatbot.lanes.business import SALES_REPORT_NOT_ENABLED_MESSAGE

    ctx, calls = _report_ctx(granted_reveals=frozenset())
    r = turn._run(ctx)
    assert r.direct_reply == SALES_REPORT_NOT_ENABLED_MESSAGE
    assert "period" not in (r.direct_reply or "").lower()
    assert calls == []


def test_sales_report_with_the_grant_asks_the_period_as_today():
    ctx, calls = _report_ctx(granted_reveals=frozenset({"sales_orders.sales_report"}))
    r = turn._run(ctx)
    assert r.direct_reply == "For which period?"
    assert len(calls) == 1


# ------------------------------------------------------------------ 3 hand-over text


def _helper():
    from app.services.chatbot.engine import _hand_over_reply_text

    return _hand_over_reply_text


def _send(text):
    return {"kind": "send_message", "text": text, "quick_replies": None, "result_set": [], "dry_run": False}


def test_hand_over_text_prefers_the_reply_text():
    assert _helper()("Hello", [_send("Other")]) == "Hello"


def test_hand_over_text_falls_back_to_the_first_send_message():
    actions = [{"kind": "assign_ticket"}, _send("Routed to our team."), _send("second")]
    assert _helper()(None, actions) == "Routed to our team."
    assert _helper()("", actions) == "Routed to our team."


def test_hand_over_text_is_none_when_nothing_says_it():
    assert _helper()(None, [{"kind": "assign_ticket"}]) is None
    assert _helper()(None, []) is None


# ------------------------------------------------------------------ 4 customer families

AC = ["ACFIRST CERAMICS SDN BHD - [A/C I]", "ACFIRST CERAMICS SDN BHD - [CERAMIC]", "ACFIRST CERAMICS SDN BHD - [IBORN]"]


def _cust_ent(phrase):
    return Entity.model_construct(kind="customer", key=None, phrase=phrase, op="set", refers_to_held=False,
                                  doc_number=None, quantity=None)


def test_one_family_with_three_accounts_is_exact_with_every_account(db):
    for i, name in enumerate(AC):
        S.customer(db, f"ACF{i}", name)
    res = DbResolver(db, pinned={}).resolve_entities([_cust_ent("Acfirst ceramics")], held=Held())[0]
    assert res.status == "exact"
    assert len(res.refs) == 3


def test_two_families_ask_once_per_family_with_family_labels(db):
    S.customer(db, "FS1", "FULLSHUN SANITARYWARE SDN BHD")
    S.customer(db, "FS2", "FULLSHUN BATH CONCEPT SDN BHD")
    res = DbResolver(db, pinned={}).resolve_entities([_cust_ent("Fullshun")], held=Held())[0]
    assert res.status == "ambiguous"
    assert len(res.options) == 2
    for o in res.options:
        S.assert_clean_label(o.label)
        assert not re.search(r"FS\d|\[", o.label), o.label


def test_picking_a_family_holds_every_account_of_it(db):
    for i, name in enumerate(AC):
        S.customer(db, f"ACF{i}", name)
    S.customer(db, "ACT1", "ACFIRST TRADING SDN BHD")
    resolver = DbResolver(db, pinned={})
    state, actions = apply_turn(
        mk_state(),
        out(_ask(domain="sales", entities=[{"kind": "customer", "key": None, "phrase": "Acfirst", "op": "set",
                                            "refers_to_held": False, "quantity": None, "doc_number": None}])),
        resolver=resolver,
    )
    options = actions[0].pending.options
    assert len(options) == 2
    pick = next(o.key for o in options if "CERAMICS" in o.label.upper())
    state, actions = apply_turn(
        state, out(_ask(act="answers_bot_question", answer={"option_key": pick, "free_value": None})),
        resolver=resolver,
    )
    assert isinstance(actions[0], Lookup)
    assert len(state.held.refs["customer"]) == 3


# ------------------------------------------------------------------ 5 catalogue


def test_catalogue_reaches_v1_as_an_attachment_entity():
    h = mk_held("resource_attachment", {"product": ["SRTWC286"]}, attachment_type="catalogue")
    v = bridge.to_v1_verdict(mk_state(h), Lookup(ask_index=0, domain=h.domain, held=h), today=date(2026, 10, 14))
    assert_emission(v)
    att = [e for e in v["entities"] if e["hint"] == "attachment" and e["canonical_code"] == "catalogue"]
    assert len(att) == 1, v["entities"]
    assert [e for e in v["entities"] if e["hint"] == "attachment_type"]


def test_catalogue_is_not_restricted_to_the_held_products_files(db):
    prod = S.product(db, "SRTWC286")
    S.attach(db, prod, S.attachment_type(db, "certification", "Certification"))
    S.attachment_type(db, "catalogue", "Catalogue")
    resolver = DbResolver(db, pinned={"attachment_type": {"catalogue", "certification"}})
    state, actions = apply_turn(
        mk_state(),
        out(_ask(domain="resource_attachment", attachment_type="catalogue",
                 entities=[{"kind": "product", "key": None, "phrase": "SRTWC286", "op": "set",
                            "refers_to_held": False, "quantity": None, "doc_number": None}])),
        resolver=resolver,
    )
    assert isinstance(actions[0], Lookup)
    assert state.held.attachment_type == "catalogue"
