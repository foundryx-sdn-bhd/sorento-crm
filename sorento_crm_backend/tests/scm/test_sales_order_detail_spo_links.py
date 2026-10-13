"""S7 - the retail SO line's own "Linked to" column names the SPO that covers it.

`PLAN-scm-spo-planner-feedback-3sep.md` section S7, UAC AC-G2..G6.

The retail half of the loop `sales_order_service._line_links` (AC-I9) never closed: a
retail tick writes no `order_inquiry_links` row (that table hangs off an order-inquiry row,
and a retail sales-order line has none), so `linked_to` read "-" for a retail line an SPO
had already promised. `spo_conversion_service.coverage_for_so_lines` is the other half,
sharing its row scan with S5's planner `taken_by` (`_spo_so_coverage_rows`) so the two
surfaces can never name a different SPO for the same line.

Postgres via `pg_session`, the same `World` builder `test_spo_conversion` and
`test_spo_planner_selection` already share - a second world here would be a second set of
assumptions about what a shipment or a retail demand line looks like.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.models.procurement import SPOAllocation
from app.services.scm import spo_conversion_service as svc
from app.services.scm.sales_order_service import SalesOrderService
from tests._pg_fixture import pg_session
from tests.scm.conftest import grant_permission, requires_pg
from tests.scm.test_outstanding_import_routes import as_company_user
from tests.scm.test_spo_conversion import MARKER, World, _u
from tests.scm.test_spo_planner_selection import _confirm, _retail_demand

pytestmark = requires_pg


def _linked_to(db, so_id: str, line_id: str):
    body = SalesOrderService(db).get(so_id)
    line = next(ln for ln in body["lines"] if str(ln["id"]) == str(line_id))
    return line["linked_to"]


def _link_core_line_to_a_project(db, so, core_line, *, po_line, qty, document):
    """A CORE sales-order line registered as a project line, with ONE order-inquiry link
    already on file - the mirror `_line_links` walks: `OrderInquiryRow.so_line_id` ->
    `ProjectSalesOrderLine.id` -> `ProjectSalesOrderLine.core_sales_order_line_id` -> the
    core `SalesOrderLine`. Used only by the AC-G5 test, which needs a core line that carries
    BOTH an order-inquiry link and (separately, via `svc.create`'s retail tick) an SPO
    coverage entry, to prove the two combine in the right order.
    """
    from app.models.project_so import (
        INQUIRY_RAISED,
        IV_ORDER,
        OrderInquiry,
        OrderInquiryLink,
        OrderInquiryRow,
        ProjectSalesOrder,
        ProjectSalesOrderLine,
    )

    pso = ProjectSalesOrder(
        id=_u(),
        project_id=None,
        so_id=so.id,
        provisional_ref=f"{MARKER}-PSO-{uuid.uuid4().hex[:6]}",
        status="published",
    )
    db.add(pso)
    db.flush()
    project_line = ProjectSalesOrderLine(
        id=_u(),
        project_sales_order_id=pso.id,
        line_no=1,
        core_sales_order_line_id=core_line.id,
        product_id=core_line.product_id,
        qty=Decimal(str(qty)),
        unit_price=Decimal("0"),
        amount=Decimal("0"),
    )
    db.add(project_line)
    db.flush()
    inquiry = OrderInquiry(id=_u(), project_sales_order_id=pso.id, state=INQUIRY_RAISED)
    db.add(inquiry)
    db.flush()
    row = OrderInquiryRow(
        id=_u(),
        order_inquiry_id=inquiry.id,
        so_line_id=project_line.id,
        item_code=MARKER,
        qty=Decimal(str(qty)),
        verb=IV_ORDER,
        state=INQUIRY_RAISED,
    )
    db.add(row)
    db.flush()
    db.add(OrderInquiryLink(
        id=_u(), row_id=row.id, po_line_id=po_line.id, document=document,
        qty=Decimal(str(qty)),
    ))
    db.flush()


def test_a_retail_line_covered_by_one_spo_reads_one_spo_link():
    """AC-G2."""
    with pg_session() as db:
        w = World(db)
        supplier = w.supplier()
        wh = w.warehouse()
        w.po("A", supplier, [("A", 100, 0)])
        retail, so = _retail_demand(db, w, "A", wh, qty=30, required=date(2026, 9, 1))

        shipment, lines = w.shipment([("A", 30, supplier)])
        created = svc.create(
            db, str(shipment.id),
            [_confirm(
                lines[0], 30,
                location_splits=[{"warehouse_id": str(wh.id), "qty": 30}],
                so_takes=[{"key": f"retail:{retail.id}", "qty": 30}],
            )],
        )
        spo_number = created["created_spos"][0]["po_number"]
        spo_po_id = created["created_spos"][0]["purchase_order_id"]

        linked = _linked_to(db, str(so.id), str(retail.id))
        assert linked == [{
            "kind": "spo",
            "document": spo_number,
            "line_label": None,
            # L4 (review round): the SPO's own header id, so `document` can be a link.
            "purchase_order_id": spo_po_id,
            "qty": "30",
            "location": wh.warehouse_code,
            "expected_date": None,
            "late": False,
            "late_days": None,
        }]


def test_covered_by_two_containers_reads_two_links_in_spo_number_order():
    """AC-G3."""
    with pg_session() as db:
        w = World(db)
        supplier = w.supplier()
        wh = w.warehouse()
        w.po("A", supplier, [("A", 100, 0)])
        retail, so = _retail_demand(db, w, "A", wh, qty=50, required=date(2026, 9, 1))

        # 50 wanted, split across two containers - each ticks only what IT can place
        # (`_validate_so_takes` refuses a take above the line's own SPO qty), 20 then 30.
        first, first_lines = w.shipment([("A", 20, supplier)])
        first_created = svc.create(
            db, str(first.id),
            [_confirm(
                first_lines[0], 20,
                location_splits=[{"warehouse_id": str(wh.id), "qty": 20}],
                so_takes=[{"key": f"retail:{retail.id}", "qty": 20}],
            )],
        )
        first_spo = first_created["created_spos"][0]["po_number"]

        second, second_lines = w.shipment([("A", 30, supplier)])
        second_created = svc.create(
            db, str(second.id),
            [_confirm(
                second_lines[0], 30,
                location_splits=[{"warehouse_id": str(wh.id), "qty": 30}],
                so_takes=[{"key": f"retail:{retail.id}", "qty": 30}],
            )],
        )
        second_spo = second_created["created_spos"][0]["po_number"]

        linked = _linked_to(db, str(so.id), str(retail.id))
        assert [l["document"] for l in linked] == sorted([first_spo, second_spo])
        assert {l["document"] for l in linked} == {first_spo, second_spo}
        assert sum(float(l["qty"]) for l in linked) == 50


def test_after_unwind_the_line_reads_none_again():
    """AC-G4."""
    with pg_session() as db:
        w = World(db)
        supplier = w.supplier()
        wh = w.warehouse()
        w.po("A", supplier, [("A", 100, 0)])
        retail, so = _retail_demand(db, w, "A", wh, qty=30, required=date(2026, 9, 1))

        shipment, lines = w.shipment([("A", 30, supplier)])
        svc.create(
            db, str(shipment.id),
            [_confirm(
                lines[0], 30,
                location_splits=[{"warehouse_id": str(wh.id), "qty": 30}],
                so_takes=[{"key": f"retail:{retail.id}", "qty": 30}],
            )],
        )
        assert _linked_to(db, str(so.id), str(retail.id)) is not None

        svc.unwind(db, str(shipment.id))

        assert _linked_to(db, str(so.id), str(retail.id)) is None


def test_a_line_with_an_inquiry_row_keeps_its_oi_links_first_then_the_spo_links():
    """AC-G5."""
    with pg_session() as db:
        w = World(db)
        supplier = w.supplier()
        wh = w.warehouse()
        other_po = w.po("OTHER", supplier, [("A", 20, 0)])
        w.po("A", supplier, [("A", 100, 0)])

        retail, so = _retail_demand(db, w, "A", wh, qty=30, required=date(2026, 9, 1))
        _link_core_line_to_a_project(
            db, so, retail, po_line=other_po.lines[0], qty=5, document=other_po.po_number,
        )

        shipment, lines = w.shipment([("A", 30, supplier)])
        created = svc.create(
            db, str(shipment.id),
            [_confirm(
                lines[0], 30,
                location_splits=[{"warehouse_id": str(wh.id), "qty": 30}],
                so_takes=[{"key": f"retail:{retail.id}", "qty": 30}],
            )],
        )
        spo_number = created["created_spos"][0]["po_number"]

        linked = _linked_to(db, str(so.id), str(retail.id))
        assert [l["kind"] for l in linked] == ["po", "spo"]
        assert linked[0]["document"] == other_po.po_number
        assert linked[1]["document"] == spo_number


def test_the_route_carries_the_spo_link(scm_app):
    """AC-G6: `response_model`-free route, but the additive shape still has to survive it -
    same discipline `test_the_route_response_carries_taken_qty_and_taken_by` uses."""
    app, db, gcu, gcuk = scm_app
    as_company_user(app, db, gcu, gcuk)
    w = World(db)
    supplier = w.supplier()
    wh = w.warehouse()
    w.po("A", supplier, [("A", 100, 0)])
    retail, so = _retail_demand(db, w, "A", wh, qty=30, required=date(2026, 9, 1))

    shipment, lines = w.shipment([("A", 30, supplier)])
    created = svc.create(
        db, str(shipment.id),
        [_confirm(
            lines[0], 30,
            location_splits=[{"warehouse_id": str(wh.id), "qty": 30}],
            so_takes=[{"key": f"retail:{retail.id}", "qty": 30}],
        )],
    )
    spo_number = created["created_spos"][0]["po_number"]
    spo_po_id = created["created_spos"][0]["purchase_order_id"]

    client = TestClient(app)
    r = client.get(f"/api/v1/scm/sales-orders/{so.id}")

    assert r.status_code == 200, r.text
    line = next(ln for ln in r.json()["lines"] if ln["id"] == str(retail.id))
    assert line["linked_to"] == [{
        "kind": "spo",
        "document": spo_number,
        "line_label": None,
        # L4 (review round): declared on `SalesOrderLineLink` or `response_model` drops it.
        "purchase_order_id": spo_po_id,
        "qty": "30",
        "location": wh.warehouse_code,
        "expected_date": None,
        "late": False,
        "late_days": None,
        # Declared on `SalesOrderLineLink` since the 17 Sep derived-SPO fix; False on a
        # stored link.
        "derived": False,
    }]


def _open_spo_covering(db, *, po_line, from_po_number, allocated_quantity):
    """A SYNTHETIC-triggering `SPOAllocation`: open per `derived_spo_open_clauses()`
    (`line_status` defaults `"open"`, `receipt_status` defaults `"pending"`, no shipment
    so never "landed", `retired_at` unset) and naming the SAME `from_po_number` +
    `product_id` as a row's own PO link - the join `_append_derived_spo_entries` fires
    on. Mirrors `tests/test_order_inquiry_derived_spo.py::_spo`'s minimal-open shape;
    not imported from there because this file's own `World`/company-scope pattern
    (CompanyScopedMixin stamps `company_id` from session scope) differs from that
    file's explicit `company_id` arguments.

    OI-SPO-EXACT: it names the PO LINE (`from_po_line_ref` = the line's own
    `source_ref`, stated here when the seeded line carries none), as AutoCount's do.
    """
    if not po_line.source_ref:
        po_line.source_ref = f"{from_po_number}:1"
        db.flush()
    allocation = SPOAllocation(
        id=_u(), spo_number=f"ZZT-SPO-{_u()[:8]}", product_id=po_line.product_id,
        from_po_number=from_po_number, from_po_line_ref=po_line.source_ref,
        allocated_quantity=allocated_quantity,
        quantity_received=0, receipt_status="pending",
    )
    db.add(allocation)
    db.flush()
    return allocation


def test_ac_hf1_so_detail_survives_a_derived_spo_entry_on_a_po_linked_line():
    """Production 500, 17 Sep: `SalesOrderService._line_links` hard-indexes
    `link["line_label"]` (and `late`/`late_days`) on EVERY entry `links_for_rows`
    returns, but S5's `_append_derived_spo_entries` (#951) appends a SYNTHETIC
    `derived`-kind spo dict carrying only id/kind/derived/document/qty/location/
    expected_date - no `line_label`, no `purchase_order_id`, no `late`, no
    `late_days`. Any sales order with a PO-linked line whose PO carries an open
    SPOAllocation for the same product 500s its own detail read with
    `KeyError: 'line_label'`.
    """
    with pg_session() as db:
        w = World(db)
        supplier = w.supplier()
        wh = w.warehouse()
        po = w.po("A", supplier, [("A", 100, 0)])
        po_line = po.lines[0]
        retail, so = _retail_demand(db, w, "A", wh, qty=8, required=date(2026, 9, 1))
        _link_core_line_to_a_project(
            db, so, retail, po_line=po_line, qty=8, document=po.po_number,
        )
        _open_spo_covering(
            db, po_line=po_line, from_po_number=po.po_number,
            allocated_quantity=5,
        )
        db.commit()

        # The exact call the route makes. Must not raise.
        body = SalesOrderService(db).get(str(so.id))

        line = next(ln for ln in body["lines"] if str(ln["id"]) == str(retail.id))
        kinds = {(l["kind"], l.get("derived", False)) for l in line["linked_to"]}
        assert ("po", False) in kinds, line["linked_to"]
        assert ("spo", True) in kinds, line["linked_to"]
        derived = next(l for l in line["linked_to"] if l.get("derived"))
        assert derived["document"].startswith("ZZT-SPO-")
        assert derived["line_label"] is None
        assert derived["purchase_order_id"] is None
        assert derived["late"] is False
        assert derived["late_days"] is None
        # `_qty_str`: allocated 5 - received 0, formatted bare, not "5.0000".
        assert derived["qty"] == "5"


def test_ac_hf2_so_detail_route_returns_200_with_a_derived_spo_entry(scm_app):
    """Route-level twin of AC-HF1, and the `response_model` guard: `SalesOrderLineLink`
    has to declare `derived` (and the other synthetic-entry fields) or FastAPI's
    response serialization silently drops them even though the service-level dict
    carries them - a passing HF1 with a 200-but-wrong-shape route would hide that."""
    from app.services.reference_seed import seed_roles

    app, db, gcu, gcuk = scm_app
    # This private CI database is built by `alembic upgrade head` alone, which never
    # replays a migration's role/grant seed - `user_roles` starts empty, so
    # `as_company_user`'s default `role="purchasing"` would otherwise fail on
    # `seed_user`'s own assertion for a reason that has nothing to do with this bug.
    # `seed_roles` is idempotent and flush-only (no commit); `grant_permission` is this
    # suite's own established pattern for the same gap (see its docstring - a route
    # permission nobody has granted on a freshly migrated, dataless database).
    seed_roles(db)
    as_company_user(app, db, gcu, gcuk)
    grant_permission(db, "purchasing", "scm.dashboard.view")
    w = World(db)
    supplier = w.supplier()
    wh = w.warehouse()
    po = w.po("A", supplier, [("A", 100, 0)])
    po_line = po.lines[0]
    retail, so = _retail_demand(db, w, "A", wh, qty=8, required=date(2026, 9, 1))
    _link_core_line_to_a_project(
        db, so, retail, po_line=po_line, qty=8, document=po.po_number,
    )
    _open_spo_covering(
        db, po_line=po_line, from_po_number=po.po_number,
        allocated_quantity=5,
    )
    db.commit()

    client = TestClient(app)
    r = client.get(f"/api/v1/scm/sales-orders/{so.id}")

    assert r.status_code == 200, r.text
    line = next(ln for ln in r.json()["lines"] if ln["id"] == str(retail.id))
    derived = next(l for l in line["linked_to"] if l["kind"] == "spo")
    assert derived["derived"] is True, derived
