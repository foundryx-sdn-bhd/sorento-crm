"""Atomic Project SO confirmation over HTTP (PLAN-scm-front-planning.md 3.1/5.2/5.3, Group C).

RED for Stage 1C: `projects.so_supply_decisions` (migration 374), `app.models.project_so.
SOSupplyDecision`, `app.services.project_supply_service.confirm`/`proposal_for`, and the
`/project-sales/sales-orders/{pso_id}/confirm` + `.../supply` routes do not exist yet. Every
test here either calls a route that 404s today, or imports a model that does not exist yet
-- both are the "right reason" this file is meant to fail for, per
`documentation/plans/scm/STAGE1C-scm-front-planning-promising.md` section 8.

Postgres via `tests/_pg_fixture.py::blank_session`, never sqlite. Every test seeds its own
full chain (company, project, project SO, lines linked to a real core SO+lines, warehouses,
stock, classification, reorder level, SPO/shipment where needed) rather than borrowing an
existing row, per PRINCIPLES.md and the CI-is-empty lesson in this repo's CLAUDE.md.
"""
from __future__ import annotations

import itertools
import os
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.inventory import Stock, Warehouse
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.project_so import (
    SO_STATUS_PUBLISHED,
    AllocationClaim,
    CLAIM_ACCEPTED,
    CLAIM_REQUESTED,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
)
from app.models.user import User
from app.services import project_seed_service

from ._pg_fixture import blank_session

MARKER = "zzt-confirm"
BASE = "/api/v1/project-sales"
VIEW = "projects.projects.view"
EDIT = "projects.projects.edit"
#: Inside the ATP reserve window, and RELATIVE so it stays inside it as the calendar moves.
#: Ladder v3 buys a line due beyond `today + lead time + 14` whole and walks no other rung
#: (section 1b rung 0), so a fixed 2027 date silently turned every case in this file into a
#: Buy the moment the default 90-day lead time reached it.
REQUIRED_DATE = date.today() + timedelta(days=30)


def _uid() -> str:
    return str(uuid.uuid4())


_SUFFIX_SEQ = itertools.count()


def _suffix() -> str:
    """A deterministic, digit-free suffix for a fixture business code (warehouse
    code, product code, SO number, ...).

    Replaces a `_uid()[:4]`/`_uid()[:8]`-style random hex slice. Hex is
    digit-bearing most draws, and a code that lands one usually reads as
    ordinary test data - until an assertion checks that a refusal message does
    NOT mention a specific quantity substring and the warehouse's own random
    suffix happens to contain it (`ZZT-BRW-712c` tripping
    `assert "12" not in reason`; this file's actual flake,
    `test_re_confirming_a_larger_reserve_only_the_increase_competes_and_is_
    named_in_the_refusal`). Base-26 letters can never contain a digit, so that
    class of collision cannot recur.

    Monotonic within this process, so two calls anywhere in this file's run
    never repeat. Prefixed with this process's own letters, so two xdist
    workers never mint the same code either. That matters because the helpers
    built on it (`_product`, `_warehouse`, ...) are imported by files that run
    on the REAL schema through `_pg_fixture.pg_session`
    (`test_partial_decision_demand_invariants`, `test_order_inquiry_place_on_po`,
    `scm/test_loading_plan`, ...), not this file's private scratch schema. Two
    workers both holding an uncommitted `ZZT-A` category / product / uom behind
    a unique index wait on each other, and when they take the rows in a
    different order Postgres raises `DeadlockDetected` inside `_seed` (CI shard
    3, `test_the_undecided_lines_of_a_partly_confirmed_order_are_still_demand`).
    """
    return f"{_PROCESS_TAG}{_letters(next(_SUFFIX_SEQ) + 1)}"


def _letters(n: int) -> str:
    """Base-26 letters for a positive int: 1 -> A, 26 -> Z, 27 -> AA."""
    letters = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(ord("A") + rem))
    return "".join(reversed(letters))


#: Letters unique to this worker process; a digit-free pid so codes stay digit-free.
_PROCESS_TAG = _letters(os.getpid())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _second_company(db) -> str:
    from app.models.company import Company

    cid = _uid()
    db.add(Company(id=cid, name=f"{MARKER} Other Co", code=f"ZZC{cid[:6]}"))
    db.flush()
    return cid


def _user(db, name: str) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _product(db, *, discontinued: bool = False) -> Product:
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_suffix()}", uom_name="Set")
    category = ProductCategory(
        id=_uid(), category_code=f"ZZT-{_suffix()}", category_name=f"{MARKER} cat"
    )
    db.add_all([uom, category])
    db.flush()
    row = Product(
        id=_uid(),
        product_code=f"ZZT-{_suffix()}",
        product_name=f"{MARKER} Basin",
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=Decimal("120.00"),
        is_discontinued=discontinued,
    )
    db.add(row)
    db.flush()
    return row


def _warehouse(
    db,
    code: str,
    *,
    segment=None,
    pool_warehouse_id=None,
    active: bool = True,
    fulfilment_planning: bool = True,
) -> Warehouse:
    """A location for a supply test, IN fulfilment planning unless the test says otherwise.

    The column defaults to false on a real database (migration 443 flags the client's own
    bins once and an admin edits it after), but a warehouse a supply test seeds exists in
    order to be planned against - so the default here is the opposite of the column's, and
    the tests that care about a flagged-OFF bin pass `fulfilment_planning=False` and say so.
    """
    row = Warehouse(
        id=_uid(),
        warehouse_code=code,
        warehouse_name=code,
        location="ZZT",
        is_active=active,
        segment=segment,
        pool_warehouse_id=pool_warehouse_id,
        fulfilment_planning=fulfilment_planning,
    )
    db.add(row)
    db.flush()
    return row


def _stock(db, product: Product, warehouse: Warehouse, on_hand, reserved=0) -> Stock:
    row = Stock(
        id=_uid(),
        product_id=product.id,
        warehouse_id=warehouse.id,
        quantity_on_hand=on_hand,
        quantity_reserved=reserved,
    )
    db.add(row)
    db.flush()
    return row


def _core_so(db, company_id: str):
    from app.models.order import SalesOrder

    so = SalesOrder(
        id=_uid(),
        company_id=company_id,
        so_number=f"ZZT-CORE-{_suffix()}",
        status="open",
        demand_class="project",
    )
    db.add(so)
    db.flush()
    return so


def _core_line(db, so, product: Product, warehouse: Warehouse, *, qty_ordered, qty_delivered="0",
                required_date=REQUIRED_DATE):
    from app.models.order import SalesOrderLine

    line = SalesOrderLine(
        id=_uid(),
        company_id=so.company_id,
        sales_order_id=so.id,
        product_id=product.id,
        warehouse_id=warehouse.id,
        qty_ordered=Decimal(qty_ordered),
        qty_delivered=Decimal(qty_delivered),
        required_date=required_date,
        line_status="open",
    )
    db.add(line)
    db.flush()
    return line


def _project_so(db, project, *, status=SO_STATUS_PUBLISHED, so_id=None):
    order = ProjectSalesOrder(
        id=_uid(),
        company_id=project.company_id,
        project_id=project.id,
        provisional_ref=f"ZZT-PSO-{_suffix()}",
        area_group="TOWER",
        status=status,
        # The reconciled core sales order. Set wherever the assertion turns on the review
        # state, because Stage 1B reads `awaiting_reconciliation` until the HEADER is
        # linked too, not only the lines (`_header` in the reconciliation service).
        so_id=so_id,
    )
    db.add(order)
    db.flush()
    return order


def _project_line(db, order, *, line_no, product: Product, core_line):
    line = ProjectSalesOrderLine(
        id=_uid(),
        company_id=order.company_id,
        project_sales_order_id=order.id,
        core_sales_order_line_id=core_line.id if core_line else None,
        line_no=line_no,
        product_id=product.id,
        description=f"{MARKER} line {line_no}",
        qty=core_line.qty_ordered if core_line else Decimal("0"),
        uom="SET",
        unit_price=Decimal("120.00"),
        amount=Decimal("0"),
        delivery_date=core_line.required_date if core_line else REQUIRED_DATE,
    )
    db.add(line)
    db.flush()
    return line


def _classification(
    db,
    product: Product,
    warehouse: Warehouse,
    *,
    abc_class_retail: str | None = "A",
    abc_class_project: str | None = None,
):
    from app.models.scm import ItemClassification

    row = ItemClassification(
        id=_uid(),
        product_id=product.id,
        warehouse_id=warehouse.id,
        abc_class_retail=abc_class_retail,
        abc_class_project=abc_class_project,
    )
    db.add(row)
    db.flush()
    return row


def _reorder_level(db, company_id: str, product: Product, warehouse: Warehouse, *, level):
    from app.models.scm import ReorderLevel

    row = ReorderLevel(
        id=_uid(), company_id=company_id, product_id=product.id, warehouse_id=warehouse.id,
        level=Decimal(level),
    )
    db.add(row)
    db.flush()
    return row


def _spo(db, company_id: str, product: Product, warehouse: Warehouse, *, qty, arrival_date,
         spo_number="ZZT-SPO-0001"):
    from app.models.procurement import InboundShipment, SPOAllocation

    shipment = InboundShipment(
        id=_uid(), company_id=company_id, shipment_date=arrival_date,
        estimated_arrival_date=arrival_date, shipment_status="in_transit",
    )
    db.add(shipment)
    db.flush()
    alloc = SPOAllocation(
        id=_uid(), company_id=company_id, spo_number=spo_number, spo_line_number=1,
        inbound_shipment_id=shipment.id, warehouse_id=warehouse.id, product_id=product.id,
        allocated_quantity=int(qty),
    )
    db.add(alloc)
    db.flush()
    return shipment, alloc


def _client(db, user_id: str):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    actor = {"id": user_id, "email": f"{user_id}@zzt.test", "role": "user"}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)
    app.dependency_overrides[apply_company_scope] = lambda: None

    originals = (
        UserPermissionService.check_user_has_permission,
        UserPermissionService.get_user_permission_slugs,
    )
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: True
    UserPermissionService.get_user_permission_slugs = lambda self, uid: [VIEW, "projects.projects.create", EDIT]
    return TestClient(app), originals


def _restore(originals) -> None:
    from app.main import app
    from app.services.user_service import UserPermissionService

    UserPermissionService.check_user_has_permission = originals[0]
    UserPermissionService.get_user_permission_slugs = originals[1]
    app.dependency_overrides.clear()


def _act_as(client, user_id: str) -> None:
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app

    actor = {"id": user_id, "email": f"{user_id}@zzt.test", "role": "user"}
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)


class _World:
    def __init__(self, db, company_id, eling, project, product, own_wh, pool_wh):
        self.db = db
        self.company_id = company_id
        self.eling = eling
        self.project = project
        self.product = product
        self.own_wh = own_wh
        self.pool_wh = pool_wh


@pytest.fixture()
def api():
    from app.models.base import company_scope
    from app.services.project_service import register_project

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        eling = _user(db, f"{MARKER} Eling")
        project = register_project(
            db, company_id=company_id, actor_user_id=eling, developer_party_id=None,
            title=f"{MARKER} Tuju Residences",
        )
        product = _product(db)
        own_wh = _warehouse(db, f"ZZT-OWN-{_suffix()}", segment="project")
        pool_wh = _warehouse(db, f"ZZT-BRW-{_suffix()}", segment="dealer")
        own_wh.pool_warehouse_id = pool_wh.id
        db.flush()
        db.commit()
        client, originals = _client(db, eling)
        world = _World(db, company_id, eling, project, product, own_wh, pool_wh)
        try:
            with company_scope(db, frozenset({company_id})):
                yield client, world
        finally:
            _restore(originals)


def _line_payload(project_line_id, *, timely_spo_qty="0", reserve=None, borrow=None,
                   buy_qty="0", buy_reason=None):
    body = {
        "project_line_id": project_line_id,
        "timely_spo_qty": timely_spo_qty,
        "reserve": reserve or [],
        "borrow": borrow or [],
        "buy_qty": buy_qty,
    }
    if buy_reason is not None:
        body["buy_reason"] = buy_reason
    return body


# --------------------------------------------------------------------------- happy path


def test_confirming_a_balanced_multi_line_so_writes_one_active_decision_with_grouped_allocations(api):
    """AC-C01/AC-C04: one Confirm writes one active revision covering every line."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=100)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line_1 = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
    core_line_2 = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="30")
    line1 = _project_line(db, order, line_no=10, product=world.product, core_line=core_line_1)
    line2 = _project_line(db, order, line_no=20, product=world.product, core_line=core_line_2)
    db.commit()

    payload = {
        "lines": [
            _line_payload(line1.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "50"}]),
            _line_payload(line2.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "30"}]),
        ]
    }
    response = client.post(f"{BASE}/sales-orders/{order.id}/confirm", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision_no"] == 1
    assert body["review_state"] == "confirmed"
    assert body["inquiry_rows_created"] == 0  # no Buy on either line
    assert body["exceptions"] == []

    from app.models.project_so import SOSupplyDecision, SOLineAllocation

    decisions = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .all()
    )
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.state == "active"
    assert decision.revision_no == 1
    assert len(decision.line_snapshots) == 2

    allocations = (
        db.query(SOLineAllocation)
        .filter(SOLineAllocation.decision_id == decision.id)
        .all()
    )
    assert len(allocations) == 2
    assert {a.so_line_id for a in allocations} == {line1.id, line2.id}


def test_a_bought_line_names_its_own_location_on_the_order_row_and_only_that_one(api):
    """AC-H5: the ORDER row is the line's OWN fulfilment location, never a join of every
    warehouse the composition touched. It used to stamp `"<own> + <pool>"` on the line -
    unreadable by purchasing, and unable to ever match a borrow-shortfall row netted by
    `(item_code, stock_location)`.

    The pool holds 30 against the line's 50, so the whole-line rule (AC-L5) buys the whole
    50 and the pool is not drawn on at all - which is exactly the composition that used to
    produce the joined location.
    """
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=30)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(line.id, buy_qty="50")
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["inquiry_rows_created"] == 1

    from app.models.project_so import IV_ORDER, OrderInquiryRow

    rows = db.query(OrderInquiryRow).filter(OrderInquiryRow.verb == IV_ORDER).all()
    assert len(rows) == 1
    assert rows[0].stock_location == world.own_wh.warehouse_code
    assert " + " not in (rows[0].stock_location or "")

    db.refresh(line)
    assert line.stock_location == world.own_wh.warehouse_code


# --------------------------------------------------------------------------- rollback


def _held_by_line(db, line_ids, *, active_only=False) -> dict:
    """SOLineAllocation qty summed per (line, warehouse); a fresh read. `active_only` reads
    just the active decision's holds (a superseded revision's rows stay for audit)."""
    from app.models.project_so import SOLineAllocation, SOSupplyDecision

    db.expire_all()
    out: dict = {}
    query = db.query(SOLineAllocation).filter(SOLineAllocation.so_line_id.in_(line_ids))
    if active_only:
        query = query.join(
            SOSupplyDecision, SOSupplyDecision.id == SOLineAllocation.decision_id
        ).filter(SOSupplyDecision.state == "active")
    for row in query:
        key = (row.so_line_id, row.warehouse_id)
        out[key] = out.get(key, Decimal("0")) + row.qty
    return out


def _assert_short_notice(body, line, *, free, short, count=1):
    """The Confirm result names the line and says how much was free and how much is short."""
    notices = body.get("stock_short_notices") or []
    assert len(notices) == count, body
    notice = next(n for n in notices if n["line_no"] == line.line_no)
    assert f"only {free} free at" in notice["reason"], notice
    assert f", {short} short" in notice["reason"], notice
    return notice


def test_one_unbalanced_line_is_held_back_and_the_rest_of_the_order_confirms(api):
    """AC-C02: line 2's Buy is short of open qty by 10, so that line is held back by name
    while line 1's valid Reserve still confirms and holds its 50. Nothing is written for
    line 2.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    client, world = api
    db = world.db
    _stock(db, world.product, world.own_wh, on_hand=100)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line_1 = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
    core_line_2 = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="30")
    line1 = _project_line(db, order, line_no=10, product=world.product, core_line=core_line_1)
    line2 = _project_line(db, order, line_no=20, product=world.product, core_line=core_line_2)
    db.commit()

    payload = {
        "lines": [
            _line_payload(line1.id, reserve=[{"warehouse_id": world.own_wh.id, "qty": "50"}]),
            # 20 buy against an open qty of 30: unbalanced by 10.
            _line_payload(line2.id, buy_qty="20"),
        ]
    }
    response = client.post(f"{BASE}/sales-orders/{order.id}/confirm", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["line_no"] for row in body["lines_held_back"]] == [20], body

    from app.models.project_so import OrderInquiryRow, SOSupplyDecision

    decisions = db.query(SOSupplyDecision).filter(
        SOSupplyDecision.project_sales_order_id == order.id
    ).all()
    assert len(decisions) == 1 and decisions[0].state == "active"
    assert {s["project_line_id"] for s in decisions[0].line_snapshots} == {str(line1.id)}
    assert _held_by_line(db, [line1.id, line2.id]) == {(line1.id, world.own_wh.id): Decimal("50")}
    assert db.query(OrderInquiryRow).filter(
        OrderInquiryRow.so_line_id == line2.id
    ).count() == 0


# --------------------------------------------------------------------------- recheck


def test_confirmation_rechecks_stock_and_holds_only_what_is_free_when_it_changed_after_the_sheet_was_read(api):
    """AC-C03: the sheet proposed Reserve 10 when 10 was free; another allocation then
    claims it, so the recheck at confirmation time reads the live figure, not the payload:
    the line confirms as decided, holds nothing (0 free) and records the 10 short.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    client, world = api
    db = world.db
    stock = _stock(db, world.product, world.own_wh, on_hand=10, reserved=0)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    # Something else claimed the stock after the sheet was opened.
    stock.quantity_reserved = 10
    db.commit()

    payload = {"lines": [_line_payload(line.id, reserve=[{"warehouse_id": world.own_wh.id, "qty": "10"}])]}
    response = client.post(f"{BASE}/sales-orders/{order.id}/confirm", json=payload)
    assert response.status_code == 200, response.text
    _assert_short_notice(response.json(), line, free=0, short=10)

    from app.models.project_so import SOSupplyDecision

    active = db.query(SOSupplyDecision).filter(
        SOSupplyDecision.project_sales_order_id == order.id,
        SOSupplyDecision.state == "active",
    ).one()
    snap = next(s for s in active.line_snapshots if s["project_line_id"] == str(line.id))
    assert Decimal(str(snap["stock_short"][0]["short"])) == Decimal("10"), snap
    assert _held_by_line(db, [line.id]) == {}


# --------------------------------------------------------------------------- revision chain


def test_reconfirming_supersedes_the_active_decision_and_increments_the_revision(api):
    """AC-C04/AC-C07: a second Confirm on the same SO supersedes the first revision rather
    than replacing it in place, and the superseded revision's allocations stay for audit."""
    client, world = api
    db = world.db
    stock = _stock(db, world.product, world.pool_wh, on_hand=50)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    first = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "50"}])]},
    )
    assert first.status_code == 200, first.text
    assert first.json()["revision_no"] == 1

    second = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(line.id, buy_qty="50")
            ]
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["revision_no"] == 2

    from app.models.project_so import SOSupplyDecision, SOLineAllocation

    decisions = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .order_by(SOSupplyDecision.revision_no)
        .all()
    )
    assert [d.revision_no for d in decisions] == [1, 2]
    assert [d.state for d in decisions] == ["superseded", "active"]

    old_allocations = (
        db.query(SOLineAllocation).filter(SOLineAllocation.decision_id == decisions[0].id).all()
    )
    assert len(old_allocations) == 1, "the superseded revision's allocation must stay for audit"


# --------------------------------------------------------------------------- concurrency


def test_a_second_confirmation_racing_an_already_active_decision_gets_a_conflict_with_no_partial_writes(api):
    """AC-C05, driven through the DB-level singleton rather than through real threads.

    A confirmation that SEES an active decision supersedes it - that is reconfirmation,
    and it is the test above. The race is the other case: two sessions each read "nothing
    active here" and both insert, which is what the partial unique index exists to settle.

    Patching `active_decision` to answer None while an active row is committed reproduces
    exactly that: the service takes the first-confirmation path, its insert collides with
    the winner's row, and what has to be proven is that it loses WHOLE - a 409, one
    decision left, and not a single allocation or inquiry row from the loser's attempt.
    """
    from app.models.project_so import OrderInquiryRow, SOLineAllocation, SOSupplyDecision
    from app.services.project_supply_service import ProjectSupplyService

    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=50)
    order = _project_so(db, world.project, so_id=None)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    winner = SOSupplyDecision(
        id=_uid(),
        company_id=world.company_id,
        project_sales_order_id=order.id,
        revision_no=1,
        state="active",
        line_snapshots=[{"line_no": 10, "project_line_id": line.id}],
        confirmed_by=world.eling,
        confirmed_at=None,
    )
    db.add(winner)
    db.commit()

    original = ProjectSupplyService.active_decision
    try:
        ProjectSupplyService.active_decision = lambda self, pso_id: None
        response = client.post(
            f"{BASE}/sales-orders/{order.id}/confirm",
            json={"lines": [_line_payload(line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "50"}])]},
        )
        assert response.status_code == 409, response.text
    finally:
        ProjectSupplyService.active_decision = original

    remaining = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .all()
    )
    assert len(remaining) == 1, "the loser must not have written a second active row"
    assert remaining[0].id == winner.id
    assert db.query(SOLineAllocation).filter(
        SOLineAllocation.so_line_id == line.id
    ).count() == 0
    assert db.query(OrderInquiryRow).filter(
        OrderInquiryRow.so_line_id == line.id
    ).count() == 0


def test_the_database_refuses_a_second_active_revision_for_one_sales_order(api):
    """The mechanism the test above leans on, stated on its own (AC-C05).

    `uq_so_supply_decisions_active` is what makes the loser of a real race lose: without
    it two confirmations would both commit and the same stock would be promised twice, with
    nothing in the data to say which promise was real.
    """
    from sqlalchemy.exc import IntegrityError

    from app.models.project_so import SOSupplyDecision

    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=50)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    first = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "50"}])]},
    )
    assert first.status_code == 200, first.text

    db.add(
        SOSupplyDecision(
            id=_uid(),
            company_id=world.company_id,
            project_sales_order_id=order.id,
            revision_no=99,
            state="active",
            line_snapshots=[],
            confirmed_by=world.eling,
            confirmed_at=None,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# --------------------------------------------------------------------------- supersession


def test_publishing_an_amendment_supersedes_the_active_decision(api):
    """AC-C06 (amendment leg, PLAN 5.3): a material amendment publish must supersede the
    active revision so the SO returns to Needs CS review rather than staying confirmed
    against stale facts."""
    from app.models.project_so import (
        AMENDMENT_PROPOSED,
        OrderChangeNotice,
        SOAmendment,
        SOSupplyDecision,
    )

    client, world = api
    db = world.db
    _stock(db, world.product, world.own_wh, on_hand=50)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    decision = SOSupplyDecision(
        id=_uid(), company_id=world.company_id, project_sales_order_id=order.id,
        revision_no=1, state="active",
        line_snapshots=[{"line_no": 10, "project_line_id": line.id}],
        confirmed_by=world.eling, confirmed_at=None,
    )
    db.add(decision)
    db.commit()

    ocn = OrderChangeNotice(
        id=_uid(), company_id=world.company_id, ocn_number=f"ZZT-OCN-{_suffix()}",
        project_id=world.project.id, project_sales_order_id=order.id,
        approver_id=world.eling, approved_at=None,
    )
    db.add(ocn)
    db.flush()
    amendment = SOAmendment(
        id=_uid(), company_id=world.company_id, project_sales_order_id=order.id,
        ocn_id=ocn.id, delta_json={"rows": []}, status=AMENDMENT_PROPOSED,
    )
    db.add(amendment)
    db.commit()

    from app.services.project_so_delta_service import ProjectSODeltaService

    ProjectSODeltaService(db).publish_amendment(amendment.id, actor_user_id=world.eling)
    db.commit()

    db.expire_all()
    refreshed = db.get(SOSupplyDecision, decision.id)
    assert refreshed.state == "superseded"
    assert refreshed.superseded_reason


def test_a_reconciliation_link_change_supersedes_the_active_decision(api):
    """AC-C06 (reconciliation leg, PLAN 5.3; Slice E one signal, issue #860): `_persist`
    re-linking a Project line to a DIFFERENT core line is the one thing a reconciliation
    run supersedes an active decision for. The old core line closes (AutoCount split/
    replaced it) so it drops out of the candidate pool, and a fresh core line at the same
    product/date is the only remaining candidate -- the line relinks to it, `_persist`'s
    `relinked` list is non-empty, and `ProjectSupplyService.supersede_for_material_change`
    fires with its exact reason text. A plain fact drift with no mapping change (the
    non-relink case) is covered elsewhere by AC-E1c
    (`tests/scm/test_one_signal.py::test_reconciliation_without_a_relink_leaves_the_decision_active`),
    which asserts the decision stays active -- this test is the other branch, the actual
    relink. Was `test_a_reconciliation_link_change_supersedes_the_active_decision`
    asserting a bare quantity drift with no relink and tolerating the retired `challenged`
    outcome."""
    from app.models.project_so import SOSupplyDecision

    client, world = api
    db = world.db
    _stock(db, world.product, world.own_wh, on_hand=50)
    core_so = _core_so(db, world.company_id)
    order = _project_so(db, world.project, so_id=core_so.id)
    old_core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
    line = _project_line(
        db, order, line_no=10, product=world.product, core_line=old_core_line
    )
    db.commit()

    decision = SOSupplyDecision(
        id=_uid(), company_id=world.company_id, project_sales_order_id=order.id,
        revision_no=1, state="active",
        line_snapshots=[
            {
                "line_no": 10,
                "project_line_id": line.id,
                "core_line_id": old_core_line.id,
                "open_qty": "50",
            }
        ],
        confirmed_by=world.eling, confirmed_at=None,
    )
    db.add(decision)
    db.commit()

    # The AutoCount line mapping changes: the old core line closes (no longer a
    # candidate) and a new one appears at the same product/required date, so the
    # matching pass relinks the Project line to it instead of leaving it missing.
    old_core_line.line_status = "closed"
    new_core_line = _core_line(
        db, core_so, world.product, world.own_wh, qty_ordered="50"
    )
    db.commit()

    from app.services.project_so_reconciliation_service import ProjectSOReconciliationService

    ProjectSOReconciliationService(db).reconcile(order)
    db.commit()

    db.expire_all()
    db.refresh(line)
    assert str(line.core_sales_order_line_id) == str(new_core_line.id)

    refreshed = db.get(SOSupplyDecision, decision.id)
    assert refreshed.state == "superseded"
    assert (
        refreshed.superseded_reason
        == "The AutoCount line mapping changed after this revision was confirmed."
    )


def test_a_fact_drift_on_read_leaves_the_active_decision_active(api):
    """AC-E1/AC-E2 (`PLAN-scm-change-management-one-engine.md` rule 9, issue #860):
    `proposal_for` no longer calls `challenge_if_drifted` on the sheet read - a drift is
    the change batch's job now, not a flip on the decision the sheet is reading. Was
    `test_a_fact_drift_challenges_the_active_decision_on_read`, asserting the opposite."""
    from app.models.project_so import SOSupplyDecision

    client, world = api
    db = world.db
    _stock(db, world.product, world.own_wh, on_hand=50)
    core_so = _core_so(db, world.company_id)
    order = _project_so(db, world.project, so_id=core_so.id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    decision = SOSupplyDecision(
        id=_uid(), company_id=world.company_id, project_sales_order_id=order.id,
        revision_no=1, state="active",
        line_snapshots=[{"line_no": 10, "project_line_id": line.id, "open_qty": "50"}],
        confirmed_by=world.eling, confirmed_at=None,
    )
    db.add(decision)
    db.commit()

    # The customer took more delivery than the snapshot knew about: open qty has moved.
    core_line.qty_delivered = Decimal("20")
    db.commit()

    response = client.get(f"{BASE}/sales-orders/{order.id}/supply")
    assert response.status_code == 200, response.text

    db.expire_all()
    assert db.get(SOSupplyDecision, decision.id).state == "active"


def test_review_states_for_reads_confirmed_when_an_active_decision_exists(api):
    """PLAN 5.3: review state reads `confirmed` iff the Stage 1B conditions hold AND an
    active decision exists, from the batched `review_states_for` the list route uses."""
    from app.models.project_so import SOSupplyDecision

    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    order = _project_so(db, world.project, so_id=core_so.id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    decision = SOSupplyDecision(
        id=_uid(), company_id=world.company_id, project_sales_order_id=order.id,
        revision_no=1, state="active",
        line_snapshots=[{"line_no": 10, "project_line_id": line.id}],
        confirmed_by=world.eling, confirmed_at=None,
    )
    db.add(decision)
    db.commit()

    from app.services.project_so_reconciliation_service import ProjectSOReconciliationService

    states = ProjectSOReconciliationService(db).review_states_for([order.id])
    assert states[str(order.id)]["review_state"] == "confirmed"


# --------------------------------------------------------------------------- borrow / donor


def test_borrow_without_a_reason_is_refused(api):
    """AC-B09/AC-C03: a Borrow component with a blank reason must fail confirmation."""
    client, world = api
    db = world.db
    other_wh = _warehouse(db, f"ZZT-OTH-{_suffix()}")
    _stock(db, world.product, other_wh, on_hand=100)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    payload = {
        "lines": [
            _line_payload(
                line.id,
                borrow=[
                    {"source": "other_location", "warehouse_id": other_wh.id, "qty": "20", "reason": ""}
                ],
            )
        ]
    }
    response = client.post(f"{BASE}/sales-orders/{order.id}/confirm", json=payload)
    assert response.status_code == 422, response.text
    body = response.json()
    assert any(row["line_no"] == 10 for row in body["failing_lines"])


def test_cross_project_borrow_writes_an_accepted_claim_directly_with_no_requested_state(api):
    """AC-B10/AC-C08: cross-project Borrow is one action -- the confirming CS's own
    confirmation writes `allocation_claims` straight to `accepted`, actor-stamped, with
    `decided_at` set. No row is ever left `requested` on this path."""
    from app.services.project_service import register_project

    client, world = api
    db = world.db
    donor_wh = _warehouse(db, f"ZZT-DNR-{_suffix()}")
    _stock(db, world.product, donor_wh, on_hand=100)
    farah = _user(db, f"{MARKER} Farah")
    donor_project = register_project(
        db, company_id=world.company_id, actor_user_id=farah, developer_party_id=None,
        title=f"{MARKER} Seri Heights",
    )
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="40")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    payload = {
        "lines": [
            _line_payload(
                line.id,
                borrow=[
                    {
                        "source": "other_project",
                        "warehouse_id": donor_wh.id,
                        "donor_project_id": donor_project.id,
                        "qty": "40",
                        "reason": "Seri Heights has surplus this month.",
                    }
                ],
            )
        ]
    }
    response = client.post(f"{BASE}/sales-orders/{order.id}/confirm", json=payload)
    assert response.status_code == 200, response.text

    claims = (
        db.query(AllocationClaim)
        .filter(AllocationClaim.from_project_id == world.project.id)
        .all()
    )
    assert len(claims) == 1
    claim = claims[0]
    assert claim.state == CLAIM_ACCEPTED
    assert claim.requested_by == world.eling
    assert claim.decided_by == world.eling
    assert claim.decided_at is not None
    assert claim.reason == "Seri Heights has surplus this month."

    assert (
        db.query(AllocationClaim)
        .filter(AllocationClaim.from_project_id == world.project.id, AllocationClaim.state == CLAIM_REQUESTED)
        .count()
        == 0
    ), "no requested-state claim should ever exist on the confirmation path"


def test_a_borrow_from_another_group_raises_an_order_back_for_the_whole_take(api):
    """LADDER V4, ruled 26 August 2026 (`PLAN-scm-cs-planning-uat.md` section 1d): "a
    borrow from another `-xx` group or from another order raises an ORDER BACK row against
    the donor".

    For the WHOLE quantity taken, and no longer only where the donor's own availability
    went negative. That older rule (PLAN 13.11) was right while a warehouse's own reading
    decided what it could lend; under v4 rung 4 borrows only within the donor GROUP's own
    net, so no borrow the engine proposes can push a donor below zero and the negativity
    test would raise nothing at all, ever. What is left to record is the fact itself: the
    donor lent 20 and is owed 20 back, at the DONOR's location.
    """
    from app.models.project_so import IV_BORROW_SHORTFALL, OrderInquiryRow

    client, world = api
    db = world.db
    donor_wh = _warehouse(db, f"ZZT-SHORT-{_suffix()}")
    _stock(db, world.product, donor_wh, on_hand=100)
    # The donor's own book: 90 of that 100 is already owed to somebody.
    theirs = _core_so(db, world.company_id)
    _core_line(db, theirs, world.product, donor_wh, qty_ordered="90")

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=20, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id,
                    borrow=[
                        {
                            "source": "other_location",
                            "warehouse_id": donor_wh.id,
                            "qty": "20",
                            "reason": "Their hand-over is in December.",
                        }
                    ],
                )
            ]
        },
    )
    assert response.status_code == 200, response.text
    # Nothing is BOUGHT for this line - it is fully borrowed - so the only row purchasing
    # gets is the hole the borrow opened.
    assert response.json()["inquiry_rows_created"] == 1

    rows = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.verb == IV_BORROW_SHORTFALL)
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert str(row.qty) in ("20", "20.0000")
    assert row.stock_location == donor_wh.warehouse_code
    assert row.state == "raised"
    assert row.so_line_id == line.id
    # The sentence purchasing reads: who lent what, and to whom.
    assert "20" in row.note and donor_wh.warehouse_code in row.note
    assert "lent 20" in row.note
    assert order.provisional_ref in row.note


def test_a_donor_that_can_easily_afford_the_borrow_is_still_owed_it_back(api):
    """The other half of the 26 August ruling, and the one that changed direction.

    This donor holds 100 with nothing sold against it, so under PLAN 13.11 the borrow left
    no hole and raised nothing. Under ladder v4 what is recorded is not a hole, it is a
    DEBT: another ownership group's 20 units are now on this order, and the row against the
    donor is how anybody ever learns that. A pool draw still raises nothing (AC-L13) -
    the pool is shared and nobody is owed it back.
    """
    from app.models.project_so import IV_BORROW_SHORTFALL, OrderInquiryRow

    client, world = api
    db = world.db
    donor_wh = _warehouse(db, f"ZZT-RICH-{_suffix()}")
    _stock(db, world.product, donor_wh, on_hand=100)

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=20, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id,
                    borrow=[
                        {
                            "source": "other_location",
                            "warehouse_id": donor_wh.id,
                            "qty": "20",
                            "reason": "They are holding far more than they will ship.",
                        }
                    ],
                )
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["inquiry_rows_created"] == 1
    rows = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.verb == IV_BORROW_SHORTFALL)
        .all()
    )
    assert len(rows) == 1
    assert str(rows[0].qty) in ("20", "20.0000")
    assert rows[0].stock_location == donor_wh.warehouse_code


def test_re_confirming_the_same_borrow_does_not_stack_a_second_shortfall_row(api):
    """A shortfall row is superseded like every other still-raised row, never doubled."""
    from app.models.project_so import IV_BORROW_SHORTFALL, OrderInquiryRow

    client, world = api
    db = world.db
    donor_wh = _warehouse(db, f"ZZT-SHORT-{_suffix()}")
    _stock(db, world.product, donor_wh, on_hand=100)
    theirs = _core_so(db, world.company_id)
    _core_line(db, theirs, world.product, donor_wh, qty_ordered="90")

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=20, product=world.product, core_line=core_line)
    db.commit()

    payload = {
        "lines": [
            _line_payload(
                line.id,
                borrow=[
                    {
                        "source": "other_location",
                        "warehouse_id": donor_wh.id,
                        "qty": "20",
                        "reason": "Their hand-over is in December.",
                    }
                ],
            )
        ]
    }
    assert client.post(f"{BASE}/sales-orders/{order.id}/confirm", json=payload).status_code == 200
    assert client.post(f"{BASE}/sales-orders/{order.id}/confirm", json=payload).status_code == 200

    rows = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.verb == IV_BORROW_SHORTFALL)
        .all()
    )
    # ORDERBACK-DELTA (owner, 13 Oct 2026): the unchanged hole is the same row, kept,
    # not cancelled and raised again beside itself.
    assert [row.state for row in rows] == ["raised"], [(r.state, r.note) for r in rows]


# --------------------------------------------------------------------------- discontinued


def test_a_discontinued_buy_without_a_reason_confirms_and_raises_the_oi_row(api):
    """AC-39: the discontinued-buy-needs-a-reason gate is removed. A Buy on a discontinued
    product confirms with no `buy_reason`, same as any other Buy, and still raises its
    Order Inquiry row and an active decision revision."""
    client, world = api
    db = world.db
    discontinued = _product(db, discontinued=True)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, discontinued, world.own_wh, qty_ordered="15")
    line = _project_line(db, order, line_no=10, product=discontinued, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, buy_qty="15")]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["inquiry_rows_created"] == 1

    from app.models.project_so import SOSupplyDecision

    decisions = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .all()
    )
    assert len(decisions) == 1
    assert decisions[0].revision_no == 1
    assert decisions[0].state == "active"


def test_a_discontinued_buy_without_a_reason_freezes_the_warning_and_a_null_reason(api):
    """AC-40: the frozen line snapshot still carries the lifecycle warning (it is informational,
    not a gate), and `buy_reason` freezes as null rather than an empty string when none was
    given."""
    client, world = api
    db = world.db
    discontinued = _product(db, discontinued=True)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, discontinued, world.own_wh, qty_ordered="15")
    line = _project_line(db, order, line_no=10, product=discontinued, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, buy_qty="15")]},
    )
    assert response.status_code == 200, response.text

    from app.models.project_so import SOSupplyDecision

    decision = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .one()
    )
    assert len(decision.line_snapshots) == 1
    snapshot = decision.line_snapshots[0]
    assert snapshot["lifecycle_warning"] == "This product is discontinued."
    assert snapshot["buy_reason"] is None


def test_a_discontinued_buy_with_a_reason_confirms(api):
    """The other half of AC-B11: the same line, with the reason supplied, confirms and its
    Buy residual reaches Purchasing without substitution."""
    client, world = api
    db = world.db
    discontinued = _product(db, discontinued=True)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, discontinued, world.own_wh, qty_ordered="15")
    line = _project_line(db, order, line_no=10, product=discontinued, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id, buy_qty="15",
                    buy_reason="Customer committed; no substitute in the range.",
                )
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["inquiry_rows_created"] == 1


# --------------------------------------------------------------------------- authz


def test_confirmation_is_denied_without_the_edit_permission(api):
    """AC-C08: a view-only actor may read the proposal but may not confirm it."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.own_wh, on_hand=50)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    from app.services.user_service import UserPermissionService

    original = UserPermissionService.check_user_has_permission
    try:
        UserPermissionService.check_user_has_permission = (
            lambda self, uid, slug: slug != EDIT
        )
        response = client.post(
            f"{BASE}/sales-orders/{order.id}/confirm",
            json={"lines": [_line_payload(line.id, reserve=[{"warehouse_id": world.own_wh.id, "qty": "50"}])]},
        )
        assert response.status_code == 403, response.text
    finally:
        UserPermissionService.check_user_has_permission = original


def test_a_cross_company_project_so_is_denied_without_a_leak():
    """AC-C08: a Sorento-scoped session confirming a Mocha-owned Project SO id gets a
    plain 404 -- never a 403 that would confirm the id exists in another company."""
    from app.models.base import company_scope
    from app.services.project_service import register_project

    with blank_session() as db:
        sorento_id = _sorento(db)
        other_id = _second_company(db)
        project_seed_service.run(db, company_id=sorento_id)
        project_seed_service.run(db, company_id=other_id)

        eling = _user(db, f"{MARKER} Eling")
        register_project(
            db, company_id=sorento_id, actor_user_id=eling, developer_party_id=None,
            title=f"{MARKER} Sorento Residences",
        )

        farah = _user(db, f"{MARKER} Farah")
        other_project = register_project(
            db, company_id=other_id, actor_user_id=farah, developer_party_id=None,
            title=f"{MARKER} Mocha Residences",
        )
        other_product = _product(db)
        other_wh = _warehouse(db, f"ZZT-OTHWH-{_suffix()}")
        other_order = _project_so(db, other_project)
        other_core_so = _core_so(db, other_id)
        other_core_line = _core_line(db, other_core_so, other_product, other_wh, qty_ordered="10")
        other_line = _project_line(
            db, other_order, line_no=10, product=other_product, core_line=other_core_line
        )
        db.commit()

        client, originals = _client(db, eling)
        try:
            with company_scope(db, frozenset({sorento_id})):
                response = client.post(
                    f"{BASE}/sales-orders/{other_order.id}/confirm",
                    json={"lines": [_line_payload(other_line.id, buy_qty="10")]},
                )
                assert response.status_code == 404, response.text
                assert "mocha" not in response.text.lower()
                assert other_order.id not in response.text

            # Confirmed here to make this test genuinely RED, not merely a coincidental
            # 404 off a route that does not exist at all yet: nothing was written for the
            # foreign order either, checked against the model this slice adds.
            from app.models.project_so import SOSupplyDecision

            assert (
                db.query(SOSupplyDecision)
                .filter(SOSupplyDecision.project_sales_order_id == other_order.id)
                .count()
                == 0
            )
        finally:
            _restore(originals)


# --------------------------------------------------------------------------- list filter


def test_the_fulfilment_planning_list_filters_by_the_confirmed_review_state(api):
    """Section 6: `GET /fulfilment-planning` `review_state` Literal gains `confirmed`."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    order = _project_so(db, world.project, so_id=core_so.id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    # The worklist row names the Project SO in `id` (`FulfilmentPlanningRow`, Stage 1B);
    # `project_sales_order_id` is the RECONCILIATION summary's spelling of the same thing.
    unconfirmed = client.get(f"{BASE}/fulfilment-planning?review_state=confirmed")
    assert unconfirmed.status_code == 200, unconfirmed.text
    assert order.id not in {row["id"] for row in unconfirmed.json()["data"]}

    needs_review = client.get(f"{BASE}/fulfilment-planning?review_state=needs_cs_review")
    assert needs_review.status_code == 200, needs_review.text
    assert order.id in {row["id"] for row in needs_review.json()["data"]}

    confirmed = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, buy_qty="10")]},
    )
    assert confirmed.status_code == 200, confirmed.text

    now_confirmed = client.get(f"{BASE}/fulfilment-planning?review_state=confirmed")
    assert now_confirmed.status_code == 200, now_confirmed.text
    assert order.id in {row["id"] for row in now_confirmed.json()["data"]}


# --------------------------------------------------------------- pool buckets per product


def test_two_products_sharing_one_pool_warehouse_never_share_a_reserve_bucket(api):
    """The pool ledger is per PRODUCT per pool warehouse (AC-B07, AC-B12).

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.

    Product A has 40 free at the shared pool; product B has no stock anywhere. Keyed by
    warehouse alone, B's line would read A's remaining headroom as its own free stock:
    the proposal would offer B a Reserve out of a pile that holds none of B, and the
    confirm-time recheck would let that over-Reserve be held. It is now recorded short, holding nothing.
    """
    client, world = api
    db = world.db
    product_b = _product(db)
    _stock(db, world.product, world.pool_wh, on_hand=40)
    core_so = _core_so(db, world.company_id)
    core_line_a = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10")
    core_line_b = _core_line(db, core_so, product_b, world.own_wh, qty_ordered="15")
    order = _project_so(db, world.project, so_id=core_so.id)
    line_a = _project_line(db, order, line_no=10, product=world.product, core_line=core_line_a)
    line_b = _project_line(db, order, line_no=20, product=product_b, core_line=core_line_b)
    db.commit()

    proposal = client.get(f"{BASE}/sales-orders/{order.id}/supply")
    assert proposal.status_code == 200, proposal.text
    by_line = {row["line_no"]: row for row in proposal.json()["lines"]}
    kinds_b = {c["kind"]: Decimal(str(c["qty"])) for c in by_line[20]["components"]}
    assert kinds_b == {"buy": Decimal("15")}, (
        "product B holds nothing at the pool, so its whole open quantity is Buy; "
        f"proposed {by_line[20]['components']}"
    )
    kinds_a = {c["kind"]: Decimal(str(c["qty"])) for c in by_line[10]["components"]}
    assert kinds_a == {"reserve": Decimal("10")}

    payload = {
        "lines": [
            _line_payload(line_a.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "10"}]),
            _line_payload(line_b.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "15"}]),
        ]
    }
    response = client.post(f"{BASE}/sales-orders/{order.id}/confirm", json=payload)
    assert response.status_code == 200, response.text
    # B's line is short by its whole ask: the pile holds none of B, so 0 is free to it.
    notice = _assert_short_notice(response.json(), line_b, free=0, short=15)
    assert notice["line_no"] == 20
    # A is held in full; nothing of A's 40 leaks into B's line.
    assert _held_by_line(db, [line_a.id, line_b.id]) == {
        (line_a.id, world.pool_wh.id): Decimal("10")
    }


def test_a_negative_component_beside_a_positive_one_is_refused_not_netted(api):
    """A crafted payload of reserve [-50, +100] sums to 50 and balances, but subtracting
    the negative row would INFLATE the capacity ledger and the +100 would commit against
    50 free. Every individual component is checked, not the per-kind totals."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.own_wh, on_hand=50)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    payload = {
        "lines": [
            _line_payload(
                line.id,
                reserve=[
                    {"warehouse_id": world.own_wh.id, "qty": "-50"},
                    {"warehouse_id": world.own_wh.id, "qty": "100"},
                ],
            ),
        ]
    }
    refused = client.post(f"{BASE}/sales-orders/{order.id}/confirm", json=payload)
    assert refused.status_code in (409, 422), refused.text
    failing = refused.json().get("failing_lines") or []
    assert any("negative" in (f.get("reason") or "").lower() for f in failing), refused.text

    from app.models.project_so import SOSupplyDecision, SOLineAllocation

    assert db.query(SOSupplyDecision).filter(
        SOSupplyDecision.project_sales_order_id == order.id
    ).count() == 0
    assert db.query(SOLineAllocation).filter(
        SOLineAllocation.so_line_id == line.id
    ).count() == 0


def test_a_line_named_twice_in_the_payload_is_refused_not_promised_twice(api):
    """Two payload entries for one line would each be offered the line's undepleted share
    and each write a full set of allocations: double the promise against the same stock."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.own_wh, on_hand=100)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    entry = _line_payload(line.id, reserve=[{"warehouse_id": world.own_wh.id, "qty": "50"}])
    refused = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm", json={"lines": [entry, entry]}
    )
    assert refused.status_code in (409, 422), refused.text
    failing = refused.json().get("failing_lines") or []
    assert any("twice" in (f.get("reason") or "").lower() for f in failing), refused.text

    from app.models.project_so import SOSupplyDecision

    assert db.query(SOSupplyDecision).filter(
        SOSupplyDecision.project_sales_order_id == order.id
    ).count() == 0


# ------------------------------------------------- order back on a POOL reserve (13.11)


def _behind_ours(core_so):
    """A core sales order the queue ranks BEHIND every `ZZT-PSO-*` line of this file.

    With no policy row the active weights name only `po_document_sequence`, which no sales-
    order line carries, so every demand row scores 0.0 and the pile's queue falls through to
    the sales-order number. `ZZT-ZCORE-*` sorts after `ZZT-PSO-*`; the default `ZZT-CORE-*`
    sorts before it and would put this order AHEAD of ours.
    """
    core_so.so_number = f"ZZT-ZCORE-{_suffix()}"
    return core_so


def test_a_pool_reserve_asking_more_than_the_pools_available_position_holds_only_the_available_and_records_the_short(api):
    """Ladder v2 (`PLAN-demo-followups-19aug-ladder-v2.md` section E rule 2): pool capacity
    is now `max(min(free, available), 0)` for EVERY pool, not only a hot-selling one - so a
    SINGLE line can no longer oversell the pool through Reserve at all; the recheck refuses
    the ask before it is ever written, which is the whole reason the old order-back-on-
    oversell case this test used to pin (captain, 19 August 2026, "if available quantity is
    negative ... we must have an order back") can no longer occur through this rung -
    ladder v2's `group_borrow` (see `test_a_group_borrow_raises_its_own_order_back` in
    `tests/scm/test_project_supply_service_ladder.py`) is where an order-back now comes
    from instead.

    The pool holds 100 with 90 of it already owed to its own book (ranked behind our line),
    leaving `available` at 10 - and 20 holds only the 10 and records 10 short, never more.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=100)
    # The pool's OWN book: 90 of that 100 is already owed there, ranked behind our line.
    theirs = _behind_ours(_core_so(db, world.company_id))
    _core_line(db, theirs, world.product, world.pool_wh, qty_ordered="90")

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=20, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id,
                    reserve=[{"warehouse_id": world.pool_wh.id, "qty": "20"}],
                )
            ]
        },
    )
    assert response.status_code == 200, response.text
    notice = _assert_short_notice(response.json(), line, free=10, short=10)
    assert notice["line_no"] == 20
    assert _held_by_line(db, [line.id]) == {(line.id, world.pool_wh.id): Decimal("10")}


def test_a_pool_reserve_up_to_the_pools_available_position_succeeds_and_raises_no_shortfall(api):
    """The boundary of the same rule: asking for EXACTLY the pool's available position (10)
    is allowed - the capacity formula caps it, so `available` lands at 0, never negative,
    and no order-back is raised."""
    from app.models.project_so import IV_BORROW_SHORTFALL, OrderInquiryRow

    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=100)
    theirs = _behind_ours(_core_so(db, world.company_id))
    _core_line(db, theirs, world.product, world.pool_wh, qty_ordered="90")

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10")
    line = _project_line(db, order, line_no=20, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id,
                    reserve=[{"warehouse_id": world.pool_wh.id, "qty": "10"}],
                )
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["inquiry_rows_created"] == 0
    assert (
        db.query(OrderInquiryRow).filter(OrderInquiryRow.verb == IV_BORROW_SHORTFALL).count()
        == 0
    )


def test_a_pool_reserve_the_pool_can_afford_raises_nothing(api):
    """A pool still covering its own book is not short of anything."""
    from app.models.project_so import IV_BORROW_SHORTFALL, OrderInquiryRow

    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=100)

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=20, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id,
                    reserve=[{"warehouse_id": world.pool_wh.id, "qty": "20"}],
                )
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["inquiry_rows_created"] == 0
    assert (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.verb == IV_BORROW_SHORTFALL)
        .count()
        == 0
    )


def test_an_own_location_reserve_is_confirmed_ladder_v3_gave_that_rung_back(api):
    """Ladder v3, section 1b rung 2 (the captain, 25 August 2026): "consider the group
    location first (only available quantity)" - and the line's own location is a location of
    its group. A line standing on 20 free units of its own reserves them, where v2's rule 7
    refused the submission and sent purchasing to buy stock the business already held."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.own_wh, on_hand=20)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=20, product=world.product, core_line=core_line)
    db.commit()

    proposal = client.get(f"{BASE}/sales-orders/{order.id}/supply")
    assert proposal.status_code == 200, proposal.text
    proposed = proposal.json()["lines"][0]["components"]
    assert [(c["kind"], c["qty"], c["rung"]) for c in proposed] == [
        ("reserve", "20", "group_take")
    ]
    assert proposed[0]["source_location"] == world.own_wh.warehouse_code

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id,
                    reserve=[{"warehouse_id": world.own_wh.id, "qty": "20"}],
                )
            ]
        },
    )
    assert response.status_code == 200, response.text


def test_an_own_location_reserve_beyond_what_the_queue_ahead_leaves_holds_nothing_and_records_the_short(api):
    """The other half of rung 2: the own location is capped at what the queue ranked AHEAD
    of this line leaves it, not at the raw pile. 20 on hand with an earlier order owing all
    20 leaves nothing, so a Reserve naming the own location holds nothing and records the 20
    short - the location is allowed, the stock is not.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    client, world = api
    db = world.db
    _stock(db, world.product, world.own_wh, on_hand=20)
    theirs = _behind_ours(_core_so(db, world.company_id))
    _core_line(db, theirs, world.product, world.own_wh, qty_ordered="20",
               required_date=REQUIRED_DATE - timedelta(days=10))

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=20, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id,
                    reserve=[{"warehouse_id": world.own_wh.id, "qty": "20"}],
                )
            ]
        },
    )
    assert response.status_code == 200, response.text
    notice = _assert_short_notice(response.json(), line, free=0, short=20)
    assert notice["line_no"] == 20
    assert world.own_wh.warehouse_code in notice["reason"]
    assert _held_by_line(db, [line.id]) == {}


def test_a_pool_reserve_across_two_lines_of_one_confirmation_shares_the_pools_available_capacity(api):
    """S7 (`documentation/plans/scm/PLAN-demo-followups-19aug-ladder-v2.md`, review fixes):
    the pool's SIGNED availability (`max(min(free, available), 0)`, section E rule 2) is a
    RUNNING LEDGER across every line of one confirmation, not judged per line against the
    same live figure read afresh.

    100 on hand, 90 owed to the pool's own book leaves `available` at 10. Before this fix,
    each line's own ask (6) cleared that cap independently - `_check_line` rebuilt its
    capacity dict from live figures on every line and only ever carried the line's OWN
    site pool forward - so the confirmation wrote both Reserves and oversold the pool by 2,
    caught only afterwards by an order-back. Now the second line is refused outright: the
    pool has only 4 left to give it, so the second line holds 4 and records 2 short, and
    the two lines together hold exactly the pool's 10 (the ledger runs across lines).

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    from app.models.project_so import SOSupplyDecision

    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=100)
    theirs = _behind_ours(_core_so(db, world.company_id))
    _core_line(db, theirs, world.product, world.pool_wh, qty_ordered="90")

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    first = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="6")
    second = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="6")
    line_one = _project_line(db, order, line_no=1, product=world.product, core_line=first)
    line_two = _project_line(db, order, line_no=2, product=world.product, core_line=second)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line_one.id,
                    reserve=[{"warehouse_id": world.pool_wh.id, "qty": "6"}],
                ),
                _line_payload(
                    line_two.id,
                    reserve=[{"warehouse_id": world.pool_wh.id, "qty": "6"}],
                ),
            ]
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    _assert_short_notice(body, line_two, free=4, short=2)
    assert [n["line_no"] for n in body["stock_short_notices"]] == [2], body
    assert _held_by_line(db, [line_one.id, line_two.id]) == {
        (line_one.id, world.pool_wh.id): Decimal("6"),
        (line_two.id, world.pool_wh.id): Decimal("4"),
    }
    assert (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .count()
        == 1
    )


def test_one_lending_groups_book_is_spent_once_across_a_confirmations_lines(api):
    """R-M (3 Sep 2026) bounds the LENDING GROUP, so one confirmation spends it once.

    The recheck seeded its capacity per (product, location) from each unit's own capped
    read, and a unit's read is capped by the group's book on its own. Two units whose dates
    bring DIFFERENT bins of one lending group into view therefore each cleared the whole
    budget: the group here holds 100 at each of two bins and owes 160 (100 on day 45, 60 on
    day 100), so its book spares 40, and a line due on day 30 reserving 40 at the first bin
    and a line due on day 60 reserving 40 at the second both landed - 80 written out of a
    book with 40 in it, on the lending group's own short.

    The second line now holds nothing and records the 40 short (0 free at its bin): the
    group's 40 is spent once, by line 1.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    from app.models.project_so import SOSupplyDecision

    client, world = api
    db = world.db
    lender = f"LN{_suffix()}"
    bins = [_warehouse(db, f"ZZTA-{lender}"), _warehouse(db, f"ZZTB-{lender}")]
    for row in bins:
        _stock(db, world.product, row, on_hand=100)
    # The assignment draws a group's pile oldest first, and two on-hand piles agree about
    # the day - so the tie falls to the event key, `on_hand:<warehouse id>`. Read here
    # rather than assumed, so the case does not depend on which id Postgres minted.
    first, second = sorted(bins, key=lambda row: f"on_hand:{row.id}")
    theirs = _behind_ours(_core_so(db, world.company_id))
    _core_line(
        db, theirs, world.product, first, qty_ordered="100",
        required_date=date.today() + timedelta(days=45),
    )
    _core_line(
        db, theirs, world.product, first, qty_ordered="60",
        required_date=date.today() + timedelta(days=100),
    )

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    near = _core_line(
        db, core_so, world.product, world.own_wh, qty_ordered="40",
        required_date=date.today() + timedelta(days=30),
    )
    far = _core_line(
        db, core_so, world.product, world.own_wh, qty_ordered="40",
        required_date=date.today() + timedelta(days=60),
    )
    line_one = _project_line(db, order, line_no=1, product=world.product, core_line=near)
    line_two = _project_line(db, order, line_no=2, product=world.product, core_line=far)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line_one.id, reserve=[{"warehouse_id": first.id, "qty": "40"}],
                ),
                _line_payload(
                    line_two.id, reserve=[{"warehouse_id": second.id, "qty": "40"}],
                ),
            ]
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    notice = _assert_short_notice(body, line_two, free=0, short=40)
    assert second.warehouse_code in notice["reason"], notice
    assert _held_by_line(db, [line_one.id, line_two.id]) == {
        (line_one.id, first.id): Decimal("40")
    }
    assert (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .count()
        == 1
    )

# --------------------------------------------------- the donor's availability nets holds


def test_two_borrowers_of_one_donor_each_owe_back_what_they_took(api):
    """F4(a), re-expressed for ladder v4 (26 August 2026).

    It used to test that the SECOND borrower's hole counted the first one's hold, because
    the donor's `on hand - SO + SPO` cannot see an allocation and the two would otherwise
    have been offered the same stock twice. The order back is no longer a hole, so there is
    nothing to net between them: each order took 30 from another group and each owes 30
    back. What stops the two being promised the same stock is the borrow check itself
    (`_check_borrow`, free stock net of holds), which is where that guard belongs.
    """
    from app.models.project_so import IV_BORROW_SHORTFALL, OrderInquiryRow

    client, world = api
    db = world.db
    donor_wh = _warehouse(db, f"ZZT-TWICE-{_suffix()}")
    _stock(db, world.product, donor_wh, on_hand=100)
    theirs = _core_so(db, world.company_id)
    _core_line(db, theirs, world.product, donor_wh, qty_ordered="60")

    def borrower():
        order = _project_so(db, world.project)
        core_so = _core_so(db, world.company_id)
        core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="30")
        line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
        db.commit()
        return order, line

    order_b, line_b = borrower()
    order_a, line_a = borrower()

    def borrow(order, line):
        return client.post(
            f"{BASE}/sales-orders/{order.id}/confirm",
            json={
                "lines": [
                    _line_payload(
                        line.id,
                        borrow=[
                            {
                                "source": "other_location",
                                "warehouse_id": donor_wh.id,
                                "qty": "30",
                                "reason": "Their hand-over is in December.",
                            }
                        ],
                    )
                ]
            },
        )

    first = borrow(order_b, line_b)
    assert first.status_code == 200, first.text
    assert first.json()["inquiry_rows_created"] == 1

    second = borrow(order_a, line_a)
    assert second.status_code == 200, second.text
    assert second.json()["inquiry_rows_created"] == 1

    rows = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.verb == IV_BORROW_SHORTFALL)
        .order_by(OrderInquiryRow.created_at.asc())
        .all()
    )
    assert {row.so_line_id for row in rows} == {line_a.id, line_b.id}
    assert [str(row.qty) for row in rows] == ["30.0000", "30.0000"]


def test_a_dealer_hot_selling_line_reserving_the_pool_still_opens_no_hole_when_it_fits(api):
    """F4(b), re-expressed for ladder v2 (19 August 2026 follow-up, section E rule 7):
    own-location Reserve is GONE - a dealer hot-selling line's own 10 units at its own
    location are simply not offered by this ladder at all any more (dealer-hot also
    excludes the pool), so the whole 20 is bought. Purchasing is told about the 20, and
    about no hole - a Buy is never a donor's hole."""
    from app.models.project_so import IV_BORROW_SHORTFALL, IV_ORDER, OrderInquiryRow

    client, world = api
    db = world.db
    _classification(db, world.product, world.pool_wh, abc_class_retail="A")
    _stock(db, world.product, world.own_wh, on_hand=10)

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, buy_qty="20")]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["inquiry_rows_created"] == 1, "the Buy, and nothing else"

    rows = db.query(OrderInquiryRow).all()
    assert [row.verb for row in rows] == [IV_ORDER]
    assert not [row for row in rows if row.verb == IV_BORROW_SHORTFALL]


def test_a_dealer_hot_selling_line_may_borrow_its_own_location_as_free_stock(api):
    """BORROW-OWN-LOC (owner, 13 Oct 2026) reverses ladder v2's refusal: the line's OWN
    location is a plain Borrow source. Confirm holds what is free there and passes."""
    client, world = api
    db = world.db
    _classification(db, world.product, world.pool_wh, abc_class_retail="A")
    _stock(db, world.product, world.own_wh, on_hand=10)

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id,
                    borrow=[
                        {
                            "source": "other_location",
                            "warehouse_id": world.own_wh.id,
                            "qty": "10",
                            "reason": "It is our own stock; the pool has none.",
                        }
                    ],
                    buy_qty="10",
                )
                | {"amend_reason": "Borrow what our own bin has, buy the rest."}
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert not response.json().get("stock_short_notices"), response.text


# ---------------------------------------------- a placed shortfall is not raised again


def test_a_shortfall_purchasing_already_placed_is_netted_off_the_next_revision(api):
    """F5. Revision 1 owes the donor 25 back and purchasing actions it; a re-confirm of the
    same borrow raises nothing new - the same rule the ORDER rows already follow, because
    placed supply is in the ledger and this service does not get to ask for it twice."""
    from app.models.project_so import (
        INQUIRY_ACTIONED,
        IV_BORROW_SHORTFALL,
        OrderInquiryRow,
    )

    client, world = api
    db = world.db
    donor_wh = _warehouse(db, f"ZZT-PLACED-{_suffix()}")
    _stock(db, world.product, donor_wh, on_hand=100)
    theirs = _core_so(db, world.company_id)
    _core_line(db, theirs, world.product, donor_wh, qty_ordered="90")

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="25")
    line = _project_line(db, order, line_no=20, product=world.product, core_line=core_line)
    db.commit()

    def confirm():
        """The whole line, borrowed (AC-L5: a line is wholly from stock or wholly bought).
        The HOLE it opens is what changes between revisions, because the donor's own book
        changes underneath it."""
        return client.post(
            f"{BASE}/sales-orders/{order.id}/confirm",
            json={
                "lines": [
                    _line_payload(
                        line.id,
                        borrow=[
                            {
                                "source": "other_location",
                                "warehouse_id": donor_wh.id,
                                "qty": "25",
                                "reason": "Their hand-over is in December.",
                            }
                        ],
                    )
                ]
            },
        )

    def shortfall_rows():
        return (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.verb == IV_BORROW_SHORTFALL)
            .order_by(OrderInquiryRow.created_at.asc())
            .all()
        )

    # Revision 1: 25 borrowed from another group, so 25 is owed back. Purchasing places it.
    assert confirm().status_code == 200
    rows = shortfall_rows()
    assert len(rows) == 1 and str(rows[0].qty) in ("25", "25.0000")
    rows[0].state = INQUIRY_ACTIONED
    db.commit()

    # Revision 2, the same borrow: nothing outstanding, so nothing new.
    assert confirm().status_code == 200
    db.expire_all()
    rows = shortfall_rows()
    assert [row.state for row in rows] == [INQUIRY_ACTIONED]

    # And the donor's own book growing underneath it changes nothing, which is the ladder-v4
    # part: what is owed back is what was TAKEN, so it moves only when the borrow moves.
    _core_line(db, theirs, world.product, donor_wh, qty_ordered="5")
    db.commit()
    assert confirm().status_code == 200
    db.expire_all()
    assert [row.state for row in shortfall_rows()] == [INQUIRY_ACTIONED]


# ------------------------------------------------------- carried holds (defect A)
#
# PLAN-so-book-diff-replanning.md section 10, defect A: a re-confirm names every covered
# line verbatim (there is no un-decide verb), and `_facts_for` un-nets a NAMED line's own
# hold so the composition can be re-judged. That un-netting used to put the WHOLE
# resubmitted quantity back in the queue, so a component this order already held could
# lose to demand that only showed up after the hold was taken - "BRW has nothing free for
# this line now" against stock the order itself was still sitting on. `_check_line` now
# credits back what this order's own active (or just-superseded) revision already held for
# that exact (line, kind, location[, donor]) before checking the rest against free stock.


def test_re_confirming_an_unchanged_reserve_survives_a_rival_taking_the_rest_of_the_location(api):
    """(a) Reserve 10 at W holds it. Something else then claims every unit of W that was
    freed by un-netting this order's own hold for the recheck - simulating a rival that
    only appeared after revision 1 was confirmed. Resubmitting the SAME 10 is not a new
    ask, so it must not be refused for "nothing free", and the hold must still read 10."""
    client, world = api
    db = world.db
    stock = _stock(db, world.product, world.pool_wh, on_hand=10)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    first = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "10"}])]},
    )
    assert first.status_code == 200, first.text
    assert first.json()["revision_no"] == 1

    # A rival claims the location dry - everything this order's own un-netted hold would
    # otherwise have shown as free.
    stock.quantity_reserved = 10
    db.commit()

    second = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "10"}])]},
    )
    assert second.status_code == 200, second.text
    assert second.json()["revision_no"] == 2

    from app.models.project_so import SOLineAllocation, SOSupplyDecision

    active = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id, SOSupplyDecision.state == "active")
        .one()
    )
    allocations = (
        db.query(SOLineAllocation)
        .filter(SOLineAllocation.decision_id == active.id, SOLineAllocation.so_line_id == line.id)
        .all()
    )
    assert len(allocations) == 1
    assert allocations[0].qty == Decimal("10")


def test_re_confirming_a_larger_reserve_only_the_increase_competes_and_is_named_in_the_short_notice(api):
    """(b) Revision 1 holds Reserve 10 of an open qty of 10. The customer then orders 2
    more, and once this order's own carried 10 is counted the location has nothing left for
    the increase. Asking for 12 (an increase of 2) confirms, keeps the carried 10 held and
    records the short against the 2 increase only, never the 12 the order is resubmitting.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    client, world = api
    db = world.db
    # 20 on hand, deliberately: the subject here is the INCREASE accounting, and asking 12
    # of a location that physically holds 10 is short earlier, at the R14 on-hand guard, for
    # a different (also true) reason. `quantity_reserved` below is what leaves nothing free
    # for the increase once this order's own carried 10 is counted.
    stock = _stock(db, world.product, world.pool_wh, on_hand=20)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    first = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id,
                    reserve=[{"warehouse_id": world.pool_wh.id, "qty": "10"}],
                )
            ]
        },
    )
    assert first.status_code == 200, first.text

    # The customer orders 2 more, and nothing is free for the 2-unit increase beyond this
    # order's own carried 10.
    core_line.qty_ordered = Decimal("12")
    stock.quantity_reserved = 19
    db.commit()

    second = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id,
                    reserve=[{"warehouse_id": world.pool_wh.id, "qty": "12"}],
                )
            ]
        },
    )
    assert second.status_code == 200, second.text
    # Only the 2-unit INCREASE competed: the short is 2 (never the whole resubmitted 12),
    # and the 10 the order already carried stays held at the pool.
    _assert_short_notice(second.json(), line, free=10, short=2)
    assert _held_by_line(db, [line.id], active_only=True) == {
        (line.id, world.pool_wh.id): Decimal("10")
    }


def test_re_confirming_the_reserve_at_a_different_location_competes_fully(api):
    """(c) Moving an already-held Reserve from one pool to a DIFFERENT (empty) pool is a
    real new ask there - the carried credit is keyed by (line, kind, WAREHOUSE), so it
    carries nothing to a location this order never held, and it is recorded short for the
    full 10 (0 free) exactly as an ordinary first ask would be, holding nothing there.

    A second, unrelated site pool - ladder v2 reaches every active pool (section E rule
    2), not only this line's own, so any other pool is a real candidate to move to.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    client, world = api
    db = world.db
    other_pool = _warehouse(db, f"ZZT-OTHERPOOL-{_suffix()}")
    other_owner = _warehouse(db, f"ZZT-OTHEROWN-{_suffix()}", pool_warehouse_id=other_pool.id)
    _stock(db, world.product, world.pool_wh, on_hand=10)
    # `other_pool` has no stock at all.
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    first = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "10"}])]},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, reserve=[{"warehouse_id": other_pool.id, "qty": "10"}])]},
    )
    assert second.status_code == 200, second.text
    _assert_short_notice(second.json(), line, free=0, short=10)
    # Nothing is held at the new pool, and the carried credit is not handed across to it.
    assert (line.id, other_pool.id) not in _held_by_line(db, [line.id])


# ------------------------------------------ the whole-line rule, extended to Amend (AC-L5)


def test_a_line_mixing_stock_with_a_buy_without_a_reason_is_refused(api):
    """AC-L5, the captain 25 August 2026: "a line is either wholly covered from stock (own
    group, pools, borrow, incoming in any mix) or wholly Buy".

    The engine has refused to PROPOSE such a mix since ladder v2's rule 6, but a person could
    still hand-compose one, and half a line bought while the other half is reserved is
    exactly the composition purchasing cannot act on: the order inquiry asks for 15 of a
    line the customer owes 20 of, at a location holding the other 5, and nobody can tell from
    the row whether that is a partial buy or a mistake.

    The 8 Sep 2026 ruling lifts the rule for a MANUAL AMENDMENT that states why (see the
    sibling test below); without a reason it still refuses, and now names the reason as the
    way out.

    LADDER V8 (R-C) carves ONE case out of the rule - the site pool's own share plus a Buy of
    the rest, which is a proposal the engine itself now makes - so the mix pinned here is
    composed at the line's own GROUP location, where the rule is untouched.
    """
    client, world = api
    db = world.db
    _stock(db, world.product, world.own_wh, on_hand=100)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                {
                    "project_line_id": str(line.id),
                    "timely_spo_qty": "0",
                    "reserve": [{"warehouse_id": world.own_wh.id, "qty": "5"}],
                    "buy_qty": "15",
                }
            ]
        },
    )

    assert response.status_code == 422, response.text
    failing = response.json()["failing_lines"]
    assert failing[0]["line_no"] == 10
    assert failing[0]["reason"] == (
        "A line is either met wholly from stock or wholly bought unless a reason is given. "
        "This one mixes 5 from stock with a Buy of 15: take the whole 20 from stock, buy "
        "the whole 20, or say why this differs from the proposal."
    )


def test_a_line_mixing_stock_with_a_buy_with_an_amend_reason_confirms(api):
    """The 8 Sep 2026 ruling's other half: the SAME composition as the test above, but with
    a stated `amend_reason`, saves - and BOTH halves of the split are frozen with the line,
    beside the reason, so purchasing sees the whole decision on reload.

    `amend_reason` is the manual-adjustment signal itself (the schema comment on
    `ConfirmLine.amend_reason`: "Absent when they took the proposal as it stood"), not a
    board-only flag - the whole-line rule reads it directly off the confirm line.
    """
    client, world = api
    db = world.db
    _stock(db, world.product, world.own_wh, on_hand=100)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                {
                    "project_line_id": str(line.id),
                    "timely_spo_qty": "0",
                    "reserve": [{"warehouse_id": world.own_wh.id, "qty": "5"}],
                    "buy_qty": "15",
                    "amend_reason": "Customer takes 5 now, the rest on the next shipment",
                }
            ]
        },
    )

    assert response.status_code == 200, response.text

    snapshot = _active_snapshots(db, order.id)[0]
    assert _shape(snapshot["components"]) == [
        ("reserve", "5", world.own_wh.warehouse_code, "group_take"),
        ("buy", "15", None, None),
    ]
    assert snapshot["amend_reason"] == "Customer takes 5 now, the rest on the next shipment"


def test_a_line_wholly_from_stock_and_a_line_wholly_bought_both_confirm(api):
    """The other side of AC-L5: the rule refuses the MIX without a reason, not either pure
    composition."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=100)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    stocked = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    bought = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="7")
    stocked_line = _project_line(
        db, order, line_no=10, product=world.product, core_line=stocked
    )
    bought_line = _project_line(
        db, order, line_no=20, product=world.product, core_line=bought
    )
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                {
                    "project_line_id": str(stocked_line.id),
                    "timely_spo_qty": "0",
                    "reserve": [{"warehouse_id": world.pool_wh.id, "qty": "20"}],
                    "buy_qty": "0",
                },
                {
                    "project_line_id": str(bought_line.id),
                    "timely_spo_qty": "0",
                    "reserve": [],
                    "buy_qty": "7",
                },
            ]
        },
    )

    assert response.status_code == 200, response.text


# ------------------------ TEMPORARY LIFT (the captain, 8 Sep 2026): own-group bin ask


def test_a_reserve_at_an_oversold_own_bin_without_a_reason_is_still_capped_and_recorded_short(api):
    """The 8 Sep 2026 ruling's baseline: without a stated reason, an oversold ownership
    group still caps a Reserve at the line's own location exactly as it always has.

    10 on hand at the own location, a competing earlier order (ranked ahead by date)
    owing 100 of it, and this line owing 5 - the group is deep in deficit, so this line's
    own bin offers it nothing. A plain `reserve 5` with no `amend_reason` confirms as
    decided but holds nothing and records the 5 short. (The mix of stock with a Buy, which
    needs a reason under AC-L5, is a structural refusal pinned by its own test.)

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    client, world = api
    db = world.db
    _stock(db, world.product, world.own_wh, on_hand=10)
    theirs = _behind_ours(_core_so(db, world.company_id))
    _core_line(
        db, theirs, world.product, world.own_wh, qty_ordered="100",
        required_date=REQUIRED_DATE - timedelta(days=10),
    )

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="5")
    line = _project_line(db, order, line_no=20, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                {
                    "project_line_id": str(line.id),
                    "timely_spo_qty": "0",
                    "reserve": [{"warehouse_id": world.own_wh.id, "qty": "5"}],
                    "buy_qty": "0",
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    _assert_short_notice(response.json(), line, free=0, short=5)
    assert _held_by_line(db, [line.id]) == {}


def test_a_reserve_at_an_oversold_own_bin_confirms_with_a_reason(api):
    """The 8 Sep 2026 ruling's lift: the SAME oversold group, the SAME composition, but a
    stated `amend_reason` frees the own-location ask from the group-net cap - and, since a
    reason is also what AC-L5 asks for to mix stock with a Buy, the same payload proves
    both halves of the ruling end to end."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.own_wh, on_hand=10)
    theirs = _behind_ours(_core_so(db, world.company_id))
    _core_line(
        db, theirs, world.product, world.own_wh, qty_ordered="100",
        required_date=REQUIRED_DATE - timedelta(days=10),
    )

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="5")
    line = _project_line(db, order, line_no=20, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                {
                    "project_line_id": str(line.id),
                    "timely_spo_qty": "0",
                    "reserve": [{"warehouse_id": world.own_wh.id, "qty": "3"}],
                    "buy_qty": "2",
                    "amend_reason": "customer collects 3 from the floor this week",
                }
            ]
        },
    )
    assert response.status_code == 200, response.text

    snapshot = _active_snapshots(db, order.id)[0]
    assert _shape(snapshot["components"]) == [
        ("reserve", "3", world.own_wh.warehouse_code, "group_take"),
        ("buy", "2", None, None),
    ]
    assert snapshot["amend_reason"] == "customer collects 3 from the floor this week"


def test_a_reason_does_not_push_a_reserve_past_on_hand_and_records_the_short(api):
    """The ruling's floor: the reason lifts the GROUP-NET cap, never R14
    (`_check_reserve_against_on_hand`), which reads live on-hand less other lines'
    already-confirmed holds independent of any reason. An earlier order confirms 2 of the
    10 on hand at the own location; this line then asks for 15 more of it, with a reason -
    R14 still refuses, because 15 is more than what physically remains (10 - 2 = 8).

    R14 no longer refuses: the line holds only the 8 that physically remain and records
    7 short, so the two orders together never hold more than the 10 on hand.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    client, world = api
    db = world.db
    _stock(db, world.product, world.own_wh, on_hand=10)

    earlier = _project_so(db, world.project)
    earlier_core_so = _core_so(db, world.company_id)
    earlier_core_line = _core_line(
        db, earlier_core_so, world.product, world.own_wh, qty_ordered="2"
    )
    earlier_line = _project_line(
        db, earlier, line_no=7, product=world.product, core_line=earlier_core_line
    )
    db.commit()
    held = client.post(
        f"{BASE}/sales-orders/{earlier.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    earlier_line.id,
                    reserve=[{"warehouse_id": world.own_wh.id, "qty": "2"}],
                )
            ]
        },
    )
    assert held.status_code == 200, held.text

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="15")
    line = _project_line(db, order, line_no=20, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                {
                    "project_line_id": str(line.id),
                    "timely_spo_qty": "0",
                    "reserve": [{"warehouse_id": world.own_wh.id, "qty": "15"}],
                    "buy_qty": "0",
                    "amend_reason": "customer collects 15 from the floor this week",
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    _assert_short_notice(response.json(), line, free=8, short=7)
    held = _held_by_line(db, [earlier_line.id, line.id])
    assert held == {
        (earlier_line.id, world.own_wh.id): Decimal("2"),
        (line.id, world.own_wh.id): Decimal("8"),
    }, held
    assert sum(held.values()) <= Decimal("10")


# ------------------------------------------------- AC-D1: what the engine had said


def _active_snapshots(db, order_id) -> list:
    from app.models.project_so import SOSupplyDecision

    decision = (
        db.query(SOSupplyDecision)
        .filter(
            SOSupplyDecision.project_sales_order_id == order_id,
            SOSupplyDecision.state == "active",
        )
        .first()
    )
    return list(decision.line_snapshots or [])


def _shape(components) -> list:
    """A composition reduced to what the two sides are compared on: what, how much,
    from where, off which rung. The sentences beside them are written by two different
    rules (the engine's fragment, the confirmation's own) and are not the comparison."""
    return [
        (c.get("kind"), c.get("qty"), c.get("source_location"), c.get("rung"))
        for c in components or []
    ]


def test_confirming_an_untouched_line_freezes_a_proposal_equal_to_what_was_decided(api):
    """AC-D1: the snapshot carries the engine's own composition beside the decided one.

    Taken as it stood, the two say the same thing - which is exactly what the board's
    decision strip has to be able to show as "Suggested 20, Decided 20" rather than
    inferring agreement from the absence of an amendment.
    """
    client, world = api
    db = world.db
    _stock(db, world.product, world.own_wh, on_hand=20)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id, reserve=[{"warehouse_id": world.own_wh.id, "qty": "20"}]
                )
            ]
        },
    )
    assert response.status_code == 200, response.text

    snapshot = _active_snapshots(db, order.id)[0]
    assert _shape(snapshot["components"]) == [
        ("reserve", "20", world.own_wh.warehouse_code, "group_take")
    ]
    assert _shape(snapshot["proposed_components"]) == _shape(snapshot["components"])


def test_confirming_an_amended_line_freezes_both_sides_and_they_differ(api):
    """AC-D1: the whole point of the key. The engine offered the line's own stock and the
    planner bought it instead; without the proposal frozen beside the decision, the board
    can only ever show what was decided and "Suggested" is unanswerable a day later."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.own_wh, on_hand=20)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                {
                    "project_line_id": str(line.id),
                    "timely_spo_qty": "0",
                    "reserve": [],
                    "buy_qty": "20",
                    "amend_reason": "Site wants new stock, not the units held here.",
                }
            ]
        },
    )
    assert response.status_code == 200, response.text

    snapshot = _active_snapshots(db, order.id)[0]
    assert _shape(snapshot["components"]) == [("buy", "20", None, None)]
    assert _shape(snapshot["proposed_components"]) == [
        ("reserve", "20", world.own_wh.warehouse_code, "group_take")
    ]


def test_a_line_the_engine_cannot_plan_freezes_an_empty_proposal_not_a_missing_one(api):
    """A line beyond its reserve window with nothing incoming is a whole-line Buy, and a
    line the ladder never walks proposes an empty list - never an absent key, which is the
    one thing the board reads as "this decision predates the field"."""
    client, world = api
    db = world.db
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(
        db,
        core_so,
        world.product,
        world.own_wh,
        qty_ordered="9",
        required_date=date.today() + timedelta(days=900),
    )
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, buy_qty="9")]},
    )
    assert response.status_code == 200, response.text

    snapshot = _active_snapshots(db, order.id)[0]
    assert "proposed_components" in snapshot
    assert _shape(snapshot["proposed_components"]) == [("buy", "9", None, "buy")]


def test_the_frozen_proposal_does_not_depend_on_the_order_the_lines_were_posted_in(api):
    """The pool ledger is drawn down as the proposal walk goes, so a walk in PAYLOAD order
    would freeze a different suggestion for the same board depending on which line the client
    happened to send first. It walks LINE order, which is the order `proposal_for` walks and
    therefore the order the planner was actually shown."""
    client, world = api
    db = world.db
    # 60 in the pool - 30 of it lendable to a project - against two lines wanting 20 each:
    # whoever is walked first takes the bigger share, so the order of the walk is visible in
    # the frozen numbers.
    #
    # TWO DELIVERY DATES, which is what keeps that true under ladder v6: one order's lines
    # for the same item, location and date are ONE planning unit now, and a unit has no
    # internal walk order to be sensitive to - both lines of it would simply buy. A week
    # apart they are two units, the ledger still passes from the first to the second, and
    # this test is still about the order the units are walked in.
    # 60, not 30: ladder v8 lends a project HALF the pool (R-B), so 60 is what leaves the
    # first line 20 to take and the second nothing - the shape this case is about.
    _stock(db, world.product, world.pool_wh, on_hand=60)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    first = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    second = _core_line(
        db, core_so, world.product, world.own_wh, qty_ordered="20",
        required_date=REQUIRED_DATE + timedelta(days=7),
    )
    line_a = _project_line(db, order, line_no=10, product=world.product, core_line=first)
    line_b = _project_line(db, order, line_no=20, product=world.product, core_line=second)
    db.commit()

    def confirm(payload_lines) -> dict:
        response = client.post(
            f"{BASE}/sales-orders/{order.id}/confirm", json={"lines": payload_lines}
        )
        assert response.status_code == 200, response.text
        return {
            snapshot["line_no"]: _shape(snapshot["proposed_components"])
            for snapshot in _active_snapshots(db, order.id)
        }

    a_then_b = confirm(
        [_line_payload(line_a.id, buy_qty="20"), _line_payload(line_b.id, buy_qty="20")]
    )
    b_then_a = confirm(
        [_line_payload(line_b.id, buy_qty="20"), _line_payload(line_a.id, buy_qty="20")]
    )

    assert a_then_b == b_then_a
    # And the walk really was order-sensitive, or the assertion above proves nothing: line 10
    # is reached first and takes what the pool has.
    assert a_then_b[10] != a_then_b[20]
    # Which the LEDGER decided, and not the reserve window: both lines are well inside it,
    # and line 20's own sentence names the pool it was too late to draw on. Asserted because
    # the two dates were introduced to keep the lines in separate planning units, and a date
    # far enough out to leave the window would have made this test pass for the wrong reason.
    assert a_then_b[10] == [("reserve", "20", world.pool_wh.warehouse_code, "pool")]
    assert a_then_b[20] == [("buy", "20", None, "buy")]
    bought = next(
        snapshot
        for snapshot in _active_snapshots(db, order.id)
        if snapshot["line_no"] == 20
    )
    assert "of 20 can be covered from stock" in (
        bought["proposed_components"][0]["reason"]
    )


def test_the_frozen_proposal_is_walked_as_of_the_day_the_planner_was_deciding(api):
    """A board opened on a Friday and confirmed on the Monday must not record a suggestion
    nobody was ever shown. `as_of` moves the reserve window the ladder judges the line
    against; it moves nothing about the decision, which is always judged against now."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.own_wh, on_hand=20)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    # As of today the line is inside its window, so the ladder offers the stock it stands on.
    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(line.id, buy_qty="20")]},
    )
    assert response.status_code == 200, response.text
    assert _shape(_active_snapshots(db, order.id)[0]["proposed_components"]) == [
        ("reserve", "20", world.own_wh.warehouse_code, "group_take")
    ]

    # Backdated far enough that the required date is beyond the lead-time window, the ladder
    # walks no stock rung at all and the whole line is a Buy (section 1b rung 0).
    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [_line_payload(line.id, buy_qty="20")],
            "as_of": (date.today() - timedelta(days=900)).isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    assert _shape(_active_snapshots(db, order.id)[0]["proposed_components"]) == [
        ("buy", "20", None, "buy")
    ]


def test_a_frozen_proposal_is_stamped_with_the_ladder_that_composed_it(api):
    """A frozen suggestion outlives the rule that made it.

    "MWH-IB has 30 available in the IB group" is a v3 sentence about ONE warehouse's own
    availability, and under v4 that is not a reading anybody makes - so the board has to be
    able to tell a suggestion composed today from one composed under a rule that no longer
    runs. The stamp is on every component of `proposed_components`, and its ABSENCE is the
    signal for every snapshot written before it existed: a JSON column needs no migration to
    grow a key, and "no stamp" is exactly "before ladder v4".
    """
    from app.models.project_so import SOSupplyDecision
    from app.services.project_supply_service import LADDER_VERSION

    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=40)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="40")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "40"}]
                )
            ]
        },
    )
    assert response.status_code == 200, response.text

    decision = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .order_by(SOSupplyDecision.revision_no.desc())
        .first()
    )
    proposed = decision.line_snapshots[0]["proposed_components"]
    assert proposed, "the engine's own composition is frozen beside the decided one"
    assert {part["ladder"] for part in proposed} == {LADDER_VERSION}


# --------------------------------------------------------------------------- R-N (3 Sep)


def test_the_pool_chain_composes_two_pools_and_the_confirm_admits_each_ones_floor(api):
    """AC-N.9 (`PLAN-scm-pool-chain-first.md`): step 0 walks EVERY site pool.

    The board's own SO419417 shape, end to end: the asking bin's pool may spare 4 (half of
    the 8 it holds, the other half kept for dealers) and a second site pool holds 687, so a
    line of 8 composes `BRW 4 + WH3 4` rather than stopping at the 4 its own pool could
    give and reaching for another site's GROUP bin. Confirming it writes TWO Reserve
    components at two pool warehouses, and the confirm-time recheck admits each against
    THAT pool's own free floor - `pool_reserve_capacity` spends the one five-pool net down
    across the chain, so neither pool is judged by the other's pile.
    """
    from app.models.project_so import SOLineAllocation, SOSupplyDecision

    client, world = api
    db = world.db
    far_own = _warehouse(db, f"ZZT-OWN2-{_suffix()}", segment="project")
    far_pool = _warehouse(db, f"ZZT-WH3-{_suffix()}", segment="dealer")
    far_own.pool_warehouse_id = far_pool.id
    _stock(db, world.product, world.pool_wh, on_hand=8)
    _stock(db, world.product, far_pool, on_hand=687)

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="8")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    proposal = client.get(f"{BASE}/sales-orders/{order.id}/supply")
    assert proposal.status_code == 200, proposal.text
    composed = [
        (c["kind"], c["qty"], c["source_location"])
        for c in proposal.json()["lines"][0]["components"]
    ]
    assert composed == [
        ("reserve", "4", world.pool_wh.warehouse_code),
        ("reserve", "4", far_pool.warehouse_code),
    ], composed

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id,
                    reserve=[
                        {"warehouse_id": world.pool_wh.id, "qty": "4"},
                        {"warehouse_id": far_pool.id, "qty": "4"},
                    ],
                )
            ]
        },
    )
    assert response.status_code == 200, response.text

    decision = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .one()
    )
    allocations = (
        db.query(SOLineAllocation)
        .filter(SOLineAllocation.decision_id == decision.id)
        .all()
    )
    reserved = {
        str(row.warehouse_id): Decimal(str(row.qty)) for row in allocations
    }
    assert reserved == {
        str(world.pool_wh.id): Decimal("4"),
        str(far_pool.id): Decimal("4"),
    }, (
        "both pools are held, each against its own floor",
        [(a.source_type, str(a.warehouse_id), str(a.qty)) for a in allocations],
    )


def test_a_pool_of_the_chain_is_still_judged_by_its_own_floor_at_confirm(api):
    """AC-N.9's other half: the chain widened WHAT may be composed, not by how much.

    The second pool holding 687 does not make the asking pool's 4 into 5: each pool of the
    chain is admitted against its own free floor, and the short notice names that pool:
    4 held of the 5 asked there, 1 short, while the far pool's 3 is held in full.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    client, world = api
    db = world.db
    far_own = _warehouse(db, f"ZZT-OWN2-{_suffix()}", segment="project")
    far_pool = _warehouse(db, f"ZZT-WH3-{_suffix()}", segment="dealer")
    far_own.pool_warehouse_id = far_pool.id
    _stock(db, world.product, world.pool_wh, on_hand=4)
    _stock(db, world.product, far_pool, on_hand=687)

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="8")
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id,
                    reserve=[
                        {"warehouse_id": world.pool_wh.id, "qty": "5"},
                        {"warehouse_id": far_pool.id, "qty": "3"},
                    ],
                )
            ]
        },
    )
    assert response.status_code == 200, response.text
    notice = _assert_short_notice(response.json(), line, free=4, short=1)
    assert world.pool_wh.warehouse_code in notice["reason"], notice
    assert _held_by_line(db, [line.id]) == {
        (line.id, world.pool_wh.id): Decimal("4"),
        (line.id, far_pool.id): Decimal("3"),
    }
