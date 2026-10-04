"""Saving a whole drafted sales order, and deleting one so the build can be repeated.

The sales order detail screen is an edit VIEW now, the way the quotation document and the
customer PO already are: nothing is written while somebody types, and one Save covers the
header and every line. That needs one atomic write, so ``PUT /sales-orders/{pso_id}`` accepts
an optional ``lines`` array holding the FULL desired set.

Deleting is the other half of the same request from the client: *"the sales order must be able
to get deleted so the process of building the draft SO can be repeated"*. The build is
idempotent per (PO version, schedule version), so removing a draft and building again is the
supported correction - and the tests below pin BOTH what the delete takes with it and what it
refuses to touch, because a live commitment is amended, never deleted.

Route-level, because the decisions worth pinning are at the HTTP boundary: the whole-set
semantics (a stored line whose id is absent is deleted), the refusals a client must be able to
show a person, and the fact that a header-only save leaves the lines alone.

Every test seeds its own chain. Nothing is borrowed off the shared database, which is a copy
of production and empty in CI.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.project_so import (
    AMENDMENT_PROPOSED,
    AMENDMENT_PUBLISHED,
    DECISION_ACTIVE,
    DECISION_SUPERSEDED,
    DIVERGENCE_OPEN,
    INQUIRY_RAISED,
    SO_STATUS_DRAFT,
    SO_STATUS_PUBLISHED,
    AllocationClaim,
    OrderChangeNotice,
    OrderInquiry,
    OrderInquiryRow,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
    ProjectSODivergence,
    ProjectSODivergenceLine,
    SOAmendment,
    SODraftFinding,
    SOLineAllocation,
    SOSupplyDecision,
)
from app.models.projects import ProjectParty, ProjectPurchaseOrder
from app.models.user import User
from app.services import project_seed_service

from ._pg_fixture import blank_session

MARKER = "zzt-so-doc"
BASE = "/api/v1/project-sales"
EDIT = "projects.projects.edit"
DELETE = "projects.projects.delete"


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _user(db, name: str) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _product(db, code_hint: str, list_price: str = "100.00") -> Product:
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:4]}", uom_name="Piece")
    category = ProductCategory(
        id=_uid(), category_code=f"ZZT-{_uid()[:8]}", category_name=f"{MARKER} cat"
    )
    db.add_all([uom, category])
    db.flush()
    row = Product(
        id=_uid(),
        product_code=f"ZZT-{code_hint}-{_uid()[:6]}",
        product_name=f"{MARKER} {code_hint}",
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=Decimal(list_price),
    )
    db.add(row)
    db.commit()
    return row


def _client(db, user_id: str):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    actor = {"id": user_id, "email": f"{user_id}@zzt.test", "role": "superadmin"}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)
    app.dependency_overrides[apply_company_scope] = lambda: None

    originals = (
        UserPermissionService.check_user_has_permission,
        UserPermissionService.get_user_permission_slugs,
    )
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: True
    UserPermissionService.get_user_permission_slugs = lambda self, uid: [
        "projects.projects.view",
        "projects.projects.create",
        "projects.projects.edit",
        "projects.projects.delete",
        "projects.projects.manage",
    ]
    return TestClient(app), originals


def _restore(originals) -> None:
    from app.main import app
    from app.services.user_service import UserPermissionService

    UserPermissionService.check_user_has_permission = originals[0]
    UserPermissionService.get_user_permission_slugs = originals[1]
    app.dependency_overrides.clear()


@pytest.fixture()
def api():
    from app.models.base import company_scope
    from app.services.project_service import register_project

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        user_id = _user(db, f"{MARKER} CONTACT AH")
        project = register_project(
            db,
            company_id=company_id,
            actor_user_id=user_id,
            developer_party_id=None,
            title=f"{MARKER} Tuju Residences",
        )
        db.commit()
        client, originals = _client(db, user_id)
        try:
            with company_scope(db, frozenset({company_id})):
                yield client, db, company_id, user_id, project
        finally:
            _restore(originals)


def _po(db, project) -> ProjectPurchaseOrder:
    party = ProjectParty(
        id=_uid(),
        company_id=project.company_id,
        party_type="trading_house",
        name=f"{MARKER} Buimaco {_uid()[:6]}",
    )
    db.add(party)
    db.flush()
    row = ProjectPurchaseOrder(
        id=_uid(),
        company_id=project.company_id,
        project_id=project.id,
        po_source="trading_house",
        issuing_party_id=party.id,
        po_number=f"HQ/26/01/{_uid()[:4]}",
        po_date=date(2026, 1, 19),
        term_days=60,
        status="approved",
    )
    db.add(row)
    db.commit()
    return row


def _order(
    db,
    project,
    *,
    po=None,
    status: str = SO_STATUS_DRAFT,
    area_group: str | None = "TOWER",
    lines: list[tuple[Product | None, str, str, str]] | None = None,
) -> ProjectSalesOrder:
    """A drafted sales order and its lines, seeded directly.

    Direct rather than through ``build``: the whole-document save and the delete act on the
    ROWS, and driving the whole extraction / schedule-spread chain to produce them would test
    the builder instead of the routes under test here. ``lines`` is
    (product, qty, unit_price, uom) in printed order.
    """
    order = ProjectSalesOrder(
        id=_uid(),
        company_id=project.company_id,
        project_id=project.id,
        purchase_order_id=po.id if po else None,
        area_group=area_group,
        provisional_ref=f"PSO-{_uid()[:8]}",
        status=status,
        grouping_origin="area",
        total_amount=Decimal("0"),
    )
    db.add(order)
    db.flush()
    total = Decimal("0")
    for index, (product, qty, unit_price, uom) in enumerate(lines or [], start=1):
        amount = (Decimal(qty) * Decimal(unit_price)).quantize(Decimal("0.01"))
        total += amount
        db.add(
            ProjectSalesOrderLine(
                id=_uid(),
                company_id=project.company_id,
                project_sales_order_id=order.id,
                line_no=index,
                product_id=product.id if product else None,
                description=f"{MARKER} line {index}",
                qty=Decimal(qty),
                uom=uom,
                unit_price=Decimal(unit_price),
                amount=amount,
                delivery_date=date(2026, 7, 1),
                explosion_source="quotation",
            )
        )
    order.total_amount = total
    # COMMITTED, not merely flushed. Every refusal below is raised by a route that rolls the
    # session back, and `blank_session` maps a commit onto a savepoint inside the outer
    # transaction -- so without this the assertion "the order is still there" would be reading
    # a session the rollback had emptied of the fixture too, and every refusal test would fail
    # for a reason that has nothing to do with the refusal.
    db.commit()
    return order


def _stored_lines(db, pso_id: str) -> list[ProjectSalesOrderLine]:
    return (
        db.query(ProjectSalesOrderLine)
        .filter(ProjectSalesOrderLine.project_sales_order_id == pso_id)
        .order_by(ProjectSalesOrderLine.line_no.asc())
        .all()
    )


def _detail(client, pso_id: str) -> dict:
    got = client.get(f"{BASE}/sales-orders/{pso_id}")
    assert got.status_code == 200, got.text
    return got.json()


# ------------------------------------------------------------------ the save


def test_saving_the_header_alone_leaves_every_line_where_it_was(api):
    """``lines`` absent is not an empty set. The header moves; the lines do not."""
    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "10", "100.00", "UNIT")])

    saved = client.put(
        f"{BASE}/sales-orders/{order.id}", json={"area_group": "COMMON AREA"}
    )

    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["area_group"] == "COMMON AREA"
    assert body["line_count"] == 1
    assert len(_stored_lines(db, order.id)) == 1


def test_renaming_the_area_group_records_that_a_person_chose_it(api):
    """The origin says which namespace the group name lives in, so a hand rename moves it
    to `manual` exactly as Move lines does. An unchanged name leaves it alone."""
    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])

    unchanged = client.put(f"{BASE}/sales-orders/{order.id}", json={"area_group": "TOWER"})
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["grouping_origin"] == "area"

    renamed = client.put(f"{BASE}/sales-orders/{order.id}", json={"area_group": "PODIUM"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["grouping_origin"] == "manual"


def test_the_whole_line_set_is_written_in_one_request(api):
    """One kept line corrected, one added, one absent and therefore deleted."""
    client, db, _company_id, _user_id, project = api
    keep = _product(db, "KEEP")
    drop = _product(db, "DROP")
    added = _product(db, "ADD")
    order = _order(
        db,
        project,
        lines=[(keep, "10", "100.00", "UNIT"), (drop, "5", "50.00", "UNIT")],
    )
    stored = _stored_lines(db, order.id)
    keep_id, drop_id = stored[0].id, stored[1].id

    saved = client.put(
        f"{BASE}/sales-orders/{order.id}",
        json={
            "lines": [
                {
                    "id": keep_id,
                    "product_id": keep.id,
                    "description": "corrected",
                    "qty": "12",
                    "uom": "UNIT",
                    "unit_price": "100.00",
                    "delivery_date": "2026-09-01",
                },
                {
                    "product_id": added.id,
                    "description": "added by hand",
                    "qty": "3",
                    "uom": "SET",
                    "unit_price": "20.00",
                },
            ]
        },
    )

    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert [line["line_no"] for line in body["lines"]] == [1, 2]
    assert body["lines"][0]["id"] == keep_id
    assert body["lines"][0]["description"] == "corrected"
    # amount is recomputed, never accepted: 12 x 100.00
    assert Decimal(body["lines"][0]["amount"]) == Decimal("1200.00")
    assert body["lines"][0]["delivery_date"] == "2026-09-01"
    assert body["lines"][1]["id"] != drop_id
    assert Decimal(body["lines"][1]["amount"]) == Decimal("60.00")
    # 1200.00 + 60.00, recomputed once at the end
    assert Decimal(body["total_amount"]) == Decimal("1260.00")
    assert body["line_count"] == 2
    assert db.query(ProjectSalesOrderLine).filter(
        ProjectSalesOrderLine.id == drop_id
    ).first() is None


def test_a_kept_line_keeps_the_links_the_save_does_not_mention(api):
    """Only the fields on the body are written. The reconciled core line, the phase, the
    quotation line and the PO line it exploded from all survive an edit."""
    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "10", "100.00", "UNIT")])
    line = _stored_lines(db, order.id)[0]
    line.explosion_source = "package"
    db.commit()

    saved = client.put(
        f"{BASE}/sales-orders/{order.id}",
        json={
            "lines": [
                {"id": line.id, "product_id": product.id, "qty": "11", "unit_price": "100.00"}
            ]
        },
    )

    assert saved.status_code == 200, saved.text
    db.expire_all()
    after = _stored_lines(db, order.id)[0]
    assert after.id == line.id
    assert after.qty == Decimal("11.0000")
    assert after.explosion_source == "package"
    # And the order's own identity is untouched by an edit.
    assert saved.json()["provisional_ref"] == order.provisional_ref


def test_a_published_order_is_refused_and_told_to_raise_an_amendment(api):
    """It is in AutoCount. Editing it silently would leave purchasing and AutoCount looking
    at different documents, so the refusal names the way forward."""
    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(
        db,
        project,
        status=SO_STATUS_PUBLISHED,
        lines=[(product, "10", "100.00", "UNIT")],
    )

    refused = client.put(
        f"{BASE}/sales-orders/{order.id}", json={"area_group": "SOMETHING ELSE"}
    )

    assert refused.status_code == 409, refused.text
    assert "amendment" in refused.text.lower()
    db.expire_all()
    assert (
        db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id == order.id).first().area_group
        == "TOWER"
    )


def test_an_empty_line_set_is_refused(api):
    """A sales order with no lines is not a proposal, which is the same answer the builder
    gives when every line is cancelled."""
    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "10", "100.00", "UNIT")])

    refused = client.put(f"{BASE}/sales-orders/{order.id}", json={"lines": []})

    assert refused.status_code == 422, refused.text
    assert len(_stored_lines(db, order.id)) == 1


def test_a_line_from_another_order_is_refused_rather_than_treated_as_new(api):
    """Treating it as new would duplicate the row the caller meant to move."""
    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    mine = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])
    theirs = _order(db, project, area_group="OTHER", lines=[(product, "1", "1.00", "UNIT")])
    foreign = _stored_lines(db, theirs.id)[0]

    refused = client.put(
        f"{BASE}/sales-orders/{mine.id}",
        json={"lines": [{"id": foreign.id, "qty": "2", "unit_price": "1.00"}]},
    )

    assert refused.status_code == 404, refused.text
    assert len(_stored_lines(db, mine.id)) == 1
    assert len(_stored_lines(db, theirs.id)) == 1


def test_the_same_line_twice_in_one_save_is_refused(api):
    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])
    line = _stored_lines(db, order.id)[0]

    refused = client.put(
        f"{BASE}/sales-orders/{order.id}",
        json={
            "lines": [
                {"id": line.id, "qty": "1", "unit_price": "1.00"},
                {"id": line.id, "qty": "2", "unit_price": "1.00"},
            ]
        },
    )

    assert refused.status_code == 422, refused.text


def test_a_quantity_of_zero_is_refused_by_the_schema(api):
    """Validation, before anything is written: a line ordering nothing is not a line."""
    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])

    refused = client.put(
        f"{BASE}/sales-orders/{order.id}",
        json={"lines": [{"product_id": product.id, "qty": "0", "unit_price": "1.00"}]},
    )

    assert refused.status_code == 422, refused.text
    assert len(_stored_lines(db, order.id)) == 1


def test_a_reader_cannot_save_a_sales_order(api):
    """Auth denial at the route, before any of the whole-set logic runs."""
    from app.services.user_service import UserPermissionService

    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])

    granted = UserPermissionService.check_user_has_permission
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: slug != EDIT
    try:
        refused = client.put(
            f"{BASE}/sales-orders/{order.id}", json={"area_group": "HACKED", "lines": []}
        )
    finally:
        UserPermissionService.check_user_has_permission = granted

    assert refused.status_code == 403, refused.text
    db.expire_all()
    assert (
        db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id == order.id).first().area_group
        == "TOWER"
    )


# ---------------------------------------------------------------- the delete


def test_deleting_a_draft_takes_its_lines_and_everything_hanging_off_them(api):
    """Children first, over the real foreign key graph, the same order `purge.py` uses."""
    client, db, company_id, user_id, project = api
    product = _product(db, "WC")
    po = _po(db, project)
    order = _order(db, project, po=po, lines=[(product, "10", "100.00", "UNIT")])
    line = _stored_lines(db, order.id)[0]

    finding = SODraftFinding(
        id=_uid(),
        company_id=company_id,
        project_sales_order_id=order.id,
        line_id=line.id,
        severity="warn",
        code="price_vs_quotation",
        detail=f"{MARKER} warning",
    )
    allocation = SOLineAllocation(
        id=_uid(), company_id=company_id, so_line_id=line.id, source_type="own", qty=Decimal("10")
    )
    claim = AllocationClaim(
        id=_uid(),
        company_id=company_id,
        from_project_id=project.id,
        to_project_id=project.id,
        so_line_id=line.id,
        product_id=product.id,
        qty=Decimal("10"),
    )
    decision = SOSupplyDecision(
        id=_uid(),
        company_id=company_id,
        project_sales_order_id=order.id,
        revision_no=1,
        state=DECISION_SUPERSEDED,
        line_snapshots=[],
    )
    ocn = OrderChangeNotice(
        id=_uid(),
        company_id=company_id,
        ocn_number=f"OCN-{_uid()[:8]}",
        project_id=project.id,
        project_sales_order_id=order.id,
    )
    amendment = SOAmendment(
        id=_uid(),
        company_id=company_id,
        project_sales_order_id=order.id,
        ocn_id=ocn.id,
        status=AMENDMENT_PROPOSED,
    )
    inquiry = OrderInquiry(
        id=_uid(),
        company_id=company_id,
        project_sales_order_id=order.id,
        state=INQUIRY_RAISED,
        raised_by=user_id,
    )
    divergence = ProjectSODivergence(
        id=_uid(),
        company_id=company_id,
        project_sales_order_id=order.id,
        status=DIVERGENCE_OPEN,
    )
    db.add_all([finding, allocation, claim, decision, ocn, amendment, inquiry, divergence])
    db.commit()
    inquiry_row = OrderInquiryRow(
        id=_uid(),
        company_id=company_id,
        order_inquiry_id=inquiry.id,
        so_line_id=line.id,
        qty=Decimal("10"),
        verb="ORDER",
    )
    divergence_line = ProjectSODivergenceLine(
        id=_uid(),
        company_id=company_id,
        divergence_id=divergence.id,
        scope="line",
        presence="both",
        so_line_id=line.id,
        line_no=1,
    )
    db.add_all([inquiry_row, divergence_line])
    db.commit()

    # Read off the instances BEFORE the delete. Afterwards every one of them is a row that no
    # longer exists, and touching an expired attribute raises ObjectDeletedError rather than
    # answering the id it was created with.
    order_id, reference, po_id = order.id, order.provisional_ref, po.id
    child_ids = {
        SODraftFinding: finding.id,
        SOLineAllocation: allocation.id,
        AllocationClaim: claim.id,
        SOSupplyDecision: decision.id,
        OrderChangeNotice: ocn.id,
        SOAmendment: amendment.id,
        OrderInquiry: inquiry.id,
        OrderInquiryRow: inquiry_row.id,
        ProjectSODivergence: divergence.id,
        ProjectSODivergenceLine: divergence_line.id,
    }

    deleted = client.delete(f"{BASE}/sales-orders/{order_id}")

    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert body["success"] is True
    assert body["provisional_ref"] == reference
    assert body["deleted"]["projects.sales_order_lines"] == 1

    db.expire_all()
    assert db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id == order_id).first() is None
    assert _stored_lines(db, order_id) == []
    for model, row_id in child_ids.items():
        assert db.query(model).filter(model.id == row_id).first() is None, model.__name__

    # The purchase order it was drafted from is untouched, which is what makes the rebuild
    # possible at all.
    assert (
        db.query(ProjectPurchaseOrder).filter(ProjectPurchaseOrder.id == po_id).first()
        is not None
    )


def test_a_published_order_cannot_be_deleted(api):
    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(
        db, project, status=SO_STATUS_PUBLISHED, lines=[(product, "1", "1.00", "UNIT")]
    )

    refused = client.delete(f"{BASE}/sales-orders/{order.id}")

    assert refused.status_code == 409, refused.text
    db.expire_all()
    assert db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id == order.id).first()


def test_an_order_adopted_by_autocount_cannot_be_deleted(api):
    """The status may still read draft on a row an ingest matched to a document number.
    The link is the commitment, not the status beside it."""
    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])
    order.autocount_doc_no = "SO397450"
    db.commit()

    refused = client.delete(f"{BASE}/sales-orders/{order.id}")

    assert refused.status_code == 409, refused.text
    assert "AutoCount" in refused.text


def test_an_order_with_a_published_amendment_cannot_be_deleted(api):
    client, db, company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])
    db.add(
        SOAmendment(
            id=_uid(),
            company_id=company_id,
            project_sales_order_id=order.id,
            status=AMENDMENT_PUBLISHED,
        )
    )
    db.commit()

    refused = client.delete(f"{BASE}/sales-orders/{order.id}")

    assert refused.status_code == 409, refused.text
    db.expire_all()
    assert db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id == order.id).first()


def test_an_order_whose_supply_is_confirmed_cannot_be_deleted(api):
    """Stock is being held against it. Deleting would unwind a promise nobody withdrew."""
    client, db, company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])
    db.add(
        SOSupplyDecision(
            id=_uid(),
            company_id=company_id,
            project_sales_order_id=order.id,
            revision_no=1,
            state=DECISION_ACTIVE,
            line_snapshots=[],
        )
    )
    db.commit()

    refused = client.delete(f"{BASE}/sales-orders/{order.id}")

    assert refused.status_code == 409, refused.text
    db.expire_all()
    assert db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id == order.id).first()


def test_deleting_an_unknown_sales_order_is_a_404(api):
    client, _db, _company_id, _user_id, _project = api

    missing = client.delete(f"{BASE}/sales-orders/{_uid()}")

    assert missing.status_code == 404, missing.text


def test_a_reader_cannot_delete_a_sales_order(api):
    from app.services.user_service import UserPermissionService

    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])

    granted = UserPermissionService.check_user_has_permission
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: slug != DELETE
    try:
        refused = client.delete(f"{BASE}/sales-orders/{order.id}")
    finally:
        UserPermissionService.check_user_has_permission = granted

    assert refused.status_code == 403, refused.text
    db.expire_all()
    assert db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id == order.id).first()


def test_a_deleted_draft_leaves_nothing_for_a_rebuild_to_trip_over(api):
    """The client's reason for asking: *"the sales order must be able to get deleted so the
    process of building the draft SO can be repeated"*.

    The build is idempotent per (PO version, schedule version) and starts by clearing what
    that pair drafted before. With the draft deleted there is nothing to clear, and the
    clearing step has to answer "nothing replaced, nothing skipped, no reference to carry"
    rather than fail on the absence.
    """
    from app.models.project_so import DeliverySchedule, DeliveryScheduleVersion
    from app.services.project_so_draft_service import ProjectSODraftService

    client, db, company_id, _user_id, project = api
    product = _product(db, "WC")
    po = _po(db, project)
    schedule = DeliverySchedule(
        id=_uid(),
        company_id=company_id,
        project_id=project.id,
        purchase_order_id=po.id,
        label=f"{MARKER} programme",
    )
    db.add(schedule)
    db.flush()
    version = DeliveryScheduleVersion(
        id=_uid(),
        company_id=company_id,
        delivery_schedule_id=schedule.id,
        version_no=1,
        extraction_state="done",
    )
    db.add(version)
    db.commit()
    order = _order(db, project, po=po, lines=[(product, "1", "1.00", "UNIT")])
    order.schedule_version_id = version.id
    db.commit()

    assert client.delete(f"{BASE}/sales-orders/{order.id}").status_code == 200

    db.expire_all()
    replaced, skipped, committed, reusable = ProjectSODraftService(
        db
    )._clear_previous_drafts(po, version)
    assert (replaced, skipped, committed, reusable) == (0, 0, set(), {})


# ----------------------------------------------------------- the bulk delete
#
# The list lets a reviewer tick several drafts at once, so the delete has to be one call:
# N round trips would half-apply on the first refusal and leave the user looking at a
# selection that is partly gone. The rule this section pins is ALL OR NOTHING - one
# undeletable order in the selection refuses the whole call and deletes nothing, so the
# answer is "fix the selection and retry" rather than "some of it happened".

BULK = f"{BASE}/sales-orders/bulk-delete"


def test_bulk_delete_removes_every_selected_draft_and_leaves_the_rest(api):
    client, db, company_id, _user_id, project = api
    product = _product(db, "WC")
    po = _po(db, project)
    first = _order(db, project, po=po, area_group="TOWER", lines=[(product, "10", "100.00", "UNIT")])
    second = _order(db, project, po=po, area_group="PODIUM", lines=[(product, "5", "50.00", "UNIT")])
    survivor = _order(db, project, po=po, area_group="COMMON", lines=[(product, "1", "1.00", "UNIT")])
    line = _stored_lines(db, first.id)[0]
    db.add(
        SODraftFinding(
            id=_uid(),
            company_id=company_id,
            project_sales_order_id=first.id,
            line_id=line.id,
            severity="warn",
            code="price_vs_quotation",
            detail=f"{MARKER} warning",
        )
    )
    db.commit()
    first_id, second_id, survivor_id = first.id, second.id, survivor.id

    deleted = client.post(BULK, json={"ids": [first_id, second_id]})

    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert body["success"] is True
    assert body["deleted_count"] == 2
    assert body["refused"] == []
    # The counts are summed across the batch, not reported per order.
    assert body["deleted"]["projects.sales_order_lines"] == 2
    assert body["deleted"]["projects.so_draft_findings"] == 1

    db.expire_all()
    for row_id in (first_id, second_id):
        assert db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id == row_id).first() is None
        assert _stored_lines(db, row_id) == []
    assert db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id == survivor_id).first()
    assert len(_stored_lines(db, survivor_id)) == 1


def test_one_undeletable_order_refuses_the_whole_selection(api):
    """All or nothing. A half-applied bulk delete leaves the user unable to say what went."""
    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    ok = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])
    published = _order(
        db, project, status=SO_STATUS_PUBLISHED, lines=[(product, "1", "1.00", "UNIT")]
    )
    ok_id, published_id = ok.id, published.id
    published_ref = published.provisional_ref

    refused = client.post(BULK, json={"ids": [ok_id, published_id]})

    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["code"] == "so_bulk_not_deletable"
    assert [item["id"] for item in body["refused"]] == [published_id]
    assert body["refused"][0]["code"] == "so_published_not_deletable"
    assert body["refused"][0]["provisional_ref"] == published_ref
    # The sentence a person reads names the reference, not the id - and reads as English at
    # a count of one, which "1 of the selected sales order" did not.
    assert published_ref in body["message"]
    assert body["message"].startswith("One of the selected sales orders cannot be deleted")

    db.expire_all()
    assert db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id == ok_id).first()
    assert db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id == published_id).first()


def test_every_refused_order_is_named_with_its_own_reason(api):
    """Four separate facts refuse a delete, and the reviewer has to see which applies to
    which order or the selection cannot be corrected in one pass."""
    client, db, company_id, _user_id, project = api
    product = _product(db, "WC")
    adopted = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])
    adopted.autocount_doc_no = "SO397450"
    amended = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])
    db.add(
        SOAmendment(
            id=_uid(),
            company_id=company_id,
            project_sales_order_id=amended.id,
            status=AMENDMENT_PUBLISHED,
        )
    )
    supplied = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])
    db.add(
        SOSupplyDecision(
            id=_uid(),
            company_id=company_id,
            project_sales_order_id=supplied.id,
            revision_no=1,
            state=DECISION_ACTIVE,
            line_snapshots=[],
        )
    )
    db.commit()

    refused = client.post(
        BULK, json={"ids": [adopted.id, amended.id, supplied.id]}
    )

    assert refused.status_code == 409, refused.text
    codes = {item["id"]: item["code"] for item in refused.json()["refused"]}
    assert codes[adopted.id] == "so_autocount_linked"
    assert codes[amended.id] == "so_amendment_published"
    assert codes[supplied.id] == "so_supply_confirmed"


def test_an_unknown_id_in_the_selection_is_a_404(api):
    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])
    order_id = order.id

    missing = client.post(BULK, json={"ids": [order_id, _uid()]})

    assert missing.status_code == 404, missing.text
    db.expire_all()
    assert db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id == order_id).first()


def test_a_selection_spanning_two_projects_is_refused(api):
    """The ids come from ONE project's list, and the edit right is that project's. A payload
    mixing two would delete another project's drafts under this project's permission."""
    from app.services.project_service import register_project

    client, db, company_id, user_id, project = api
    other = register_project(
        db,
        company_id=company_id,
        actor_user_id=user_id,
        developer_party_id=None,
        title=f"{MARKER} Another tower",
    )
    product = _product(db, "WC")
    mine = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])
    theirs = _order(db, other, lines=[(product, "1", "1.00", "UNIT")])
    mine_id, theirs_id = mine.id, theirs.id

    refused = client.post(BULK, json={"ids": [mine_id, theirs_id]})

    assert refused.status_code == 422, refused.text
    assert "one project" in refused.text.lower()
    db.expire_all()
    assert db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id == mine_id).first()
    assert db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id == theirs_id).first()


def test_an_empty_selection_is_refused_by_the_schema(api):
    client, _db, _company_id, _user_id, _project = api

    refused = client.post(BULK, json={"ids": []})

    assert refused.status_code == 422, refused.text


def test_a_selection_beyond_the_cap_is_refused(api):
    """A cap, so one crafted payload cannot ask for a delete that holds the worker for
    minutes. 200 is far above any page of the list."""
    client, _db, _company_id, _user_id, _project = api

    refused = client.post(BULK, json={"ids": [_uid() for _ in range(201)]})

    assert refused.status_code == 422, refused.text


def test_a_reader_cannot_bulk_delete(api):
    from app.services.user_service import UserPermissionService

    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])
    order_id = order.id

    granted = UserPermissionService.check_user_has_permission
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: slug != DELETE
    try:
        refused = client.post(BULK, json={"ids": [order_id]})
    finally:
        UserPermissionService.check_user_has_permission = granted

    assert refused.status_code == 403, refused.text
    db.expire_all()
    assert db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id == order_id).first()


def test_the_same_id_twice_deletes_it_once(api):
    """A selection cannot really hold a duplicate, but a retry or a crafted payload can, and
    deleting a row twice would count it twice in the answer."""
    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])
    order_id = order.id

    deleted = client.post(BULK, json={"ids": [order_id, order_id]})

    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_count"] == 1
