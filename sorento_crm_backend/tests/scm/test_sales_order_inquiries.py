"""The sales-order list names the order inquiries raised against each order.

The business sees SOs and order inquiries, and nothing else: no plan entity, no "Planning"
column. So the link between the two has to be readable from the order itself - which
inquiry, what state it is in, who raised it and how much of it purchasing has placed - or a
buyer looking at an order has no way to tell whether anything has been done about it.

Attached in ONE query per page, the same shape `with_links` uses for the purchase-order
claims: per-row would be an N+1 across a 15,000-order list.

Postgres, the prod-copy database, everything inside a rolled-back savepoint.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.order import SalesOrder
from app.models.project_so import (
    INQUIRY_PLACED,
    INQUIRY_RAISED,
    IV_ORDER,
    OrderInquiry,
    OrderInquiryRow,
    ProjectSalesOrder,
)
from app.services.scm.sales_order_service import SalesOrderService
from tests.scm.conftest import as_user, requires_pg, seed_user

pytestmark = requires_pg

MARKER = "ZZTSOOI"


def _uid() -> str:
    return str(uuid.uuid4())


def _as(scm_app, role_slug="purchasing"):
    app, db, gcu, gcuak = scm_app
    uid = seed_user(db, role_slug)
    as_user(app, gcu, gcuak, uid)
    return app, db, uid


def _core_order(db) -> SalesOrder:
    order = SalesOrder(
        id=_uid(),
        so_number=f"{MARKER}-{_uid()[:8].upper()}",
        order_date=date(2026, 5, 4),
        status="open",
        source_system="scm_upload",
    )
    db.add(order)
    db.flush()
    return order


def _planned(db, core: SalesOrder) -> ProjectSalesOrder:
    """The planning record for an ADOPTED order: no project registration, just the link."""
    pso = ProjectSalesOrder(
        id=_uid(),
        company_id=core.company_id,
        project_id=None,
        so_id=core.id,
        provisional_ref=f"{MARKER}-PSO-{_uid()[:8]}",
        status="published",
    )
    db.add(pso)
    db.flush()
    return pso


def _inquiry(db, pso, *, number, raised_by=None, raised_at=None, amendment_id=None,
             rows=((INQUIRY_RAISED, 3), (INQUIRY_PLACED, 1))) -> OrderInquiry:
    inquiry = OrderInquiry(
        id=_uid(),
        company_id=pso.company_id,
        project_sales_order_id=pso.id,
        amendment_id=amendment_id,
        state=INQUIRY_RAISED,
        inquiry_no=number,
        raised_by=raised_by,
    )
    if raised_at is not None:
        inquiry.raised_at = raised_at
    db.add(inquiry)
    db.flush()
    for state, count in rows:
        for _ in range(count):
            db.add(OrderInquiryRow(
                id=_uid(),
                company_id=pso.company_id,
                order_inquiry_id=inquiry.id,
                item_code=f"{MARKER}-ITEM",
                qty=1,
                verb=IV_ORDER,
                state=state,
            ))
    db.flush()
    return inquiry


def _row_for(body, so_number):
    return next(r for r in body["data"] if r["so_number"] == so_number)


def test_the_list_names_each_inquiry_raised_on_the_order(scm_app):
    app, db, uid = _as(scm_app)
    core = _core_order(db)
    pso = _planned(db, core)
    _inquiry(db, pso, number=f"{MARKER}-OI-1", raised_by=uid,
             raised_at=datetime(2026, 6, 1, 9, 0))

    with TestClient(app) as c:
        res = c.get("/api/v1/scm/sales-orders", params={"query": core.so_number})

    assert res.status_code == 200, res.text
    row = _row_for(res.json(), core.so_number)
    # It survives `response_model`, which silently drops anything undeclared.
    assert "order_inquiries" in row, row.keys()
    assert len(row["order_inquiries"]) == 1
    inquiry = row["order_inquiries"][0]
    assert inquiry["inquiry_no"] == f"{MARKER}-OI-1"
    assert inquiry["state"] == INQUIRY_RAISED
    assert inquiry["raised_at"]
    assert inquiry["raised_by_name"] == "SCM Test"
    assert inquiry["rows_total"] == 4
    assert inquiry["rows_placed"] == 1


def test_an_order_nobody_has_planned_carries_an_empty_list(scm_app):
    """Never null and never absent: an empty list is what lets the column render "-"
    instead of the screen having to tell an unplanned order from a broken payload."""
    app, db, _uid_ = _as(scm_app)
    core = _core_order(db)

    with TestClient(app) as c:
        res = c.get("/api/v1/scm/sales-orders", params={"query": core.so_number})

    assert res.status_code == 200, res.text
    assert _row_for(res.json(), core.so_number)["order_inquiries"] == []


def test_the_sales_order_own_read_carries_them_too(scm_app):
    """The detail page shows the same fact, so it comes off the same service rather than a
    second query that could answer differently."""
    app, db, uid = _as(scm_app)
    core = _core_order(db)
    pso = _planned(db, core)
    _inquiry(db, pso, number=f"{MARKER}-OI-9", raised_by=uid)

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    assert res.status_code == 200, res.text
    assert [i["inquiry_no"] for i in res.json()["order_inquiries"]] == [f"{MARKER}-OI-9"]


def test_the_sales_order_inquiry_comes_before_its_amendments(scm_app):
    """The order's own inquiry first, then whatever amended it, oldest first: that is the
    sequence purchasing was told things in, and any other order makes the list read as
    arbitrary."""
    app, db, uid = _as(scm_app)
    core = _core_order(db)
    pso = _planned(db, core)
    from app.models.project_so import SOAmendment

    amendment = SOAmendment(
        id=_uid(), company_id=pso.company_id, project_sales_order_id=pso.id,
        status="published")
    db.add(amendment)
    db.flush()
    amendment_id = amendment.id
    # The amendment is raised FIRST in wall-clock terms, so an ordering that only sorted by
    # time would put it above the order's own inquiry.
    _inquiry(db, pso, number=f"{MARKER}-OI-B", raised_by=uid, amendment_id=amendment_id,
             raised_at=datetime(2026, 6, 1, 9, 0), rows=())
    _inquiry(db, pso, number=f"{MARKER}-OI-A", raised_by=uid,
             raised_at=datetime(2026, 7, 1, 9, 0), rows=())

    service = SalesOrderService(db)
    rows = service.with_order_inquiries([service.serialize(core)])

    assert [i["inquiry_no"] for i in rows[0]["order_inquiries"]] == [
        f"{MARKER}-OI-A", f"{MARKER}-OI-B",
    ]


# ------------------------------------------------------- what each LINE already carries
#
# The header answers "has anything been planned about this order". The Lines tab has to
# answer it per LINE, because a confirmation since 13.4 may cover a SUBSET of the order:
# an order carrying an inquiry and an active decision can still hold lines that neither
# touches, and a header-level answer would report those as handled.


def _product(db):
    from app.models.product import Product, ProductCategory, UnitOfMeasure

    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:6]}", uom_name="Unit")
    category = ProductCategory(
        id=_uid(), category_code=f"ZZT-{_uid()[:8]}", category_name=f"{MARKER} cat"
    )
    db.add_all([uom, category])
    db.flush()
    product = Product(
        id=_uid(),
        product_code=f"ZZT-{_uid()[:8]}",
        product_name=f"{MARKER} product",
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=0,
    )
    db.add(product)
    db.flush()
    return product


def _core_line(db, core: SalesOrder, *, qty=10):
    from app.models.order import SalesOrderLine

    line = SalesOrderLine(
        id=_uid(),
        sales_order_id=core.id,
        product_id=_product(db).id,
        qty_ordered=qty,
        qty_delivered=0,
        line_status="open",
        required_date=date(2026, 6, 30),
    )
    db.add(line)
    db.flush()
    return line


def _mirror(db, pso: ProjectSalesOrder, core_line, *, line_no=1):
    """The planning record's copy of a core line: `core_sales_order_line_id` is the whole
    link, and it is what every read here traverses."""
    from app.models.project_so import ProjectSalesOrderLine

    line = ProjectSalesOrderLine(
        id=_uid(),
        company_id=pso.company_id,
        project_sales_order_id=pso.id,
        core_sales_order_line_id=core_line.id,
        line_no=line_no,
        qty=core_line.qty_ordered,
    )
    db.add(line)
    db.flush()
    return line


def _inquiry_row(db, inquiry: OrderInquiry, mirror_line, *, state=INQUIRY_RAISED):
    row = OrderInquiryRow(
        id=_uid(),
        company_id=inquiry.company_id,
        order_inquiry_id=inquiry.id,
        so_line_id=mirror_line.id,
        item_code=f"{MARKER}-ITEM",
        qty=1,
        verb=IV_ORDER,
        state=state,
    )
    db.add(row)
    db.flush()
    return row


def _active_decision(db, pso: ProjectSalesOrder, *, revision_no, core_line_ids):
    from app.models.project_so import DECISION_ACTIVE, SOSupplyDecision

    decision = SOSupplyDecision(
        id=_uid(),
        company_id=pso.company_id,
        project_sales_order_id=pso.id,
        revision_no=revision_no,
        state=DECISION_ACTIVE,
        # NOT NULL on the live table, whatever the model says - a decision nobody
        # confirmed is not a decision.
        confirmed_at=datetime(2026, 6, 1, 9, 0),
        line_snapshots=[{"core_line_id": str(cid)} for cid in core_line_ids],
    )
    db.add(decision)
    db.flush()
    return decision


def test_a_line_names_the_inquiry_covering_it_and_the_revision_that_decided_it(scm_app):
    app, db, _uid_ = _as(scm_app)
    core = _core_order(db)
    decided = _core_line(db, core)
    pso = _planned(db, core)
    mirror = _mirror(db, pso, decided)
    inquiry = _inquiry(db, pso, number=f"{MARKER}-OI-L", rows=())
    row = _inquiry_row(db, inquiry, mirror, state=INQUIRY_PLACED)
    _active_decision(db, pso, revision_no=2, core_line_ids=[decided.id])

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    assert res.status_code == 200, res.text
    line = next(l for l in res.json()["lines"] if l["id"] == decided.id)
    # Both survive `response_model`, which silently drops anything undeclared.
    assert "order_inquiry" in line, line.keys()
    assert "decision_revision" in line, line.keys()
    # AC-B6-8 (deep links): the dict also carries the ids the board's own OI column links
    # through - the seeded inquiry and row, not a guess.
    assert line["order_inquiry"] == {
        "inquiry_no": f"{MARKER}-OI-L",
        "state": INQUIRY_PLACED,
        "inquiry_id": str(inquiry.id),
        "row_id": str(row.id),
        # PR 1622 (mock v3): the refusal fields are declared on the schema, so they always
        # serialise; nobody has refused this placed row, so each one is null.
        "ack_state": None,
        "rejected_reason": None,
        "rejected_by_name": None,
        "rejected_at": None,
    }
    assert line["decision_revision"] == 2


def test_a_line_nothing_has_been_raised_or_decided_on_says_so_with_nulls(scm_app):
    """Null, never an empty object and never a 0 revision: "nobody has been told about
    this line" is a different answer from "told, about nothing"."""
    app, db, _uid_ = _as(scm_app)
    core = _core_order(db)
    untouched = _core_line(db, core)

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    assert res.status_code == 200, res.text
    line = next(l for l in res.json()["lines"] if l["id"] == untouched.id)
    assert line["order_inquiry"] is None
    assert line["decision_revision"] is None


def test_a_line_the_active_revision_left_out_is_not_reported_as_decided(scm_app):
    """A confirmation covers the SUBSET the planner chose (13.4). The order has an active
    decision and an inquiry; this line is in neither, and reads exactly like a line on an
    order nobody has planned."""
    app, db, _uid_ = _as(scm_app)
    core = _core_order(db)
    decided = _core_line(db, core)
    left_out = _core_line(db, core)
    pso = _planned(db, core)
    mirror = _mirror(db, pso, decided)
    _mirror(db, pso, left_out, line_no=2)
    inquiry = _inquiry(db, pso, number=f"{MARKER}-OI-S", rows=())
    _inquiry_row(db, inquiry, mirror)
    _active_decision(db, pso, revision_no=1, core_line_ids=[decided.id])

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    assert res.status_code == 200, res.text
    by_id = {l["id"]: l for l in res.json()["lines"]}
    assert by_id[decided.id]["decision_revision"] == 1
    assert by_id[left_out.id]["decision_revision"] is None
    assert by_id[left_out.id]["order_inquiry"] is None


def test_a_superseded_revision_does_not_decide_a_line(scm_app):
    """Only the ACTIVE revision is a promise anybody holds. A superseded one is history,
    and printing its number would say the line is settled when it is back in the queue."""
    app, db, _uid_ = _as(scm_app)
    from app.models.project_so import DECISION_SUPERSEDED, SOSupplyDecision

    core = _core_order(db)
    line = _core_line(db, core)
    pso = _planned(db, core)
    _mirror(db, pso, line)
    db.add(SOSupplyDecision(
        id=_uid(),
        company_id=pso.company_id,
        project_sales_order_id=pso.id,
        revision_no=1,
        state=DECISION_SUPERSEDED,
        confirmed_at=datetime(2026, 6, 1, 9, 0),
        line_snapshots=[{"core_line_id": str(line.id)}],
    ))
    db.flush()

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    assert res.status_code == 200, res.text
    assert res.json()["lines"][0]["decision_revision"] is None


def test_the_list_does_not_pay_for_what_only_the_detail_page_prints(scm_app):
    """The list has no column for either, so it never runs the two reads. The keys are
    still declared, so the client reads one shape whichever route answered."""
    app, db, _uid_ = _as(scm_app)
    core = _core_order(db)
    line = _core_line(db, core)
    pso = _planned(db, core)
    mirror = _mirror(db, pso, line)
    inquiry = _inquiry(db, pso, number=f"{MARKER}-OI-N", rows=())
    _inquiry_row(db, inquiry, mirror)

    with TestClient(app) as c:
        res = c.get("/api/v1/scm/sales-orders", params={"query": core.so_number})

    assert res.status_code == 200, res.text
    listed = _row_for(res.json(), core.so_number)["lines"][0]
    assert listed["order_inquiry"] is None
    assert listed["decision_revision"] is None


# --------------------------------------------------------------------------- #
# AC-D4: the Lines tab says what was suggested and what was decided
#
# The SO detail is the SECONDARY surface for PLAN section 2's vocabulary (the board is the
# primary one), so it carries the same two compositions per line and the screen words them
# with the same `describe()`. Text is deliberately NOT built here: one vocabulary, one
# implementation, and a sentence composed on this side would be a second one.
# --------------------------------------------------------------------------- #


def _decision_with_components(db, pso, *, core_line_id, components, proposed=...):
    from app.models.project_so import DECISION_ACTIVE, SOSupplyDecision

    snapshot = {"core_line_id": str(core_line_id), "components": components}
    if proposed is not ...:
        snapshot["proposed_components"] = proposed
    decision = SOSupplyDecision(
        id=_uid(),
        company_id=pso.company_id,
        project_sales_order_id=pso.id,
        revision_no=1,
        state=DECISION_ACTIVE,
        confirmed_at=datetime(2026, 6, 1, 9, 0),
        line_snapshots=[snapshot],
    )
    db.add(decision)
    db.flush()
    return decision


def test_a_decided_line_carries_both_compositions_in_the_boards_own_words(scm_app):
    app, db, _uid_ = _as(scm_app)
    core = _core_order(db)
    line = _core_line(db, core)
    pso = _planned(db, core)
    _decision_with_components(
        db,
        pso,
        core_line_id=line.id,
        components=[{"kind": "buy", "qty": "20", "reason": "remaining uncovered need"}],
        proposed=[
            {
                "kind": "reserve",
                "qty": "20",
                "source_location": "BRW",
                "rung": "pool",
                "reason": "free stock at BRW covers the need",
            }
        ],
    )

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    assert res.status_code == 200, res.text
    body = next(l for l in res.json()["lines"] if l["id"] == line.id)
    # Both survive `response_model`, which silently drops anything undeclared.
    assert "supply_proposed" in body, body.keys()
    assert "supply_decided" in body, body.keys()
    assert body["supply_decided"] == [
        {
            "kind": "buy",
            "qty": "20",
            "source_location": None,
            "rung": None,
            "donor_so_number": None,
        }
    ]
    assert body["supply_proposed"] == [
        {
            "kind": "reserve",
            "qty": "20",
            "source_location": "BRW",
            "rung": "pool",
            "donor_so_number": None,
        }
    ]


def test_a_line_decided_before_the_proposal_was_frozen_says_not_recorded(scm_app):
    """Null, never an empty list: an old revision recorded no suggestion, which is not the
    same claim as "the engine suggested nothing"."""
    app, db, _uid_ = _as(scm_app)
    core = _core_order(db)
    line = _core_line(db, core)
    pso = _planned(db, core)
    _decision_with_components(
        db,
        pso,
        core_line_id=line.id,
        components=[{"kind": "buy", "qty": "5", "reason": "remaining uncovered need"}],
    )

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    body = next(l for l in res.json()["lines"] if l["id"] == line.id)
    assert body["supply_decided"] == [
        {
            "kind": "buy",
            "qty": "5",
            "source_location": None,
            "rung": None,
            "donor_so_number": None,
        }
    ]
    assert body["supply_proposed"] is None


def test_an_undecided_line_carries_neither_composition(scm_app):
    app, db, _uid_ = _as(scm_app)
    core = _core_order(db)
    line = _core_line(db, core)

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    body = next(l for l in res.json()["lines"] if l["id"] == line.id)
    assert body["supply_decided"] is None
    assert body["supply_proposed"] is None


# --------------------------------------------------------------------------- #
# D10 (captain, 3 Sep): the SO page's Lines tab shows a SAVED (unconfirmed) decision too,
# not only a confirmed one - a save on the planning board used to answer "-"/"-"/"-" here
# until Confirm, which read as the save having done nothing.
# --------------------------------------------------------------------------- #

#: A composed decision exercising all three kinds `_saved_components` reads (reserve,
#: borrow, buy) in one save, in the frontend's own `BoardDecision` words - opaque to the
#: server, which stores and hands it back (`project_line_draft_service`).
SAVED_DECISION = {
    "verdict": "amended",
    "reserve": [
        {
            "warehouse_id": "irrelevant-here",
            "location": f"{MARKER}-BRW",
            "qty": "5",
            "rung": "pool",
        }
    ],
    "borrow": [
        {
            "source": "location",
            "warehouse_id": "irrelevant-here",
            "warehouse_code": f"{MARKER}-MWH",
            "qty": "2",
            "reason": "borrowed",
            "donor_so_number": f"{MARKER}-DONOR",
        }
    ],
    "buy_qty": "3",
    "reason": "Composed by hand.",
}


def _save_draft(
    db, core: SalesOrder, line_no: int, item_code: str, *, decision, saved_by, proposed=None
):
    from app.services import project_line_draft_service

    key = f"{core.id}|{line_no}|{item_code}|{MARKER}-bucket"
    return project_line_draft_service.save_draft(
        db, key, decision=decision, proposed=proposed, actor_user_id=saved_by
    )


def test_a_saved_but_unconfirmed_decision_shows_its_own_composition(scm_app):
    """D10: Save decision on the board reaches this page before Confirm does."""
    app, db, uid = _as(scm_app)
    core = _core_order(db)
    core.demand_class = "project"
    line = _core_line(db, core)
    db.flush()
    item_code = line.product.product_code

    saved = _save_draft(db, core, 1, item_code, decision=SAVED_DECISION, saved_by=uid)
    assert saved["saved_by"] == "SCM Test"

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    assert res.status_code == 200, res.text
    body = next(l for l in res.json()["lines"] if l["id"] == line.id)
    # Every field survives `response_model`, which silently drops anything undeclared.
    for field in ("supply_saved", "saved_by", "saved_at", "saved_stale"):
        assert field in body, body.keys()
    assert body["supply_saved"] == [
        {
            "kind": "reserve",
            "qty": "5",
            "source_location": f"{MARKER}-BRW",
            "rung": "pool",
            "donor_so_number": None,
        },
        {
            "kind": "borrow",
            "qty": "2",
            "source_location": f"{MARKER}-MWH",
            "rung": None,
            "donor_so_number": f"{MARKER}-DONOR",
        },
        {
            "kind": "buy",
            "qty": "3",
            "source_location": None,
            "rung": None,
            "donor_so_number": None,
        },
    ]
    assert body["saved_by"] == "SCM Test"
    assert body["saved_at"]
    assert body["saved_stale"] is False
    # Not confirmed: no active revision, so the confirmed columns stay null beside it.
    assert body["decision_revision"] is None
    assert body["supply_decided"] is None


def test_an_approved_draft_shows_its_composition_too(scm_app):
    """D11: `BoardLineDecisionPanel.save()` now writes the suggested COMPOSITION onto an
    approved draft, not only `{verdict: 'approved'}` - so a plain accept of the engine's
    suggestion reads a Decided composition here too, the same as an amendment does
    (`test_a_saved_but_unconfirmed_decision_shows_its_own_composition` above). No server
    change was needed for this: `_saved_components` already reads any decision this shape,
    verdict included or not.
    """
    app, db, uid = _as(scm_app)
    core = _core_order(db)
    core.demand_class = "project"
    line = _core_line(db, core)
    db.flush()
    item_code = line.product.product_code

    approved_decision = {
        "verdict": "approved",
        "reserve": [
            {
                "warehouse_id": "irrelevant-here",
                "location": f"{MARKER}-BRW",
                "qty": "3",
                "rung": "pool",
            }
        ],
        "borrow": [],
        "buy_qty": "0",
    }
    _save_draft(db, core, 1, item_code, decision=approved_decision, saved_by=uid)

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    assert res.status_code == 200, res.text
    body = next(l for l in res.json()["lines"] if l["id"] == line.id)
    assert body["supply_saved"] == [
        {
            "kind": "reserve",
            "qty": "3",
            "source_location": f"{MARKER}-BRW",
            "rung": "pool",
            "donor_so_number": None,
        },
    ]
    assert body["saved_stale"] is False


def test_a_saved_draft_carries_its_own_suggested_composition_too(scm_app):
    """D12 (#573, captain 3 Sep): a saved draft keeps the engine's suggestion at save
    time, so `supply_proposed` reads it here too - the way the board's list view already
    reads a live composition - until Confirm freezes a revision.

    `proposed` is stored in the board's `BoardSource` wire shape (`location`, not
    `source_location`) - the fallback `_supply_components` gained for this is what proves
    it converts correctly here.
    """
    app, db, uid = _as(scm_app)
    core = _core_order(db)
    core.demand_class = "project"
    line = _core_line(db, core)
    db.flush()
    item_code = line.product.product_code

    proposed = [
        {
            "kind": "reserve",
            "qty": "3",
            "location": f"{MARKER}-BRW",
            "reason": "Reserve from BRW",
            "rung": "pool",
        }
    ]
    _save_draft(
        db,
        core,
        1,
        item_code,
        decision={"verdict": "approved"},
        saved_by=uid,
        proposed=proposed,
    )

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    assert res.status_code == 200, res.text
    body = next(l for l in res.json()["lines"] if l["id"] == line.id)
    assert body["decision_revision"] is None
    assert body["supply_decided"] is None
    assert body["supply_proposed"] == [
        {
            "kind": "reserve",
            "qty": "3",
            "source_location": f"{MARKER}-BRW",
            "rung": "pool",
            "donor_so_number": None,
        },
    ]


def test_a_saved_draft_with_no_proposal_reads_supply_proposed_null(scm_app):
    """Additive and optional (D12): a draft saved before this field existed, or one saved
    with nothing offered, reads `supply_proposed` null - never "Not recorded", which is
    reserved for a CONFIRMED revision frozen before the field existed."""
    app, db, uid = _as(scm_app)
    core = _core_order(db)
    core.demand_class = "project"
    line = _core_line(db, core)
    db.flush()
    item_code = line.product.product_code

    _save_draft(db, core, 1, item_code, decision={"verdict": "approved"}, saved_by=uid)

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    body = next(l for l in res.json()["lines"] if l["id"] == line.id)
    assert body["supply_proposed"] is None


def test_a_confirmed_line_still_shows_the_frozen_revisions_own_proposal(scm_app):
    """D12 does not touch a line an ACTIVE revision covers: `supply_proposed` still comes
    off `decided`'s own frozen `proposed_components`, never a draft - Confirm deletes the
    draft it promotes in the same write, so the two never coexist on a real line. This is
    the same fact `test_a_decided_line_carries_both_compositions_in_the_boards_own_words`
    proves; asserted again here with `decision_revision` read alongside it, which is what
    `serialize()`'s branch on D12 actually keys off."""
    app, db, _uid_ = _as(scm_app)
    core = _core_order(db)
    line = _core_line(db, core)
    pso = _planned(db, core)
    _decision_with_components(
        db,
        pso,
        core_line_id=line.id,
        components=[{"kind": "buy", "qty": "20", "reason": "remaining uncovered need"}],
        proposed=[
            {
                "kind": "reserve",
                "qty": "20",
                "source_location": "BRW",
                "rung": "pool",
                "reason": "free stock at BRW covers the need",
            }
        ],
    )

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    body = next(l for l in res.json()["lines"] if l["id"] == line.id)
    assert body["decision_revision"] == 1
    assert body["supply_proposed"] == [
        {
            "kind": "reserve",
            "qty": "20",
            "source_location": "BRW",
            "rung": "pool",
            "donor_so_number": None,
        }
    ]


def test_a_line_with_no_saved_decision_reads_null(scm_app):
    app, db, _uid_ = _as(scm_app)
    core = _core_order(db)
    core.demand_class = "project"
    line = _core_line(db, core)
    db.flush()

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    body = next(l for l in res.json()["lines"] if l["id"] == line.id)
    assert body["supply_saved"] is None
    assert body["saved_by"] is None
    assert body["saved_at"] is None
    assert body["saved_stale"] is False


def test_a_saved_decision_whose_line_has_since_moved_reads_stale(scm_app):
    """AC-4.4's own predicate, read on this page too: the line's own PLAN QUANTITY moved
    since the save (a re-upload, say), never the proposal.

    AC-S2-17: the ask is `coalesce(qty_required, qty_ordered)`, and a change to it is the
    thing a saved suggestion can go out of date against - the planner decided supply for 10
    units and there are 25 to find. The trigger below is exactly that change and is unchanged
    by the plan-qty rule; what moved is the word for the figure.
    """
    app, db, uid = _as(scm_app)
    core = _core_order(db)
    core.demand_class = "project"
    line = _core_line(db, core, qty=10)
    db.flush()
    item_code = line.product.product_code
    _save_draft(db, core, 1, item_code, decision=SAVED_DECISION, saved_by=uid)

    line.qty_ordered = 25
    db.flush()

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    body = next(l for l in res.json()["lines"] if l["id"] == line.id)
    assert body["saved_stale"] is True


def test_a_saved_decision_on_a_delivered_line_is_not_stale_on_the_detail_page(scm_app):
    """AC-S2-17's coupling, guarded on the surface that will break if only one side moves.

    GREEN, AND THAT IS THE POINT. Three places decide whether a saved suggestion is out of
    date, and they have to name the same figure:

      * the WRITER, `project_line_draft_service._line_snapshot`, which freezes the line's own
        facts at save time;
      * the BOARD's reader, `project_fulfilment_board_service._attach_drafts`, which passes
        `row.qty`;
      * THIS PAGE's reader, `sales_order_service._saved_is_stale`.

    All three read the plan quantity. The board moved there first, under the 14 September
    ruling; the writer and this page followed. Move any one of them back and the line below
    breaks: the snapshot would say 3 while the comparison said 0, so EVERY saved decision on a
    delivered line would read stale on the sales order detail - a warning about a change
    nobody made, on the very lines this lane exists to plan, and CS sent to re-decide them.

    The line is SO421404's own shape, 3 ordered and 3 delivered, because that is where the
    still-owed figure and the plan quantity differ. On a line with nothing delivered the two
    coincide and a test there would pass whichever figure either side named, which is the
    same as not having the test at all.
    """
    app, db, uid = _as(scm_app)
    core = _core_order(db)
    core.demand_class = "project"
    core.status = "closed"
    line = _planning_line(db, core, qty=3, delivered=3, line_status="closed")
    db.flush()
    item_code = line.product.product_code

    _save_draft(db, core, 1, item_code, decision=SAVED_DECISION, saved_by=uid)

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    assert res.status_code == 200, res.text
    body = next(l for l in res.json()["lines"] if l["id"] == line.id)
    assert body["supply_saved"] is not None, "sanity: the draft reached this page at all"
    assert body["saved_stale"] is False, (
        "nothing changed since the save; the snapshot and this page's reader have to be "
        "comparing the same figure"
    )


def test_confirming_the_line_replaces_the_saved_decision_with_the_confirmed_one(scm_app):
    """What Confirm actually does to a saved line (D10): it deletes the draft and writes
    the active decision, inside the SAME transaction
    (`ProjectSupplyService._write_decision` calls `project_line_draft_service.
    delete_drafts_for_lines`). Simulated here at those same two calls rather than by
    running the whole engine: this page's own job is only to read the aftermath right, and
    the confirm write itself is covered in `tests/test_so_supply_confirmation.py`."""
    from app.services import project_line_draft_service

    app, db, uid = _as(scm_app)
    core = _core_order(db)
    core.demand_class = "project"
    line = _core_line(db, core)
    pso = _planned(db, core)
    db.flush()
    item_code = line.product.product_code
    _save_draft(db, core, 1, item_code, decision=SAVED_DECISION, saved_by=uid)

    deleted = project_line_draft_service.delete_drafts_for_lines(db, [line.id])
    assert deleted == 1
    _active_decision(db, pso, revision_no=1, core_line_ids=[line.id])

    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    assert res.status_code == 200, res.text
    body = next(l for l in res.json()["lines"] if l["id"] == line.id)
    assert body["supply_saved"] is None
    assert body["saved_by"] is None
    assert body["saved_stale"] is False
    assert body["decision_revision"] == 1


# --------------------------------------------------------------------------------------- #
# S4 - "Planned" on the sales orders list and detail header
#
# `PLAN-fulfilment-board-plans-delivered-lines.md` S4, UAC AC-S4-1 to AC-S4-5. Written
# TEST-FIRST: `SalesOrderService.with_planning_state` does not exist yet, and neither field
# is on either response.
#
# The owner's question off SO421404 (14 Sep 2026): "how do I know if the order is fully
# planned from the list itself?" The Order inquiries column above answers what purchasing was
# TOLD. Nothing answers whether anybody decided where the stock comes from - so a Completed
# order with three delivered lines and no plan at all reads exactly like one that was planned.
#
# `plannable_lines` = the lines the BOARD would admit (`is_undecided_demand()`: not cancelled,
#                     not `covered`, plan quantity above zero - delivery is not a decision).
# `planned_lines`   = how many of those are decided: an active decision on the core line, or a
#                     live order inquiry row on it (state not `cancelled`, ack not `rejected`).
#
# THE SAME two predicates the board uses, off one helper, or the list and the board come to
# disagree about the same order.
# --------------------------------------------------------------------------------------- #


def _planning_line(
    db,
    core: SalesOrder,
    *,
    qty=3,
    delivered=0,
    line_status="open",
    purchasing_status="not_reviewed",
):
    """`_core_line` with the three columns the board's admission predicate reads."""
    from app.models.order import SalesOrderLine

    line = SalesOrderLine(
        id=_uid(),
        sales_order_id=core.id,
        product_id=_product(db).id,
        qty_ordered=qty,
        qty_delivered=delivered,
        line_status=line_status,
        purchasing_status=purchasing_status,
        required_date=date(2026, 6, 30),
    )
    db.add(line)
    db.flush()
    return line


def test_list_and_detail_carry_planned_counts(scm_app):
    """AC-S4-1. BOTH manual dict builders, asserted over the WIRE: `response_model` silently
    drops what it does not declare, so a field the service computes and the schema forgets
    reaches the screen as `undefined` - which reads as a backend with no answer."""
    app, db, _u = _as(scm_app)
    core = _core_order(db)
    _planning_line(db, core)

    with TestClient(app) as c:
        listed = c.get("/api/v1/scm/sales-orders", params={"query": core.so_number})
        detail = c.get(f"/api/v1/scm/sales-orders/{core.id}")

    assert listed.status_code == 200, listed.text
    row = _row_for(listed.json(), core.so_number)
    assert "planned_lines" in row, row.keys()
    assert "plannable_lines" in row, row.keys()
    assert isinstance(row["planned_lines"], int)
    assert isinstance(row["plannable_lines"], int)

    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert "planned_lines" in body, body.keys()
    assert "plannable_lines" in body, body.keys()
    assert (body["planned_lines"], body["plannable_lines"]) == (
        row["planned_lines"], row["plannable_lines"]
    ), "one order, one answer - the list and the detail read the same helper"


def test_planned_counts_mixed(scm_app):
    """AC-S4-2. Three admitted lines: one an active decision covers, one a live inquiry row
    covers, one nobody has touched. Two of three planned.

    Both readings of "decided" count, because both are what the BOARD holds read-only.
    """
    app, db, _u = _as(scm_app)
    core = _core_order(db)
    decided = _planning_line(db, core)
    told = _planning_line(db, core)
    _untouched = _planning_line(db, core)

    pso = _planned(db, core)
    _active_decision(db, pso, revision_no=1, core_line_ids=[decided.id])
    inquiry = _inquiry(db, pso, number=f"{MARKER}-OI-S4", rows=())
    _inquiry_row(db, inquiry, _mirror(db, pso, told, line_no=2))

    with TestClient(app) as c:
        res = c.get("/api/v1/scm/sales-orders", params={"query": core.so_number})

    row = _row_for(res.json(), core.so_number)
    assert row["plannable_lines"] == 3
    assert row["planned_lines"] == 2


def test_plannable_zero_when_all_cancelled_or_covered(scm_app):
    """AC-S4-3. Nothing to plan is not "not planned": the pill reads a dash, off
    `plannable_lines` being 0, and a green "Planned" there would claim a decision nobody
    made."""
    app, db, _u = _as(scm_app)
    core = _core_order(db)
    _planning_line(db, core, line_status="cancelled", delivered=1)
    _planning_line(db, core, purchasing_status="covered")

    with TestClient(app) as c:
        res = c.get("/api/v1/scm/sales-orders", params={"query": core.so_number})

    row = _row_for(res.json(), core.so_number)
    assert row["plannable_lines"] == 0
    assert row["planned_lines"] == 0


def test_closed_delivered_order_without_plan_reads_zero_of_three(scm_app):
    """AC-S4-4. SO421404's own shape - Completed, three of three delivered, no plan ever made.

    Delivery is not a decision, so all three lines are still plannable and none is planned.
    This is the row the owner was looking at when they asked the question.
    """
    app, db, _u = _as(scm_app)
    core = _core_order(db)
    core.status = "closed"
    db.flush()
    for _ in range(3):
        _planning_line(db, core, qty=1, delivered=1, line_status="closed")

    with TestClient(app) as c:
        res = c.get("/api/v1/scm/sales-orders", params={"query": core.so_number})

    row = _row_for(res.json(), core.so_number)
    assert row["plannable_lines"] == 3
    assert row["planned_lines"] == 0


def test_planned_counts_are_one_query_per_page(scm_app):
    """AC-S4-5. ONE grouped query for the page, never one per row.

    The book holds 15,000 sales orders, so a per-row read is an N+1 that only shows itself in
    production. Asserted as a CONSTANT rather than as an exact number: what must not happen is
    the count growing with the page, and pinning "exactly 2" would break on any unrelated tidy
    of the same helper.
    """
    from sqlalchemy import event

    from app.services.scm.sales_order_service import SalesOrderService

    _app, db, _u = _as(scm_app)
    service = SalesOrderService(db)

    rows = []
    for _ in range(25):
        core = _core_order(db)
        _planning_line(db, core)
        rows.append(service.serialize(core))

    bind = db.get_bind()
    counted = {"n": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        counted["n"] += 1

    event.listen(bind, "before_cursor_execute", _count)
    try:
        counted["n"] = 0
        service.with_planning_state(rows[:1])
        for_one = counted["n"]

        counted["n"] = 0
        service.with_planning_state(rows)
        for_twenty_five = counted["n"]
    finally:
        event.remove(bind, "before_cursor_execute", _count)

    assert for_twenty_five == for_one, (
        f"the page's planning state cost {for_twenty_five} statements for 25 orders and "
        f"{for_one} for one - that is per ROW, not per page"
    )
    assert for_twenty_five <= 3, for_twenty_five
