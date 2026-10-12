"""SO-book diff replanning (`documentation/plans/scm/PLAN-so-book-diff-replanning.md`).

AC-R12 pins one test per row of section 0's rule table against `suggest()` (pure). The rest
exercise `build_batch` / `apply` against a real Postgres chain (`_pg_fixture.blank_session`,
PRINCIPLES: never sqlite, every FK seeded here) and the four HTTP routes.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.inventory import Stock, Warehouse
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, Supplier
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.project_so import (
    INQUIRY_ACTIONED,
    INQUIRY_CANCELLED,
    INQUIRY_PARTLY_LINKED,
    INQUIRY_PLACED,
    INQUIRY_RAISED,
    IV_ORDER,
    SO_STATUS_PUBLISHED,
    OrderInquiryRow,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
)
from app.models.user import User
from app.services import planning_change_service, project_seed_service
from app.services.project_order_inquiry_service import ProjectOrderInquiryService
from app.services.scm.outstanding_diff import (
    ADDED,
    CLOSED,
    DATE_MOVED,
    QTY_CHANGED,
    Change,
    Diff,
    Line,
)

from ._pg_fixture import blank_session

MARKER = "zzt-planchg"
BASE = "/api/v1/project-sales"
VIEW = "projects.projects.view"
REQUIRED_DATE = date(2027, 3, 1)


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _user(db, name: str) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _product(db, *, discontinued: bool = False) -> Product:
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:4]}", uom_name="Set")
    category = ProductCategory(
        id=_uid(), category_code=f"ZZT-{_uid()[:8]}", category_name=f"{MARKER} cat"
    )
    db.add_all([uom, category])
    db.flush()
    row = Product(
        id=_uid(),
        product_code=f"ZZT-{_uid()[:8]}",
        product_name=f"{MARKER} Basin",
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=Decimal("120.00"),
        is_discontinued=discontinued,
    )
    db.add(row)
    db.flush()
    return row


def _warehouse(db, code: str, *, segment=None, pool_warehouse_id=None) -> Warehouse:
    row = Warehouse(
        id=_uid(), warehouse_code=code, warehouse_name=code, location="ZZT",
        is_active=True, segment=segment, pool_warehouse_id=pool_warehouse_id,
        fulfilment_planning=True,
    )
    db.add(row)
    db.flush()
    return row


def _stock(db, product: Product, warehouse: Warehouse, on_hand, reserved=0) -> Stock:
    row = Stock(
        id=_uid(), product_id=product.id, warehouse_id=warehouse.id,
        quantity_on_hand=on_hand, quantity_reserved=reserved,
    )
    db.add(row)
    db.flush()
    return row


def _core_so(db, company_id: str):
    from app.models.order import SalesOrder

    so = SalesOrder(
        id=_uid(), company_id=company_id, so_number=f"ZZT-CORE-{_uid()[:8]}",
        status="open", demand_class="project",
    )
    db.add(so)
    db.flush()
    return so


def _core_line(db, so, product: Product, warehouse: Warehouse, *, qty_ordered,
                qty_delivered="0", required_date=REQUIRED_DATE):
    from app.models.order import SalesOrderLine

    line = SalesOrderLine(
        id=_uid(), company_id=so.company_id, sales_order_id=so.id, product_id=product.id,
        warehouse_id=warehouse.id, qty_ordered=Decimal(qty_ordered),
        qty_delivered=Decimal(qty_delivered), required_date=required_date,
        line_status="open",
    )
    db.add(line)
    db.flush()
    return line


def _project_so(db, project, *, status=SO_STATUS_PUBLISHED, so_id=None,
                 autocount_doc_no=None):
    order = ProjectSalesOrder(
        id=_uid(), company_id=project.company_id, project_id=project.id,
        provisional_ref=f"ZZT-PSO-{_uid()[:8]}", area_group="TOWER", status=status,
        so_id=so_id, autocount_doc_no=autocount_doc_no,
    )
    db.add(order)
    db.flush()
    return order


def _project_line(db, order, *, line_no, product: Product, core_line):
    line = ProjectSalesOrderLine(
        id=_uid(), company_id=order.company_id, project_sales_order_id=order.id,
        core_sales_order_line_id=core_line.id if core_line else None, line_no=line_no,
        product_id=product.id, description=f"{MARKER} line {line_no}",
        qty=core_line.qty_ordered if core_line else Decimal("0"), uom="SET",
        unit_price=Decimal("120.00"), amount=Decimal("0"),
        delivery_date=core_line.required_date if core_line else REQUIRED_DATE,
    )
    db.add(line)
    db.flush()
    return line


def _classification(db, product: Product, warehouse: Warehouse, *,
                     abc_class_retail=None, abc_class_project=None):
    from app.models.scm import ItemClassification

    row = ItemClassification(
        id=_uid(), product_id=product.id, warehouse_id=warehouse.id,
        abc_class_retail=abc_class_retail, abc_class_project=abc_class_project,
    )
    db.add(row)
    db.flush()
    return row


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
    UserPermissionService.get_user_permission_slugs = lambda self, uid: [VIEW]
    return TestClient(app), originals


def _restore(originals) -> None:
    from app.main import app
    from app.services.user_service import UserPermissionService

    UserPermissionService.check_user_has_permission = originals[0]
    UserPermissionService.get_user_permission_slugs = originals[1]
    app.dependency_overrides.clear()


class _World:
    def __init__(self, db, company_id, actor, project, product, own_wh, pool_wh):
        self.db = db
        self.company_id = company_id
        self.actor = actor
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
        actor = _user(db, f"{MARKER} Aina")
        project = register_project(
            db, company_id=company_id, actor_user_id=actor, developer_party_id=None,
            title=f"{MARKER} Tuju Residences",
        )
        product = _product(db)
        own_wh = _warehouse(db, f"ZZT-OWN-{_uid()[:4]}", segment="project")
        pool_wh = _warehouse(db, f"ZZT-BRW-{_uid()[:4]}", segment="dealer")
        own_wh.pool_warehouse_id = pool_wh.id
        db.flush()
        db.commit()
        client, originals = _client(db, actor)
        world = _World(db, company_id, actor, project, product, own_wh, pool_wh)
        try:
            with company_scope(db, frozenset({company_id})):
                yield client, world
        finally:
            _restore(originals)


def _line_payload(project_line_id, *, timely_spo_qty="0", reserve=None, borrow=None,
                   buy_qty="0", buy_reason=None, amend_reason=None):
    body = {
        "project_line_id": project_line_id, "timely_spo_qty": timely_spo_qty,
        "reserve": reserve or [], "borrow": borrow or [], "buy_qty": buy_qty,
    }
    if buy_reason is not None:
        body["buy_reason"] = buy_reason
    if amend_reason is not None:
        body["amend_reason"] = amend_reason
    return body


def _place_row_on_a_real_po(db, world, row: OrderInquiryRow, *, qty_ordered):
    """Places `row` through the REAL section-G path (`ProjectOrderInquiryService.place_on_po`)
  - never by hand-setting `row.state`. The 20 Aug regression only reproduces through this
    path: every earlier green test that hand-set `INQUIRY_ACTIONED` never exercised the state
    the live "Place on PO" workflow actually writes (`INQUIRY_PLACED`), which is why 145 live
    placed rows sat invisible to this netting for as long as they did. Returns `(po, po_line)`."""
    supplier = Supplier(
        id=_uid(), company_id=world.company_id, supplier_code=f"ZZT-{_uid()[:8]}",
        supplier_name=f"{MARKER} supplier",
    )
    po = PurchaseOrder(
        id=_uid(), company_id=world.company_id, po_number=f"ZZT-PO-{_uid()[:8]}",
        supplier_id=supplier.id,
    )
    db.add_all([supplier, po])
    db.flush()
    po_line = PurchaseOrderLine(
        id=_uid(), company_id=world.company_id, purchase_order_id=po.id,
        product_id=world.product.id, warehouse_id=world.own_wh.id,
        qty_ordered=Decimal(str(qty_ordered)), qty_received=Decimal("0"), line_status="open",
    )
    db.add(po_line)
    db.commit()
    ProjectOrderInquiryService(db).place_on_po(row.id, po_line.id, actor_user_id=world.actor)
    db.commit()
    return po, po_line


# ============================================================================
# build_batch
# ============================================================================


def _diff_change(kind, core_line, *, doc_number, item_code, location, old_date, new_date,
                  old_qty, new_qty):
    before = Line(doc_number=doc_number, item_code=item_code, location=location,
                  qty=float(old_qty), required_date=old_date, row_ref=str(core_line.id))
    after = None
    if kind != CLOSED:
        after = Line(doc_number=doc_number, item_code=item_code, location=location,
                     qty=float(new_qty), required_date=new_date, row_ref="1")
    return Change(kind, doc_number, item_code, location, before=before, after=after)


def test_build_batch_covers_only_held_lines_and_is_none_when_nothing_held_changed(api):
    """Supersedes the old 'covers only planned/adopted lines' rule
    (`PLAN-scm-planning-change-gate-held-or-inquiry.md`, AC-G1/AC-G4): being adopted onto
    `projects.sales_orders` is no longer enough to keep a changed line in the batch. Only a
    line frozen in the order's ACTIVE decision (or carrying an open inquiry) stays in -
    `held_line` here is confirmed with a full Buy before the diff runs; `undecided_line` is
    adopted the same way but never confirmed, and never makes the batch."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    held_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="72",
                            required_date=date(2026, 8, 20))
    undecided_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10",
                                 required_date=date(2026, 8, 20))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    held_project_line = _project_line(db, order, line_no=1, product=world.product,
                                       core_line=held_line)
    _project_line(db, order, line_no=2, product=world.product, core_line=undecided_line)
    db.commit()
    _confirm(client, order.id, {"lines": [
        _line_payload(held_project_line.id, buy_qty="72", buy_reason="ZZT no stock anywhere"),
    ]})

    changed = _diff_change(
        DATE_MOVED, held_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 20),
        new_date=date(2026, 9, 3), old_qty="72", new_qty="72",
    )
    undecided_changed = _diff_change(
        DATE_MOVED, undecided_line, doc_number=core_so.so_number, item_code="ZZT-ITEM-2",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 20),
        new_date=date(2026, 9, 3), old_qty="10", new_qty="10",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed, undecided_changed])
    applied_line_ids = {id(changed): str(held_line.id), id(undecided_changed): str(undecided_line.id)}
    order_ids = {core_so.so_number: str(core_so.id)}

    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids=applied_line_ids, order_ids=order_ids,
        actor=world.actor, import_job_id=None, file_name="test.xlsx",
    )
    db.commit()
    assert batch is not None
    assert batch.order_count == 1
    assert batch.line_count == 1  # the undecided line is not in the batch

    only_undecided_diff = Diff(scope_documents=(core_so.so_number,), changes=[undecided_changed])
    no_batch = planning_change_service.build_batch(
        db, only_undecided_diff, applied_line_ids=applied_line_ids, order_ids=order_ids,
        actor=world.actor, import_job_id=None, file_name="test.xlsx",
    )
    assert no_batch is None


def test_build_batch_skips_a_date_move_anchored_on_a_null_date(api):
    """PLAN section 10 / the 19 Aug 2026 incident: a date change with no FROM or no TO
    builds no reaction - a first-time date, or one an unreadable cell wiped, is not a
    delay or an advance.

    `real_line` is frozen with a full Buy (`PLAN-scm-planning-change-gate-held-or-inquiry.md`):
    an undecided line raises nothing regardless of the null-anchor rule, so this test's
    "a real move still builds a batch" assertion needs a held line to prove that rule on."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="72",
                            required_date=date(2026, 8, 20))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    null_from = _diff_change(
        DATE_MOVED, core_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=None,
        new_date=date(2026, 9, 3), old_qty="72", new_qty="72",
    )
    no_batch = planning_change_service.build_batch(
        db, Diff(scope_documents=(core_so.so_number,), changes=[null_from]),
        applied_line_ids={id(null_from): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="test.xlsx",
    )
    assert no_batch is None

    null_to = _diff_change(
        DATE_MOVED, core_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 20),
        new_date=None, old_qty="72", new_qty="72",
    )
    no_batch_2 = planning_change_service.build_batch(
        db, Diff(scope_documents=(core_so.so_number,), changes=[null_to]),
        applied_line_ids={id(null_to): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="test.xlsx",
    )
    assert no_batch_2 is None

    # A real move alongside a null-anchored one still builds a batch, with only the real
    # move as a row - the null-anchored one is silently dropped, not merely unclassified.
    real_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10",
                            required_date=date(2026, 8, 20))
    real_project_line = _project_line(db, order, line_no=2, product=world.product,
                                       core_line=real_line)
    db.commit()
    _confirm(client, order.id, {"lines": [
        _line_payload(real_project_line.id, buy_qty="10", buy_reason="ZZT no stock anywhere"),
    ]})
    real_move = _diff_change(
        DATE_MOVED, real_line, doc_number=core_so.so_number, item_code="ZZT-ITEM-2",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 20),
        new_date=date(2026, 9, 3), old_qty="10", new_qty="10",
    )
    batch = planning_change_service.build_batch(
        db, Diff(scope_documents=(core_so.so_number,), changes=[null_from, real_move]),
        applied_line_ids={id(null_from): str(core_line.id), id(real_move): str(real_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="test.xlsx",
    )
    db.commit()
    assert batch is not None
    assert batch.line_count == 1


def test_build_batch_row_shape_has_facts_and_why_for_a_held_line(api):
    """AC-R03 ('no decision, no acceptance offered') is superseded
    (`PLAN-scm-planning-change-gate-held-or-inquiry.md`): a changed line with no decision and
    no inquiry now raises nothing at all, so there is no row left to shape-test on that
    path. This shape-tests a HELD line's row instead, and keeps the gate itself explicit:
    the identical change on a sibling order's undecided line still raises no batch."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="72",
                            required_date=date(2026, 8, 20))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    project_line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()
    _confirm(client, order.id, {"lines": [
        _line_payload(project_line.id, buy_qty="72", buy_reason="ZZT no stock anywhere"),
    ]})

    changed = _diff_change(
        DATE_MOVED, core_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 20),
        new_date=date(2026, 9, 3), old_qty="72", new_qty="72",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    assert batch is not None

    out = planning_change_service.get_batch(db, str(batch.id))
    assert out["source"]["file_name"] == "book.xlsx"
    assert len(out["orders"]) == 1
    row = out["orders"][0]["rows"][0]
    assert row["kind"] == "delayed"
    assert row["held"] is not None  # the row exists BECAUSE the line is held
    assert row["decision"] is None  # never auto-decided (rule 2)
    assert row["suggestion"]["components"], row["suggestion"]
    assert row["facts"]["days_moved"] == 14

    # The gate itself, explicit: the identical change on an undecided line (adopted, never
    # confirmed) on a sibling order raises no batch at all.
    undecided_so = _core_so(db, world.company_id)
    undecided_line = _core_line(db, undecided_so, world.product, world.own_wh, qty_ordered="72",
                                 required_date=date(2026, 8, 20))
    undecided_order = _project_so(db, world.project, so_id=undecided_so.id,
                                   autocount_doc_no=undecided_so.so_number)
    _project_line(db, undecided_order, line_no=1, product=world.product, core_line=undecided_line)
    db.commit()
    undecided_change = _diff_change(
        DATE_MOVED, undecided_line, doc_number=undecided_so.so_number, item_code="ZZT-ITEM-2",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 20),
        new_date=date(2026, 9, 3), old_qty="72", new_qty="72",
    )
    no_batch = planning_change_service.build_batch(
        db, Diff(scope_documents=(undecided_so.so_number,), changes=[undecided_change]),
        applied_line_ids={id(undecided_change): str(undecided_line.id)},
        order_ids={undecided_so.so_number: str(undecided_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    assert no_batch is None


# ============================================================================
# apply()
# ============================================================================


def _confirm(client, order_id, payload):
    response = client.post(f"{BASE}/sales-orders/{order_id}/confirm", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_apply_release_returns_the_whole_line_to_the_board_with_no_buy_and_no_oi_change(api):
    """AC-R06 as corrected 19 Aug 2026 (PLAN section 6) is superseded by Slice C rule 10:
    Release is no longer "the whole line back on the board, nothing bought" - it is the
    inquiry row cancelled/settled and a NEW Buy raised for the new date, the same as any
    other Reduce/Reallocate branch. Measured directly: a wholly-reserved line delayed past
    the window composes [release reserve, buy for the new date]; confirming it raises a
    fresh ORDER row for the Buy (purchasing demand DOES exist now) and a DELAY-verb row at
    the reserve's own pool location documents the released claim - neither the "no OI
    change" nor the "back on the board, no active decision" halves of the old title hold
    any more, so both are replaced with what the code actually does."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=100)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="40",
                            required_date=date(2026, 8, 25))
    order = _project_so(db, world.project, so_id=core_so.id, status=SO_STATUS_PUBLISHED,
                         autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    _confirm(client, order.id, {"lines": [
        _line_payload(line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "40"}]),
    ]})

    new_date = date(2027, 3, 10)
    # Write-first: production always writes the book before building the batch off it.
    core_line.required_date = new_date
    line.delivery_date = new_date
    db.flush()
    changed = _diff_change(
        DATE_MOVED, core_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 25),
        new_date=new_date, old_qty="40", new_qty="40",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    assert batch is not None
    out = planning_change_service.get_batch(db, str(batch.id))
    row = out["orders"][0]["rows"][0]
    components = row["suggestion"]["components"]
    assert any(c["action"] == "release" and c["source"] == "reserve" for c in components), (
        components
    )
    assert any(c["action"] == "buy" for c in components), components

    planning_change_service.set_row_decision(db, str(batch.id), row["id"], "confirm")
    result = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()
    assert result["failed_orders"] == []
    assert result["applied_orders"] == [core_so.so_number]

    db.expire_all()
    order_rows = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id)
        .all()
    )
    live_order_rows = [r for r in order_rows if r.verb == IV_ORDER]
    assert len(live_order_rows) == 1, [
        (r.verb, r.state, str(r.qty)) for r in order_rows
    ]
    assert live_order_rows[0].qty == Decimal("40")
    assert live_order_rows[0].delivery_date == new_date

    from app.models.planning_change import PlanningChangeRow as PlanningChangeRowModel

    row_model = db.query(PlanningChangeRowModel).filter(
        PlanningChangeRowModel.id == row["id"]
    ).one()
    assert row_model.applied_state == "applied"


def test_apply_release_gives_up_a_reserved_lines_whole_claim_and_asks_purchasing_for_nothing(api):
    """Captain, 19 August 2026 (correcting the first cut of AC-R08), superseded again by
    Slice C rule 10: a release is no longer silent on Order Inquiry - it cancels/settles
    the row and raises a fresh Buy for the new date, exactly like the other reaction
    branches. "asks purchasing for nothing" no longer holds once the line is delayed past
    the reserve window: releasing the reserve is a real purchasing demand now, measured
    directly the same way as the sibling test above.

    The line is wholly reserved (AC-L5: a line is met entirely from stock or entirely
    bought), which is also the only shape a release can now find a reserve on."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=150)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="150",
                            required_date=date(2026, 8, 25))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    _confirm(client, order.id, {"lines": [
        _line_payload(line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "150"}]),
    ]})

    # A reserved line asks purchasing for nothing UNTIL it changes - before the delay, it
    # still raises no ORDER row at all.
    assert (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == IV_ORDER)
        .count()
        == 0
    )

    new_date = date(2027, 3, 10)
    # Write-first: production always writes the book before building the batch off it.
    core_line.required_date = new_date
    line.delivery_date = new_date
    db.flush()
    changed = _diff_change(
        DATE_MOVED, core_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 25),
        new_date=new_date, old_qty="150", new_qty="150",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    out = planning_change_service.get_batch(db, str(batch.id))
    row = out["orders"][0]["rows"][0]
    components = row["suggestion"]["components"]
    assert any(c["action"] == "release" and c["source"] == "reserve" for c in components), (
        components
    )
    assert any(c["action"] == "buy" for c in components), components

    planning_change_service.set_row_decision(db, str(batch.id), row["id"], "confirm")
    result = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()
    assert result["failed_orders"] == []

    # The release IS a real purchasing demand now - one live ORDER row for the new date,
    # not zero.
    db.expire_all()
    live_order_rows = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == IV_ORDER,
                OrderInquiryRow.state != INQUIRY_CANCELLED)
        .all()
    )
    assert len(live_order_rows) == 1, [(r.state, str(r.qty)) for r in live_order_rows]
    assert live_order_rows[0].qty == Decimal("150")
    assert live_order_rows[0].delivery_date == new_date


def test_apply_qty_down_reduces_the_row_in_place_and_writes_no_cancel_balance(api):
    """AC-P3-8 (26 August 2026), replacing the CANCEL_BALANCE this test used to pin.

    The drop is not a second instruction: the row purchasing already holds is REDUCED, and
    its own note carries what it was. An exception row beside it said the same thing twice,
    on a screen whose whole rule is one row per sales-order line."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=50)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="66",
                            required_date=date(2027, 1, 15))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    _confirm(client, order.id, {"lines": [
        # Wholly bought (AC-L5), so the 16-unit reduction has only the Buy to come off.
        _line_payload(line.id, buy_qty="66", buy_reason="Nothing free elsewhere."),
    ]})

    core_line.qty_ordered = Decimal("50")
    db.flush()
    changed = _diff_change(
        QTY_CHANGED, core_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2027, 1, 15),
        new_date=date(2027, 1, 15), old_qty="66", new_qty="50",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    out = planning_change_service.get_batch(db, str(batch.id))
    row = out["orders"][0]["rows"][0]
    assert row["kind"] == "qty_down"
    assert row["suggestion"]["components"], row["suggestion"]
    planning_change_service.set_row_decision(db, str(batch.id), row["id"], "confirm")

    result = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()
    assert result["failed_orders"] == []

    from app.models.project_so import SOSupplyDecision

    decision = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id,
                SOSupplyDecision.state == "active")
        .one()
    )
    snapshot = decision.line_snapshots[0]
    buy = [c for c in snapshot["components"] if c["kind"] == "buy"]
    # 16 dropped off the Buy, for the whole 16-unit reduction; the line is still wholly
    # bought, now for 50.
    assert [c["qty"] for c in buy] == ["50"]

    cancel_rows = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == "CANCEL_BALANCE")
        .all()
    )
    assert cancel_rows == [], "the row absorbed the drop; nothing is raised beside it"

    db.expire_all()
    live = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id,
                OrderInquiryRow.state != INQUIRY_CANCELLED)
        .all()
    )
    assert len(live) == 1, "one order inquiry row per sales-order line, always"
    assert live[0].qty == Decimal("50")
    assert "66" in (live[0].note or ""), "and it says what it was"


def test_apply_closed_retires_open_row_and_notes_actioned_row(api):
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=50)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="12",
                            required_date=date(2027, 1, 20))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    _confirm(client, order.id, {"lines": [
        # Wholly bought (AC-L5).
        _line_payload(line.id, buy_qty="12", buy_reason="Timely stock short."),
    ]})

    # The Buy above already raised a row against this order's OWN inquiry
    # (`refresh_for_decision`, inside `confirm()`); action it here as purchasing would,
    # rather than creating a second inquiry (the `amendment_id IS NULL` singleton would
    # refuse it).
    actioned_row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == IV_ORDER)
        .one()
    )
    actioned_row.state = INQUIRY_ACTIONED
    actioned_row.spo_ref = "PO2026-0398"
    db.commit()

    changed = _diff_change(
        CLOSED, core_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2027, 1, 20), new_date=None,
        old_qty="12", new_qty="0",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    out = planning_change_service.get_batch(db, str(batch.id))
    row = out["orders"][0]["rows"][0]
    # Slice A (`PLAN-scm-change-management-one-engine.md`, AC-A1): the row kind is
    # renamed CLOSED -> "cancelled" - outstanding_diff's own CLOSED constant stays "closed",
    # only the planning-change ROW's stored/wire kind changes.
    assert row["kind"] == "cancelled"
    # Every held component release/reallocate, no new sourcing (rule 3).
    assert row["suggestion"]["components"], row["suggestion"]
    # A cancelled line drops every held component: a stock claim releases/reallocates,
    # a raised-not-yet-placed Buy reduces to 0 (measured) - no new sourcing either way.
    assert all(
        c["action"] in ("release", "reallocate", "reduce")
        for c in row["suggestion"]["components"]
    ), row["suggestion"]["components"]

    result = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()
    assert result["failed_orders"] == []

    db.refresh(actioned_row)
    assert actioned_row.state == INQUIRY_ACTIONED  # kept, never retired
    assert "closed" in (actioned_row.note or "").lower()


def test_apply_per_order_failure_does_not_block_the_other_order(api):
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=200)

    core_so_a = _core_so(db, world.company_id)
    core_line_a = _core_line(db, core_so_a, world.product, world.own_wh, qty_ordered="40",
                              required_date=date(2026, 8, 25))
    order_a = _project_so(db, world.project, so_id=core_so_a.id,
                           autocount_doc_no=core_so_a.so_number)
    line_a = _project_line(db, order_a, line_no=1, product=world.product, core_line=core_line_a)

    core_so_b = _core_so(db, world.company_id)
    core_line_b = _core_line(db, core_so_b, world.product, world.own_wh, qty_ordered="30",
                              required_date=date(2026, 8, 25))
    order_b = _project_so(db, world.project, so_id=core_so_b.id,
                           autocount_doc_no=core_so_b.so_number)
    line_b = _project_line(db, order_b, line_no=1, product=world.product, core_line=core_line_b)
    db.commit()

    _confirm(client, order_a.id, {"lines": [
        _line_payload(line_a.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "40"}]),
    ]})
    _confirm(client, order_b.id, {"lines": [
        _line_payload(line_b.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "30"}]),
    ]})

    changed_a = _diff_change(
        DATE_MOVED, core_line_a, doc_number=core_so_a.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 25),
        new_date=date(2027, 3, 10), old_qty="40", new_qty="40",
    )
    changed_b = _diff_change(
        DATE_MOVED, core_line_b, doc_number=core_so_b.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 25),
        new_date=date(2027, 3, 10), old_qty="30", new_qty="30",
    )
    diff = Diff(scope_documents=(core_so_a.so_number, core_so_b.so_number),
                changes=[changed_a, changed_b])
    batch = planning_change_service.build_batch(
        db, diff,
        applied_line_ids={id(changed_a): str(core_line_a.id), id(changed_b): str(core_line_b.id)},
        order_ids={core_so_a.so_number: str(core_so_a.id), core_so_b.so_number: str(core_so_b.id)},
        actor=world.actor, import_job_id=None, file_name="book.xlsx",
    )
    db.commit()

    # Order B is re-planned on the board BETWEEN batch build and Apply - a real revision
    # bump so `apply()`'s own drift path (the same `confirm()` re-validation every other
    # apply goes through) refuses it honestly, without hand-rolling a failure.
    _confirm(client, order_b.id, {"lines": [
        _line_payload(line_b.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "30"}],
                      amend_reason="Re-planned on the board."),
    ]})

    result = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()
    assert core_so_a.so_number in result["applied_orders"]
    failed_numbers = {f["so_number"] for f in result["failed_orders"]}
    # Order A applies; order B's row was superseded, so it applies nothing and is excluded
    # rather than named a failure (AC-R11: superseded, not failed).
    assert core_so_b.so_number not in failed_numbers or core_so_b.so_number in result["applied_orders"]


def test_apply_twice_is_a_noop(api):
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=100)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="40",
                            required_date=date(2026, 8, 25))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()
    _confirm(client, order.id, {"lines": [
        _line_payload(line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "40"}]),
    ]})

    changed = _diff_change(
        DATE_MOVED, core_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 25),
        new_date=date(2027, 3, 10), old_qty="40", new_qty="40",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    # A row nobody decided is no longer applied - confirm it first.
    row_id = planning_change_service.get_batch(db, str(batch.id))["orders"][0]["rows"][0]["id"]
    planning_change_service.set_row_decision(db, str(batch.id), row_id, "confirm")

    first = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()
    assert first["already_applied"] is False

    second = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()
    assert second["already_applied"] is True
    assert second["applied_orders"] == first["applied_orders"]


def test_set_row_decision_refuses_accept_on_a_superseded_row(api):
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=100)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="40",
                            required_date=date(2026, 8, 25))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()
    _confirm(client, order.id, {"lines": [
        _line_payload(line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "40"}]),
    ]})

    changed = _diff_change(
        DATE_MOVED, core_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 25),
        new_date=date(2027, 3, 10), old_qty="40", new_qty="40",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()

    # The board re-plans it before anybody reviews the batch.
    _confirm(client, order.id, {"lines": [
        _line_payload(line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "40"}],
                      amend_reason="Re-planned on the board."),
    ]})

    out = planning_change_service.get_batch(db, str(batch.id))
    row_id = out["orders"][0]["rows"][0]["id"]
    assert out["orders"][0]["rows"][0]["applied_state"] == "superseded"

    # "accept" is not a decision at all now (AC-C7 / rule 5) - 422, invalid decision,
    # regardless of whether the row is superseded.
    with pytest.raises(Exception) as invalid:
        planning_change_service.set_row_decision(db, str(batch.id), row_id, "accept")
    assert getattr(invalid.value, "status_code", None) == 422

    # "confirm" IS a valid decision, and THIS row is superseded - 409.
    with pytest.raises(Exception) as superseded:
        planning_change_service.set_row_decision(db, str(batch.id), row_id, "confirm")
    assert getattr(superseded.value, "status_code", None) == 409


def test_apply_stamps_applied_at_only_when_something_actually_applied(api):
    """PLAN section 10, defect B: a batch every one of whose orders fails must stay
    pending - not `applied_at` stamped with nothing written, which locked the row
    decisions AND a retry both behind the "already applied" gate."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=100)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="40",
                            required_date=date(2026, 8, 25))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()
    _confirm(client, order.id, {"lines": [
        _line_payload(line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "40"}]),
    ]})

    # A 14-day move, within the reserve window: the re-run composes "keep" (AC-C2), so
    # confirming it derives a real composition and reaches `confirm()` - unlike a
    # release/buy row, which needs no composition-derived confirm at all to fail the way
    # this test needs.
    changed = _diff_change(
        DATE_MOVED, core_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 25),
        new_date=date(2026, 9, 8), old_qty="40", new_qty="40",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    out = planning_change_service.get_batch(db, str(batch.id))
    row_id = out["orders"][0]["rows"][0]["id"]
    assert out["orders"][0]["rows"][0]["suggestion"]["components"], out["orders"][0]["rows"][0]
    # A row nobody decided is no longer applied - confirm it first.
    planning_change_service.set_row_decision(db, str(batch.id), row_id, "confirm")

    # A guaranteed, fix-independent failure: the order is no longer confirmable.
    order.status = "draft"
    db.commit()

    result = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()
    assert result["applied_orders"] == []
    assert len(result["failed_orders"]) == 1
    assert result["already_applied"] is False

    from app.models.planning_change import PlanningChangeBatch as PlanningChangeBatchModel

    stored = db.query(PlanningChangeBatchModel).filter(
        PlanningChangeBatchModel.id == batch.id
    ).one()
    assert stored.applied_at is None  # left pending, not falsely marked done

    # Re-apply is not blocked by an "already applied" no-op, and the row decision is
    # still editable - the two consequences PLAN section 10 named.
    again = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()
    assert again["already_applied"] is False

    updated = planning_change_service.set_row_decision(db, str(batch.id), row_id, "confirm")
    assert updated["decision"] == "confirm"


# ============================================================================
# Routes
# ============================================================================


def test_routes_list_get_put_and_apply_happy_path(api):
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=100)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="72",
                            required_date=date(2026, 8, 20))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    _confirm(client, order.id, {"lines": [
        _line_payload(line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "72"}]),
    ]})

    changed = _diff_change(
        DATE_MOVED, core_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 20),
        new_date=date(2026, 9, 3), old_qty="72", new_qty="72",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()

    listing = client.get(f"{BASE}/planning-changes")
    assert listing.status_code == 200, listing.text
    assert any(b["id"] == str(batch.id) for b in listing.json()["data"])

    detail = client.get(f"{BASE}/planning-changes/{batch.id}")
    assert detail.status_code == 200, detail.text
    row = detail.json()["orders"][0]["rows"][0]
    # Slice C: no more reaction verb - the row carries a COMPOSED suggestion instead.
    assert row["suggestion"]["components"], row["suggestion"]
    assert "suggested" not in row, row
    row_id = row["id"]
    # `confirm` derives its own composition from the row's proposal server-side - no body
    # composition needed (`set_row_decision`'s "confirm" branch).
    put = client.put(
        f"{BASE}/planning-changes/{batch.id}/rows/{row_id}", json={"decision": "confirm"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["decision"] == "confirm"

    apply_response = client.post(f"{BASE}/planning-changes/{batch.id}/apply")
    assert apply_response.status_code == 200, apply_response.text
    assert apply_response.json()["already_applied"] is False


def test_routes_denied_without_the_view_permission(api):
    client, world = api
    from app.services.user_service import UserPermissionService

    original = UserPermissionService.check_user_has_permission
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: False
    try:
        response = client.get(f"{BASE}/planning-changes")
        assert response.status_code == 403
    finally:
        UserPermissionService.check_user_has_permission = original


def test_apply_and_put_are_denied_for_a_view_only_principal(api):
    """PUT and Apply take `projects.projects.edit` - the same dependency the board's own
    `confirm` route uses - not the read-only `projects.projects.view` GETs sit on.

    `PLAN-scm-planning-change-gate-held-or-inquiry.md`: the line must be HELD for the
    change to raise a row at all, so it is frozen with a full Buy first."""
    client, world = api
    db = world.db
    from app.services.user_service import UserPermissionService

    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="72",
                            required_date=date(2026, 8, 20))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()
    _confirm(client, order.id, {"lines": [
        _line_payload(line.id, buy_qty="72", buy_reason="ZZT no stock anywhere"),
    ]})

    changed = _diff_change(
        DATE_MOVED, core_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 20),
        new_date=date(2026, 9, 3), old_qty="72", new_qty="72",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    row_id = planning_change_service.get_batch(db, str(batch.id))["orders"][0]["rows"][0]["id"]

    original = UserPermissionService.check_user_has_permission
    # A view-only principal: holds VIEW, but not EDIT.
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: slug == VIEW
    try:
        listing = client.get(f"{BASE}/planning-changes")
        assert listing.status_code == 200

        put = client.put(
            f"{BASE}/planning-changes/{batch.id}/rows/{row_id}", json={"decision": "confirm"},
        )
        assert put.status_code == 403

        apply_response = client.post(f"{BASE}/planning-changes/{batch.id}/apply")
        assert apply_response.status_code == 403
    finally:
        UserPermissionService.check_user_has_permission = original


def test_route_put_rejects_an_unknown_decision_with_422(api):
    """`PLAN-scm-planning-change-gate-held-or-inquiry.md`: the line must be HELD for the
    change to raise a row at all, so it is frozen with a full Buy first."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="72",
                            required_date=date(2026, 8, 20))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()
    _confirm(client, order.id, {"lines": [
        _line_payload(line.id, buy_qty="72", buy_reason="ZZT no stock anywhere"),
    ]})

    changed = _diff_change(
        DATE_MOVED, core_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 20),
        new_date=date(2026, 9, 3), old_qty="72", new_qty="72",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    row_id = planning_change_service.get_batch(db, str(batch.id))["orders"][0]["rows"][0]["id"]

    response = client.put(
        f"{BASE}/planning-changes/{batch.id}/rows/{row_id}", json={"decision": "yolo"},
    )
    assert response.status_code == 422


# ============================================================================
# `confirm` / `amend` (captain, 19 August 2026: "clicking accept here has no effect" on a
# replan row - `accept` recorded a decision Apply never executed for it).
# ============================================================================


def test_composition_from_proposal_reads_the_boards_own_sources():
    proposal = {
        "project_line_id": "line-1",
        "qty": "50",
        "qty_outstanding": "50",
        "qty_proposed_incoming": "0",
        "qty_proposed_reserve": "20",
        "qty_proposed_buy": "30",
        "sources": [
            {"kind": "reserve", "qty": "20", "warehouse_id": "wh-own", "location": "OWN"},
            {"kind": "buy", "qty": "30"},
        ],
    }
    composed = planning_change_service.composition_from_proposal(proposal)
    assert composed["project_line_id"] == "line-1"
    assert composed["reserve"] == [
        {"warehouse_id": "wh-own", "qty": "20", "location": "OWN"}
    ]
    assert composed["borrow"] == []
    assert composed["buy_qty"] == "30"
    assert composed["timely_spo_qty"] == "0"


def test_composition_from_proposal_derives_buy_when_the_server_states_no_figure():
    proposal = {
        "project_line_id": "line-2",
        "qty": "10",
        "qty_outstanding": "5",
        "sources": [],
    }
    composed = planning_change_service.composition_from_proposal(proposal)
    assert composed["buy_qty"] == "10"
    assert composed["reserve"] == []


def test_composition_from_proposal_warns_when_sources_disagree_with_the_aggregate(caplog):
    """Fix 2, the cheap guard: if a future producer bug lets `sources` drift from the
    aggregate `qty_proposed_reserve` (the original defect on SO397450 - the aggregate
    read 0 while `sources` still named a 432 pool draw), the mismatch is LOGGED rather
    than passing in silence. The aggregate still wins - `composition_from_proposal` has
    always trusted it over `sources` - but a warning now names the row."""
    proposal = {
        "project_line_id": "line-9",
        "key": "SO-1|1|ITEM|w1",
        "qty": "50",
        "qty_outstanding": "50",
        "qty_proposed_incoming": "0",
        "qty_proposed_reserve": "0",
        "qty_proposed_buy": "50",
        "sources": [
            {"kind": "reserve", "qty": "40", "warehouse_id": "wh-brw", "location": "BRW"},
        ],
    }
    with caplog.at_level(logging.WARNING, logger="app.services.planning_change_service"):
        composed = planning_change_service.composition_from_proposal(proposal)
    assert composed["reserve"] == []  # the aggregate (0) is still authoritative
    assert composed["buy_qty"] == "50"
    assert any(
        "disagrees with the proposed reserve" in record.message and "SO-1|1|ITEM|w1" in record.message
        for record in caplog.records
    )


def test_composition_from_proposal_no_warning_when_sources_agree_with_the_aggregate(caplog):
    proposal = {
        "project_line_id": "line-9",
        "key": "SO-1|1|ITEM|w1",
        "qty": "40",
        "qty_outstanding": "40",
        "qty_proposed_incoming": "0",
        "qty_proposed_reserve": "40",
        "qty_proposed_buy": "0",
        "sources": [
            {"kind": "reserve", "qty": "40", "warehouse_id": "wh-brw", "location": "BRW"},
        ],
    }
    with caplog.at_level(logging.WARNING, logger="app.services.planning_change_service"):
        planning_change_service.composition_from_proposal(proposal)
    assert not any("disagrees with the proposed reserve" in r.message for r in caplog.records)


# ============================================================================
# `_apply_placed_offset` (pure): the captain's 21 Aug ruling on SO397450 / SRT382-6-DIY.
# ============================================================================


def test_apply_placed_offset_is_a_noop_with_nothing_placed():
    proposal = {"qty_proposed_reserve": "10", "sources": ["sentinel"], "trail": ["sentinel"]}
    out = planning_change_service._apply_placed_offset(proposal, Decimal("0"))
    assert out is proposal


def _held_line_advanced_replan_row(client, world, *, qty="72", days_moved=14):
    """A HELD line (`PLAN-scm-planning-change-gate-held-or-inquiry.md` superseded AC-R03's
    "no decision -> replan": a row now needs a held decision or an inquiry row to exist at
    all) whose date moves EARLIER: `suggest()` returns `replan` unconditionally for
    `advanced`, whatever is held, so `decision` still comes back `None` and the row still
    carries a real board `proposal` (no stock seeded, so the board proposes the whole
    quantity as Buy) - the common case the captain's original fix targets."""
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered=qty,
                            required_date=date(2026, 8, 20))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()
    _confirm(client, order.id, {"lines": [
        _line_payload(line.id, buy_qty=qty, buy_reason="ZZT no stock anywhere"),
    ]})

    new_date = date(2026, 8, 20) - timedelta(days=days_moved)
    # Write-first: production always writes the book before building the batch off it.
    core_line.required_date = new_date
    line.delivery_date = new_date
    db.flush()
    changed = _diff_change(
        DATE_MOVED, core_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 20),
        new_date=new_date, old_qty=qty, new_qty=qty,
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    out = planning_change_service.get_batch(db, str(batch.id))
    row = out["orders"][0]["rows"][0]
    assert row["suggestion"]["components"], row["suggestion"]
    assert row["decision"] is None  # the fix: no longer defaults to a no-op `accept`
    assert row["proposal"] is not None
    return batch, order, line, row


def test_route_put_confirm_composes_from_the_proposal_and_apply_writes_it(api):
    client, world = api
    batch, order, line, row = _held_line_advanced_replan_row(client, world)

    put = client.put(
        f"{BASE}/planning-changes/{batch.id}/rows/{row['id']}", json={"decision": "confirm"},
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["decision"] == "confirm"
    assert body["composition"]["buy_qty"] == "72"  # no stock seeded - the whole line is Buy
    assert body["composition"]["project_line_id"] == line.id

    apply_response = client.post(f"{BASE}/planning-changes/{batch.id}/apply")
    assert apply_response.status_code == 200, apply_response.text
    result = apply_response.json()
    assert result["applied_orders"] == [order.autocount_doc_no]

    from app.models.project_so import SOSupplyDecision

    decision = (
        world.db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id,
                SOSupplyDecision.state == "active")
        .one()
    )
    snapshot = decision.line_snapshots[0]
    buy = [c for c in snapshot["components"] if c["kind"] == "buy"]
    assert buy and Decimal(buy[0]["qty"]) == Decimal("72")

    batch_out = planning_change_service.get_batch(world.db, str(batch.id))
    assert batch_out["result"]["lines_confirmed"] == 1


def test_route_put_amend_requires_a_composition_with_422(api):
    client, world = api
    batch, _order, _line, row = _held_line_advanced_replan_row(client, world)

    response = client.put(
        f"{BASE}/planning-changes/{batch.id}/rows/{row['id']}", json={"decision": "amend"},
    )
    assert response.status_code == 422


def test_route_put_amend_rejects_a_composition_that_does_not_balance_with_422(api):
    client, world = api
    batch, _order, line, row = _held_line_advanced_replan_row(client, world)

    response = client.put(
        f"{BASE}/planning-changes/{batch.id}/rows/{row['id']}",
        json={
            "decision": "amend",
            "composition": _line_payload(line.id, buy_qty="10"),  # line is open for 72
        },
    )
    assert response.status_code == 422


def test_route_put_amend_stores_the_planners_own_composition_and_apply_writes_it(api):
    client, world = api
    _stock(world.db, world.product, world.pool_wh, on_hand=100)
    batch, order, line, row = _held_line_advanced_replan_row(client, world)

    response = client.put(
        f"{BASE}/planning-changes/{batch.id}/rows/{row['id']}",
        json={
            "decision": "amend",
            # Wholly from stock (AC-L5): the planner takes the whole line from the pool
            # rather than the Buy the proposal offered.
            "composition": _line_payload(
                line.id,
                reserve=[{"warehouse_id": world.pool_wh.id, "qty": "72"}],
                amend_reason="The pool has stock the proposal did not use.",
            ),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["composition"]["reserve"] == [
        {
            "warehouse_id": world.pool_wh.id,
            "qty": "72",
            "location": world.pool_wh.warehouse_code,
        }
    ]

    apply_response = client.post(f"{BASE}/planning-changes/{batch.id}/apply")
    assert apply_response.status_code == 200, apply_response.text

    from app.models.project_so import SOSupplyDecision

    decision = (
        world.db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id,
                SOSupplyDecision.state == "active")
        .one()
    )
    snapshot = decision.line_snapshots[0]
    reserve = [c for c in snapshot["components"] if c["kind"] == "reserve"]
    assert reserve and Decimal(reserve[0]["qty"]) == Decimal("72")


# ============================================================================
# (d) PLAN-so-book-diff-replanning.md section 10, defect A, through the real caller.
# ============================================================================


def test_apply_carries_a_kept_lines_own_reserve_past_a_rival_that_moved_in(api):
    """Live reproduction, 19 August 2026: a covered line whose reserve is a Reserve at the
    shared pool moves 90 days out (beyond the window), so the system suggests Release; the
    planner overrides it with "Keep as is" instead. A second, unrelated covered line on the
    same order is accepted normally. Between the batch being built and Apply, something
    else claims the pool dry - exactly what this order's own un-netted hold would show as
    free once the kept line is named again. Apply must still write one new revision: the
    kept line's Reserve carries unchanged, and the accepted line applies alongside it."""
    client, world = api
    db = world.db
    # `date.today()`-relative, not a fixed 2026 calendar date: this test hinges on
    # WINDOW classification (keep vs release), which measures from `as_of` (today), and a
    # fixed base date drifts out of the window boundary as real time passes it.
    BASE_DATE = date.today() + timedelta(days=20)
    _stock(db, world.product, world.pool_wh, on_hand=10)
    core_so = _core_so(db, world.company_id)
    core_line_kept = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10",
                                 required_date=BASE_DATE)
    core_line_accepted = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="5",
                                     required_date=BASE_DATE)
    order = _project_so(db, world.project, so_id=core_so.id, status=SO_STATUS_PUBLISHED,
                         autocount_doc_no=core_so.so_number)
    line_kept = _project_line(db, order, line_no=1, product=world.product, core_line=core_line_kept)
    line_accepted = _project_line(db, order, line_no=2, product=world.product,
                                   core_line=core_line_accepted)
    db.commit()

    _confirm(client, order.id, {"lines": [
        _line_payload(line_kept.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "10"}]),
        _line_payload(line_accepted.id, buy_qty="5", buy_reason="Nothing free elsewhere."),
    ]})

    # A rival claims the pool dry - everything this order's own un-netted Reserve would
    # otherwise show as free once the kept line names it again.
    pool_stock = (
        db.query(Stock)
        .filter(Stock.product_id == world.product.id, Stock.warehouse_id == world.pool_wh.id)
        .one()
    )
    pool_stock.quantity_reserved = 10
    db.commit()

    new_date_kept = BASE_DATE + timedelta(days=90)
    new_date_accepted = BASE_DATE + timedelta(days=14)
    # Write-first: production always writes the book before building the batch off it.
    core_line_kept.required_date = new_date_kept
    line_kept.delivery_date = new_date_kept
    core_line_accepted.required_date = new_date_accepted
    line_accepted.delivery_date = new_date_accepted
    db.flush()
    changed_kept = _diff_change(
        DATE_MOVED, core_line_kept, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=BASE_DATE,
        new_date=new_date_kept, old_qty="10", new_qty="10",
    )
    changed_accepted = _diff_change(
        DATE_MOVED, core_line_accepted, doc_number=core_so.so_number, item_code="ZZT-ITEM-2",
        location=world.own_wh.warehouse_code, old_date=BASE_DATE,
        new_date=new_date_accepted, old_qty="5", new_qty="5",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed_kept, changed_accepted])
    batch = planning_change_service.build_batch(
        db, diff,
        applied_line_ids={
            id(changed_kept): str(core_line_kept.id),
            id(changed_accepted): str(core_line_accepted.id),
        },
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    out = planning_change_service.get_batch(db, str(batch.id))
    rows = {row["line_no"]: row for row in out["orders"][0]["rows"]}
    # 90 days out, beyond the reserve window: composes release (+ buy).
    assert any(c["action"] == "release" for c in rows[1]["suggestion"]["components"])
    # 14 days, within window, Buy only: composes keep.
    assert all(c["action"] == "keep" for c in rows[2]["suggestion"]["components"])

    # The planner overrides the system's Release suggestion and keeps line 1 as is - an
    # amend with the row's own held composition, unchanged; line 2 is confirmed on the
    # system's own suggestion.
    held_composition = dict(rows[1]["held"])
    held_composition["project_line_id"] = line_kept.id
    planning_change_service.set_row_decision(
        db, str(batch.id), rows[1]["id"], "amend", held_composition,
    )
    planning_change_service.set_row_decision(db, str(batch.id), rows[2]["id"], "confirm")
    db.commit()

    result = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()
    assert result["failed_orders"] == [], result["failed_orders"]
    assert result["applied_orders"] == [core_so.so_number]

    from app.models.project_so import SOLineAllocation, SOSupplyDecision

    decision = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id, SOSupplyDecision.state == "active")
        .one()
    )
    assert decision.revision_no == 2  # a new revision was actually written, not skipped
    allocations = {
        a.so_line_id: a
        for a in db.query(SOLineAllocation).filter(SOLineAllocation.decision_id == decision.id).all()
    }
    assert allocations[line_kept.id].qty == Decimal("10")
    assert str(allocations[line_kept.id].warehouse_id) == world.pool_wh.id


def test_apply_uncovers_a_replan_line_instead_of_letting_confirm_carry_it_forward(api):
    """Live reproduction, 19 August 2026: `_apply_one_order` deliberately leaves a
    `replan`/`release`/`retire` row OUT of the body it posts to `ProjectSupplyService
    .confirm()` so the line returns to the board undecided - but `confirm()`'s own "union
    is the server's" rule (PLAN 13.4) carries any covered line the body does not name
    forward VERBATIM, so the instant another line on the SAME order IS named, the excluded
    one rode along uninvited with its stale composition (seen live: SO403765 rev 5 kept
    line 12's old Buy and old date after an ADVANCE had been raised for it). The un-decide
    seam (`confirm(..., uncover_line_ids=...)`) is what `_apply_one_order` now uses to name
    it instead of merely omitting it."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=100)
    core_so = _core_so(db, world.company_id)
    core_line_a = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="40",
                              required_date=date(2026, 8, 25))
    core_line_b = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="21",
                              required_date=date(2026, 8, 25))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line_a = _project_line(db, order, line_no=1, product=world.product, core_line=core_line_a)
    line_b = _project_line(db, order, line_no=2, product=world.product, core_line=core_line_b)
    db.commit()

    _confirm(client, order.id, {"lines": [
        _line_payload(line_a.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "40"}]),
        _line_payload(line_b.id, buy_qty="21", buy_reason="Nothing free elsewhere."),
    ]})
    buy_row_b = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line_b.id, OrderInquiryRow.verb == IV_ORDER)
        .one()
    )

    new_date_a = date(2026, 8, 25) + timedelta(days=14)
    new_date_b = date(2026, 8, 25) - timedelta(days=14)
    # Write-first: production always writes the book before building the batch off it.
    core_line_a.required_date = new_date_a
    line_a.delivery_date = new_date_a
    core_line_b.required_date = new_date_b
    line_b.delivery_date = new_date_b
    db.flush()
    changed_a = _diff_change(
        DATE_MOVED, core_line_a, doc_number=core_so.so_number, item_code="ZZT-ITEM-A",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 25),
        new_date=new_date_a, old_qty="40", new_qty="40",
    )
    changed_b = _diff_change(
        DATE_MOVED, core_line_b, doc_number=core_so.so_number, item_code="ZZT-ITEM-B",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 25),
        new_date=new_date_b, old_qty="21", new_qty="21",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed_a, changed_b])
    batch = planning_change_service.build_batch(
        db, diff,
        applied_line_ids={
            id(changed_a): str(core_line_a.id), id(changed_b): str(core_line_b.id),
        },
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    out = planning_change_service.get_batch(db, str(batch.id))
    rows = {row["line_no"]: row for row in out["orders"][0]["rows"]}
    assert all(c["action"] == "keep" for c in rows[1]["suggestion"]["components"])  # reserve held

    # Line 1 is confirmed; line 2 is left with NO decision at all - Slice C's own version
    # of the shape that used to carry a line forward wrongly: a decision-less row is
    # simply not applied (rule 4), so this is now the ordinary "uncovered" case, not a
    # special "accepted but excluded" one (`accept` is retired, and there is no longer a
    # `suggested` value that both accepts a row AND excludes it from the confirm body).
    planning_change_service.set_row_decision(db, str(batch.id), rows[1]["id"], "confirm")
    db.commit()

    result = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()
    assert result["failed_orders"] == [], result["failed_orders"]
    assert result["applied_orders"] == [core_so.so_number]

    from app.models.project_so import SOSupplyDecision

    decision = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id,
                SOSupplyDecision.state == "active")
        .one()
    )
    snapshot_line_ids = {s.get("project_line_id") for s in decision.line_snapshots}
    assert str(line_a.id) in snapshot_line_ids  # accepted normally: revised, still covered
    assert str(line_b.id) not in snapshot_line_ids  # replan: dropped, not carried forward

    from app.services.project_supply_service import ProjectSupplyService

    supply = ProjectSupplyService(db)
    frozen = supply.frozen_lines_of(supply.active_decision(str(order.id)))
    assert str(line_b.id) not in frozen

    # SO422492 BUG A: the line leaves the PLAN (above), but its raised Buy row stands - its
    # AutoCount change is still undecided and the book still needs the 21. Cancelling it
    # told purchasing CANCEL ALL; CS's later decision restates the row instead.
    db.refresh(buy_row_b)
    assert buy_row_b.state != INQUIRY_CANCELLED
    assert Decimal(str(buy_row_b.qty)) == Decimal("21")


@pytest.mark.usefixtures("ladder_steps_on")
def test_build_batch_proposal_for_a_covered_replan_row_is_the_boards_full_contribution(api):
    """Captain, 19 August 2026: the trail popover on an `advanced` row showed nothing - the
    row's `proposal_json` had one source and `trail: []`. Root cause: the ACTIVE decision
    still covers the line when `build_batch` builds its proposal (Apply is what excludes it,
    and Apply has not run yet), so a plain board build found it `covered` and handed back the
    FROZEN composition (`_apply_frozen`: one source, no trail, no `rank_factors`) instead of
    running the ladder fresh. `_proposal_for` now previews this one line as uncovered
    (`FulfilmentBoardService.build(..., exclude_covered_line_ids=[project_line_id])`), so the
    proposal is the board's own full `BoardContribution` - sources, a 7-rung trail,
    `rank_factors`, and `item_flags` - the same shape `BoardTrailPopover` / `BoardAmendDialog`
    already render off the live board."""
    client, world = api
    db = world.db
    # 1000, not 500: ladder v8 lends a project HALF the pool (R-B), and these cases are
    # about a fresh proposal CITING the pool rather than about the size of the share.
    _stock(db, world.product, world.pool_wh, on_hand=1000)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="432",
                            required_date=date(2026, 8, 25))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    # Covered today by a plain Buy - the "Buy 432" the captain read with nothing under it.
    _confirm(client, order.id, {"lines": [
        _line_payload(line.id, buy_qty="432", buy_reason="Nothing free elsewhere."),
    ]})

    changed = _diff_change(
        DATE_MOVED, core_line, doc_number=core_so.so_number, item_code="ZZT-ADV",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 25),
        new_date=date(2026, 8, 25) - timedelta(days=14), old_qty="432", new_qty="432",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff,
        applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    out = planning_change_service.get_batch(db, str(batch.id))
    row = out["orders"][0]["rows"][0]
    assert row["suggestion"]["components"], row["suggestion"]  # advanced - runs the ladder

    proposal = row["proposal"]
    assert proposal is not None
    # LADDER V5 (section 1e): the four questions plus Buy. The read-only own-location
    # strip is folded into question 1 - it existed to name the queue, which is one of that
    # question's facts - and incoming is not a question at all, because an SPO is inside
    # the ownership group's own net.
    assert [step["step"] for step in proposal["trail"]] == [1, 2, 3, 4, 5]
    # LADDER V8 (R-A): the site pool leads the walk, and the proof follows it.
    assert [step["kind"] for step in proposal["trail"]] == [
        "pool", "own", "order_borrow", "supply_borrow", "buy",
    ]
    assert proposal["rank_factors"]
    assert proposal["sources"]
    assert proposal["item_flags"] is not None
    # Now free at its own location, so the fresh ladder proposes a Reserve rather than
    # repeating the stale frozen Buy - the tell that the ladder was actually walked.
    assert Decimal(proposal["qty_proposed_reserve"]) == Decimal("432")


def test_apply_confirms_only_the_batchs_own_line_leaving_an_unconfirmable_sibling_carried(api):
    """Live reproduction, SO391698 rev 2, 20 August 2026: a batch with ONE row (line 39, a
    `qty_up` 12 -> 14) failed Apply with "9 lines cannot be confirmed. Nothing was written."
  - `_apply_one_order` renamed EVERY line the active revision covered from its frozen
    snapshot (`_confirm_payload`) into the confirm body, even the 39 this batch never
    touched, so the whole order was re-validated against live facts on an apply that decided
    one line. `confirm()` already carries an UNNAMED covered line forward verbatim (PLAN
    13.4, "the union is the server's") - the fix is to stop naming a bystander line at all
    and let that carry-forward do its job, so a sibling that would refuse if re-validated is
    never asked.

    This test reproduces the same `_apply_one_order` code path with an `advanced` row rather
    than `qty_up` - `challenge_if_drifted` (PLAN 5.3, unrelated to this bug) always
    supersedes the WHOLE order's active decision the moment a covered line's own live open
    quantity has already moved off what was frozen, which a `qty_up` row's own composition
    needs live to total correctly; that would drop every OTHER line to undecided AFTER this
    fix, confounding the assertion below (a challenged revision carries nothing, module
    docstring). It does NOT drop them regardless of this fix: pre-fix, `_apply_one_order`
    renamed every covered line explicitly from its own frozen snapshot, so a clean sibling
    was preserved in the new revision whatever the target line's own drift did - the carry-
    forward path that a challenge starves was never in use. An `advanced` row exercises the
    identical bystander-renaming bug (any `suggested='replan'` + `decision='confirm'` row
    takes the same branch in `_apply_one_order`) without that unrelated confound, since
    neither its own nor the sibling's frozen facts move in the DB. See
    `test_apply_returns_a_dropped_bystander_in_returned_to_review` below for the drifted
    case this one deliberately avoids."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=8)
    core_so = _core_so(db, world.company_id)
    core_line_target = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="12",
                                   required_date=date(2026, 9, 4))
    core_line_sibling = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="8",
                                    required_date=date(2026, 9, 4))
    order = _project_so(db, world.project, so_id=core_so.id, status=SO_STATUS_PUBLISHED,
                         autocount_doc_no=core_so.so_number)
    line_target = _project_line(db, order, line_no=1, product=world.product,
                                 core_line=core_line_target)
    line_sibling = _project_line(db, order, line_no=2, product=world.product,
                                  core_line=core_line_sibling)
    db.commit()

    _confirm(client, order.id, {"lines": [
        _line_payload(line_target.id, buy_qty="12", buy_reason="Nothing free elsewhere."),
        _line_payload(line_sibling.id,
                      reserve=[{"warehouse_id": world.pool_wh.id, "qty": "8"}]),
    ]})

    # Between this confirm and the planning-change batch, the sibling's pool is retired -
    # if it were re-named and re-validated, `_check_line` would refuse it (the location is
    # no longer in `_reserve_ladder_locations`). It is never touched by this batch, so it
    # must never be asked.
    world.pool_wh.is_active = False
    db.commit()

    changed_target = _diff_change(
        DATE_MOVED, core_line_target, doc_number=core_so.so_number, item_code="ZZT-TARGET",
        location=world.own_wh.warehouse_code, old_date=date(2026, 9, 4),
        new_date=date(2026, 9, 4) - timedelta(days=14), old_qty="12", new_qty="12",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed_target])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed_target): str(core_line_target.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    out = planning_change_service.get_batch(db, str(batch.id))
    assert len(out["orders"][0]["rows"]) == 1  # the sibling never appears in this batch
    row = out["orders"][0]["rows"][0]
    assert row["kind"] == "advanced"
    assert row["suggestion"]["components"], row["suggestion"]

    planning_change_service.set_row_decision(db, str(batch.id), row["id"], "confirm")
    db.commit()

    result = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()
    assert result["failed_orders"] == [], result["failed_orders"]
    assert result["applied_orders"] == [core_so.so_number]

    from app.models.planning_change import PlanningChangeRow as PlanningChangeRowModel
    from app.models.project_so import SOSupplyDecision

    row_model = db.query(PlanningChangeRowModel).filter(
        PlanningChangeRowModel.id == row["id"]
    ).one()
    assert row_model.applied_state == "applied"

    decision = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id,
                SOSupplyDecision.state == "active")
        .one()
    )
    assert decision.revision_no == 2  # a new revision was written
    snapshots = {s["project_line_id"]: s for s in decision.line_snapshots}
    assert str(line_target.id) in snapshots
    assert str(line_sibling.id) in snapshots  # carried forward, not dropped

    # The sibling's hold is untouched: same warehouse, same qty, even though that
    # warehouse is no longer a valid Reserve source - proof it was carried, not re-posed.
    sibling_components = snapshots[str(line_sibling.id)]["components"]
    sibling_reserve = [c for c in sibling_components if c["kind"] == "reserve"]
    assert len(sibling_reserve) == 1
    assert sibling_reserve[0]["source_warehouse_id"] == str(world.pool_wh.id)
    assert sibling_reserve[0]["qty"] == "8"

    # The target line's own composition was actually re-decided (its pool is retired too,
    # so the fresh ladder still lands on Buy 12) and its OI row reflects that decision.
    target_components = snapshots[str(line_target.id)]["components"]
    target_buy = sum(Decimal(c["qty"]) for c in target_components if c["kind"] == "buy")
    assert target_buy == Decimal("12")

    order_row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line_target.id, OrderInquiryRow.verb == IV_ORDER,
                OrderInquiryRow.state != INQUIRY_CANCELLED)
        .one()
    )
    assert order_row.qty == Decimal("12")


def test_apply_returns_a_dropped_bystander_in_returned_to_review(api):
    """B1 (code review, 20 Aug 2026) is superseded by Slice E's one signal (rule 9) plus
    the coder's own b40eafce0 rule: "a line the batch changed and nobody decided is
    uncovered, not carried forward; a true bystander is carried". `challenge_if_drifted`
    (and the whole-order-undecided side effect it used to trigger) is retired - measured
    directly rather than assumed: confirming the batch's own qty_up row now writes a new
    revision (2) that STILL covers the unrelated sibling line the batch never named - the
    old revision reads SUPERSEDED, the sibling is carried into the new one (present in
    `frozen_lines_of`, its Buy re-raised at its original qty rather than left uncovered),
    and nothing is silently dropped, so `returned_to_review` is empty."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_line_target = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="12",
                                   required_date=date(2026, 9, 4))
    core_line_sibling = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="8",
                                    required_date=date(2026, 9, 4))
    order = _project_so(db, world.project, so_id=core_so.id, status=SO_STATUS_PUBLISHED,
                         autocount_doc_no=core_so.so_number)
    line_target = _project_line(db, order, line_no=1, product=world.product,
                                 core_line=core_line_target)
    line_sibling = _project_line(db, order, line_no=2, product=world.product,
                                  core_line=core_line_sibling)
    db.commit()

    _confirm(client, order.id, {"lines": [
        _line_payload(line_target.id, buy_qty="12", buy_reason="Nothing free elsewhere."),
        _line_payload(line_sibling.id, buy_qty="8", buy_reason="Nothing free elsewhere."),
    ]})
    buy_row_sibling = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line_sibling.id, OrderInquiryRow.verb == IV_ORDER)
        .one()
    )

    # The book upload that produced this diff already wrote the new quantity to the core
    # line (the real `outstanding_import_service.apply()` order of operations) - the batch
    # is built AFTER that write, so its own proposal already walks the ladder for 14.
    core_line_target.qty_ordered = Decimal("14")
    db.flush()
    changed_target = _diff_change(
        QTY_CHANGED, core_line_target, doc_number=core_so.so_number, item_code="ZZT-TARGET",
        location=world.own_wh.warehouse_code, old_date=date(2026, 9, 4),
        new_date=date(2026, 9, 4), old_qty="12", new_qty="14",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed_target])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed_target): str(core_line_target.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    out = planning_change_service.get_batch(db, str(batch.id))
    assert len(out["orders"][0]["rows"]) == 1  # the sibling never appears in this batch
    row = out["orders"][0]["rows"][0]
    assert row["kind"] == "qty_up"
    assert row["suggestion"]["components"], row["suggestion"]

    planning_change_service.set_row_decision(db, str(batch.id), row["id"], "confirm")
    db.commit()

    result = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()
    assert result["failed_orders"] == [], result["failed_orders"]
    assert result["applied_orders"] == [core_so.so_number]

    # Nothing is silently dropped any more - the sibling is carried, not returned.
    assert result["returned_to_review"] == [], result["returned_to_review"]

    from app.models.project_so import SOSupplyDecision

    old_decision_id = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id,
                SOSupplyDecision.revision_no == 1)
        .one()
        .id
    )
    db.expire_all()
    old_decision = db.get(SOSupplyDecision, old_decision_id)
    assert old_decision.state == "superseded", old_decision.state

    decision = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id,
                SOSupplyDecision.state == "active")
        .one()
    )
    assert decision.revision_no == 2  # a new revision was written
    snapshots = {s["project_line_id"]: s for s in decision.line_snapshots}
    # The batch's own line is confirmed at the NEW quantity...
    assert str(line_target.id) in snapshots
    target_components = snapshots[str(line_target.id)]["components"]
    target_buy = sum(Decimal(c["qty"]) for c in target_components if c["kind"] == "buy")
    assert target_buy == Decimal("14")
    # ...and the untouched sibling is CARRIED into the new revision too (one signal, rule
    # 9 / b40eafce0): a true bystander the batch never named is not dropped.
    assert str(line_sibling.id) in snapshots

    from app.services.project_supply_service import ProjectSupplyService

    supply = ProjectSupplyService(db)
    frozen = supply.frozen_lines_of(supply.active_decision(str(order.id)))
    assert str(line_sibling.id) in frozen

    # Its Buy is re-raised at its original qty rather than left uncovered - the ORIGINAL
    # row is cancelled ("Superseded by revision 2") and a fresh one takes its place, the
    # same cancel-and-reraise every covered line not itself confirmed gets on a new
    # revision; what matters is a LIVE row still covers it.
    db.refresh(buy_row_sibling)
    assert buy_row_sibling.state == INQUIRY_CANCELLED
    live_sibling_rows = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line_sibling.id,
                OrderInquiryRow.state != INQUIRY_CANCELLED)
        .all()
    )
    assert len(live_sibling_rows) == 1, [(r.state, str(r.qty)) for r in live_sibling_rows]
    assert live_sibling_rows[0].qty == Decimal("8")


def test_apply_of_an_already_challenged_revision_still_reports_its_bystanders(api):
    """S4 (fix-cluster, 20 Aug 2026): the headline case the report was built for, and the one
    the first version of the fix could not actually reach. `_apply_one_order` used to read the
    ORDER's `active_decision` as "the previous revision" - but a revision a drift challenge
    already flipped to CHALLENGED (the state a GET on the supply page, or an earlier apply,
    leaves behind) is not `active_decision` any more, so `previous_decision` read None and the
    whole report short-circuited to []. This is the same drift as the test above, except the
    challenge has ALREADY happened by the time Apply runs - `active_decision` is None from the
    first line of `_apply_one_order`, not merely mutated mid-flight."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_line_target = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="12",
                                   required_date=date(2026, 9, 4))
    core_line_sibling = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="8",
                                    required_date=date(2026, 9, 4))
    order = _project_so(db, world.project, so_id=core_so.id, status=SO_STATUS_PUBLISHED,
                         autocount_doc_no=core_so.so_number)
    line_target = _project_line(db, order, line_no=1, product=world.product,
                                 core_line=core_line_target)
    line_sibling = _project_line(db, order, line_no=2, product=world.product,
                                  core_line=core_line_sibling)
    db.commit()

    _confirm(client, order.id, {"lines": [
        _line_payload(line_target.id, buy_qty="12", buy_reason="Nothing free elsewhere."),
        _line_payload(line_sibling.id, buy_qty="8", buy_reason="Nothing free elsewhere."),
    ]})

    core_line_target.qty_ordered = Decimal("14")
    db.flush()

    # `challenge_if_drifted` is retired (Slice E, one signal - `4f012b34b`): the legacy
    # state it used to write is seeded directly instead - a revision already CHALLENGED
    # before Apply runs, exactly what a GET on the supply page or an earlier apply leaves
    # behind, days before a book upload is even imported.
    from app.models.project_so import DECISION_CHALLENGED, SOSupplyDecision
    from datetime import datetime as _datetime

    expected_reason = f"{MARKER} qty drift on the target line, ZZT-TARGET 12 -> 14"
    challenged = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .one()
    )
    challenged.state = DECISION_CHALLENGED
    challenged.superseded_at = _datetime.utcnow()
    challenged.superseded_reason = expected_reason
    db.commit()

    assert challenged.state == "challenged"  # confirmed: apply enters with NO active decision

    changed_target = _diff_change(
        QTY_CHANGED, core_line_target, doc_number=core_so.so_number, item_code="ZZT-TARGET",
        location=world.own_wh.warehouse_code, old_date=date(2026, 9, 4),
        new_date=date(2026, 9, 4), old_qty="12", new_qty="14",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed_target])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed_target): str(core_line_target.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    out = planning_change_service.get_batch(db, str(batch.id))
    row = out["orders"][0]["rows"][0]
    planning_change_service.set_row_decision(db, str(batch.id), row["id"], "confirm")
    db.commit()

    result = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()
    assert result["failed_orders"] == [], result["failed_orders"]
    assert result["applied_orders"] == [core_so.so_number]

    # The fix: the report fires even though the order entered Apply with no ACTIVE decision
    # at all, only a CHALLENGED one.
    assert len(result["returned_to_review"]) == 1
    returned = result["returned_to_review"][0]
    assert returned["so_number"] == core_so.so_number
    assert returned["line_nos"] == [2]
    assert returned["line_count"] == len(returned["line_nos"])
    # The genuine drift reason travels through, not the generic "Reconfirmed by CS." caption
    # `_write_decision` stamps on an ACTIVE row it supersedes (this row was never ACTIVE
    # again once challenged, so that caption is never written here at all).
    assert returned["reason"] == expected_reason
    assert returned["reason"] != "Reconfirmed by CS."


def test_apply_of_a_target_line_whose_pool_was_claimed_applies_holding_nothing_and_records_the_short(api):
    """The fix above stops a BYSTANDER line from being re-validated - it must not also stop
    the line the batch actually decided from being checked. A target line whose Reserve pool
    was claimed by a rival between the decision and Apply is still re-read against live
    facts: it applies as decided, holds nothing (0 free) and the active decision records the
    5 short on that line.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=5)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10",
                            required_date=date(2026, 9, 4))
    order = _project_so(db, world.project, so_id=core_so.id, status=SO_STATUS_PUBLISHED,
                         autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    _confirm(client, order.id, {"lines": [
        _line_payload(line.id, buy_qty="10", buy_reason="Nothing free elsewhere."),
    ]})

    core_line.qty_ordered = Decimal("15")
    db.flush()
    changed = _diff_change(
        QTY_CHANGED, core_line, doc_number=core_so.so_number, item_code="ZZT-TARGET",
        location=world.own_wh.warehouse_code, old_date=date(2026, 9, 4),
        new_date=date(2026, 9, 4), old_qty="10", new_qty="15",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    out = planning_change_service.get_batch(db, str(batch.id))
    row = out["orders"][0]["rows"][0]
    assert row["suggestion"]["components"], row["suggestion"]

    # A hand-amended composition: the extra 5 comes off the pool, 10 stays Buy - legal
    # shape at PUT time (totals to the 15 the line is open for).
    planning_change_service.set_row_decision(
        db, str(batch.id), row["id"], "amend",
        composition={
            "project_line_id": str(line.id),
            "timely_spo_qty": "0",
            "reserve": [{"warehouse_id": world.pool_wh.id, "qty": "5"}],
            "borrow": [],
            "buy_qty": "10",
            "buy_reason": "Nothing free elsewhere.",
            "amend_reason": "The extra 5 comes off the pool.",
        },
    )
    db.commit()

    # A rival claims the pool dry before Apply runs.
    pool_stock = (
        db.query(Stock)
        .filter(Stock.product_id == world.product.id, Stock.warehouse_id == world.pool_wh.id)
        .one()
    )
    pool_stock.quantity_reserved = pool_stock.quantity_on_hand
    db.commit()

    result = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()
    assert result["failed_orders"] == [], result["failed_orders"]
    assert result["applied_orders"] == [core_so.so_number]

    from app.models.planning_change import PlanningChangeRow as PlanningChangeRowModel
    from app.models.project_so import SOLineAllocation, SOSupplyDecision

    row_model = db.query(PlanningChangeRowModel).filter(
        PlanningChangeRowModel.id == row["id"]
    ).one()
    assert row_model.applied_state == "applied", row_model.applied_reason

    # The new active revision carries the shortfall on THIS line, with the right figure,
    # and no unit of the claimed pool is held for it.
    decision = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id,
                SOSupplyDecision.state == "active")
        .one()
    )
    assert decision.revision_no == 2
    snap = next(s for s in decision.line_snapshots if s["project_line_id"] == str(line.id))
    assert [Decimal(str(entry["short"])) for entry in snap["stock_short"]] == [Decimal("5")]
    held_at_pool = (
        db.query(SOLineAllocation)
        .filter(SOLineAllocation.decision_id == decision.id,
                SOLineAllocation.warehouse_id == world.pool_wh.id)
        .all()
    )
    assert sum((a.qty for a in held_at_pool), Decimal("0")) == Decimal("0")


def test_apply_of_a_target_line_whose_components_no_longer_add_up_still_fails_with_its_reason(api):
    """The structural half of the check above: Apply is never held back, so a target line
    whose hand-amended components no longer add up to what the line is open for (the line
    shrank between the decision and Apply) still fails the order with its reason, and
    nothing is written for it. A stock shortfall no longer does this; arithmetic still does.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=5)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10",
                            required_date=date(2026, 9, 4))
    order = _project_so(db, world.project, so_id=core_so.id, status=SO_STATUS_PUBLISHED,
                         autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    _confirm(client, order.id, {"lines": [
        _line_payload(line.id, buy_qty="10", buy_reason="Nothing free elsewhere."),
    ]})

    core_line.qty_ordered = Decimal("15")
    db.flush()
    changed = _diff_change(
        QTY_CHANGED, core_line, doc_number=core_so.so_number, item_code="ZZT-TARGET",
        location=world.own_wh.warehouse_code, old_date=date(2026, 9, 4),
        new_date=date(2026, 9, 4), old_qty="10", new_qty="15",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    out = planning_change_service.get_batch(db, str(batch.id))
    row = out["orders"][0]["rows"][0]
    assert row["suggestion"]["components"], row["suggestion"]

    # A hand-amended composition: the extra 5 comes off the pool, 10 stays Buy - legal
    # shape at PUT time (totals to the 15 the line is open for).
    planning_change_service.set_row_decision(
        db, str(batch.id), row["id"], "amend",
        composition={
            "project_line_id": str(line.id),
            "timely_spo_qty": "0",
            "reserve": [{"warehouse_id": world.pool_wh.id, "qty": "5"}],
            "borrow": [],
            "buy_qty": "10",
            "buy_reason": "Nothing free elsewhere.",
            "amend_reason": "The extra 5 comes off the pool.",
        },
    )
    db.commit()

    # A rival claims the pool dry before Apply runs.
    pool_stock = (
        db.query(Stock)
        .filter(Stock.product_id == world.product.id, Stock.warehouse_id == world.pool_wh.id)
        .one()
    )
    pool_stock.quantity_reserved = pool_stock.quantity_on_hand
    db.commit()

    # The line shrinks back to 12: reserve 5 + buy 10 is now 15 against an open qty of 12.
    core_line.qty_ordered = Decimal("12")
    db.commit()

    result = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()
    assert result["applied_orders"] == []
    assert len(result["failed_orders"]) == 1
    assert result["failed_orders"][0]["so_number"] == core_so.so_number
    assert "cannot be confirmed" in result["failed_orders"][0]["reason"]

    from app.models.planning_change import PlanningChangeRow as PlanningChangeRowModel
    from app.models.project_so import SOSupplyDecision

    row_model = db.query(PlanningChangeRowModel).filter(
        PlanningChangeRowModel.id == row["id"]
    ).one()
    assert row_model.applied_state == "failed"
    assert "cannot be confirmed" in row_model.applied_reason

    # Nothing was written: the order's active decision is still the original revision.
    decision = (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id,
                SOSupplyDecision.state == "active")
        .one()
    )
    assert decision.revision_no == 1


# ============================================================================
# The 20 Aug placed/actioned state-vocabulary regression (the captain, live-testing
# section G). Every fixture below places its row through the REAL section-G path
# (`_place_row_on_a_real_po`), never by hand-setting `INQUIRY_ACTIONED` - that hand-set
# shortcut is exactly what let this regression through the existing suite for a day.
# ============================================================================


def test_apply_qty_up_after_a_real_place_on_po_updates_the_one_row_and_keeps_its_link(api):
    """Case A (the captain, 20 Aug): SO349754 WESERP10B - a line already covered by an
    active decision, placed 5 on a real PO, then the book raised the qty to 10. Before the
    fix, the netting predicate only recognised `INQUIRY_ACTIONED` (0 rows company-wide;
    "Place on PO" writes `INQUIRY_PLACED`), so a reconfirm re-raised the FULL new need on
    top of the untouched placed row - 15 against a 10 line.

    Since AC-P3-5 the answer is ONE row, not two: the line's own row is updated in place
    to the 10 the book says, its 5 stays on the purchase order it was placed on, and the
    row reads partly linked. The name used to promise a freshly-raised delta row, which is
    the shape this test now exists to say does NOT happen."""
    client, world = api
    db = world.db
    # No pool stock: the board proposes the whole line as Buy, isolating this case from
    # the Reserve-relabelling fix (Case B, covered separately below).
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="5",
                            required_date=date(2027, 2, 1))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    _confirm(client, order.id, {"lines": [
        _line_payload(line.id, buy_qty="5", buy_reason="Nothing free elsewhere."),
    ]})
    placed_row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == IV_ORDER)
        .one()
    )
    po, _po_line = _place_row_on_a_real_po(db, world, placed_row, qty_ordered="5")
    db.expire_all()
    placed_row = db.get(OrderInquiryRow, placed_row.id)
    assert placed_row.state == INQUIRY_PLACED
    assert placed_row.po_ref == po.po_number

    core_line.qty_ordered = Decimal("10")
    db.commit()
    changed = _diff_change(
        QTY_CHANGED, core_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2027, 2, 1),
        new_date=date(2027, 2, 1), old_qty="5", new_qty="10",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    out = planning_change_service.get_batch(db, str(batch.id))
    row = out["orders"][0]["rows"][0]
    assert row["kind"] == "qty_up"
    assert row["suggestion"]["components"], row["suggestion"]
    assert row["proposal"]["qty_proposed_buy"] == "10"  # no stock - the whole new qty
    # No pool source at all (`qty_proposed_reserve` is "0"), so there is nothing for the
    # placed 5 to redirect against - the boundary the captain's 21 Aug ruling names, and
    # the old relabel-onto-Buy path (netted below by `refresh_for_decision`, not by a
    # trim here, because there is nothing on Reserve/incoming to trim) still applies.
    # `placed_redirect_qty` itself is retired (Slice D, rule 6): where the placed
    # quantity goes is decided at Apply by the reallocation the suggestion names, not
    # carried as a second answer on the proposal.
    assert "placed_redirect_qty" not in row["proposal"]

    put = client.put(
        f"{BASE}/planning-changes/{batch.id}/rows/{row['id']}", json={"decision": "confirm"},
    )
    assert put.status_code == 200, put.text
    assert Decimal(put.json()["composition"]["buy_qty"]) == Decimal("10")

    apply_response = client.post(f"{BASE}/planning-changes/{batch.id}/apply")
    assert apply_response.status_code == 200, apply_response.text
    assert apply_response.json()["applied_orders"] == [order.autocount_doc_no]

    db.expire_all()
    live_rows = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.state != INQUIRY_CANCELLED)
        .all()
    )
    # AC-P3-5 (26 August 2026): ONE row, updated. The 5 already on the purchase order
    # stays on it - the link is untouched - and the row now asks for the 10 the book says,
    # of which 5 is covered. It used to be two rows, the placed 5 and a fresh 5, which is
    # the same line reading as two instructions on purchasing's list.
    assert len(live_rows) == 1, "one order inquiry row per sales-order line, always"
    survivor = live_rows[0]
    assert survivor.id == placed_row.id
    assert survivor.qty == Decimal("10")
    assert survivor.state == INQUIRY_PARTLY_LINKED
    assert survivor.po_ref == po.po_number  # the placement is intact
    links = ProjectOrderInquiryService(db)._links_of(survivor.id)
    assert [str(link.qty) for link in links] == ["5.0000"]


def test_apply_qty_up_with_no_decision_and_a_real_placed_row_is_cancelled_and_unlinked(api):
    """Case B (the captain, 20 Aug; Slice D, issue #859, measured after the coder's
    landing): SO349754 SRTWC287A-RL - a real placed 5 and NO active decision at all, the
    pool holding plenty of stock. The board still proposes Reserve for the WHOLE new
    quantity (10, all from the pool) - the pool take stands, nothing is trimmed onto Buy.

    But the REDIRECT-to-pool this case used to name only runs off `_persist`/apply's
    decision-scoped reallocation (AC-D1-D3): it is the counterpart to an active decision
    being superseded, and there is no active decision here to attribute it to (AC-R03,
    `held` is null). Measured directly: apply still succeeds, the composed need is 0 more
    Buy, and the ordinary qty-down netting path takes over instead of a redirect - the
    placed row is CANCELLED and UNLINKED from the PO it was placed on (the PO's own
    history is untouched; only this line's claim on it is). Was named "...redirects_it_to
    _the_pool", asserting the opposite outcome under the pre-Slice-D mechanism."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=50)
    core_so = _core_so(db, world.company_id)
    # Inside the lead-time reserve window, and relative so it stays there: ladder v3 buys a
    # line beyond `today + lead time + 14` whole and consults no pool at all (AC-L1).
    needed = date.today() + timedelta(days=30)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="5",
                            required_date=needed)
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    from app.models.project_so import INQUIRY_RAISED, OrderInquiry

    inquiry = OrderInquiry(
        id=_uid(), company_id=world.company_id, project_sales_order_id=order.id,
        amendment_id=None, state=INQUIRY_RAISED, raised_by=world.actor,
    )
    db.add(inquiry)
    db.flush()
    row = OrderInquiryRow(
        id=_uid(), company_id=world.company_id, order_inquiry_id=inquiry.id,
        so_line_id=line.id, qty=Decimal("5"), verb=IV_ORDER, state=INQUIRY_RAISED,
        supply_decision_id=None, stock_location=world.own_wh.warehouse_code,
    )
    db.add(row)
    db.commit()
    po, po_line = _place_row_on_a_real_po(db, world, row, qty_ordered="5")

    core_line.qty_ordered = Decimal("10")
    db.commit()
    changed = _diff_change(
        QTY_CHANGED, core_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=needed,
        new_date=needed, old_qty="5", new_qty="10",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    out = planning_change_service.get_batch(db, str(batch.id))
    row_out = out["orders"][0]["rows"][0]
    assert row_out["kind"] == "qty_up"
    assert row_out["suggestion"]["components"], row_out["suggestion"]
    assert row_out["held"] is None  # no active decision at all (AC-R03)
    assert row_out["facts"]["buy_actioned"]["value"] is True
    assert Decimal(row_out["facts"]["buy_actioned"]["qty"]) == Decimal("5")
    assert row_out["facts"]["buy_actioned"]["po_number"] == po.po_number

    proposal = row_out["proposal"]
    assert Decimal(proposal["qty_proposed_reserve"]) == Decimal("10"), (
        "the pool take stands in full - it is no longer trimmed by the placed 5"
    )
    assert Decimal(proposal["qty_proposed_buy"]) == Decimal("0")
    # `placed_redirect_qty` is retired (Slice D, rule 6): where a placed quantity goes is
    # decided at Apply by the reallocation the suggestion names, not carried as a second
    # answer on the proposal.
    assert "placed_redirect_qty" not in proposal
    # sources/trail agree with the untouched aggregate - nothing was trimmed.
    reserve_sources_total = sum(
        (Decimal(s["qty"]) for s in proposal["sources"] if s["kind"] == "reserve"),
        Decimal("0"),
    )
    assert reserve_sources_total == Decimal("10")

    put = client.put(
        f"{BASE}/planning-changes/{batch.id}/rows/{row_out['id']}", json={"decision": "confirm"},
    )
    assert put.status_code == 200, put.text
    composition = put.json()["composition"]
    assert Decimal(composition["buy_qty"]) == Decimal("0")
    reserve_total = sum((Decimal(c["qty"]) for c in composition["reserve"]), Decimal("0"))
    assert reserve_total == Decimal("10")

    apply_response = client.post(f"{BASE}/planning-changes/{batch.id}/apply")
    assert apply_response.status_code == 200, apply_response.text
    assert apply_response.json()["applied_orders"] == [order.autocount_doc_no]

    db.expire_all()
    # No active decision exists for this reallocation to attribute a redirect to (AC-R03),
    # so it does not run: the ordinary qty-down netting path takes over instead - the
    # composed need is 0 more Buy, so this row (nothing else was raised for the line) is
    # cancelled, and its link to the PO it was placed on is removed with it.
    all_rows = db.query(OrderInquiryRow).filter(OrderInquiryRow.so_line_id == line.id).all()
    assert len(all_rows) == 1, "no CANCEL_BALANCE, no fresh ORDER row - the one row is reused"
    assert all_rows[0].id == row.id
    assert all_rows[0].state == INQUIRY_CANCELLED
    assert all_rows[0].redirected_to_pool is not True
    links = ProjectOrderInquiryService(db)._links_of(all_rows[0].id)
    assert links == [], "the PO link is removed - purchasing's own PO history is untouched"
    assert "the book left nothing to buy" in (all_rows[0].note or "")

    # Owner ruling 29 Sep 2026 (PR #1369, option (c)): the freed document quantity is NOT
    # re-dealt by the planning side (was D2: a pool row or a raised row of another order).
    # The PO line reads unclaimed until purchasing moves it in AutoCount, and the batch
    # row records that intent.
    from app.models.project_so import OrderInquiryLink

    all_links_on_po_line = (
        db.query(OrderInquiryLink).filter(OrderInquiryLink.po_line_id == po_line.id).all()
    )
    assert all_links_on_po_line == [], all_links_on_po_line
    applied = planning_change_service.get_batch(db, str(batch.id))
    said = applied["orders"][0]["rows"][0]["result"] or {}
    assert not (said.get("executed_reallocations") or []), said
    assert any(
        po.po_number in text and "5" in text and "AutoCount" in text
        for text in (said.get("released_documents") or [])
    ), said


def test_apply_advance_with_pool_available_records_the_freed_placed_qty_for_purchasing(api):
    """The SO397450 / SRT382-6-DIY shape, end-to-end: an ADVANCE whose fresh proposal draws
    432 from the pool at BRW while the line already has 432 on TWO real purchase orders
    (300 + 132). Confirming as proposed keeps the pool's Reserve 432 whole - no relabel
    onto Buy.

    The line's own ORDER row is CANCELLED and unlinked from both purchase orders by the
    confirm's own settle (the book left nothing to buy; their own PO history is untouched -
    only this line's claim on them is). Owner ruling 29 Sep 2026 (PR #1369, option (c)):
    the freed 432 is NOT re-dealt by the planning side - no pool-location row, no fresh
    link; the batch row records the intent for purchasing to carry out in AutoCount, one
    notice per purchase order, and the next sync brings the new links to Order Inquiries.
    No CANCEL_BALANCE, no duplicate, and no informational ADVANCE row either: the pool
    covers the whole need before the ladder logs a date-change instruction. Was
    "...redirects_the_freed_placed_qty_to_a_pool_row" (Slice D), before that
    "...redirects_both_placed_rows_to_the_pool" (the pre-Slice-D same-row flag)."""
    client, world = api
    db = world.db
    # 1000, not 500: ladder v8 lends a project HALF the pool (R-B), and these cases are
    # about a fresh proposal CITING the pool rather than about the size of the share.
    _stock(db, world.product, world.pool_wh, on_hand=1000)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="432",
                            required_date=date(2026, 8, 25))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    # Covered today by a plain Buy - the same starting shape the trail-popover test above
    # uses, plus the real placed PO the live row also carried.
    _confirm(client, order.id, {"lines": [
        _line_payload(line.id, buy_qty="432", buy_reason="Nothing free elsewhere."),
    ]})
    placed_row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == IV_ORDER)
        .one()
    )

    supplier = Supplier(
        id=_uid(), company_id=world.company_id, supplier_code=f"ZZT-{_uid()[:8]}",
        supplier_name=f"{MARKER} supplier",
    )
    po_a = PurchaseOrder(
        id=_uid(), company_id=world.company_id, po_number=f"ZZT-PO-{_uid()[:8]}",
        supplier_id=supplier.id,
    )
    po_b = PurchaseOrder(
        id=_uid(), company_id=world.company_id, po_number=f"ZZT-PO-{_uid()[:8]}",
        supplier_id=supplier.id,
    )
    db.add_all([supplier, po_a, po_b])
    db.flush()
    po_line_a = PurchaseOrderLine(
        id=_uid(), company_id=world.company_id, purchase_order_id=po_a.id,
        product_id=world.product.id, warehouse_id=world.own_wh.id,
        qty_ordered=Decimal("300"), qty_received=Decimal("0"), line_status="open",
    )
    po_line_b = PurchaseOrderLine(
        id=_uid(), company_id=world.company_id, purchase_order_id=po_b.id,
        product_id=world.product.id, warehouse_id=world.own_wh.id,
        qty_ordered=Decimal("132"), qty_received=Decimal("0"), line_status="open",
    )
    db.add_all([po_line_a, po_line_b])
    db.commit()

    # The G2 cascade split - the real path two placed rows come from, never hand-set.
    ProjectOrderInquiryService(db).place_on_po_allocations(
        placed_row.id,
        [
            {"po_line_id": po_line_a.id, "qty": "300"},
            {"po_line_id": po_line_b.id, "qty": "132"},
        ],
        actor_user_id=world.actor,
    )
    db.commit()

    placed_rows = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.state == INQUIRY_PLACED)
        .all()
    )
    assert len(placed_rows) == 1, "one instruction per sales-order line (AC-I6)"
    assert placed_rows[0].qty == Decimal("432")
    assert len(ProjectOrderInquiryService(db)._links_of(placed_rows[0].id)) == 2

    changed = _diff_change(
        DATE_MOVED, core_line, doc_number=core_so.so_number, item_code="ZZT-ADV",
        location=world.own_wh.warehouse_code, old_date=date(2026, 8, 25),
        new_date=date(2026, 8, 25) - timedelta(days=14), old_qty="432", new_qty="432",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    out = planning_change_service.get_batch(db, str(batch.id))
    row_out = out["orders"][0]["rows"][0]
    assert row_out["suggestion"]["components"], row_out["suggestion"]  # advance - runs the ladder

    proposal = row_out["proposal"]
    assert Decimal(proposal["qty_proposed_reserve"]) == Decimal("432")
    assert Decimal(proposal["qty_proposed_buy"]) == Decimal("0")
    # `placed_redirect_qty` is retired (Slice D, rule 6): where a placed quantity goes is
    # decided at Apply by the reallocation the suggestion names, not carried on the proposal.
    assert "placed_redirect_qty" not in proposal
    # The trail still reads "the pool took 432 at BRW", unedited - the pool take stands,
    # so there is nothing to relabel and nothing to narrate.
    pool_step = next(step for step in proposal["trail"] if step["kind"] == "pool")
    assert Decimal(pool_step["took"]) == Decimal("432")
    # Its note says which pools were opened (ladder v5's own hint) and nothing about a
    # placed quantity, because there was nothing to relabel.
    assert "already placed" not in (pool_step.get("note") or "")
    # sources agree with the aggregate - Fix 2's guard has nothing to warn about here.
    reserve_sources_total = sum(
        (Decimal(s["qty"]) for s in proposal["sources"] if s["kind"] == "reserve"),
        Decimal("0"),
    )
    assert reserve_sources_total == Decimal("432")

    put = client.put(
        f"{BASE}/planning-changes/{batch.id}/rows/{row_out['id']}", json={"decision": "confirm"},
    )
    assert put.status_code == 200, put.text
    composition = put.json()["composition"]
    assert Decimal(composition["buy_qty"]) == Decimal("0")
    reserve_total = sum((Decimal(c["qty"]) for c in composition["reserve"]), Decimal("0"))
    assert reserve_total == Decimal("432")

    apply_response = client.post(f"{BASE}/planning-changes/{batch.id}/apply")
    assert apply_response.status_code == 200, apply_response.text
    assert apply_response.json()["applied_orders"] == [order.autocount_doc_no]

    db.expire_all()
    # The line's own row is CANCELLED (not redirected in place) and unlinked from both POs
    # it was placed on - `redirected_to_pool` is never set under Slice D.
    own_rows = db.query(OrderInquiryRow).filter(OrderInquiryRow.so_line_id == line.id).all()
    assert len(own_rows) == 1, "no CANCEL_BALANCE, no fresh ORDER row - the one row is reused"
    original = own_rows[0]
    assert original.id == placed_row.id
    assert original.state == INQUIRY_CANCELLED
    assert original.redirected_to_pool is not True
    assert ProjectOrderInquiryService(db)._links_of(original.id) == [], (
        "unlinked - the two purchase orders' own history is untouched, only this line's "
        "claim on them is"
    )

    # Option (c): no pool-location row is written for the freed 432 and nothing is linked
    # by the planning side. Both purchase-order lines read unclaimed until purchasing
    # moves them in AutoCount.
    pool_rows = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id.is_(None), OrderInquiryRow.verb == IV_ORDER,
                OrderInquiryRow.stock_location == world.pool_wh.warehouse_code)
        .all()
    )
    assert pool_rows == [], pool_rows
    from app.models.project_so import OrderInquiryLink

    for po_line in (po_line_a, po_line_b):
        assert (
            db.query(OrderInquiryLink).filter(OrderInquiryLink.po_line_id == po_line.id).all()
            == []
        )

    # The batch row records the intent, one notice per purchase order, naming the
    # AutoCount line and the quantity, for purchasing to carry out in AutoCount.
    applied = planning_change_service.get_batch(db, str(batch.id))
    result = applied["orders"][0]["rows"][0]["result"]
    assert result is not None, applied
    assert not (result.get("executed_reallocations") or []), result
    notices = result.get("released_documents") or []
    for po_doc, qty in ((po_a, "300"), (po_b, "132")):
        assert any(
            po_doc.po_number in text and qty in text and "AutoCount" in text
            and f"{core_so.so_number} line 1" in text
            for text in notices
        ), (po_doc.po_number, qty, notices)

    # No informational ADVANCE row: the pool covers the whole need before the ladder logs
    # a date-change instruction that has nothing left to say.
    from app.models.project_so import OrderInquiry as _OrderInquiry

    advance_rows = (
        db.query(OrderInquiryRow)
        .join(_OrderInquiry, _OrderInquiry.id == OrderInquiryRow.order_inquiry_id)
        .filter(_OrderInquiry.project_sales_order_id == order.id, OrderInquiryRow.verb == "ADVANCE")
        .all()
    )
    assert advance_rows == []


def test_build_batch_facts_read_buy_actioned_true_for_a_really_placed_row(api):
    """`_inquiry_rows_and_buy_actioned` must read `INQUIRY_PLACED` as actioned, not only
    `INQUIRY_ACTIONED` - proven through a row placed on a real PO, not a hand-set state."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20",
                            required_date=date(2027, 2, 1))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    _confirm(client, order.id, {"lines": [
        _line_payload(line.id, buy_qty="20", buy_reason="Nothing free elsewhere."),
    ]})
    placed_row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == IV_ORDER)
        .one()
    )
    po, _po_line = _place_row_on_a_real_po(db, world, placed_row, qty_ordered="20")

    changed = _diff_change(
        DATE_MOVED, core_line, doc_number=core_so.so_number, item_code="ZZT-ITEM",
        location=world.own_wh.warehouse_code, old_date=date(2027, 2, 1),
        new_date=date(2027, 2, 11), old_qty="20", new_qty="20",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(changed): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    out = planning_change_service.get_batch(db, str(batch.id))
    row = out["orders"][0]["rows"][0]
    assert row["kind"] == "delayed"
    assert row["facts"]["buy_actioned"]["value"] is True
    assert row["facts"]["buy_actioned"]["po_number"] == po.po_number
    assert Decimal(row["facts"]["buy_actioned"]["qty"]) == Decimal("20")
    assert row["suggestion"]["components"][0]["action"] == "keep"
    assert row["suggestion"]["components"][0]["document"] == po.po_number
