"""PV2-FIX round 5: guards that can fail (reference kinds), resolver scope, found-kind refs,
status held with the subject, did-you-mean always asked. No LLM."""
from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from app.models.order import SalesOrder
from app.models.procurement import PurchaseOrder
from app.services.chatbot.head.parser import assert_emission
from app.services.chatbot.pv2 import bridge
from app.services.chatbot.pv2.apply import ClarifyPolicy, apply_turn
from app.services.chatbot.pv2.resolve import DbResolver, Entity, Held
from app.services.chatbot.pv2.schema import Ask
from app.services.chatbot.pv2.state import ClarifyAct, Lookup
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import mk_state, out, ask as _base_ask
from tests.chatbot.pv2.test_fix_round2 import _Fake, _e

TODAY = date(2026, 10, 14)
SO = "sales_orders.outstanding"
ALL_KINDS = ["customer_order", "sales_order", "customer_po_ref", "purchase_order", "purchase_request"]
LEAK = {"sales_order", "customer_po_ref", "purchase_order", "purchase_request"}


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _ent(kind, phrase=None, doc_number=None) -> Entity:
    return Entity.model_construct(
        kind=kind, key=None, phrase=phrase, op="set", refers_to_held=False, doc_number=doc_number, quantity=None
    )


def _seed(db):
    so = SalesOrder(id=str(uuid.uuid4()), so_number="SO-PV2-1", debtor_name="Test Debtor", company_id=DEFAULT_COMPANY_ID)
    po = PurchaseOrder(id=str(uuid.uuid4()), po_number="PO-PV2-1", status="active")
    if hasattr(PurchaseOrder, "company_id"):
        po.company_id = DEFAULT_COMPANY_ID
    db.add_all([so, po])
    db.flush()


def _res(db, ent, kinds):
    return DbResolver(db, pinned={}, reference_kinds=kinds).resolve_entities([ent], held=Held())[0]


def _kinds_in(res):
    return {r.kind for r in res.refs} | {o.payload.get("kind") for o in res.options} | {o.key for o in res.options if False}


# ------------------------------------------------------------------ 1 guards that can fail


@pytest.mark.parametrize("ent", [
    _ent("sales_order", None, "SO-PV2-1"),
    _ent("order", None, "SO-PV2-1"),
    _ent("purchase_order", None, "PO-PV2-1"),
], ids=["sales_order", "order", "purchase_order"])
def test_kinds_the_asker_cannot_see_never_resolve(db, ent):
    _seed(db)
    res = _res(db, ent, ["customer_order"])
    assert not (_kinds_in(res) & LEAK), res
    hidden = {"SO-PV2-1", "PO-PV2-1"}
    shown = {r.key for r in res.refs} | {r.label for r in res.refs}
    shown |= {o.key for o in res.options} | {o.label for o in res.options}
    assert not (shown & hidden), f"hidden document number shown: {shown & hidden}"


def test_positive_control_all_kinds_resolve_the_so_and_po(db):
    _seed(db)
    so = _res(db, _ent("sales_order", None, "SO-PV2-1"), ALL_KINDS)
    po = _res(db, _ent("purchase_order", None, "PO-PV2-1"), ALL_KINDS)
    assert so.status == "exact" and so.refs[0].key == "SO-PV2-1"
    assert po.status == "exact" and po.refs[0].key == "PO-PV2-1"


# ------------------------------------------------------------------ 2 scope_for_resolver


def _scope(monkeypatch, *, staff, attrs=(SO,), scope=None, scope_raises=False, regions=("CENTRAL",), regions_raise=False):
    import app.services.chatbot.head.access as access
    import app.services.chatbot.lanes.business.services as bsvc
    import app.services.chatbot.turn.state as tstate
    import app.services.eta_policy as eta
    from app.services.chatbot.pv2.turn import scope_for_resolver

    monkeypatch.setattr(access, "_granted_field_reveal_keys", lambda *a, **k: list(attrs))

    def cs(*a, **k):
        if scope_raises:
            raise RuntimeError("boom")
        return scope

    monkeypatch.setattr(bsvc, "customer_scope", cs)
    monkeypatch.setattr(tstate, "is_staff_profile", lambda profile: staff)
    monkeypatch.setattr(eta, "resolve_request_contact", lambda *a, **k: object())

    def rules(*a, **k):
        if regions_raise:
            raise RuntimeError("boom")
        return SimpleNamespace(regions=list(regions))

    monkeypatch.setattr(eta, "rules_for_contact", rules)
    return scope_for_resolver(object(), "contact-1", "space-1", object())


def test_non_staff_never_gets_so_or_po_ref_kinds(monkeypatch):
    r = _scope(monkeypatch, staff=False, scope=None)
    assert not ({"sales_order", "customer_po_ref"} & set(r["reference_kinds"]))


def test_staff_with_an_enforced_scope_loses_so_and_po_ref(monkeypatch):
    r = _scope(monkeypatch, staff=True, scope={"enforced": True, "ids": ["x"]})
    assert not ({"sales_order", "customer_po_ref"} & set(r["reference_kinds"]))


def test_staff_unscoped_with_the_so_grant_sees_sales_orders(monkeypatch):
    r = _scope(monkeypatch, staff=True, scope=None)
    assert "sales_order" in r["reference_kinds"]


def test_a_failing_scope_lookup_fails_closed(monkeypatch):
    r = _scope(monkeypatch, staff=True, scope_raises=True)
    assert "sales_order" not in r["reference_kinds"]
    assert r["customer_scope"]["enforced"] is True


def test_the_customer_scope_reaches_the_resolver(monkeypatch):
    scope = {"enforced": True, "ids": ["c1", "c2"]}
    r = _scope(monkeypatch, staff=False, scope=scope)
    assert r["customer_scope"] == scope


def test_regions_come_from_the_contact_rules_and_fail_to_unresolved(monkeypatch):
    from app.services.eta_policy import UNRESOLVED

    assert _scope(monkeypatch, staff=True, scope=None, regions=("CENTRAL", "NORTH"))["regions"] == ["CENTRAL", "NORTH"]
    assert _scope(monkeypatch, staff=True, scope=None, regions_raise=True)["regions"] == UNRESOLVED.regions


# ------------------------------------------------------------------ 3 found-kind refs


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


def _run(state, table, **kw):
    policy = kw.pop("policy", None)
    extra = {"policy": policy} if policy else {}
    return apply_turn(state, out(_ask(**kw)), resolver=_Fake(table), **extra)


def test_cross_kind_hit_is_filed_under_the_kind_found():
    state, actions = _run(mk_state(), {"order|97503": ("exact", [("product", "MBF97503")], False)},
                          domain="inventory", entities=[_e(kind="order", phrase="97503")])
    assert isinstance(actions[0], Lookup)
    assert [r.key for r in state.held.refs["product"]] == ["MBF97503"]
    assert "order" not in state.held.refs


def test_inbound_shipment_hit_is_filed_as_delivery_order():
    state, _ = _run(mk_state(), {"inbound_shipment|202610-0838": ("exact", [("delivery_order", "202610-0838")], False)},
                    domain="incoming", entities=[_e(kind="inbound_shipment", phrase="202610-0838")])
    assert [r.key for r in state.held.refs["delivery_order"]] == ["202610-0838"]
    assert "inbound_shipment" not in state.held.refs


# ------------------------------------------------------------------ 4 status held with the subject


def _status_after_pick():
    table = {"product|SRTX": ("ambiguous", [("product", "P1"), ("product", "P2")], False),
             "product|P1": ("exact", [("product", "P1")], False)}
    state, actions = _run(mk_state(), table, domain="order", status="outstanding",
                          entities=[_e(phrase="SRTX")])
    assert isinstance(actions[0], ClarifyAct)
    state, actions = _run(state, table, act="answers_bot_question", answer={"option_key": "P1", "free_value": None})
    return state, actions


def _verdict(state, action):
    return bridge.to_v1_verdict(state, action, today=TODAY)


def test_status_survives_a_clarify_pick():
    state, actions = _status_after_pick()
    assert isinstance(actions[0], Lookup)
    v = _verdict(state, actions[0])
    assert_emission(v)
    assert v["status"] == "outstanding"


def test_refines_held_without_status_keeps_it():
    state, _ = _status_after_pick()
    state, actions = _run(state, {}, act="refines_held", domain="order")
    assert isinstance(actions[0], Lookup)
    assert _verdict(state, actions[0])["status"] == "outstanding"


def test_new_topic_without_status_clears_it():
    state, _ = _status_after_pick()
    table = {"product|P3": ("exact", [("product", "P3")], False)}
    state, actions = _run(state, table, act="new_topic", domain="inventory", entities=[_e(phrase="P3")])
    assert isinstance(actions[0], Lookup)
    assert _verdict(state, actions[0])["status"] is None


# ------------------------------------------------------------------ 5 did-you-mean always asked


def test_single_did_you_mean_candidate_goes_to_v1s_miss_lane_under_any_policy():
    policy = ClarifyPolicy({("entity", "inventory"): "default"})
    _, actions = _run(mk_state(), {"product|MKS9505-HC-SS": ("ambiguous", [("product", "MKS9505-SS-HC")], True)},
                      policy=policy, domain="inventory", entities=[_e(phrase="MKS9505-HC-SS")])
    # PV2-FIX r5c: crew 14 Oct, single did-you-mean uses v1's own miss lane
    assert isinstance(actions[0], Lookup)
    assert [m.phrase for m in actions[0].misses] == ["MKS9505-HC-SS"]
