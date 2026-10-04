"""Shared seed + fixtures for the CONTACT-BRAND-SCOPE red tests (not a test module).

`documentation/plans/chatbot/contact-brand-scope-4oct-acceptance-criteria.md`.

Every row is seeded here on a blank Postgres schema (CI's database is empty). Two brands
(MOCHA, SORENTO), three products (one per brand plus one with `brand_id` NULL), a scoped
contact (`brand_ids = [MOCHA]`) and an unscoped one (column left NULL).

The `api` fixture does NOT override `apply_company_scope`: the real dependency runs, with a
pinned `X-API-Key` and `contact_id` + `space_id`, because AC-7 stamps the brand scope THERE.
Only authentication (`get_current_user_or_api_key`) is stubbed to a superadmin principal.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.config import settings
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.access import ContactAccessType, RespondContact, respond_contact_access_types
from app.models.base import set_company_scope
from app.models.company import RespondContactCompany
from app.models.order import SalesOrder, SalesOrderLine
from app.models.product import Brand
from app.models.user import User, UserRole, UserRoleAssignment
from app.services.company_scope import DEFAULT_COMPANY_ID

from app.models.order import Order, OrderLine  # noqa: F401
from tests._mc_lookup_seed import (
    attachment,
    attachment_type,
    customer,
    inbound_shipment,
    inbound_shipment_line,
    order,
    order_line,
    product,
    product_attachment,
    stock,
    warehouse,
)
from tests._pg_fixture import blank_session, unique_code

API_KEY = "zzt-cbs-api-key"
SPACE = "zzt-space"
GRANTS = ("sales_orders.sales_report", "scm.low_stock_report", "sales_orders.outstanding")


def brand(db, name: str) -> Brand:
    row = Brand(
        id=str(uuid.uuid4()),
        brand_code=unique_code(name)[:50],
        brand_name=f"ZZT {name}",
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    return row


def branded_product(db, code: str, brand_row: Brand | None):
    row = product(db, company_id=DEFAULT_COMPANY_ID, code=code)
    row.brand_id = brand_row.id if brand_row is not None else None
    db.flush()
    return row


def contact(db, *, brand_ids: list[str] | None = None) -> RespondContact:
    kwargs = {"brand_ids": brand_ids} if brand_ids else {}
    row = RespondContact(
        id=str(uuid.uuid4()), phone_number=f"+6{unique_code('PH')[:10]}", **kwargs
    )
    db.add(row)
    db.flush()
    db.add(
        RespondContactCompany(
            id=str(uuid.uuid4()), respond_contact_id=row.id, company_id=DEFAULT_COMPANY_ID
        )
    )
    db.flush()
    from app.services.contact_field_reveal_service import set_granted_keys

    set_granted_keys(db, row.id, list(GRANTS), actor_id=None)
    # An office access type: the report routes serve staff-tier contacts without a customer
    # link, and AC-7 says the brand scope applies to office staff too (Q2).
    code = unique_code("zzt_at").lower().replace("-", "_")[:50]
    db.add(ContactAccessType(code=code, name="Sorento Office", is_active=True))
    db.flush()
    db.execute(respond_contact_access_types.insert().values(contact_id=row.id, access_type_code=code))
    db.flush()
    return row


class BrandWorld:
    """Brands, products, two contacts, a warehouse with stock and two sales orders."""

    def __init__(self, db) -> None:
        self.mocha = brand(db, "MOCHA")
        self.sorento = brand(db, "SORENTO")
        self.p_mocha = branded_product(db, unique_code("MCH", alpha=True), self.mocha)
        self.p_sorento = branded_product(db, unique_code("SRT", alpha=True), self.sorento)
        self.p_null = branded_product(db, unique_code("NUL", alpha=True), None)
        self.products = (self.p_mocha, self.p_sorento, self.p_null)
        self.wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
        self._db = db
        self._scoped = None
        self.unscoped = contact(db)
        self.cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT BRAND CUSTOMER")
        # mixed order: MOCHA 2 x 100 and SORENTO 3 x 1000; SORENTO-only order: 5 x 7
        self.mixed = self._so(db, [(self.p_mocha, 2, Decimal("100.00")), (self.p_sorento, 3, Decimal("1000.00"))])
        self.sorento_only = self._so(db, [(self.p_sorento, 5, Decimal("7.00"))])
        self.null_only = self._so(db, [(self.p_null, 4, Decimal("9.00"))])
        self.delivered_null = self._so(db, [(self.p_null, 4, Decimal("9.00"))], delivered=True)
        # fully delivered: what the delivered-basis sales report and top selling count
        self.delivered_mixed = self._so(
            db, [(self.p_mocha, 2, Decimal("100.00")), (self.p_sorento, 3, Decimal("1000.00"))], delivered=True
        )
        # delivery orders (the `orders` / `orders-by-product` tools read these, not SOs)
        self.do_mixed = self._do(db, [(self.p_mocha, 2, Decimal("100.00")), (self.p_sorento, 3, Decimal("1000.00"))])
        self.do_sorento_only = self._do(db, [(self.p_sorento, 5, Decimal("7.00"))])
        self.do_null_only = self._do(db, [(self.p_null, 4, Decimal("9.00"))])
        for prod, qty in ((self.p_mocha, 50), (self.p_sorento, 60), (self.p_null, 70)):
            stock(db, company_id=DEFAULT_COMPANY_ID, product_id=prod.id, warehouse_id=self.wh.id, on_hand=qty)
        self.shipment = inbound_shipment(db, company_id=DEFAULT_COMPANY_ID, eta=date(2027, 3, 1))
        for prod in self.products:
            inbound_shipment_line(
                db, company_id=DEFAULT_COMPANY_ID, shipment_id=self.shipment.id, product_id=prod.id, shipped=11
            )
        atype = attachment_type(db, name="Stock List")
        self.stock_list = attachment(db, type_id=atype.id, filename=unique_code("stocklist") + ".pdf")
        for prod in self.products:
            product_attachment(
                db, company_id=DEFAULT_COMPANY_ID, product_id=prod.id, attachment_id=self.stock_list.id
            )
        self._seed_po_promotion_complaints(db)
        db.commit()

    def _seed_po_promotion_complaints(self, db) -> None:
        from datetime import date as _d

        from app.models.complaints import Complaint, ComplaintProductLine
        from app.models.procurement import PurchaseOrder, PurchaseOrderLine
        from tests._mc_lookup_seed import promotion, promotion_group, promotion_product

        # one active PO, one open line per product: MOCHA 10 @ 5, SORENTO 20 @ 6, unbranded 30 @ 7
        po = PurchaseOrder(
            id=str(uuid.uuid4()), po_number=unique_code("PO"), status="active",
            issue_date=_d(2026, 6, 1), expected_date=_d(2026, 12, 1), company_id=DEFAULT_COMPANY_ID,
        )
        db.add(po)
        db.flush()
        self.po = po
        for prod, qty, cost in ((self.p_mocha, 10, 5), (self.p_sorento, 20, 6), (self.p_null, 30, 7)):
            db.add(PurchaseOrderLine(
                id=str(uuid.uuid4()), purchase_order_id=po.id, product_id=prod.id,
                warehouse_id=self.wh.id, qty_ordered=qty, qty_received=0, unit_cost=cost,
                line_total=qty * cost, expected_date=_d(2026, 12, 1), line_status="open",
                company_id=DEFAULT_COMPANY_ID,
            ))
        promo = promotion(db, company_id=DEFAULT_COMPANY_ID)
        group = promotion_group(db, company_id=DEFAULT_COMPANY_ID, promotion_id=promo.id)
        for prod in self.products:
            promotion_product(
                db, company_id=DEFAULT_COMPANY_ID, promotion_id=promo.id,
                promotion_group_id=group.id, product_id=prod.id,
            )
        # complaints: A mixed (MOCHA + SORENTO), B SORENTO only, C unbranded only
        self.complaints = {}
        for key, prods in (("mixed", (self.p_mocha, self.p_sorento)), ("sorento_only", (self.p_sorento,)),
                           ("null_only", (self.p_null,))):
            c = Complaint(
                id=str(uuid.uuid4()), complaint_number=unique_code("CMP"), complaint_date=_d(2026, 6, 2),
                customer_name="ZZT BRAND CUSTOMER", product_code=",".join(p.product_code for p in prods),
                status="approved",
            )
            db.add(c)
            db.flush()
            for n, prod in enumerate(prods):
                db.add(ComplaintProductLine(
                    id=str(uuid.uuid4()), complaint_id=c.id, product_code=prod.product_code,
                    quantity="1", sort_order=n,
                ))
            self.complaints[key] = c
        db.flush()

    def _do(self, db, lines) -> Order:
        row = order(db, company_id=DEFAULT_COMPANY_ID, customer_id=self.cust.id, number=unique_code("DO"))
        row.debtor_name = "ZZT BRAND CUSTOMER"
        row.source_book = "db1"  # AutoCount DO: each line carries its own total
        row.order_date = date(2026, 6, 1)  # inside the reports' 2026 window
        for seq, (prod, qty, total) in enumerate(lines, start=1):
            db.add(
                OrderLine(
                    id=str(uuid.uuid4()), order_id=row.id, product_id=prod.id,
                    warehouse_id=self.wh.id, quantity=qty, total=total, total_excluding_tax=total, line_sequence=seq,
                    company_id=DEFAULT_COMPANY_ID,
                )
            )
        row.total_amount = sum((t for _p, _q, t in lines), Decimal("0"))
        db.flush()
        return row

    @property
    def scoped(self) -> RespondContact:
        """MOCHA-only contact, built on first use so an unscoped-only test never needs the column."""
        if self._scoped is None:
            self._scoped = contact(self._db, brand_ids=[self.mocha.id])
            self._db.commit()
        return self._scoped

    def _so(self, db, lines, *, delivered: bool = False) -> SalesOrder:
        so = SalesOrder(
            id=str(uuid.uuid4()),
            so_number=unique_code("SO"),
            customer_id=self.cust.id,
            order_date=date(2026, 6, 1),
            status="closed" if delivered else "open",
            company_id=DEFAULT_COMPANY_ID,
        )
        db.add(so)
        db.flush()
        for prod, qty, total in lines:
            db.add(
                SalesOrderLine(
                    id=str(uuid.uuid4()),
                    sales_order_id=so.id,
                    product_id=prod.id,
                    qty_ordered=qty,
                    qty_delivered=qty if delivered else 0,
                    line_total=total,
                    line_status="closed" if delivered else "open",
                    company_id=DEFAULT_COMPANY_ID,
                )
            )
        db.flush()
        return so

    @property
    def codes(self) -> dict[str, str]:
        return {
            "mocha": self.p_mocha.product_code,
            "sorento": self.p_sorento.product_code,
            "null": self.p_null.product_code,
        }


@pytest.fixture
def db():
    with blank_session() as s:
        set_company_scope(s, frozenset({DEFAULT_COMPANY_ID}))
        yield s


@pytest.fixture
def world(db) -> BrandWorld:
    return BrandWorld(db)


def _superadmin(db) -> dict:
    user = User(id=str(uuid.uuid4()), email=f"{unique_code('SA')}@test.com", name="ZZT SA", status="ACTIVE")
    role = UserRole(id=str(uuid.uuid4()), slug="superadmin", name="Superadmin")
    db.add_all([user, role])
    db.flush()
    db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
    db.flush()
    return {"id": user.id, "email": user.email}


class Api:
    """X-API-Key caller. `as_contact(...)` adds `contact_id` + `space_id` (the MCP shape)."""

    def __init__(self, client: TestClient, db) -> None:
        self.client = client
        self.db = db

    def get(self, path: str, who: RespondContact | None, **params):
        # Each request re-stamps its own scope; drop anything a previous call left on the
        # shared test session so one call cannot lean on another's stamp.
        self.db.info.pop("brand_scope", None)
        self.db.commit()
        query = {k: v for k, v in params.items() if v is not None}
        if who is not None:
            query.update({"contact_id": who.id, "space_id": SPACE})
        try:
            return self.client.get(path, params=query, headers={"X-API-Key": API_KEY})
        finally:
            self.db.info.pop("brand_scope", None)
            set_company_scope(self.db, frozenset({DEFAULT_COMPANY_ID}))


@pytest.fixture
def api(db, monkeypatch):
    monkeypatch.setattr(settings, "external_api_key", API_KEY)
    principal = _superadmin(db)

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    try:
        yield Api(TestClient(app), db)
    finally:
        app.dependency_overrides.clear()


def body_text(resp) -> str:
    return resp.text


def leaks(resp, world: BrandWorld) -> dict[str, bool]:
    """Which product codes appear anywhere in a response body."""
    return {name: code in resp.text for name, code in world.codes.items()}
