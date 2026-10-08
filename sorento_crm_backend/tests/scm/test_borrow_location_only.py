"""BORROW-LOCATION-ONLY (`documentation/plans/scm/PLAN-borrow-location-only-8oct.md`).

RED for Phase 2. The card's B1-B6, D1-D6 and K1-K9 are the contract:

* `system_settings.borrow_from_order_enabled` (bool, default false) and the reader
  `app.services.scm.borrow_mode.borrow_from_order_enabled(db)`.
* Switch OFF (the default): the engine still picks the same step-2 stock, but no candidate
  or component names a donor; confirm raises ONE ORDER_BACK per (borrowing line, borrowed
  location) on the BORROWING line at its own date, and leaves the donor's decision alone.
  The write keys on the switch, never on the payload (K5).
* Switch ON: today's behaviour, pinned (row on the donor line at the donor date, donor
  decision superseded, donor named).

Postgres via `blank_session`, every chain seeded here. Fixtures are reused from
`test_ladder_v7_borrow` / `test_project_supply_service_ladder` with the justification that
earned them there.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.project_so import (
    DECISION_ACTIVE,
    DECISION_SUPERSEDED,
    INQUIRY_ACTIONED,
    INQUIRY_CANCELLED,
    INQUIRY_RAISED,
    IV_ORDER,
    IV_ORDER_BACK,
    OrderInquiryRow,
    SOSupplyDecision,
)
from app.models.user import SystemSetting
from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody
from app.services.error_handler import AppException
from app.services.project_supply_service import ProjectSupplyService

from .._pg_fixture import blank_session
from ..test_so_supply_confirmation import _stock, _uid, _warehouse
from .test_ladder_v7_borrow import LEAD_DAYS, WINDOW_DAY, _borrow_world, _decide, _policy
from .test_project_supply_service_ladder import (
    _components,
    _group_sites,
    _lead_time,
    _seed_line,
    _world,
)

DONOR_FIELDS = ("donor_so_number", "donor_line_no", "donor_agent_code")


# --------------------------------------------------------------------------- helpers


def _set_borrow_order(db, value: bool) -> None:
    """Get-or-create the singleton and set the flag (mirrors
    `test_confirm_local_buy_no_oi.py:490-498`). Written through the CLASS attribute so a
    missing column fails loudly with an AttributeError rather than silently on a plain
    instance assignment."""
    row = db.query(SystemSetting).first()
    if row is None:
        row = SystemSetting(id=str(uuid.uuid4()))
        db.add(row)
        db.flush()
    db.query(SystemSetting).filter(SystemSetting.id == row.id).update(
        {SystemSetting.borrow_from_order_enabled: value}
    )
    db.flush()
    db.expire(row)


def _borrow_order_on(db) -> None:
    _set_borrow_order(db, True)


def _borrow_order_off(db) -> None:
    _set_borrow_order(db, False)


def _component_to_payload(component, *, source="other_location", warehouse=None, **extra):
    """The borrow dict the sheet posts back for a proposal component, donor fields and all."""
    body = {
        "source": source,
        "warehouse_id": str(warehouse.id) if warehouse is not None else component["source_warehouse_id"],
        "qty": component["qty"],
        "reason": "Ladder v7.1 step 2.",
        "donor_core_line_id": component.get("donor_core_line_id"),
        "donor_so_number": component.get("donor_so_number"),
        "donor_line_no": component.get("donor_line_no"),
        "donor_agent_code": component.get("donor_agent_code"),
        "donor_required_date": component.get("donor_required_date"),
    }
    body.update(extra)
    return body


def _confirm_borrow(db, world, borrow, *, buy_qty=None):
    line = ConfirmLine(
        project_line_id=world["asker_line"].id,
        borrow=[borrow],
        **(
            {
                "buy_qty": Decimal(str(buy_qty)),
                "amend_reason": "Borrow what the later order can spare and buy the rest.",
            }
            if buy_qty
            else {}
        ),
    )
    ProjectSupplyService(db).confirm(
        world["asker"], ConfirmSupplyBody(lines=[line]), actor_user_id=world["eling"]
    )
    db.commit()


def _step_two_borrow(db, world):
    component = _components(ProjectSupplyService(db).proposal_for(world["asker"]))[0]
    assert component["kind"] == "borrow" and component["rung"] == "order_borrow", component
    return component


def _order_backs(db, *, live=True):
    query = db.query(OrderInquiryRow).filter(OrderInquiryRow.verb == IV_ORDER_BACK)
    if live:
        query = query.filter(OrderInquiryRow.state != INQUIRY_CANCELLED)
    return query.order_by(OrderInquiryRow.created_at, OrderInquiryRow.id).all()


def _reference(order) -> str:
    return order.autocount_doc_no or order.provisional_ref


def _assert_location_order_back(row, *, line, order, code, qty="30"):
    """The OFF shape (B3): on the BORROWING line, at its own date, at the borrowed bin."""
    assert str(row.so_line_id) == str(line.id), "the row hangs on the borrowing line"
    assert row.delivery_date == line.delivery_date, "at the borrowing line's own date"
    assert row.stock_location == code
    assert row.qty == Decimal(qty)
    assert row.note == f"Order-back: {code} lent {qty} to {_reference(order)} line {line.line_no}", row.note


# --------------------------------------------------------------------------- T1


def test_the_switch_reads_false_with_no_settings_row():
    from app.services.scm.borrow_mode import borrow_from_order_enabled

    with blank_session() as db:
        assert db.query(SystemSetting).first() is None, "blank schema, nothing seeded"
        assert borrow_from_order_enabled(db) is False


def test_the_setting_column_defaults_false_and_the_reader_follows_it():
    from app.services.scm.borrow_mode import borrow_from_order_enabled

    with blank_session() as db:
        row = SystemSetting(id=str(uuid.uuid4()))
        db.add(row)
        db.flush()
        db.refresh(row)
        assert row.borrow_from_order_enabled is False
        assert borrow_from_order_enabled(db) is False
        _borrow_order_on(db)
        assert borrow_from_order_enabled(db) is True


# --------------------------------------------------------------------------- T2


def test_off_the_proposal_component_is_a_location_borrow_that_names_no_donor():
    with blank_session() as db:
        world = _borrow_world(db)
        donor_so = world["donor_order"].provisional_ref
        donor_core_so = world["donor_cline"].sales_order_id
        components = _components(ProjectSupplyService(db).proposal_for(world["asker"]))
        code = world["donor_bin"].warehouse_code
        donor_numbers = {
            donor_so,
            db.execute(
                text("select so_number from sales_orders where id = :i"), {"i": donor_core_so}
            ).scalar(),
        }

    assert len(components) == 1
    component = components[0]
    assert component["kind"] == "borrow"
    assert component["qty"] == "30"
    for field in DONOR_FIELDS:
        assert component[field] is None, (field, component[field])
    assert component["same_agent"] is False
    assert component["donor_core_line_id"], "stays INTERNAL so the re-borrow guard counts (K6)"
    reason = component["reason"]
    assert f"on hand at {code}" in reason, reason
    assert "ordered back to" in reason, reason
    assert "debt lands in" not in reason, reason
    assert "from SO" not in reason, reason
    for number in donor_numbers:
        if number:
            assert number not in reason, reason


# --------------------------------------------------------------------------- T3


def test_off_the_supply_route_offers_the_order_borrow_candidate_without_naming_a_donor():
    from app.models.base import company_scope

    from tests.test_fulfilment_board import BASE, VIEW, _client, _restore

    with blank_session() as db:
        world = _borrow_world(db)
        db.commit()
        client, originals = _client(db, world["eling"], [VIEW])
        try:
            with company_scope(db, frozenset({world["company_id"]})):
                response = client.get(f"{BASE}/sales-orders/{world['asker'].id}/supply")
        finally:
            _restore(originals)
        assert response.status_code == 200, response.text
        line = response.json()["lines"][0]
        candidates = line["borrow_candidates"]

    assert [c for c in candidates if c.get("rung") == "order_borrow"], candidates
    for candidate in candidates:
        assert candidate.get("donor_so_number") is None, candidate
    offered = [c for c in candidates if c.get("rung") == "order_borrow"][0]
    assert offered["donor_core_line_id"], "internal id still travels"


# --------------------------------------------------------------------------- T4 / T5


def test_off_confirm_raises_one_order_back_on_the_borrowing_line():
    with blank_session() as db:
        world = _borrow_world(db)
        component = _step_two_borrow(db, world)
        _confirm_borrow(db, world, _component_to_payload(component, warehouse=world["donor_bin"]))

        rows = _order_backs(db)
        donor_decisions = (
            db.query(SOSupplyDecision)
            .filter(SOSupplyDecision.project_sales_order_id == world["donor_order"].id)
            .all()
        )
        on_donor = [r for r in rows if str(r.so_line_id) == str(world["donor_line"].id)]

        assert len(rows) == 1, [(r.so_line_id, r.qty, r.note) for r in rows]
        _assert_location_order_back(
            rows[0], line=world["asker_line"], order=world["asker"],
            code=world["donor_bin"].warehouse_code,
        )
        assert on_donor == [], "nothing is raised against the donor's line"
        assert [d.state for d in donor_decisions] == [DECISION_ACTIVE], (
            "the donor's own decision is left alone (Q2)"
        )


def test_off_confirm_ignores_donor_fields_from_a_stale_tab():
    """K5: a tab opened while ON posts a donor-named borrow after the switch went OFF. The
    server keys on the switch, so the outcome is the location borrow, never the ON rule."""
    with blank_session() as db:
        world = _borrow_world(db)
        component = _step_two_borrow(db, world)
        stale = _component_to_payload(
            component, warehouse=world["donor_bin"],
            donor_so_number="ZZT-STALE", donor_line_no=2,
        )
        _confirm_borrow(db, world, stale)

        rows = _order_backs(db)
        donor_decisions = (
            db.query(SOSupplyDecision)
            .filter(SOSupplyDecision.project_sales_order_id == world["donor_order"].id)
            .all()
        )

        assert len(rows) == 1, [(r.so_line_id, r.qty, r.note) for r in rows]
        _assert_location_order_back(
            rows[0], line=world["asker_line"], order=world["asker"],
            code=world["donor_bin"].warehouse_code,
        )
        assert "ZZT-STALE" not in (rows[0].note or "")
        assert [d.state for d in donor_decisions] == [DECISION_ACTIVE]

        # S1 (K5): the frozen snapshot keeps no donor name either, only the internal id.
        decision = (
            db.query(SOSupplyDecision)
            .filter(
                SOSupplyDecision.project_sales_order_id == world["asker"].id,
                SOSupplyDecision.state == DECISION_ACTIVE,
            )
            .one()
        )
        frozen = json.dumps(decision.line_snapshots, default=str)
        assert "ZZT-STALE" not in frozen, frozen
        borrows = [
            c
            for snap in decision.line_snapshots
            for c in (snap.get("components") or [])
            if c.get("kind") == "borrow"
        ]
        assert borrows, frozen
        for c in borrows:
            for field in DONOR_FIELDS:
                assert c.get(field) is None, (field, c)
            assert not c.get("same_agent")
            assert c.get("donor_core_line_id"), "K6: the internal id stays"
        after = json.dumps(ProjectSupplyService(db).proposal_for(world["asker"]), default=str)
        assert "ZZT-STALE" not in after


# --------------------------------------------------------------------------- T6


def _own_bin_world(db):
    """The asker's OWN bin holds stock pinned by a LATER decided order at the SAME bin."""
    company_id, eling, project, product = _world(db)
    _group, sites = _group_sites(db)
    own, _pool = sites["BRW"]
    _stock(db, product, own, on_hand=30)
    _lead_time(db, product, LEAD_DAYS)
    _policy(db)
    donor_order, donor_line, _dcso, donor_cline = _seed_line(
        db, company_id, project, product, own, qty_ordered="30",
        required_date=date.today() + timedelta(days=WINDOW_DAY + 20), line_no=2,
        so_number=f"ZZT-SO-DONOR{_uid()[:4]}",
    )
    _decide(db, donor_order, donor_line, own, "30", eling)
    asker, asker_line, _cso, _cline = _seed_line(
        db, company_id, project, product, own, qty_ordered="30", line_no=1,
        so_number=f"ZZT-SO-ASK{_uid()[:5]}",
    )
    return {
        "company_id": company_id, "eling": eling, "product": product, "own": own,
        "donor_bin": own, "donor_order": donor_order, "donor_line": donor_line,
        "donor_cline": donor_cline, "asker": asker, "asker_line": asker_line,
    }


def test_off_borrowing_at_the_lines_own_location_raises_an_order_back_on_this_line():
    with blank_session() as db:
        world = _own_bin_world(db)
        # Measured on today's code: the engine DOES offer the own bin's pinned stock, as a
        # step-2 order_borrow with the donor sitting at the same warehouse.
        component = _step_two_borrow(db, world)
        assert component["source_warehouse_id"] == str(world["own"].id)
        for field in DONOR_FIELDS:
            assert component[field] is None, (field, component[field])
        payload = _component_to_payload(component, warehouse=world["own"])
        _confirm_borrow(db, world, payload)

        rows = _order_backs(db)
        assert len(rows) == 1, [(r.so_line_id, r.qty, r.note) for r in rows]
        _assert_location_order_back(
            rows[0], line=world["asker_line"], order=world["asker"],
            code=world["own"].warehouse_code,
        )


# --------------------------------------------------------------------------- T7


def test_off_a_same_group_sibling_borrow_raises_an_order_back_on_this_line():
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        sibling, _sp = sites["MWH"]
        _stock(db, product, sibling, on_hand=30)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)
        asker, asker_line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="30", line_no=1,
            so_number=f"ZZT-SO-ASK{_uid()[:5]}",
        )
        world = {"asker": asker, "asker_line": asker_line, "eling": eling}
        _confirm_borrow(db, world, {
            "source": "other_location",
            "warehouse_id": str(sibling.id),
            "qty": "30",
            "reason": "Free stock at a sibling bin of the group.",
        })

        rows = _order_backs(db)
        assert len(rows) == 1, "today a same-group sibling borrow raises nothing"
        _assert_location_order_back(
            rows[0], line=asker_line, order=asker, code=sibling.warehouse_code,
        )


# --------------------------------------------------------------------------- T8


def test_k1_a_row_made_under_on_ends_as_one_row_on_the_borrowing_line_after_a_flip_to_off():
    with blank_session() as db:
        world = _borrow_world(db)
        _borrow_order_on(db)
        component = _step_two_borrow(db, world)
        assert component["donor_so_number"], "ON names the donor"
        payload = _component_to_payload(component, warehouse=world["donor_bin"])
        _confirm_borrow(db, world, payload)

        first = _order_backs(db)
        assert len(first) == 1
        assert str(first[0].so_line_id) == str(world["donor_line"].id), "ON: donor line"

        _borrow_order_off(db)
        _confirm_borrow(db, world, payload)

        live = _order_backs(db)
        everything = _order_backs(db, live=False)
        assert len(live) == 1, [(r.so_line_id, r.state, r.note) for r in everything]
        _assert_location_order_back(
            live[0], line=world["asker_line"], order=world["asker"],
            code=world["donor_bin"].warehouse_code,
        )
        assert any(
            r.state == INQUIRY_CANCELLED and str(r.so_line_id) == str(world["donor_line"].id)
            for r in everything
        ), "the earlier donor-line row is cancelled, not left standing"


# --------------------------------------------------------------------------- T9


def test_on_pins_todays_behaviour_donor_named_superseded_and_the_row_on_the_donor_line():
    with blank_session() as db:
        world = _borrow_world(db)
        _borrow_order_on(db)
        component = _step_two_borrow(db, world)
        assert component["donor_so_number"]
        assert component["donor_line_no"] is not None
        assert "debt lands in" in component["reason"]
        _confirm_borrow(db, world, _component_to_payload(component, warehouse=world["donor_bin"]))

        rows = _order_backs(db)
        donor_decisions = (
            db.query(SOSupplyDecision)
            .filter(SOSupplyDecision.project_sales_order_id == world["donor_order"].id)
            .all()
        )

        assert len(rows) == 1
        assert str(rows[0].so_line_id) == str(world["donor_line"].id)
        assert rows[0].delivery_date == component_date(component)
        assert rows[0].stock_location == world["donor_bin"].warehouse_code
        assert DECISION_SUPERSEDED in [d.state for d in donor_decisions]
        assert DECISION_ACTIVE not in [d.state for d in donor_decisions]


def component_date(component):
    return date.fromisoformat(str(component["donor_required_date"]))


# --------------------------------------------------------------------------- T10


def test_k7_an_order_back_buy_beside_a_borrow_keeps_the_actioned_borrow_row_and_never_doubles_it():
    """K7: ask 40, the donor holds 30, so the composition is built by hand: Borrow 30 plus
    an Amend "Order back" Buy of 10 on the SAME line. Purchasing actions the borrow's
    order-back, then the line is re-confirmed under OFF. The Buy-line path must not take
    the borrow row as its own (netting 30 off a Buy of 10), and the borrow path must not
    skip it (raising a second 30)."""
    with blank_session() as db:
        world = _borrow_world(db, ask="40")
        line = ConfirmLine(
            project_line_id=world["asker_line"].id,
            borrow=[{
                "source": "other_location",
                "warehouse_id": str(world["donor_bin"].id),
                "qty": "30",
                "reason": "Ladder v7.1 step 2.",
                "donor_core_line_id": str(world["donor_cline"].id),
            }],
            buy_qty=Decimal("10"),
            order_back=True,
            amend_reason="Borrow what the later order can spare and order the rest back.",
        )

        def confirm():
            ProjectSupplyService(db).confirm(
                world["asker"], ConfirmSupplyBody(lines=[line]), actor_user_id=world["eling"]
            )
            db.commit()

        confirm()
        first = _order_backs(db)
        borrow_rows = [r for r in first if (r.note or "").startswith("Order-back:")]
        assert len(borrow_rows) == 1, [(r.qty, r.note) for r in first]
        borrow_rows[0].state = INQUIRY_ACTIONED
        db.commit()

        confirm()
        asker_id = str(world["asker_line"].id)
        live = sorted(
            (r.qty, r.state)
            for r in _order_backs(db)
            if str(r.so_line_id) == asker_id
        )
        assert live == [(Decimal("10"), INQUIRY_RAISED), (Decimal("30"), INQUIRY_ACTIONED)], [
            (r.qty, r.state, r.note) for r in _order_backs(db, live=False)
        ]


# --------------------------------------------------------------------------- T11

SETTINGS_ENDPOINT = "/api/v1/user-management/settings/"
GENERAL_ENDPOINT = "/api/v1/user-management/settings/general"


@pytest.fixture
def api_db():
    with blank_session() as s:
        yield s


@pytest.fixture
def api(api_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user
    from app.main import app
    from app.services.user_service import UserPermissionService

    allow = {"user_management.settings.view", "user_management.settings.edit"}

    def _override_db():
        yield api_db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: {
        "id": str(uuid.uuid4()), "email": "zzt-borrow-settings@zzt.test",
    }
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in allow
    )
    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_settings_api_reads_false_by_default_and_a_put_persists_true(api, api_db):
    api_db.add(SystemSetting(id=str(uuid.uuid4())))
    api_db.commit()

    first = api.get(SETTINGS_ENDPOINT)
    assert first.status_code == 200
    assert first.json()["settings"]["borrow_from_order_enabled"] is False

    saved = api.put(GENERAL_ENDPOINT, json={"borrow_from_order_enabled": True})
    assert saved.status_code == 200, saved.text

    again = api.get(SETTINGS_ENDPOINT)
    assert again.json()["settings"]["borrow_from_order_enabled"] is True


# --------------------------------------------------------------------------- fix round 1


def test_b1_off_a_donor_needing_authorisation_is_not_offered_and_a_crafted_confirm_names_no_so():
    """B1: under OFF nobody can be asked "who authorised it", so a same-agent donor ranked
    above this line is not offered, and a confirm naming it anyway is refused in the
    location's words (S2), authorisation or not."""
    from .test_project_supply_service_ladder import _borrow_body, _same_agent_world

    with blank_session() as db:
        _company_id, eling, order, line, donor_cline, own = _same_agent_world(db)
        proposal = ProjectSupplyService(db).proposal_for(order)
        offered = [
            c
            for c in proposal["lines"][0]["borrow_candidates"]
            if str(c.get("donor_core_line_id") or "") == str(donor_cline.id)
        ]
        assert offered == [], offered

        with pytest.raises(AppException) as refused:
            ProjectSupplyService(db).confirm(
                order,
                _borrow_body(
                    line, own, donor_cline, "Authorised by agent CYNDI: on the phone."
                ),
                actor_user_id=eling,
            )

    reason = refused.value.detail["failing_lines"][0]["reason"]
    assert "SO920001" not in reason, reason
    assert own.warehouse_code in reason, reason


def _second_asker(db, world, *, line_no=1):
    from app.models.projects import Project

    project = db.get(Project, world["asker"].project_id)
    asker, asker_line, _cso, _cline = _seed_line(
        db, world["company_id"], project, world["product"], world["own"],
        qty_ordered="30", line_no=line_no, so_number=f"ZZT-SO-TWO{_uid()[:5]}",
    )
    return asker, asker_line


def test_s4_k6_off_a_second_asker_cannot_borrow_what_a_first_already_lent_and_no_so_is_named():
    """K6 under OFF: the donor id stays recorded, so the re-borrow guard still counts the
    first location borrow. The refusal names the location, never the donor SO (S2)."""
    with blank_session() as db:
        world = _borrow_world(db)
        component = _step_two_borrow(db, world)
        _confirm_borrow(db, world, _component_to_payload(component, warehouse=world["donor_bin"]))

        asker, asker_line = _second_asker(db, world)
        db.commit()
        donor_numbers = {
            world["donor_order"].provisional_ref,
            world["donor_order"].autocount_doc_no,
            db.execute(
                text("select so_number from sales_orders where id = :i"),
                {"i": world["donor_cline"].sales_order_id},
            ).scalar(),
        }
        with pytest.raises(AppException) as refused:
            ProjectSupplyService(db).confirm(
                asker,
                ConfirmSupplyBody(lines=[ConfirmLine(
                    project_line_id=asker_line.id,
                    borrow=[_component_to_payload(component, warehouse=world["donor_bin"])],
                )]),
                actor_user_id=world["eling"],
            )

    reason = refused.value.detail["failing_lines"][0]["reason"]
    assert world["donor_bin"].warehouse_code in reason, reason
    for number in donor_numbers:
        if number:
            assert number not in reason, reason


def test_s4_off_the_pool_borrow_half_is_a_location_borrow_owed_back_on_this_line():
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=60)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)
        _seed_line(
            db, company_id, project, product, pool, qty_ordered="60",
            required_date=date.today() + timedelta(days=WINDOW_DAY + 60), line_no=9,
            so_number=f"ZZTSO-POOL{_uid()[:4]}",
        )
        asker, asker_line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="60", line_no=1,
            so_number=f"ZZTSO-ASK{_uid()[:5]}",
        )
        components = _components(ProjectSupplyService(db).proposal_for(asker))
        assert [(c["kind"], c["rung"]) for c in components] == [("borrow", "pool")]
        component = components[0]
        for field in DONOR_FIELDS:
            assert component[field] is None, (field, component)
        assert f"ordered back to {pool.warehouse_code} on this line" in component["reason"]
        assert "debt lands in" not in component["reason"]

        world = {"asker": asker, "asker_line": asker_line, "eling": eling}
        _confirm_borrow(db, world, _component_to_payload(component, warehouse=pool))

        rows = _order_backs(db)
        superseded = (
            db.query(SOSupplyDecision)
            .filter(SOSupplyDecision.state == DECISION_SUPERSEDED)
            .count()
        )
        assert len(rows) == 1, [(r.so_line_id, r.qty, r.note) for r in rows]
        _assert_location_order_back(
            rows[0], line=asker_line, order=asker, code=pool.warehouse_code, qty="60",
        )
        assert superseded == 0


# --------------------------------------------------------------------------- BR1


@pytest.mark.parametrize("enabled", [False, True])
def test_br1_the_board_route_carries_the_switch_for_the_decide_menu(enabled):
    """BR1: the Decide menu offers "Borrow from another order" exactly when the switch is
    on, so the board payload states it. Through the ROUTE, because a field the response
    model does not declare is dropped silently."""
    from app.models.base import company_scope

    from tests.test_fulfilment_board import (
        BASE, TODAY, VIEW, _board_world, _client, _restore, _sorento, _user,
    )

    with blank_session() as db:
        company_id = _sorento(db)
        actor = _user(db, "zzt borrow switch planner")
        order, _product = _board_world(db)
        _set_borrow_order(db, enabled)
        db.commit()
        client, originals = _client(db, actor, [VIEW])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(
                    f"{BASE}/fulfilment-planning/board",
                    params={
                        "orders": order.so_number,
                        "granularity": "week",
                        "as_of": TODAY.isoformat(),
                    },
                )
        finally:
            _restore(originals)

    assert response.status_code == 200, response.text
    assert response.json()["borrow_from_order_enabled"] is enabled


# --------------------------------------------------------------------------- BR2


def test_br2_off_no_offered_candidate_names_a_donor_project():
    """BR2: under OFF a borrow is a location's, so the dialog's rows carry no donor project
    either - the decided donor's hold showed a second row at the same bin tagged with its
    project code. The id stays, since confirm needs it."""
    with blank_session() as db:
        world = _borrow_world(db)
        proposal = ProjectSupplyService(db).proposal_for(world["asker"])
        candidates = proposal["lines"][0]["borrow_candidates"]

    assert candidates, "the donor's bin is offered"
    for candidate in candidates:
        assert candidate.get("donor_project_ref") is None, candidate
        assert candidate.get("donor_so_number") is None, candidate


# --------------------------------------------------------------------------- fix round 2


@pytest.mark.parametrize("enabled", [False, True])
def test_r2_an_over_asked_project_hold_is_refused_by_location_under_off(enabled):
    """Fix round 2 item 1: under OFF the refusal of an over-asked `other_project` hold names
    the bin, never the project code. ON keeps today's sentence."""
    from app.models.projects import Project

    with blank_session() as db:
        world = _borrow_world(db, ask="40")
        _set_borrow_order(db, enabled)
        donor_project = db.get(Project, world["donor_order"].project_id)
        code = donor_project.project_code
        bin_code = world["donor_bin"].warehouse_code
        db.commit()
        with pytest.raises(AppException) as refused:
            ProjectSupplyService(db).confirm(
                world["asker"],
                ConfirmSupplyBody(lines=[ConfirmLine(
                    project_line_id=world["asker_line"].id,
                    borrow=[{
                        "source": "other_project",
                        "warehouse_id": str(world["donor_bin"].id),
                        "donor_project_id": str(donor_project.id),
                        "qty": "40",
                        "reason": "Take the held stock.",
                    }],
                )]),
                actor_user_id=world["eling"],
            )

    reason = refused.value.detail["failing_lines"][0]["reason"]
    if enabled:
        assert reason.startswith(f"{code} has "), reason
    else:
        assert code not in reason, reason
        assert reason.startswith(f"Stock held at {bin_code} now has "), reason
        assert reason.endswith("and 40 was asked for."), reason
