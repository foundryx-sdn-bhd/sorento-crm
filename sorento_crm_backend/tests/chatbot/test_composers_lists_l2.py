"""CHAT-COMPOSER Lists slice red tests, L2 (backend half): the outstanding report's By customer / By product
group pages 20 at a time. The backend sends `group_offset` only gate on; "more" re-runs the open offer's own
filters with the offset; "1" still picks the detail list.

Spec: `documentation/plans/chatbot/chat-composer-seam-design-lists.md` section 2 (L2-a, L2-e to L2-h), section 7.
The presenter half is `sorento_crm_mcp/tests/test_outstanding_group_offset.py`. No implementation exists yet.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.base import set_company_scope
from app.models.product import Brand
from app.models.user import User, UserRole, UserRoleAssignment
from app.services.chatbot.lanes.business import fetch
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from tests._pg_fixture import blank_session, unique_code
from tests.chatbot.test_composers_stock_xd import _set_gate

BASE = "/api/v1/order-management/outstanding-report"
CUSTOMER = "11111111-1111-4111-8111-111111111111"
REPORT_TEXT = (
    "Product: ZZT-A\nCustomer: all\nLocation: all\nOrder date: all\n\n"
    "*Delivery order outstanding*\nDelivery orders: 4\n\nReply 1 for the delivery order list."
)


# --------------------------------------------------------------------------- #
# L2-a: the route echoes group_offset and does nothing else with it
# --------------------------------------------------------------------------- #


@pytest.fixture
def db():
    with blank_session() as s:
        set_company_scope(s, frozenset({DEFAULT_COMPANY_ID}))
        yield s


@pytest.fixture
def client(db):
    user = User(id=str(uuid.uuid4()), email=f"{unique_code('SA')}@test.com", name="ZZT Superadmin", status="ACTIVE")
    role = UserRole(id=str(uuid.uuid4()), slug="superadmin", name="Superadmin")
    db.add_all([user, role])
    db.flush()
    db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
    db.flush()
    principal = {"id": user.id, "email": user.email}

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[apply_company_scope] = _override_scope
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _brand(db):
    row = Brand(
        id=str(uuid.uuid4()),
        brand_name="ZZT Brand",
        brand_code=unique_code("ZB")[:20],
        is_active=True,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    return row


def test_route_echoes_group_offset(client, db):
    brand = _brand(db)
    params = {"brand_ids": [brand.id], "scope": "so"}
    with_offset = client.get(BASE, params={**params, "group_offset": 20})
    assert with_offset.status_code == 200, with_offset.text
    assert with_offset.json()["group_offset"] == 20
    without = client.get(BASE, params=params)
    assert without.status_code == 200, without.text
    assert "group_offset" not in without.json()


def test_route_refuses_a_negative_group_offset(client, db):
    brand = _brand(db)
    resp = client.get(BASE, params={"brand_ids": [brand.id], "scope": "so", "group_offset": -1})
    assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------------- #
# L2-e: the argument is sent gate on only
# --------------------------------------------------------------------------- #


def _trigger(**semantic):
    return {
        "entities": [],
        "tool": "crm_outstanding_report",
        "semantic_input": {"contact_id": "1", "space_id": "s", **semantic},
    }


def test_arg_gate_on_only(monkeypatch):
    _set_gate(monkeypatch, False)
    assert "group_offset" not in fetch.entity_ids_transformer(_trigger())
    assert "group_offset" not in fetch.entity_ids_transformer(_trigger(outstanding_group_offset=20))
    _set_gate(monkeypatch, True)
    assert fetch.entity_ids_transformer(_trigger())["group_offset"] == 0
    assert fetch.entity_ids_transformer(_trigger(outstanding_group_offset=20))["group_offset"] == 20
    picked = fetch.entity_ids_transformer(_trigger(outstanding_detail_pick="so"))
    assert "group_offset" not in picked and picked["detail"] == "so"


# --------------------------------------------------------------------------- #
# L2-g: the answered offer's offset rides to the fetch
# --------------------------------------------------------------------------- #


def test_outstanding_carry_offset():
    from app.services.chatbot import turn_runtime
    from app.services.chatbot.turn.state import Focus

    out = turn_runtime.outstanding_carry({}, Focus(), {"group_offset": 20})
    assert out["outstanding_group_offset"] == 20
    out = turn_runtime.outstanding_carry({}, Focus(), {})
    assert "outstanding_group_offset" not in out


# --------------------------------------------------------------------------- #
# L2-f: the report output carries the window and the carry
# --------------------------------------------------------------------------- #


def _result(window):
    return {
        "result_type": "outstanding_report",
        "response": REPORT_TEXT,
        "has_result": True,
        **({"group_window": window} if window else {}),
    }


def _ctx():
    return {"tool": "crm_outstanding_report", "semantic_input": {}, "entities": []}


def test_report_output_window(monkeypatch):
    window = {"offset": 0, "shown": 20, "total": 45}
    _set_gate(monkeypatch, False)
    off = fetch._outstanding_report_output(_result(window), _ctx())
    assert "set_window" not in off
    assert off["response"] == fetch._outstanding_report_output(_result(None), _ctx())["response"]
    _set_gate(monkeypatch, True)
    on = fetch._outstanding_report_output(_result(window), _ctx())
    assert on["set_window"] == {
        "first": 1,
        "last": 20,
        "total": 45,
        "next": 20,
        "carry": {"report_groups": True, "total": 45, "shown": 20},
    }
    assert on["response"] == off["response"]
    assert on["outstanding_ask"] is not None, "the detail offer is re-armed on every page"


def test_report_output_without_a_window_has_none(monkeypatch):
    _set_gate(monkeypatch, True)
    assert "set_window" not in fetch._outstanding_report_output(_result(None), _ctx())


def test_the_last_page_keeps_a_carry_so_a_further_more_says_the_end(monkeypatch):
    _set_gate(monkeypatch, True)
    on = fetch._outstanding_report_output(_result({"offset": 40, "shown": 5, "total": 45}), _ctx())
    assert on["set_window"]["carry"] == {"report_groups": True, "total": 45, "shown": 45}
    assert on["set_window"]["next"] == 0


# --------------------------------------------------------------------------- #
# L2-h: "more" under the open detail offer is a REFINE with an offset
# --------------------------------------------------------------------------- #


def _pending(kind="outstanding_detail"):
    from app.services.chatbot.turn.pending import Pending

    return Pending(
        kind=kind,
        expects=None,
        options=[{"position": 1, "label": "Delivery order list", "payload": {"value": "do"}}],
        team=None,
        payload={
            "domain": "order",
            "filters": {"scope": "do", "product_code": "ZZT-A", "customer_ids": [CUSTOMER], "warehouse_codes": []},
        },
        asked_at_turn=1,
    )


def _decision(kind="refine", why="refines_subject", **kw):
    from app.services.chatbot.turn.decide import Decision

    return Decision(kind, why, **kw)


def _focus():
    from app.services.chatbot.turn.state import Focus

    focus = Focus(domains=["order"])
    focus.set_page = {"set_key": {"report_groups": True, "total": 45, "shown": 20, "domain": "order"}}
    return focus


def test_answer_outstanding_more():
    from app.services.chatbot.turn.apply import _answer_outstanding
    from app.services.chatbot.turn.plan import Trace

    pending, trace, focus = _pending(), Trace(), _focus()
    focus_out, pending_out, question, handled = _answer_outstanding(
        pending, _decision("carry", "nothing_answered"), focus, trace, more=20
    )
    assert handled is True and question is None
    assert pending_out is pending, "the detail offer stays open"
    assert trace.outstanding == {"kind": "outstanding_detail", "scope": "do", "detail": None, "group_offset": 20}
    assert "outstanding_more" in trace.rules_fired
    assert focus_out.set_page is None
    assert [p["canonical_code"] for p in focus_out.products] == ["ZZT-A"]
    assert [c.get("uuid") or c.get("id") for c in focus_out.customers] == [CUSTOMER]
    assert focus_out.status == "outstanding"
    assert focus_out.document == ["DO"]
    assert focus_out.domains == ["order"]


def test_more_none_is_todays_refine():
    from app.services.chatbot.turn.apply import _answer_outstanding
    from app.services.chatbot.turn.plan import Trace

    def run(**kw):
        trace = Trace()
        result = _answer_outstanding(_pending(), _decision(scope="do"), _focus(), trace, **kw)
        return result[0], result[2], result[3], trace.outstanding, trace.rules_fired

    default, explicit = run(), run(more=None)
    assert default == explicit
    assert default[3] == {"kind": "outstanding_detail", "scope": "do", "detail": None}
    assert "outstanding_refined" in default[4]


def test_more_none_leaves_an_answer_alone():
    from app.services.chatbot.turn.apply import _answer_outstanding
    from app.services.chatbot.turn.plan import Trace

    answer = _decision("answer", "picked_position", positions=(1,), scope="do")
    for kw in ({}, {"more": None}):
        trace = Trace()
        focus, carried, question, handled = _answer_outstanding(_pending(), answer, _focus(), trace, **kw)
        assert handled is True
        assert trace.outstanding == {"kind": "outstanding_detail", "scope": "do", "detail": "do"}
        assert carried is not None
