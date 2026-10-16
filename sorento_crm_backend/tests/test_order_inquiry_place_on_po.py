"""Section G - "Place on PO" (PLAN-demo-followups-19aug-ladder-v2.md section G, migration
400_oi_place_on_po).

The captain, 20 Aug: "identify which outstanding PO has quantity to fulfil this order
inquiry, tag it, and the quantity to be ordered is deducted." A raised ORDER /
RESERVE_AND_ORDER row offers candidates from the product's open purchase-order lines,
netted by what other PLACED rows already tagged, soonest `expected_date` first with the
earliest covering line recommended. Placing sets `po_ref` / `po_line_id` / `state=placed`,
appends the note and writes a resolved `OrderLinkClaim`; Unplace reverses all of it. A
placed row leaves `scm.committed_v`'s confirmed leg - the deduction the captain asked for.

Most of this file runs on `blank_session` (Postgres, scratch schema) - candidates, place,
unplace and the worklist columns need only the tables involved, seeded fresh per test. The
NETTING PROOF against `scm.committed_v` cannot: that view is installed by a migration and
does not exist in the scratch schema, so it runs on `pg_session` (the real local database,
rolled back at teardown), reusing `test_so_supply_confirmation`'s seed helpers - the same
substrate `test_partial_decision_demand_invariants.py` uses for the identical reason.

Every test seeds its own chain with a `zzt-oi-place` marker rather than borrowing an
existing row (CI's database is empty).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.procurement import PurchaseOrder, PurchaseOrderLine, Supplier
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.inventory import Warehouse
from app.models.project_so import (
    ACK_ACKNOWLEDGED,
    ACK_AWAITING,
    INQUIRY_ACTIONED,
    INQUIRY_CANCELLED,
    INQUIRY_PLACED,
    INQUIRY_RAISED,
    IV_ALREADY_INBOUND,
    IV_ORDER,
    IV_RESERVE_AND_ORDER,
    SO_STATUS_DRAFT,
    OrderInquiry,
    OrderInquiryRow,
    ProjectSalesOrder,
    SOSupplyDecision,
)
from app.models.scm import OrderLinkClaim
from app.models.user import User
from app.services import project_seed_service

from ._pg_fixture import blank_session, pg_session
from .test_order_inquiry_suggested_links import _suggested_of
from .test_so_supply_confirmation import (
    _client as _confirm_client,
    _core_line as _confirm_core_line,
    _core_so as _confirm_core_so,
    _line_payload as _confirm_line_payload,
    _product as _confirm_product,
    _project_line as _confirm_project_line,
    _project_so as _confirm_project_so,
    _restore as _confirm_restore,
    _sorento as _confirm_sorento,
    _user as _confirm_user,
    _warehouse as _confirm_warehouse,
)

MARKER = "zzt-oi-place"
BASE = "/api/v1/project-sales"

READ_ONLY = ["projects.projects.view"]
PURCHASING = ["projects.projects.view", "projects.order_inquiry.action"]


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _user(db, name: str) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _product(db, company_id: str, code: str) -> Product:
    uom = UnitOfMeasure(id=_uid(), company_id=company_id, uom_code=f"ZZT{_uid()[:6]}", uom_name="Unit")
    category = ProductCategory(
        id=_uid(), company_id=company_id, category_code=f"ZZT-{_uid()[:8]}", category_name=f"{MARKER} cat"
    )
    db.add_all([uom, category])
    db.flush()
    row = Product(
        id=_uid(),
        company_id=company_id,
        product_code=code,
        product_name=f"{MARKER} {code}",
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=Decimal("100.00"),
    )
    db.add(row)
    db.flush()
    return row


def _warehouse(
    db, company_id: str, code: str, *, pool_warehouse_id: str | None = None
) -> Warehouse:
    row = Warehouse(
        id=_uid(),
        company_id=company_id,
        warehouse_code=code,
        warehouse_name=code,
        location="ZZT",
        pool_warehouse_id=pool_warehouse_id,
    )
    db.add(row)
    db.flush()
    return row


def _supplier(db, company_id: str, name: str) -> Supplier:
    row = Supplier(
        id=_uid(), company_id=company_id, supplier_code=f"ZZT-{_uid()[:8]}", supplier_name=name
    )
    db.add(row)
    db.flush()
    return row


def _po(db, company_id: str, supplier: Supplier, po_number: str) -> PurchaseOrder:
    """An OUTSTANDING purchase order - ``status="active"``, never the model's own bare
    ``"draft"`` default. Code review, 21 Aug (B3): ``_open_po_lines_for_product`` now
    gates candidates to ``active``/``partial`` (a draft is not yet a real order - the
    same M4-D5 doctrine that keeps a draft outside ``scm.on_order_v``), so every
    fixture in this file that means "a real PO a row can be placed against" has to say
    so explicitly."""
    row = PurchaseOrder(
        id=_uid(), company_id=company_id, po_number=po_number,
        supplier_id=supplier.id if supplier else None, status="active",
    )
    db.add(row)
    db.flush()
    return row


def _po_line(
    db, company_id: str, po: PurchaseOrder, product: Product, warehouse,
    *, qty_ordered, qty_received="0", expected_date=None, line_status="open",
) -> PurchaseOrderLine:
    row = PurchaseOrderLine(
        id=_uid(),
        company_id=company_id,
        purchase_order_id=po.id,
        product_id=product.id,
        warehouse_id=warehouse.id if warehouse else None,
        qty_ordered=Decimal(str(qty_ordered)),
        qty_received=Decimal(str(qty_received)),
        expected_date=expected_date,
        line_status=line_status,
    )
    db.add(row)
    db.flush()
    return row


def _row(
    db, company_id: str, inquiry: OrderInquiry, *,
    verb=IV_ORDER, state=INQUIRY_RAISED, qty="10", item_code=None,
    po_ref=None, po_line_id=None, note=None, ack_state=ACK_ACKNOWLEDGED,
) -> OrderInquiryRow:
    """One instruction, ACKNOWLEDGED by default (`PLAN-scm-oi-handshake.md`).

    Linking is purchasing's word since the handshake, and the cascade this file exercises
    refuses a row nobody has taken on. These rows stand for instructions a buyer is
    working, so they are seeded as such; `ack_state` is a parameter for the test that
    wants an awaiting one.
    """
    row = OrderInquiryRow(
        id=_uid(),
        company_id=company_id,
        order_inquiry_id=inquiry.id,
        item_code=item_code,
        qty=Decimal(str(qty)),
        verb=verb,
        state=state,
        ack_state=ack_state,
        po_ref=po_ref,
        po_line_id=po_line_id,
        note=note,
    )
    db.add(row)
    db.flush()
    return row


def _client(db, user_id: str, permissions):
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
    granted = list(permissions)
    UserPermissionService.check_user_has_permission = (
        lambda self, uid, slug: slug in granted
    )
    UserPermissionService.get_user_permission_slugs = lambda self, uid: list(granted)
    return TestClient(app), originals


def _restore(originals) -> None:
    from app.main import app
    from app.services.user_service import UserPermissionService

    UserPermissionService.check_user_has_permission = originals[0]
    UserPermissionService.get_user_permission_slugs = originals[1]
    app.dependency_overrides.clear()


def _seed_world(db, company_id: str, user_id: str) -> dict:
    from app.services.project_service import register_project

    project = register_project(
        db, company_id=company_id, actor_user_id=user_id, developer_party_id=None,
        title=f"{MARKER} Residences {_uid()[:12]}",
    )
    order = ProjectSalesOrder(
        id=_uid(),
        company_id=company_id,
        project_id=project.id,
        area_group="TOWER",
        provisional_ref=f"ZZT-PSO-{_uid()[:8]}",
        autocount_doc_no=f"SO{_uid()[:6].upper()}",
        status=SO_STATUS_DRAFT,
        grouping_origin="area",
    )
    db.add(order)
    db.flush()
    inquiry = OrderInquiry(
        id=_uid(), company_id=company_id, project_sales_order_id=order.id, state=INQUIRY_RAISED,
    )
    db.add(inquiry)
    db.flush()
    product = _product(db, company_id, f"ZZT-BASIN-{_uid()[:6]}")
    other_product = _product(db, company_id, f"ZZT-WC-{_uid()[:6]}")
    warehouse = _warehouse(db, company_id, f"ZZT{_uid()[:6]}")
    # H: cascadability is decided by the FK, not the code's shape, and a warehouse only
    # counts as a pool once something points at it via `pool_warehouse_id`. None of this
    # file's rows carry a `stock_location` (AC-H6: a row naming none is dealt pool lines
    # only), so `warehouse` has to genuinely be one for the automatic-pass assertions in
    # this file to mean what they say.
    _warehouse(db, company_id, f"ZZT{_uid()[:6]}-SIB", pool_warehouse_id=warehouse.id)
    supplier = _supplier(db, company_id, f"{MARKER} Dafuyuan")
    po = _po(db, company_id, supplier, f"ZZT-PO-{_uid()[:8]}")
    db.commit()
    return {
        "company_id": company_id,
        "project": project,
        "order": order,
        "inquiry": inquiry,
        "product": product,
        "other_product": other_product,
        "warehouse": warehouse,
        "supplier": supplier,
        "po": po,
    }


def _api(permissions):
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        user_id = _user(db, f"{MARKER} Eling")
        world = _seed_world(db, company_id, user_id)
        client, originals = _client(db, user_id, permissions)
        try:
            with company_scope(db, frozenset({company_id})):
                yield client, db, world, user_id
        finally:
            _restore(originals)


@pytest.fixture()
def api():
    yield from _api(PURCHASING)


@pytest.fixture()
def reader_api():
    yield from _api(READ_ONLY)


# ---------------------------------------------------------------- candidates


def test_candidates_are_soonest_expected_date_first_with_the_earliest_covering_recommended(api):
    client, db, world, _user_id = api
    early_short = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"],
        qty_ordered="10", expected_date=date(2026, 9, 1),
    )
    late_covers = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"],
        qty_ordered="30", expected_date=date(2026, 9, 15),
    )
    row = _row(db, world["company_id"], world["inquiry"], qty="25", item_code=world["product"].product_code)

    response = client.get(f"{BASE}/order-inquiry-rows/{row.id}/po-candidates")

    assert response.status_code == 200, response.text
    body = response.json()["candidates"]
    assert [c["po_line_id"] for c in body] == [early_short.id, late_covers.id]
    assert body[0]["covers"] is False
    assert body[0]["recommended"] is False
    assert body[0]["remaining"] == "10"
    assert body[1]["covers"] is True
    assert body[1]["recommended"] is True
    assert body[1]["remaining"] == "30"


def test_candidates_are_netted_by_what_other_placed_rows_already_tagged(api):
    client, db, world, _user_id = api
    line = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"],
        qty_ordered="50", expected_date=date(2026, 9, 1),
    )
    row_a = _row(db, world["company_id"], world["inquiry"], qty="20", item_code=world["product"].product_code)
    placed = client.post(
        f"{BASE}/order-inquiry-rows/{row_a.id}/place-on-po", json={"po_line_id": line.id}
    )
    assert placed.status_code == 200, placed.text

    row_b = _row(db, world["company_id"], world["inquiry"], qty="25", item_code=world["product"].product_code)
    response = client.get(f"{BASE}/order-inquiry-rows/{row_b.id}/po-candidates")

    assert response.status_code == 200, response.text
    body = response.json()["candidates"]
    assert body[0]["already_tagged"] == "20"
    assert body[0]["remaining"] == "30"
    assert body[0]["covers"] is True


def test_candidates_claims_array_names_every_other_row_tagged_with_price(api):
    """The candidate's expand (section G): every OTHER row already tagged to the line,
    one by one, plus the line's own held price - not just the `already_tagged` total."""
    client, db, world, _user_id = api
    line = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"],
        qty_ordered="50", expected_date=date(2026, 9, 10),
    )
    line.unit_cost = Decimal("12.75")
    line.currency = "MYR"
    db.flush()
    db.commit()

    row_a = _row(db, world["company_id"], world["inquiry"], qty="20", item_code=world["product"].product_code)
    placed_a = client.post(
        f"{BASE}/order-inquiry-rows/{row_a.id}/place-on-po", json={"po_line_id": line.id}
    )
    assert placed_a.status_code == 200, placed_a.text

    row_b = _row(db, world["company_id"], world["inquiry"], qty="15", item_code=world["product"].product_code)
    placed_b = client.post(
        f"{BASE}/order-inquiry-rows/{row_b.id}/place-on-po", json={"po_line_id": line.id}
    )
    assert placed_b.status_code == 200, placed_b.text

    row_c = _row(db, world["company_id"], world["inquiry"], qty="10", item_code=world["product"].product_code)
    response = client.get(f"{BASE}/order-inquiry-rows/{row_c.id}/po-candidates")

    assert response.status_code == 200, response.text
    candidate = next(c for c in response.json()["candidates"] if c["po_line_id"] == line.id)
    assert candidate["already_tagged"] == "35"
    assert candidate["unit_cost"] == "12.75"
    assert candidate["currency"] == "MYR"

    claims = candidate["claims"]
    assert len(claims) == 2
    so_numbers = {claim["so_number"] for claim in claims}
    assert so_numbers == {world["order"].autocount_doc_no}
    qtys = sorted(claim["qty"] for claim in claims)
    assert qtys == ["15", "20"]
    for claim in claims:
        assert claim["placed_date"] is not None


def test_candidates_unit_cost_and_currency_are_blank_when_the_line_carries_none(api):
    client, db, world, _user_id = api
    line = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"], qty_ordered="10",
    )
    row = _row(db, world["company_id"], world["inquiry"], qty="5", item_code=world["product"].product_code)

    response = client.get(f"{BASE}/order-inquiry-rows/{row.id}/po-candidates")

    assert response.status_code == 200, response.text
    candidate = response.json()["candidates"][0]
    assert candidate["unit_cost"] is None
    assert candidate["currency"] is None
    assert candidate["claims"] == []


def test_candidates_409_for_a_verb_that_is_not_placeable(api):
    client, db, world, _user_id = api
    row = _row(
        db, world["company_id"], world["inquiry"], verb=IV_ALREADY_INBOUND, qty="10",
        item_code=world["product"].product_code,
    )

    response = client.get(f"{BASE}/order-inquiry-rows/{row.id}/po-candidates")

    assert response.status_code == 409
    assert response.json()["code"] == "order_inquiry_not_placeable_verb"


def test_candidates_200_for_an_actioned_row_s8_widened_the_state_gate(api):
    """S8 (AC-CF-23): "Choose document" is a one-press re-link, so a row already
    ACTIONED - the old refusal this test used to pin - is offered candidates too, same
    as PLACED. Only CANCELLED is refused now (the next test)."""
    client, db, world, _user_id = api
    row = _row(
        db, world["company_id"], world["inquiry"], verb=IV_ORDER, state=INQUIRY_ACTIONED,
        qty="10", item_code=world["product"].product_code,
    )

    response = client.get(f"{BASE}/order-inquiry-rows/{row.id}/po-candidates")

    assert response.status_code == 200, response.text
    assert response.json()["still_to_link"] == "10"


def test_candidates_409_for_a_cancelled_row(api):
    client, db, world, _user_id = api
    row = _row(
        db, world["company_id"], world["inquiry"], verb=IV_ORDER, state=INQUIRY_CANCELLED,
        qty="10", item_code=world["product"].product_code,
    )

    response = client.get(f"{BASE}/order-inquiry-rows/{row.id}/po-candidates")

    assert response.status_code == 409
    assert response.json()["code"] == "order_inquiry_not_raised"


def test_a_reader_cannot_reach_candidates(reader_api):
    client, db, world, _user_id = reader_api
    row = _row(db, world["company_id"], world["inquiry"], qty="10", item_code=world["product"].product_code)

    response = client.get(f"{BASE}/order-inquiry-rows/{row.id}/po-candidates")

    assert response.status_code == 403


# ---------------------------------------------------------------------- place


def test_placing_sets_the_tag_appends_the_note_and_writes_one_resolved_claim(api):
    client, db, world, user_id = api
    line = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"],
        qty_ordered="50", expected_date=date(2026, 9, 20),
    )
    row = _row(
        db, world["company_id"], world["inquiry"], qty="30",
        item_code=world["product"].product_code, note="Already had a note",
    )

    response = client.post(
        f"{BASE}/order-inquiry-rows/{row.id}/place-on-po", json={"po_line_id": line.id}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == INQUIRY_PLACED
    assert body["po_ref"] == world["po"].po_number
    assert body["po_line_id"] == line.id
    assert body["note"].startswith("Already had a note; Linked to")
    assert world["po"].po_number in body["note"]
    assert world["supplier"].supplier_name in body["note"]
    assert "2026-09-20" in body["note"]

    claims = (
        db.query(OrderLinkClaim)
        .filter(OrderLinkClaim.po_number == world["po"].po_number, OrderLinkClaim.source == "order_inquiry")
        .all()
    )
    assert len(claims) == 1
    # The PURCHASE half is known, so it is recorded. The SALES half is not: this fixture's
    # row hangs off no project line reconciled to a core `sales_order_lines` row, so
    # `claim_identity` has no `so_line_id` to give. "Resolved" means BOTH sides found
    # (S3, review of PR #490) - stamping it here retired the claim before the resolver
    # could finish it, because `resolve()` only ever looks at unresolved rows, and
    # dedication only ever reads claims it can join through `so_line_id`. A production row
    # raised off a real reconciled sales-order line does carry one and does resolve.
    assert claims[0].po_line_id == line.id
    assert claims[0].so_line_id is None
    assert claims[0].resolved_at is None


def test_placing_refuses_a_purchase_order_line_of_a_different_product(api):
    client, db, world, _user_id = api
    line = _po_line(
        db, world["company_id"], world["po"], world["other_product"], world["warehouse"],
        qty_ordered="50",
    )
    row = _row(db, world["company_id"], world["inquiry"], qty="10", item_code=world["product"].product_code)

    response = client.post(
        f"{BASE}/order-inquiry-rows/{row.id}/place-on-po", json={"po_line_id": line.id}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "order_inquiry_product_mismatch"


def test_placing_names_the_shortfall_when_the_line_cannot_cover_the_row(api):
    client, db, world, _user_id = api
    line = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"], qty_ordered="10",
    )
    row = _row(db, world["company_id"], world["inquiry"], qty="30", item_code=world["product"].product_code)

    response = client.post(
        f"{BASE}/order-inquiry-rows/{row.id}/place-on-po", json={"po_line_id": line.id}
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "order_inquiry_po_line_short"
    assert "10" in body["message"]
    assert "20" in body["message"]
    assert "30" in body["message"]


def test_two_rows_tagging_the_same_line_cannot_exceed_its_balance(api):
    client, db, world, _user_id = api
    line = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"], qty_ordered="50",
    )
    row_a = _row(db, world["company_id"], world["inquiry"], qty="30", item_code=world["product"].product_code)
    row_b = _row(db, world["company_id"], world["inquiry"], qty="25", item_code=world["product"].product_code)
    row_c = _row(db, world["company_id"], world["inquiry"], qty="20", item_code=world["product"].product_code)

    first = client.post(
        f"{BASE}/order-inquiry-rows/{row_a.id}/place-on-po", json={"po_line_id": line.id}
    )
    assert first.status_code == 200, first.text

    over = client.post(
        f"{BASE}/order-inquiry-rows/{row_b.id}/place-on-po", json={"po_line_id": line.id}
    )
    assert over.status_code == 409
    assert over.json()["code"] == "order_inquiry_po_line_short"

    exact = client.post(
        f"{BASE}/order-inquiry-rows/{row_c.id}/place-on-po", json={"po_line_id": line.id}
    )
    assert exact.status_code == 200, exact.text
    assert exact.json()["state"] == INQUIRY_PLACED


def test_a_fully_placed_row_has_nothing_left_for_the_single_line_form_to_add(api):
    """S8 (AC-CF-23) widened `_assert_linkable`'s state gate to every state but
    CANCELLED, so a second `place-on-po` on a PLACED row is no longer refused by the
    state gate itself - but the single `po_line_id` form KEEPS its old ADD meaning (the
    row's whole UNLINKED remainder), and that remainder is 0 once the row is fully
    placed, so there is nothing to name."""
    client, db, world, _user_id = api
    line = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"], qty_ordered="50",
    )
    other_line = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"], qty_ordered="50",
    )
    row = _row(db, world["company_id"], world["inquiry"], qty="10", item_code=world["product"].product_code)

    first = client.post(
        f"{BASE}/order-inquiry-rows/{row.id}/place-on-po", json={"po_line_id": line.id}
    )
    assert first.status_code == 200, first.text

    again = client.post(
        f"{BASE}/order-inquiry-rows/{row.id}/place-on-po", json={"po_line_id": other_line.id}
    )
    assert again.status_code == 422
    assert again.json()["code"] == "order_inquiry_no_allocations"


def test_a_reader_cannot_place_a_row(reader_api):
    client, db, world, _user_id = reader_api
    line = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"], qty_ordered="50",
    )
    row = _row(db, world["company_id"], world["inquiry"], qty="10", item_code=world["product"].product_code)

    response = client.post(
        f"{BASE}/order-inquiry-rows/{row.id}/place-on-po", json={"po_line_id": line.id}
    )

    assert response.status_code == 403


def test_placing_without_a_po_line_id_is_a_validation_error(api):
    client, db, world, _user_id = api
    row = _row(db, world["company_id"], world["inquiry"], qty="10", item_code=world["product"].product_code)

    response = client.post(f"{BASE}/order-inquiry-rows/{row.id}/place-on-po", json={})

    assert response.status_code == 422


# -------------------------------------------------------------------- unplace


def test_unplacing_reverts_the_row_and_deletes_the_claim(api):
    client, db, world, _user_id = api
    line = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"],
        qty_ordered="50", expected_date=date(2026, 10, 1),
    )
    row = _row(db, world["company_id"], world["inquiry"], qty="10", item_code=world["product"].product_code)
    placed = client.post(
        f"{BASE}/order-inquiry-rows/{row.id}/place-on-po", json={"po_line_id": line.id}
    )
    assert placed.status_code == 200, placed.text

    response = client.post(f"{BASE}/order-inquiry-rows/{row.id}/unplace")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == INQUIRY_RAISED
    assert body["po_ref"] is None
    assert body["po_line_id"] is None
    assert f"Unlinked from {world['po'].po_number}" in body["note"]

    claims = (
        db.query(OrderLinkClaim)
        .filter(OrderLinkClaim.po_number == world["po"].po_number, OrderLinkClaim.source == "order_inquiry")
        .all()
    )
    assert claims == []


def test_unplacing_a_row_that_is_not_placed_is_a_409(api):
    client, db, world, _user_id = api
    row = _row(db, world["company_id"], world["inquiry"], qty="10", item_code=world["product"].product_code)

    response = client.post(f"{BASE}/order-inquiry-rows/{row.id}/unplace")

    assert response.status_code == 409
    assert response.json()["code"] == "order_inquiry_not_placed"


def test_a_reader_cannot_unplace_a_row(reader_api):
    client, db, world, _user_id = reader_api
    row = _row(
        db, world["company_id"], world["inquiry"], qty="10", item_code=world["product"].product_code,
        state=INQUIRY_PLACED, po_ref=world["po"].po_number,
    )

    response = client.post(f"{BASE}/order-inquiry-rows/{row.id}/unplace")

    assert response.status_code == 403


# -------------------------------------------------------------------- worklist


def test_the_worklist_reads_po_no_and_supplier_off_the_direct_tag(api):
    """Off the LINK since section 3.I, not off the row's own `po_line_id`: a row holds
    many links now and the scalar is the derived display of the first."""
    client, db, world, _user_id = api
    line = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"], qty_ordered="50",
    )
    row = _row(
        db, world["company_id"], world["inquiry"], qty="10", item_code=world["product"].product_code,
    )
    response = client.post(
        f"{BASE}/order-inquiry-rows/{row.id}/place-on-po", json={"po_line_id": line.id}
    )
    assert response.status_code == 200, response.text

    response = client.get(f"{BASE}/order-inquiries", params={"query": row.item_code})

    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert len(rows) == 1
    assert rows[0]["po_number"] == world["po"].po_number
    assert rows[0]["supplier"] == world["supplier"].supplier_name


def test_the_worklist_state_filter_placed_returns_only_placed_rows(api):
    client, db, world, _user_id = api
    line = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"], qty_ordered="50",
    )
    raised_row = _row(db, world["company_id"], world["inquiry"], qty="5", item_code=f"{MARKER}-RAISED")
    placed_row = _row(
        db, world["company_id"], world["inquiry"], qty="5", item_code=f"{MARKER}-PLACED",
        state=INQUIRY_PLACED, po_ref=world["po"].po_number, po_line_id=line.id,
    )

    response = client.get(f"{BASE}/order-inquiries", params={"state": "placed"})

    assert response.status_code == 200, response.text
    ids = {row["id"] for row in response.json()["data"]}
    assert placed_row.id in ids
    assert raised_row.id not in ids


# ---------------------------------------------------------- has_link_candidate


def test_has_link_candidate_is_true_on_the_per_project_listing_and_flips_false_once_placed(api):
    """The per-project OI rows listing (`GET .../order-inquiry-rows`): true while the
    row's own product still has an open PO line with remaining balance, false once a
    placement consumes it - the same predicate `po-candidates` answers, computed once for
    the whole page."""
    client, db, world, _user_id = api
    line = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"],
        qty_ordered="20", expected_date=date(2026, 9, 5),
    )
    row_a = _row(db, world["company_id"], world["inquiry"], qty="20", item_code=world["product"].product_code)

    before = client.get(f"{BASE}/projects/{world['project'].id}/order-inquiry-rows")
    assert before.status_code == 200, before.text
    row_a_out = next(r for r in before.json()["data"] if r["id"] == row_a.id)
    assert row_a_out["has_link_candidate"] is True

    placed = client.post(
        f"{BASE}/order-inquiry-rows/{row_a.id}/place-on-po", json={"po_line_id": line.id}
    )
    assert placed.status_code == 200, placed.text

    row_b = _row(db, world["company_id"], world["inquiry"], qty="5", item_code=world["product"].product_code)
    after = client.get(f"{BASE}/projects/{world['project'].id}/order-inquiry-rows")
    assert after.status_code == 200, after.text
    row_b_out = next(r for r in after.json()["data"] if r["id"] == row_b.id)
    assert row_b_out["has_link_candidate"] is False, (
        "the line's whole remaining balance is already claimed by row_a"
    )


def test_has_link_candidate_is_false_for_a_product_with_no_open_po_line_at_all(api):
    client, db, world, _user_id = api
    row = _row(
        db, world["company_id"], world["inquiry"], qty="5",
        item_code=world["other_product"].product_code,
    )

    response = client.get(f"{BASE}/projects/{world['project'].id}/order-inquiry-rows")

    assert response.status_code == 200, response.text
    row_out = next(r for r in response.json()["data"] if r["id"] == row.id)
    assert row_out["has_link_candidate"] is False


def test_has_link_candidate_on_the_cross_project_worklist_flips_false_once_placed(api):
    """The same flag, on the cross-project worklist (`GET /order-inquiries`) purchasing
    actually works from - it must never disagree with the per-project listing."""
    client, db, world, _user_id = api
    line = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"],
        qty_ordered="12", expected_date=date(2026, 9, 12),
    )
    row_a = _row(
        db, world["company_id"], world["inquiry"], qty="12",
        item_code=world["product"].product_code,
    )

    before = client.get(f"{BASE}/order-inquiries", params={"query": world["product"].product_code})
    assert before.status_code == 200, before.text
    row_a_out = next(r for r in before.json()["data"] if r["id"] == row_a.id)
    assert row_a_out["has_link_candidate"] is True

    placed = client.post(
        f"{BASE}/order-inquiry-rows/{row_a.id}/place-on-po", json={"po_line_id": line.id}
    )
    assert placed.status_code == 200, placed.text

    row_b = _row(db, world["company_id"], world["inquiry"], qty="3", item_code=world["product"].product_code)
    after = client.get(f"{BASE}/order-inquiries", params={"query": world["product"].product_code})
    assert after.status_code == 200, after.text
    row_b_out = next(r for r in after.json()["data"] if r["id"] == row_b.id)
    assert row_b_out["has_link_candidate"] is False


# -------------------------------------------------------------- netting proof


def test_placing_leaves_committed_v_confirmed_leg_and_unplacing_restores_it():
    """The captain's "the quantity to be ordered is deducted" (section G), proven against
    the view the reorder engine actually reads rather than against the row's own state."""
    from app.models.base import company_scope
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    with pg_session() as db:
        company_id = _confirm_sorento(db)
        user_id = _confirm_user(db, f"{MARKER} Buyer")
        product = _confirm_product(db)
        warehouse = _confirm_warehouse(db, f"ZZT-NT-{str(product.id)[:4]}")

        core_so = _confirm_core_so(db, company_id)
        core_line = _confirm_core_line(db, core_so, product, warehouse, qty_ordered="40")
        from app.services.project_seed_service import seed_numbering_rule
        from app.services.project_service import register_project

        # The real database in CI is freshly bootstrapped and has no numbering rule
        # unless another test happened to leave one; seed it here (flushed, rolled
        # back with the session) so this test holds whichever shard it lands in.
        seed_numbering_rule(db)
        project = register_project(
            db, company_id=company_id, actor_user_id=user_id, developer_party_id=None,
            title=f"{MARKER} Netting Residences",
        )
        order = _confirm_project_so(db, project, so_id=core_so.id)
        line = _confirm_project_line(db, order, line_no=10, product=product, core_line=core_line)

        decision = SOSupplyDecision(
            id=_uid(), company_id=company_id, project_sales_order_id=order.id, revision_no=1,
            state="active", line_snapshots=[{"line_no": 10}], confirmed_by=user_id,
            confirmed_at=datetime.utcnow(),
        )
        db.add(decision)
        db.flush()

        inquiry = OrderInquiry(
            id=_uid(), company_id=company_id, project_sales_order_id=order.id, state=INQUIRY_RAISED,
        )
        db.add(inquiry)
        db.flush()
        row = OrderInquiryRow(
            id=_uid(), company_id=company_id, order_inquiry_id=inquiry.id, so_line_id=line.id,
            item_code=product.product_code, qty=Decimal("40"), verb=IV_ORDER, state=INQUIRY_RAISED,
            supply_decision_id=decision.id, ack_state=ACK_ACKNOWLEDGED,
        )
        db.add(row)

        supplier = _supplier(db, company_id, f"{MARKER} Netting Supplier")
        po = _po(db, company_id, supplier, f"ZZT-PO-{_uid()[:8]}")
        po_line = _po_line(
            db, company_id, po, product, warehouse, qty_ordered="60", expected_date=date(2026, 9, 20),
        )
        db.commit()

        def confirmed_committed() -> Decimal:
            value = db.execute(
                text(
                    "SELECT COALESCE(SUM(project_confirmed_committed), 0) "
                    "FROM scm.committed_v WHERE product_id = :pid AND warehouse_id = :wid"
                ),
                {"pid": product.id, "wid": warehouse.id},
            ).scalar()
            return Decimal(str(value))

        assert confirmed_committed() == Decimal("40")

        service = ProjectOrderInquiryService(db)
        with company_scope(db, frozenset({company_id})):
            placed = service.place_on_po(row.id, po_line.id, actor_user_id=user_id)
        db.commit()
        assert placed["state"] == INQUIRY_PLACED

        db.expire_all()
        assert confirmed_committed() == Decimal("0"), (
            "a placed row must leave committed_v's confirmed leg - the captain's deduction"
        )

        with company_scope(db, frozenset({company_id})):
            unplaced = service.unplace(row.id, actor_user_id=user_id)
        db.commit()
        assert unplaced["state"] == INQUIRY_RAISED

        db.expire_all()
        assert confirmed_committed() == Decimal("40"), (
            "unplacing must return the row to raised so the reorder engine counts it again"
        )


# ------------------------------------------------------------------ G2 - auto-place (20 Aug)
#
# "We need to link already at first already instead of suggesting and needing the users
# to click 1 by 1" (the captain, live-testing G). Placements now happen AUTOMATICALLY,
# cascading from the earliest expected-date PO line, ties by document sequence, partial
# coverage allowed, POs only - never SPO-. The dialog survives as override + audit.


def test_candidates_are_ordered_by_document_sequence_when_expected_dates_tie(api):
    """G2 rule 2's tie-break. Both lines carry the SAME `expected_date`, so only the PO's
    own number - the document sequence - decides which is "earliest"."""
    client, db, world, _user_id = api
    po_b = _po(db, world["company_id"], world["supplier"], f"ZZT-PO-B-{_uid()[:6]}")
    line_b = _po_line(
        db, world["company_id"], po_b, world["product"], world["warehouse"],
        qty_ordered="10", expected_date=date(2026, 9, 5),
    )
    po_a = _po(db, world["company_id"], world["supplier"], f"ZZT-PO-A-{_uid()[:6]}")
    line_a = _po_line(
        db, world["company_id"], po_a, world["product"], world["warehouse"],
        qty_ordered="10", expected_date=date(2026, 9, 5),
    )
    row = _row(db, world["company_id"], world["inquiry"], qty="15", item_code=world["product"].product_code)

    response = client.get(f"{BASE}/order-inquiry-rows/{row.id}/po-candidates")

    assert response.status_code == 200, response.text
    body = response.json()["candidates"]
    assert [c["po_line_id"] for c in body] == [line_a.id, line_b.id]
    # The cascade preview (`default_take`) walks the SAME order: the earlier document
    # takes its whole balance first, the later one takes only what is left.
    assert body[0]["default_take"] == "10"
    assert body[1]["default_take"] == "5"


def test_spo_prefixed_documents_are_never_candidates_the_flag_or_the_cascade(api):
    """G2 rule 3, "those are SPO, not PO" - the captain. An SPO- document offers nothing
    to Place on PO, at any of the three places this feature reads open PO lines from."""
    client, db, world, user_id = api
    spo_po = _po(db, world["company_id"], world["supplier"], f"SPO-{_uid()[:8]}")
    _po_line(
        db, world["company_id"], spo_po, world["product"], world["warehouse"], qty_ordered="50",
    )
    row = _row(db, world["company_id"], world["inquiry"], qty="10", item_code=world["product"].product_code)

    candidates = client.get(f"{BASE}/order-inquiry-rows/{row.id}/po-candidates")
    assert candidates.status_code == 200, candidates.text
    assert candidates.json()["candidates"] == []

    listing = client.get(f"{BASE}/projects/{world['project'].id}/order-inquiry-rows")
    assert listing.status_code == 200, listing.text
    row_out = next(r for r in listing.json()["data"] if r["id"] == row.id)
    assert row_out["has_link_candidate"] is False

    from app.models.base import company_scope
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    with company_scope(db, frozenset({world["company_id"]})):
        result = ProjectOrderInquiryService(db).auto_place_for_products(
            [world["product"].id], actor_user_id=user_id, trigger="test"
        )
    db.commit()
    assert result == {"placed_rows": 0, "allocations": 0, "products_touched": 0,
                      "book_linked_rows": 0, "suggested_rows": 0,
                      # R18: nothing was walked, so nothing changed.
                      "changed_rows": 0,
                      "after_horizon": 0, "link_up_to": None,
                      "link_horizon": "none"}
    db.refresh(row)
    assert row.state == INQUIRY_RAISED


def test_allocations_cascade_earliest_first_and_link_without_splitting(api):
    """AC-I6, the captain's ruling of 25 August 2026: "1 line here should correspond to 1
    line in sales order, so 1 line can be placed by multiple PO and SPO". The row KEEPS
    its full quantity and gains one link per allocation - the split this test used to
    assert is what turned nine sales-order lines into eleven instructions."""
    client, db, world, _user_id = api
    early = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"],
        qty_ordered="15", expected_date=date(2026, 9, 1),
    )
    later = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"],
        qty_ordered="20", expected_date=date(2026, 9, 10),
    )
    row = _row(db, world["company_id"], world["inquiry"], qty="25", item_code=world["product"].product_code)

    response = client.post(
        f"{BASE}/order-inquiry-rows/{row.id}/place-on-po",
        json={
            "allocations": [
                {"po_line_id": early.id, "qty": "15"},
                {"po_line_id": later.id, "qty": "10"},
            ]
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == row.id, "the row is the subject, whatever it took"
    assert body["qty"] == "25", "the row keeps its own quantity"
    assert body["state"] == INQUIRY_PLACED, "25 of 25 is wholly linked"
    assert body["linked_qty"] == "25"
    assert [(link["qty"], link["kind"]) for link in body["links"]] == [
        ("15", "po"),
        ("10", "po"),
    ]
    assert body["po_line_id"] == early.id, "the first link is the derived display"

    rows = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.order_inquiry_id == world["inquiry"].id)
        .all()
    )
    assert len(rows) == 1, "one instruction per sales-order line, never a split"


def test_allocations_over_the_rows_own_need_are_refused(api):
    client, db, world, _user_id = api
    line_a = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"], qty_ordered="20",
    )
    line_b = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"], qty_ordered="20",
    )
    row = _row(db, world["company_id"], world["inquiry"], qty="10", item_code=world["product"].product_code)

    response = client.post(
        f"{BASE}/order-inquiry-rows/{row.id}/place-on-po",
        json={
            "allocations": [
                {"po_line_id": line_a.id, "qty": "6"},
                {"po_line_id": line_b.id, "qty": "6"},
            ]
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "order_inquiry_over_allocated"


def test_unlinking_one_link_leaves_the_row_partly_linked(api):
    client, db, world, _user_id = api
    early = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"],
        qty_ordered="10", expected_date=date(2026, 9, 1),
    )
    later = _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"],
        qty_ordered="10", expected_date=date(2026, 9, 10),
    )
    row = _row(db, world["company_id"], world["inquiry"], qty="20", item_code=world["product"].product_code)

    placed = client.post(
        f"{BASE}/order-inquiry-rows/{row.id}/place-on-po",
        json={
            "allocations": [
                {"po_line_id": early.id, "qty": "10"},
                {"po_line_id": later.id, "qty": "10"},
            ]
        },
    )
    assert placed.status_code == 200, placed.text

    # No split to find: the two allocations are two LINKS on the one row (AC-I6), and
    # giving one back leaves the row partly linked rather than raised.
    assert (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.order_inquiry_id == world["inquiry"].id)
        .count()
        == 1
    )
    links = placed.json()["links"]
    assert [link["qty"] for link in links] == ["10", "10"]

    unplaced = client.post(
        f"{BASE}/order-inquiry-rows/{row.id}/unplace", json={"link_id": links[1]["id"]}
    )
    assert unplaced.status_code == 200, unplaced.text
    assert unplaced.json()["state"] == "partly_linked"
    assert unplaced.json()["linked_qty"] == "10"

    db.refresh(row)
    assert row.qty == Decimal("20"), "the row never gave up its own quantity"
    assert row.po_line_id == early.id


def test_auto_place_for_products_is_idempotent(api):
    """S3 reversal: the PO line carries no book match, so the walk SUGGESTS the row
    rather than placing it - a suggestion-only row stays `raised` and re-enters the
    walk on every pass (it is never a placement that settles the row), so
    `placed_rows` does NOT drop to 0 on the second pass. What idempotence means now
    is that an unchanged answer is not deleted and rewritten (`_same_placement`): the
    suggestion itself carries the SAME id and `suggested_at` across both passes, and
    the row's own note is never touched (`_write_suggested_links` writes nothing onto
    the row - only a real link's `_write_link` does)."""
    client, db, world, user_id = api
    _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"],
        qty_ordered="50", expected_date=date(2026, 9, 1),
    )
    row = _row(db, world["company_id"], world["inquiry"], qty="20", item_code=world["product"].product_code)

    from app.models.base import company_scope
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    with company_scope(db, frozenset({world["company_id"]})):
        first = ProjectOrderInquiryService(db).auto_place_for_products(
            None, actor_user_id=user_id, trigger="test"
        )
        db.commit()
        first_suggestion = _suggested_of(db, row.id)[0]
        second = ProjectOrderInquiryService(db).auto_place_for_products(
            None, actor_user_id=user_id, trigger="test"
        )
        db.commit()
        second_suggestion = _suggested_of(db, row.id)[0]

    assert first == {"placed_rows": 1, "allocations": 1, "products_touched": 1,
                     "book_linked_rows": 0, "suggested_rows": 1,
                     # R18: the first pass writes a new suggestion, so the row changed.
                     "changed_rows": 1,
                     "after_horizon": 0, "link_up_to": None,
                     "link_horizon": "none"}
    assert second == {"placed_rows": 1, "allocations": 1, "products_touched": 1,
                      "book_linked_rows": 0, "suggested_rows": 1,
                      # R18: the second pass writes the SAME suggestion
                      # (`_same_placement`), so nothing changed.
                      "changed_rows": 0,
                      "after_horizon": 0, "link_up_to": None,
                      "link_horizon": "none"}
    db.refresh(row)
    assert row.state == INQUIRY_RAISED
    assert row.note is None, "a suggestion writes nothing onto the row"
    assert second_suggestion.id == first_suggestion.id, (
        "an unchanged answer is not deleted and rewritten"
    )
    assert second_suggestion.suggested_at == first_suggestion.suggested_at


def test_auto_place_route_places_raised_rows_and_reports_totals(api):
    """S3 reversal: the PO line carries no book match, so the route SUGGESTS the row
    rather than placing it - `placed_rows` still counts it (a row the walk walked),
    `book_linked_rows` is 0 (no book match) and `suggested_rows` is 1."""
    client, db, world, _user_id = api
    _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"],
        qty_ordered="50", expected_date=date(2026, 9, 1),
    )
    row = _row(db, world["company_id"], world["inquiry"], qty="20", item_code=world["product"].product_code)

    response = client.post(f"{BASE}/order-inquiries/auto-place", json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {"placed_rows": 1, "allocations": 1, "products_touched": 1,
                    "book_linked_rows": 0, "suggested_rows": 1,
                    # R18: a new suggestion is written, so the row changed.
                    "changed_rows": 1,
                    "after_horizon": 0, "link_up_to": None,
                    "link_horizon": "none"}
    db.refresh(row)
    assert row.state == INQUIRY_RAISED


def test_a_reader_cannot_trigger_auto_place(reader_api):
    client, db, world, _user_id = reader_api

    response = client.post(f"{BASE}/order-inquiries/auto-place", json={})

    assert response.status_code == 403


def test_a_decision_confirm_raises_the_buy_row_awaiting_with_a_firm_link():
    """REVERSED AGAIN by `PLAN-scm-oi-draft-links.md` R6 (captain, 27 Aug 2026), once
    more by `PLAN-scm-reorder-oi-feedback-1sep.md` S1 (G4, 1 Sep 2026), and once more
    by `PLAN-oi-confirm-per-so.md` S1 (17 Sep 2026), which reverses G4 back out.

    G2's original trigger linked at the decision confirm; the handshake took it away
    because a buyer found their own documents dealt out to instructions they had never
    read; R6 restored the pass and answered that objection with what the link MEANT THEN -
    a DRAFT, on a row still `awaiting`. G4/S1 (1 Sep) removed the manual confirm the draft
    was waiting on entirely, born ACKNOWLEDGED; `PLAN-oi-confirm-per-so.md` S1 puts the
    manual Confirm press back - the row is born `awaiting` again, with null stamps, and it
    is purchasing's own Confirm that takes it on. What R6 answered survives unchanged
    either way: the raise-time cascade reaches the row from the moment it is written,
    whatever the row's own ack_state - linking has never waited for confirm (AC-CF-4).

    S3 reversal: the PO line carries no book match, so what the raise-time cascade
    writes is a SUGGESTION, never a firm link - the row stays `raised`.
    """
    from app.models.base import company_scope
    from app.services.project_service import register_project

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        user_id = _confirm_user(db, f"{MARKER} Confirm Buyer")
        project = register_project(
            db, company_id=company_id, actor_user_id=user_id, developer_party_id=None,
            title=f"{MARKER} Confirm Residences",
        )
        product = _confirm_product(db)
        warehouse = _confirm_warehouse(db, f"ZZT-CF-{_uid()[:4]}")
        # H: what this test is actually proving - the link is firm, written by the
        # auto-cascade the instant the row is raised - needs the cascade to WRITE
        # something. A candidate is only ever auto-taken from a genuine POOL now
        # (AC-H1/AC-H2), so `warehouse` has to be one, or the cascade would find its
        # one candidate uncascadable and this test would be proving AC-H1 instead of
        # the birth state and the link timing (AC-H1 already has its own coverage in
        # `test_order_inquiry_links.py`).
        _confirm_warehouse(db, f"ZZT-CF-SIB-{_uid()[:4]}", pool_warehouse_id=warehouse.id)
        core_so = _confirm_core_so(db, company_id)
        core_line = _confirm_core_line(db, core_so, product, warehouse, qty_ordered="20")
        order = _confirm_project_so(db, project, so_id=core_so.id)
        line = _confirm_project_line(db, order, line_no=10, product=product, core_line=core_line)

        supplier = _supplier(db, company_id, f"{MARKER} Confirm Supplier")
        po = _po(db, company_id, supplier, f"ZZT-PO-{_uid()[:8]}")
        # 61 days before the line's delivery (`REQUIRED_DATE`, today + 30): inside the
        # cascade's 90-day lead-time window, which a fixed date leaves once today moves on.
        po_line = _po_line(
            db, company_id, po, product, warehouse, qty_ordered="30",
            expected_date=date.today() - timedelta(days=31),
        )
        db.commit()

        client, originals = _confirm_client(db, user_id)
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.post(
                    f"{BASE}/sales-orders/{order.id}/confirm",
                    json={"lines": [_confirm_line_payload(line.id, buy_qty="20")]},
                )
        finally:
            _confirm_restore(originals)

        assert response.status_code == 200, response.text
        assert response.json()["inquiry_rows_created"] == 1

        row = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == IV_ORDER)
            .first()
        )
        assert row is not None
        assert row.ack_state == ACK_AWAITING, "born awaiting again (PLAN-oi-confirm-per-so S1)"
        assert row.acknowledged_by is None
        assert row.acknowledged_at is None
        assert row.state == INQUIRY_RAISED, "the 30-line only suggests, it does not place"
        assert row.po_line_id is None, "a suggestion never writes the row's own display field"
        (suggestion,) = _suggested_of(db, row.id)
        assert suggestion.po_line_id == po_line.id, "the suggestion names the open line"


def test_partial_allocation_leaves_the_remainder_raised_and_in_committed_v():
    """AC-I7, proven against the view the reorder engine actually reads: 40 owed, only 25
    on an open PO line. The row keeps its 40 and reads PARTLY LINKED; the confirmed leg
    nets `qty - linked` and carries the still-needed 15 (migration 422). Before the links
    table the row had to be SPLIT for the same arithmetic to come out."""
    from app.models.base import company_scope
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    with pg_session() as db:
        company_id = _confirm_sorento(db)
        user_id = _confirm_user(db, f"{MARKER} Partial Buyer")
        product = _confirm_product(db)
        warehouse = _confirm_warehouse(db, f"ZZT-PT-{str(product.id)[:4]}")

        core_so = _confirm_core_so(db, company_id)
        core_line = _confirm_core_line(db, core_so, product, warehouse, qty_ordered="40")
        from app.services.project_seed_service import seed_numbering_rule
        from app.services.project_service import register_project

        # The real database in CI is freshly bootstrapped and has no numbering rule
        # unless another test happened to leave one; seed it here (flushed, rolled
        # back with the session) so this test holds whichever shard it lands in.
        seed_numbering_rule(db)
        project = register_project(
            db, company_id=company_id, actor_user_id=user_id, developer_party_id=None,
            title=f"{MARKER} Partial Netting Residences",
        )
        order = _confirm_project_so(db, project, so_id=core_so.id)
        line = _confirm_project_line(db, order, line_no=10, product=product, core_line=core_line)

        decision = SOSupplyDecision(
            id=_uid(), company_id=company_id, project_sales_order_id=order.id, revision_no=1,
            state="active", line_snapshots=[{"line_no": 10}], confirmed_by=user_id,
            confirmed_at=datetime.utcnow(),
        )
        db.add(decision)
        db.flush()

        inquiry = OrderInquiry(
            id=_uid(), company_id=company_id, project_sales_order_id=order.id, state=INQUIRY_RAISED,
        )
        db.add(inquiry)
        db.flush()
        row = OrderInquiryRow(
            id=_uid(), company_id=company_id, order_inquiry_id=inquiry.id, so_line_id=line.id,
            item_code=product.product_code, qty=Decimal("40"), verb=IV_ORDER, state=INQUIRY_RAISED,
            supply_decision_id=decision.id, ack_state=ACK_ACKNOWLEDGED,
        )
        db.add(row)

        supplier = _supplier(db, company_id, f"{MARKER} Partial Supplier")
        po = _po(db, company_id, supplier, f"ZZT-PO-{_uid()[:8]}")
        po_line = _po_line(
            db, company_id, po, product, warehouse, qty_ordered="25", expected_date=date(2026, 9, 20),
        )
        db.commit()

        def confirmed_committed() -> Decimal:
            value = db.execute(
                text(
                    "SELECT COALESCE(SUM(project_confirmed_committed), 0) "
                    "FROM scm.committed_v WHERE product_id = :pid AND warehouse_id = :wid"
                ),
                {"pid": product.id, "wid": warehouse.id},
            ).scalar()
            return Decimal(str(value))

        assert confirmed_committed() == Decimal("40")

        service = ProjectOrderInquiryService(db)
        with company_scope(db, frozenset({company_id})):
            written = service.place_on_po_allocations(
                row.id, [{"po_line_id": po_line.id, "qty": "25"}], actor_user_id=user_id,
            )
        db.commit()
        assert len(written) == 1
        assert written[0]["id"] == row.id
        assert written[0]["state"] == "partly_linked"
        assert written[0]["qty"] == "40", "the row keeps its own quantity"
        assert written[0]["linked_qty"] == "25"

        db.expire_all()
        assert confirmed_committed() == Decimal("15"), (
            "the confirmed leg nets qty minus linked, so only the 15 nobody has covered "
            "still counts as demand"
        )

        rows = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.order_inquiry_id == inquiry.id)
            .all()
        )
        assert len(rows) == 1, "one instruction per sales-order line, never a split"
        assert rows[0].qty == Decimal("40")


# ---------------------------------------------------------------- G/AC-H5 demand ranking


def _policy(db, factors: dict, class_weights: dict | None = None) -> None:
    """A policy owned by this test. Deactivates any incumbent rather than deleting it -
    the same convention `test_fulfilment_board.py` and `test_priority_demand_rows.py`
    use for the identical row."""
    from app.models.scm import PriorityPolicy

    db.query(PriorityPolicy).filter(PriorityPolicy.is_active.is_(True)).update(
        {"is_active": False}, synchronize_session=False
    )
    row = PriorityPolicy(
        id=_uid(),
        name=f"{MARKER}-policy-{_uid()[:6]}",
        is_active=True,
        factors=factors,
        demand_class_weights=class_weights or {},
    )
    db.add(row)
    db.flush()


def _competing_row(
    db, company_id: str, project, *, published_at: datetime, delivery_date: date,
    qty: str, item_code: str,
) -> OrderInquiryRow:
    """A raised ORDER row on its OWN sales order and inquiry, so its document date
    (`published_at`) and delivery date can be set independently of the sibling row it
    competes with - one inquiry serves exactly one sales order (G2)."""
    order = ProjectSalesOrder(
        id=_uid(),
        company_id=company_id,
        project_id=project.id,
        area_group="TOWER",
        provisional_ref=f"ZZT-PSO-{_uid()[:8]}",
        autocount_doc_no=f"SO{_uid()[:6].upper()}",
        status=SO_STATUS_DRAFT,
        grouping_origin="area",
        published_at=published_at,
    )
    db.add(order)
    db.flush()
    inquiry = OrderInquiry(
        id=_uid(), company_id=company_id, project_sales_order_id=order.id, state=INQUIRY_RAISED,
    )
    db.add(inquiry)
    db.flush()
    row = OrderInquiryRow(
        id=_uid(), company_id=company_id, order_inquiry_id=inquiry.id, item_code=item_code,
        qty=Decimal(qty), delivery_date=delivery_date, verb=IV_ORDER, state=INQUIRY_RAISED,
        # Acknowledged, because the cascade this ranks is the one that runs when a buyer
        # takes an instruction on (`PLAN-scm-oi-handshake.md`).
        ack_state=ACK_ACKNOWLEDGED,
    )
    db.add(row)
    db.flush()
    return row


def test_auto_place_ranks_by_the_active_policys_document_age_over_the_old_delivery_date_sort(api):
    """AC-H5: the ranking that decides ANY draw-down is the same fulfilment priority
    policy. `older` has the LATER delivery date - the old `delivery_date`/`created_at`
    sort would have put it second - but its sales order is years older, and a policy
    weighting `document_age` alone must still hand it the only PO quantity there is.

    S3 reversal: the line carries no book match, so the ranking decides which row gets
    SUGGESTED the scarce quantity - never a real placement - and the loser gets nothing
    at all, suggested or otherwise, since there is no capacity left for it.
    """
    client, db, world, user_id = api
    _policy(db, {"document_age": 1.0}, {"project": 1.0})
    # AC-EA-3: 2026-09-01 sat inside `older`'s 90-day lead-time window (delivery
    # 2026-12-01, edge 2026-09-02) and would be removed as a candidate before the
    # ranking under test ever ran. Moved a couple of days later so the line stays a
    # candidate for BOTH rows and document_age still decides between them.
    _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"],
        qty_ordered="10", expected_date=date(2026, 9, 3),
    )
    older = _competing_row(
        db, world["company_id"], world["project"],
        published_at=datetime(2020, 1, 1), delivery_date=date(2026, 12, 1),
        qty="10", item_code=world["product"].product_code,
    )
    newer = _competing_row(
        db, world["company_id"], world["project"],
        published_at=datetime(2026, 8, 1), delivery_date=date(2026, 9, 5),
        qty="10", item_code=world["product"].product_code,
    )
    db.commit()

    from app.models.base import company_scope
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    with company_scope(db, frozenset({world["company_id"]})):
        result = ProjectOrderInquiryService(db).auto_place_for_products(
            None, actor_user_id=user_id, trigger="test"
        )
        db.commit()

    assert result == {"placed_rows": 1, "allocations": 1, "products_touched": 1,
                      "book_linked_rows": 0, "suggested_rows": 1,
                      # R18: a new suggestion is written for the winning row, so it changed.
                      "changed_rows": 1,
                      "after_horizon": 0, "link_up_to": None,
                      "link_horizon": "none"}
    db.refresh(older)
    db.refresh(newer)
    assert older.state == INQUIRY_RAISED, "no book match - only a suggestion"
    (older_suggestion,) = _suggested_of(db, older.id)
    assert older_suggestion, "the older document must draw the scarce quantity"
    assert newer.state == INQUIRY_RAISED
    assert _suggested_of(db, newer.id) == [], "nothing left for the newer document"


def test_auto_place_ranks_by_the_active_policys_need_by_date_over_the_old_delivery_date_sort(api):
    """The mirror of the test above, same two rows: with `need_by_date` dominant instead,
    the SOONER delivery date wins even though its own document is the newer one.

    S3 reversal: the line carries no book match, so the winner gets SUGGESTED the
    scarce quantity - never a real placement - and the loser gets nothing at all.
    """
    client, db, world, user_id = api
    _policy(db, {"need_by_date": 1.0}, {"project": 1.0})
    # AC-EA-3: same edge as the document_age test above - `older`'s 90-day window edge
    # is 2026-09-02, so the line's promise has to land after it or the cascade drops it
    # as a candidate before need_by_date gets to decide anything.
    _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"],
        qty_ordered="10", expected_date=date(2026, 9, 3),
    )
    older = _competing_row(
        db, world["company_id"], world["project"],
        published_at=datetime(2020, 1, 1), delivery_date=date(2026, 12, 1),
        qty="10", item_code=world["product"].product_code,
    )
    newer = _competing_row(
        db, world["company_id"], world["project"],
        published_at=datetime(2026, 8, 1), delivery_date=date(2026, 9, 5),
        qty="10", item_code=world["product"].product_code,
    )
    db.commit()

    from app.models.base import company_scope
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    with company_scope(db, frozenset({world["company_id"]})):
        result = ProjectOrderInquiryService(db).auto_place_for_products(
            None, actor_user_id=user_id, trigger="test"
        )
        db.commit()

    assert result == {"placed_rows": 1, "allocations": 1, "products_touched": 1,
                      "book_linked_rows": 0, "suggested_rows": 1,
                      # R18: a new suggestion is written for the winning row, so it changed.
                      "changed_rows": 1,
                      "after_horizon": 0, "link_up_to": None,
                      "link_horizon": "none"}
    db.refresh(older)
    db.refresh(newer)
    assert newer.state == INQUIRY_RAISED, "no book match - only a suggestion"
    (newer_suggestion,) = _suggested_of(db, newer.id)
    assert newer_suggestion, "the sooner delivery date must draw the scarce quantity"
    assert older.state == INQUIRY_RAISED
    assert _suggested_of(db, older.id) == [], "nothing left for the older document"


def test_auto_place_scores_a_product_the_same_alone_or_beside_an_unrelated_products_extreme_date(api):
    """S5 (code review, 20 Aug 2026): `scm.priority.factors_for_demand_rows` normalizes
    its date factors ACROSS THE ROWS PASSED IN - its own docstring: "the caller passes one
    cell's contributors, not the world" - but `auto_place_for_products` used to hand it
    every raised row across EVERY product in one call. Two rows genuinely competing for
    the SAME product's only PO line must rank the same whether the run is scoped to that
    product alone or covers the whole book alongside an unrelated product whose row
    carries an extreme document date.

    Both weighted factors are needed to see it: `document_age` alone (or `need_by_date`
    alone) is a strictly monotonic rescale, so a third point can never flip which of two
    OTHER points is larger - only their combination, unevenly compressed by a distortion
    on one axis and not the other, can flip which one wins on total weighted score."""
    client, db, world, user_id = api
    _policy(db, {"document_age": 0.6, "need_by_date": 0.4}, {"project": 1.0})

    def _arena(product):
        # AC-EA-3: same edge as the document_age test above - `older_doc`'s 90-day
        # window edge is 2026-09-02, so the line's promise has to land after it or the
        # cascade drops it as a candidate before document_age gets to decide anything.
        line = _po_line(
            db, world["company_id"], world["po"], product, world["warehouse"],
            qty_ordered="10", expected_date=date(2026, 9, 3),
        )
        # Wins on document_age (the older document) - loses on need_by_date (the later
        # delivery). Scoped 1-vs-1, `document_age`'s heavier weight (0.6 > 0.4) decides.
        older_doc = _competing_row(
            db, world["company_id"], world["project"],
            published_at=datetime(2022, 1, 1), delivery_date=date(2026, 12, 1),
            qty="10", item_code=product.product_code,
        )
        # The mirror: newer document, sooner delivery.
        sooner_delivery = _competing_row(
            db, world["company_id"], world["project"],
            published_at=datetime(2026, 6, 1), delivery_date=date(2026, 9, 5),
            qty="10", item_code=product.product_code,
        )
        return line, older_doc, sooner_delivery

    from app.models.base import company_scope
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    # Run A: scoped to its own product alone (the query itself already excludes every
    # other product's rows, whatever `_rank_raised_rows` does with what it is handed).
    _line_a, older_a, sooner_a = _arena(world["product"])
    db.commit()
    with company_scope(db, frozenset({world["company_id"]})):
        result_a = ProjectOrderInquiryService(db).auto_place_for_products(
            [world["product"].id], actor_user_id=user_id, trigger="test"
        )
        db.commit()
    assert result_a["placed_rows"] == 1
    db.refresh(older_a)
    db.refresh(sooner_a)
    # S3 reversal: no book match, so the winner is SUGGESTED, never placed for real.
    assert older_a.state == INQUIRY_RAISED
    assert _suggested_of(db, older_a.id), "document_age's heavier weight must decide"
    assert sooner_a.state == INQUIRY_RAISED
    assert _suggested_of(db, sooner_a.id) == []

    # Run B: an identical pair on a FRESH product, run for the WHOLE book (`product_ids=
    # None`) alongside a third row on `other_product` whose document date is extreme -
    # its OWN delivery date sits between the pair's two, so it moves nothing on the
    # `need_by_date` axis, only `document_age`'s span (the asymmetry S5 exploits).
    fresh_product = _product(db, world["company_id"], f"ZZT-BASIN2-{_uid()[:6]}")
    _line_b, older_b, sooner_b = _arena(fresh_product)
    _competing_row(
        db, world["company_id"], world["project"],
        published_at=datetime(1990, 1, 1), delivery_date=date(2026, 10, 15),
        qty="1", item_code=world["other_product"].product_code,
    )
    db.commit()
    with company_scope(db, frozenset({world["company_id"]})):
        result_b = ProjectOrderInquiryService(db).auto_place_for_products(
            None, actor_user_id=user_id, trigger="test"
        )
        db.commit()
    db.refresh(older_b)
    db.refresh(sooner_b)
    # The SAME winner as the scoped run (Run A) - the unrelated product's extreme date
    # must not have flipped which of these two won the only PO line for THEIR product.
    # S3 reversal: no book match, so the winner is SUGGESTED, never placed for real.
    assert older_b.state == INQUIRY_RAISED, result_b
    assert _suggested_of(db, older_b.id), result_b
    assert sooner_b.state == INQUIRY_RAISED
    assert _suggested_of(db, sooner_b.id) == []


def test_auto_place_resolves_the_active_policy_once_not_once_per_product(api):
    """S5 (code review, 20 Aug 2026): `_rank_raised_rows` groups raised rows by product and
    scores each group separately (the fix above). `factors_for_demand_rows` resolves
    `active_policy(db)` itself whenever `weights`/`class_weights` is left unset - an
    uncached query - so calling it once per group turned auto-place across N products into
    N identical policy queries. The active policy cannot change mid-call, so it is resolved
    ONCE and passed into every group explicitly; this pins the call count, not just the
    ranking outcome, so a regression that drops the explicit `weights=`/`class_weights=`
    argument fails here even if it happens not to flip any single test's winner."""
    from unittest import mock

    client, db, world, user_id = api
    _policy(db, {"document_age": 1.0}, {"project": 1.0})
    _po_line(
        db, world["company_id"], world["po"], world["product"], world["warehouse"],
        qty_ordered="10", expected_date=date(2026, 9, 1),
    )
    _po_line(
        db, world["company_id"], world["po"], world["other_product"], world["warehouse"],
        qty_ordered="10", expected_date=date(2026, 9, 1),
    )
    for product in (world["product"], world["other_product"]):
        _competing_row(
            db, world["company_id"], world["project"],
            published_at=datetime(2020, 1, 1), delivery_date=date(2026, 12, 1),
            qty="10", item_code=product.product_code,
        )
        _competing_row(
            db, world["company_id"], world["project"],
            published_at=datetime(2026, 8, 1), delivery_date=date(2026, 9, 5),
            qty="10", item_code=product.product_code,
        )
    db.commit()

    from app.models.base import company_scope
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService
    from app.services.scm import priority

    with company_scope(db, frozenset({world["company_id"]})):
        with mock.patch.object(
            priority, "active_policy", wraps=priority.active_policy
        ) as spy:
            result = ProjectOrderInquiryService(db).auto_place_for_products(
                None, actor_user_id=user_id, trigger="test"
            )
            db.commit()

    assert result["products_touched"] == 2
    # Two products competed for two scarce PO lines, so the ranking ran twice - but the
    # policy behind it is resolved ONCE, not once per product (and not once per row, which
    # would be eight here).
    #
    # One read, not the two it briefly counted: the link horizon at the top of the pass
    # used to resolve the active policy as well, and since S2 (code review 27 Aug 2026) it
    # reads the reorder RUN instead - `reorder_coverage_until` was never the right date to
    # link to. Only the ranking's own weights come off the policy now.
    assert spy.call_count == 1, "the active policy must be resolved once per auto-place run"
