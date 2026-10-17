"""AC-BILLING S2/S3/S5: the finance billing-documents read API (red tests, written before the code).

UAC: documentation/plans/finance/finance-billing-documents-27sep-acceptance-criteria.md

  S2-4   GET /api/v1/finance/billing-documents pages, sorts, searches, filters; company scoped;
         403 without `finance.billing_documents.view`
  S2-8   GET /api/v1/finance/billing-documents/{id}: header, lines, related in one response;
         another company's document is 404
  S2-11  no finance slug: every /api/v1/finance/* call answers 403
  S2-12  POST /api/v1/list-query/export, resource `finance_billing_documents`: `.view` without
         `.export` is 403; both slugs get the filtered, company-scoped rows
  S3-1   `sales_order_id` filter: documents with a line pointing at one of that SO's lines
  S3-3   the backing calls answer 403 without the view slug
  S5-8   outstanding / due_date / outstanding_as_of in the list and the record, `open_only`;
         the customer's AR total endpoint

Route level on purpose (the permission gate, the `/finance` mount and the company scope are part
of what is promised). Nothing exists yet on this branch: every test is expected to fail with a 404
on the route, or an UndefinedColumn for the not-yet-migrated `outstanding` columns, never on a
fixture bug.

Rows are seeded straight through the ORM; the `outstanding*` columns are written with raw SQL so
that a missing column fails only the tests that need it.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

# MUST be the first app import - resolves the circular import in app.modules.runtime.guards.
from app.main import app  # noqa: E402

from app.models.base import company_scope
from app.models.company import Company
from app.models.finance import BillingDocument, BillingDocumentLine
from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.sales_agent import SalesAgent
from app.services.company_scope import DEFAULT_COMPANY_ID

from ._pg_fixture import blank_session, unique_code

MARKER = "ZZFAPI"
LIST = "/api/v1/finance/billing-documents"
VIEW = "finance.billing_documents.view"
EXPORT = "finance.billing_documents.export"

LIST_ROW_KEYS = {
    "id", "document_type", "doc_no", "doc_date", "customer_id", "customer_name", "debtor_code",
    "agent_code", "agent_name", "currency_code", "net_total", "tax_total", "total", "status",
    "outstanding", "due_date", "outstanding_as_of",
}
DETAIL_EXTRA_KEYS = {
    "currency_rate", "local_net_total", "ref", "description", "last_synced_at",
    "source_modified_at", "against", "lines", "related",
}
LINE_KEYS = {
    "line_no", "item_code", "product_name", "description", "uom", "quantity", "unit_price",
    "discount_amount", "net_amount", "tax_code", "tax_rate", "tax_amount", "line_total",
    "from_doc_type", "from_doc_no", "sales_order",
}


def _uid() -> str:
    return str(uuid.uuid4())


def _money(value) -> Decimal:
    return Decimal(str(value))


class _World:
    """Company A (default) holds the caller's data; company B holds data the caller must never
    see. Everything is seeded here: CI's database holds nothing."""

    def __init__(self, db):
        self.db = db
        self.company_a = DEFAULT_COMPANY_ID
        suffix = uuid.uuid4().hex[:8]
        other = Company(id=_uid(), name=f"{MARKER} B {suffix}", code=f"ZA{suffix}")
        db.add(other)
        db.flush()
        self.company_b = str(other.id)

        category = ProductCategory(category_code=unique_code(MARKER), category_name=f"{MARKER} cat")
        uom = UnitOfMeasure(uom_code=unique_code(MARKER), uom_name=f"{MARKER} unit")
        db.add_all([category, uom])
        db.flush()
        self.product = Product(
            product_code=unique_code(MARKER),
            product_name=f"{MARKER} Widget",
            category_id=category.id,
            base_uom_id=uom.id,
            list_price=10,
            company_id=self.company_a,
        )
        db.add(self.product)
        db.flush()

        self.customer = self.customer_row("C1", self.company_a)
        self.customer2 = self.customer_row("C2", self.company_a)
        self.customer_b = self.customer_row("CB", self.company_b)
        self.agent = SalesAgent(sales_agent=f"{MARKER}-AG1", description=f"{MARKER} Agent One")
        db.add(self.agent)
        db.flush()

        self.so = SalesOrder(
            so_number=f"{MARKER}-SO1", company_id=self.company_a, source_system="autocount"
        )
        self.so2 = SalesOrder(
            so_number=f"{MARKER}-SO2", company_id=self.company_a, source_system="autocount"
        )
        db.add_all([self.so, self.so2])
        db.flush()
        self.so_line = self._so_line(self.so)
        self.so2_line = self._so_line(self.so2)
        db.flush()

    def _so_line(self, order):
        line = SalesOrderLine(
            sales_order_id=order.id,
            product_id=self.product.id,
            qty_ordered=10,
            source_ref=f"{MARKER}:{uuid.uuid4().hex[:6]}",
            company_id=self.company_a,
        )
        self.db.add(line)
        return line

    def customer_row(self, code: str, company_id: str) -> Customer:
        row = Customer(
            customer_code=f"{MARKER}-{code}-{uuid.uuid4().hex[:4]}",
            customer_name=f"{MARKER} {code}",
            company_id=company_id,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def doc(
        self,
        doc_no: str,
        *,
        document_type: str = "invoice",
        doc_date: date = date(2026, 9, 10),
        company_id: str | None = None,
        customer: Customer | None = None,
        agent_code: str | None = None,
        sales_agent_id: str | None = None,
        status: str = "posted",
        total: str = "100.00",
        customer_name: str | None = None,
        debtor_code: str | None = None,
        against: BillingDocument | None = None,
        against_doc_no: str | None = None,
        lines: list[dict] | None = None,
        outstanding: str | None = None,
        due_date: str | None = None,
        as_of: str | None = None,
    ) -> BillingDocument:
        company_id = company_id or self.company_a
        row = BillingDocument(
            company_id=company_id,
            document_type=document_type,
            doc_no=doc_no,
            doc_date=doc_date,
            customer_id=customer.id if customer is not None else None,
            customer_name=customer_name or (customer.customer_name if customer else None),
            debtor_code=debtor_code or (customer.customer_code if customer else None),
            agent_code=agent_code,
            sales_agent_id=sales_agent_id,
            currency_code="MYR",
            currency_rate=1,
            net_total=Decimal(total),
            tax_total=Decimal("0.00"),
            total=Decimal(total),
            local_net_total=Decimal(total),
            status=status,
            against_document_id=against.id if against is not None else None,
            against_doc_no=against_doc_no or (against.doc_no if against is not None else None),
            ref="REF-1",
            description="desc",
            source_system="autocount",
            source_ref=f"{MARKER}:{doc_no}:{uuid.uuid4().hex[:6]}",
            source_modified_at=datetime(2026, 9, 10, 8, 0, 0),
            last_synced_at=datetime(2026, 9, 11, 8, 0, 0),
        )
        self.db.add(row)
        self.db.flush()
        for number, spec in enumerate(lines or [], start=1):
            self.db.add(
                BillingDocumentLine(
                    company_id=company_id,
                    document_id=row.id,
                    line_no=spec.get("line_no", number),
                    product_id=spec.get("product_id"),
                    item_code=spec.get("item_code", "ITEM"),
                    description=spec.get("description", "line"),
                    uom="PCS",
                    quantity=Decimal("2"),
                    unit_price=Decimal("50"),
                    discount_amount=Decimal("0"),
                    net_amount=Decimal("100.00"),
                    tax_code="SR",
                    tax_rate=Decimal("0"),
                    tax_amount=Decimal("0.00"),
                    line_total=Decimal("100.00"),
                    sales_order_line_id=spec.get("sales_order_line_id"),
                    from_doc_type=spec.get("from_doc_type"),
                    from_doc_no=spec.get("from_doc_no"),
                    source_ref=f"{row.source_ref}:{number}",
                )
            )
        self.db.flush()
        if outstanding is not None or due_date is not None or as_of is not None:
            self.set_outstanding(row, outstanding, due_date, as_of)
        return row

    def set_outstanding(self, row, outstanding, due_date=None, as_of="2026-10-26 09:00:00"):
        self.db.execute(
            text(
                "UPDATE billing_documents SET outstanding = :o, due_date = :d, "
                "outstanding_as_of = :a WHERE id = :id"
            ),
            {"o": outstanding, "d": due_date, "a": as_of, "id": row.id},
        )
        self.db.expire_all()


@pytest.fixture
def world():
    with blank_session() as db:
        with company_scope(db, None):
            w = _World(db)
        db.commit()
        with company_scope(db, frozenset({w.company_a})):
            yield w


def _client(db, granted, *, raise_server_exceptions=True):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.services.company_scope_resolver import apply_company_scope

    user_id = _uid()
    actor = {"id": user_id, "email": f"{user_id}@zzfapi.test", "role": "user"}
    scope = frozenset({DEFAULT_COMPANY_ID})

    def _scope():
        from app.models.base import set_company_scope

        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)
    app.dependency_overrides[apply_company_scope] = _scope
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


@pytest.fixture
def api(world, monkeypatch):
    """A client holding only the slugs a test names (`api(VIEW)`), over `world`'s session."""
    from app.services.user_service import UserPermissionService

    created = []

    def make(*slugs, raise_server_exceptions=True):
        granted = list(slugs)
        monkeypatch.setattr(
            UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in granted
        )
        monkeypatch.setattr(
            UserPermissionService, "get_user_permission_slugs", lambda self, uid: list(granted)
        )
        client = _client(world.db, granted, raise_server_exceptions=raise_server_exceptions)
        created.append(client)
        return client

    try:
        yield make
    finally:
        app.dependency_overrides.clear()


def _data(res) -> list[dict]:
    assert res.status_code == 200, res.text
    return res.json()["data"]


def _nos(res) -> list[str]:
    return [row["doc_no"] for row in _data(res)]


# ===================================================================== S2-4 list
class TestList:
    def test_envelope_and_row_keys(self, world, api):
        iv = world.doc(
            "IV-1", customer=world.customer, agent_code=world.agent.sales_agent,
            sales_agent_id=world.agent.id, total="110.00",
        )
        res = api(VIEW).get(LIST)
        body = res.json()
        assert res.status_code == 200, res.text
        assert set(body) >= {"data", "pagination", "empty"}
        assert body["empty"] is False
        assert body["pagination"] == {"total": 1, "page": 1, "limit": body["pagination"]["limit"]}
        row = body["data"][0]
        assert set(row) >= LIST_ROW_KEYS, sorted(LIST_ROW_KEYS - set(row))
        assert row["id"] == str(iv.id)
        assert row["document_type"] == "invoice"
        assert row["doc_no"] == "IV-1"
        assert row["doc_date"] == "2026-09-10"
        assert row["customer_id"] == str(world.customer.id)
        assert row["customer_name"] == world.customer.customer_name
        assert row["debtor_code"] == world.customer.customer_code
        assert row["agent_code"] == world.agent.sales_agent
        assert row["agent_name"] in (world.agent.description, world.agent.sales_agent)
        assert row["currency_code"] == "MYR"
        assert _money(row["total"]) == Decimal("110.00")
        assert row["status"] == "posted"

    def test_empty_list_is_flagged_empty(self, world, api):
        body = api(VIEW).get(LIST).json()
        assert body["data"] == [] and body["empty"] is True and body["pagination"]["total"] == 0

    def test_default_sort_is_doc_date_descending(self, world, api):
        world.doc("IV-OLD", doc_date=date(2026, 1, 1))
        world.doc("IV-NEW", doc_date=date(2026, 9, 1))
        world.doc("IV-MID", doc_date=date(2026, 5, 1))
        assert _nos(api(VIEW).get(LIST)) == ["IV-NEW", "IV-MID", "IV-OLD"]

    def test_sort_and_dir_are_honoured(self, world, api):
        for no in ("IV-B", "IV-A", "IV-C"):
            world.doc(no)
        client = api(VIEW)
        assert _nos(client.get(LIST, params={"sort": "doc_no", "dir": "asc"})) == [
            "IV-A", "IV-B", "IV-C",
        ]
        assert _nos(client.get(LIST, params={"sort": "doc_no", "dir": "desc"})) == [
            "IV-C", "IV-B", "IV-A",
        ]

    def test_paging(self, world, api):
        for no in ("IV-1", "IV-2", "IV-3"):
            world.doc(no)
        client = api(VIEW)
        params = {"sort": "doc_no", "dir": "asc", "limit": 2}
        first = client.get(LIST, params={**params, "page": 1})
        second = client.get(LIST, params={**params, "page": 2})
        assert _nos(first) == ["IV-1", "IV-2"]
        assert _nos(second) == ["IV-3"]
        assert first.json()["pagination"] == {"total": 3, "page": 1, "limit": 2}
        assert second.json()["pagination"]["page"] == 2

    def test_query_matches_doc_no_customer_name_and_debtor_code(self, world, api):
        world.doc("IV-FIND-ME")
        world.doc("IV-2", customer_name="Findable Trading Sdn Bhd")
        world.doc("IV-3", debtor_code="300-FINDCODE")
        world.doc("IV-4", customer_name="Other", debtor_code="999")
        client = api(VIEW)
        assert _nos(client.get(LIST, params={"query": "find-me"})) == ["IV-FIND-ME"]
        assert _nos(client.get(LIST, params={"query": "findable"})) == ["IV-2"]
        assert _nos(client.get(LIST, params={"query": "findcode"})) == ["IV-3"]

    def test_document_type_is_repeatable(self, world, api):
        world.doc("IV-1", document_type="invoice")
        world.doc("CN-1", document_type="credit_note")
        world.doc("CS-1", document_type="cash_sale")
        client = api(VIEW)
        assert sorted(
            _nos(client.get(LIST + "?document_type=invoice&document_type=credit_note"))
        ) == ["CN-1", "IV-1"]
        assert _nos(client.get(LIST + "?document_type=cash_sale")) == ["CS-1"]

    def test_status_customer_agent_and_date_filters(self, world, api):
        world.doc("IV-POSTED", customer=world.customer, agent_code="AG-X", doc_date=date(2026, 3, 1))
        world.doc("IV-CANC", status="cancelled", customer=world.customer2, agent_code="AG-Y",
                  doc_date=date(2026, 6, 1))
        world.doc("IV-LATE", customer=world.customer2, agent_code="AG-Y", doc_date=date(2026, 9, 1))
        client = api(VIEW)
        assert _nos(client.get(LIST, params={"status": "cancelled"})) == ["IV-CANC"]
        assert sorted(_nos(client.get(LIST, params={"customer_id": world.customer2.id}))) == [
            "IV-CANC", "IV-LATE",
        ]
        assert _nos(client.get(LIST, params={"agent_code": "AG-X"})) == ["IV-POSTED"]
        assert _nos(
            client.get(LIST, params={"date_from": "2026-05-01", "date_to": "2026-06-30"})
        ) == ["IV-CANC"]
        assert sorted(_nos(client.get(LIST, params={"date_from": "2026-06-01"}))) == [
            "IV-CANC", "IV-LATE",
        ]

    def test_sales_order_id_finds_documents_with_a_line_on_that_order(self, world, api):
        """S3-1: the SO record's Invoices section."""
        world.doc("IV-ON-SO", lines=[{"sales_order_line_id": world.so_line.id},
                                     {"sales_order_line_id": None}])
        world.doc("IV-ON-SO2", lines=[{"sales_order_line_id": world.so2_line.id}])
        world.doc("IV-NONE", lines=[{"sales_order_line_id": None}])
        client = api(VIEW)
        assert _nos(client.get(LIST, params={"sales_order_id": world.so.id})) == ["IV-ON-SO"]
        # A document with two lines on the same order is one row, not two.
        world.doc("IV-TWICE", lines=[{"sales_order_line_id": world.so_line.id},
                                     {"sales_order_line_id": world.so_line.id}])
        res = client.get(LIST, params={"sales_order_id": world.so.id})
        assert sorted(_nos(res)) == ["IV-ON-SO", "IV-TWICE"]
        assert res.json()["pagination"]["total"] == 2

    def test_other_companys_documents_never_appear(self, world, api):
        world.doc("IV-MINE", customer=world.customer)
        with company_scope(world.db, None):
            world.doc("IV-THEIRS", company_id=world.company_b, customer=world.customer_b)
        world.db.commit()
        res = api(VIEW).get(LIST)
        assert _nos(res) == ["IV-MINE"]
        assert res.json()["pagination"]["total"] == 1
        # Not even by an explicit filter or search.
        client = api(VIEW)
        assert _data(client.get(LIST, params={"query": "THEIRS"})) == []
        assert _data(client.get(LIST, params={"customer_id": world.customer_b.id})) == []

    def test_validation_errors_are_422(self, world, api):
        client = api(VIEW)
        assert client.get(LIST, params={"dir": "sideways"}).status_code == 422
        assert client.get(LIST, params={"limit": 0}).status_code == 422
        assert client.get(LIST, params={"page": 0}).status_code == 422
        assert client.get(LIST, params={"date_from": "not-a-date"}).status_code == 422

    def test_403_without_the_view_slug(self, world, api):
        """S2-4, S2-11, S3-3."""
        world.doc("IV-1")
        assert api().get(LIST).status_code == 403
        assert api(EXPORT).get(LIST).status_code == 403


# ===================================================================== S5-8 outstanding
class TestOutstandingInTheList:
    def test_row_carries_outstanding_due_date_and_as_of(self, world, api):
        world.doc("IV-OPEN", outstanding="75.50", due_date="2026-11-25", as_of="2026-10-26 09:00:00")
        world.doc("IV-NEVER")
        by_no = {r["doc_no"]: r for r in _data(api(VIEW).get(LIST))}
        open_row = by_no["IV-OPEN"]
        assert _money(open_row["outstanding"]) == Decimal("75.50")
        assert open_row["due_date"] == "2026-11-25"
        assert open_row["outstanding_as_of"].startswith("2026-10-26T09:00:00")
        never = by_no["IV-NEVER"]
        assert never["outstanding"] is None  # S5-7: never covered is blank, not 0
        assert never["due_date"] is None and never["outstanding_as_of"] is None

    def test_open_only_keeps_what_is_still_owed(self, world, api):
        world.doc("IV-OWED", outstanding="10.00")
        world.doc("IV-PAID", outstanding="0.00")
        world.doc("IV-UNSYNCED")
        world.doc("CN-UNAPPLIED", document_type="credit_note", outstanding="-20.00")
        world.doc("CN-USED", document_type="credit_note", outstanding="0.00")
        res = api(VIEW).get(LIST, params={"open_only": "true"})
        assert sorted(_nos(res)) == ["CN-UNAPPLIED", "IV-OWED"]
        assert res.json()["pagination"]["total"] == 2
        everything = api(VIEW).get(LIST, params={"open_only": "false"})
        assert len(_nos(everything)) == 5


# ===================================================================== S2-8 detail
class TestDetail:
    def _invoice_world(self, world):
        iv = world.doc(
            "IV-REC",
            customer=world.customer,
            outstanding="40.00",
            due_date="2026-11-25",
            lines=[
                # Inserted line 2 first on purpose: the response is ordered by line_no.
                {"line_no": 2, "item_code": "B", "from_doc_type": "DO", "from_doc_no": "DO-77",
                 "sales_order_line_id": world.so2_line.id},
                {"line_no": 1, "item_code": "A", "product_id": world.product.id,
                 "from_doc_type": "SO", "from_doc_no": world.so.so_number,
                 "sales_order_line_id": world.so_line.id},
                {"line_no": 3, "item_code": "C", "from_doc_type": "DO", "from_doc_no": "DO-78"},
                {"line_no": 4, "item_code": "D", "from_doc_type": "DO", "from_doc_no": "DO-77"},
            ],
        )
        cn = world.doc("CN-REC", document_type="credit_note", against=iv, total="20.00",
                       outstanding="-5.00")
        dn = world.doc("DN-REC", document_type="debit_note", against=iv, total="5.00")
        return iv, cn, dn

    def test_header_keys_and_values(self, world, api):
        iv, _cn, _dn = self._invoice_world(world)
        res = api(VIEW).get(f"{LIST}/{iv.id}")
        assert res.status_code == 200, res.text
        body = res.json()
        assert set(body) >= LIST_ROW_KEYS | DETAIL_EXTRA_KEYS, sorted(
            (LIST_ROW_KEYS | DETAIL_EXTRA_KEYS) - set(body)
        )
        assert body["id"] == str(iv.id)
        assert body["doc_no"] == "IV-REC"
        assert _money(body["currency_rate"]) == Decimal("1")
        assert _money(body["local_net_total"]) == Decimal("100.00")
        assert body["ref"] == "REF-1" and body["description"] == "desc"
        assert body["last_synced_at"].startswith("2026-09-11T08:00:00")
        assert body["source_modified_at"].startswith("2026-09-10T08:00:00")
        assert _money(body["outstanding"]) == Decimal("40.00")
        assert body["due_date"] == "2026-11-25"
        assert body["against"] is None

    def test_lines_are_ordered_and_named(self, world, api):
        iv, _cn, _dn = self._invoice_world(world)
        body = api(VIEW).get(f"{LIST}/{iv.id}").json()
        lines = body["lines"]
        assert [line["line_no"] for line in lines] == [1, 2, 3, 4]
        for line in lines:
            assert set(line) >= LINE_KEYS, sorted(LINE_KEYS - set(line))
        first = lines[0]
        assert first["item_code"] == "A"
        assert first["product_name"] == world.product.product_name
        assert _money(first["quantity"]) == Decimal("2")
        assert _money(first["unit_price"]) == Decimal("50")
        assert _money(first["net_amount"]) == Decimal("100.00")
        assert _money(first["line_total"]) == Decimal("100.00")
        assert first["tax_code"] == "SR"
        assert first["from_doc_type"] == "SO" and first["from_doc_no"] == world.so.so_number
        assert first["sales_order"] == {"id": str(world.so.id), "doc_no": world.so.so_number}
        assert lines[1]["sales_order"] == {"id": str(world.so2.id), "doc_no": world.so2.so_number}
        assert lines[2]["sales_order"] is None
        assert lines[2]["product_name"] is None

    def test_related_notes_sales_orders_and_delivery_orders(self, world, api):
        iv, cn, dn = self._invoice_world(world)
        related = api(VIEW).get(f"{LIST}/{iv.id}").json()["related"]
        assert set(related) >= {"notes", "sales_orders", "delivery_order_nos"}
        notes = {n["doc_no"]: n for n in related["notes"]}
        assert set(notes) == {"CN-REC", "DN-REC"}
        assert set(notes["CN-REC"]) >= {"id", "document_type", "doc_no", "doc_date", "total", "outstanding"}
        assert notes["CN-REC"]["id"] == str(cn.id)
        assert notes["CN-REC"]["document_type"] == "credit_note"
        assert _money(notes["CN-REC"]["total"]) == Decimal("20.00")
        assert _money(notes["CN-REC"]["outstanding"]) == Decimal("-5.00")
        assert notes["DN-REC"]["id"] == str(dn.id)
        assert notes["DN-REC"]["outstanding"] is None
        # Distinct sales orders of the linked lines, once each.
        assert sorted(so["doc_no"] for so in related["sales_orders"]) == sorted(
            [world.so.so_number, world.so2.so_number]
        )
        assert {so["id"] for so in related["sales_orders"]} == {str(world.so.id), str(world.so2.id)}
        # Distinct DO numbers (DO-77 sits on two lines), SO-typed lines excluded.
        assert sorted(related["delivery_order_nos"]) == ["DO-77", "DO-78"]

    def test_a_document_with_nothing_related_has_empty_related(self, world, api):
        lone = world.doc("IV-LONE", lines=[{"item_code": "X"}])
        body = api(VIEW).get(f"{LIST}/{lone.id}").json()
        assert body["related"] == {"notes": [], "sales_orders": [], "delivery_order_nos": []}
        assert body["against"] is None

    def test_a_credit_note_names_its_invoice(self, world, api):
        iv, cn, _dn = self._invoice_world(world)
        against = api(VIEW).get(f"{LIST}/{cn.id}").json()["against"]
        assert against == {"id": str(iv.id), "doc_no": "IV-REC"}

    def test_against_with_only_the_raw_number_has_a_null_id(self, world, api):
        orphan = world.doc("CN-ORPHAN", document_type="credit_note", against_doc_no="IV-NOT-IN-CRM")
        against = api(VIEW).get(f"{LIST}/{orphan.id}").json()["against"]
        assert against == {"id": None, "doc_no": "IV-NOT-IN-CRM"}

    def test_unknown_id_is_404(self, world, api):
        client = api(VIEW)
        lone = world.doc("IV-CONTROL")
        assert client.get(f"{LIST}/{lone.id}").status_code == 200  # the route exists
        assert client.get(f"{LIST}/{_uid()}").status_code == 404

    def test_another_companys_document_is_404(self, world, api):
        with company_scope(world.db, None):
            theirs = world.doc("IV-THEIRS", company_id=world.company_b, customer=world.customer_b)
        world.db.commit()
        client = api(VIEW)
        mine = world.doc("IV-CONTROL")
        assert client.get(f"{LIST}/{mine.id}").status_code == 200  # the route exists
        assert client.get(f"{LIST}/{theirs.id}").status_code == 404

    def test_403_without_the_view_slug(self, world, api):
        lone = world.doc("IV-LONE")
        assert api().get(f"{LIST}/{lone.id}").status_code == 403
        assert api(EXPORT).get(f"{LIST}/{lone.id}").status_code == 403


# ===================================================================== customer AR total
class TestCustomerOutstanding:
    def _url(self, customer) -> str:
        return f"/api/v1/finance/customers/{customer.id}/outstanding"

    def test_returns_the_stored_total_and_as_of(self, world, api):
        world.db.execute(
            text("UPDATE customers SET ar_outstanding = 15230.40, ar_as_of = :a WHERE id = :id"),
            {"a": "2026-10-26 09:00:00", "id": world.customer.id},
        )
        world.db.expire_all()
        res = api(VIEW).get(self._url(world.customer))
        assert res.status_code == 200, res.text
        body = res.json()
        assert set(body) >= {"customer_id", "ar_outstanding", "ar_as_of"}
        assert body["customer_id"] == str(world.customer.id)
        assert _money(body["ar_outstanding"]) == Decimal("15230.40")
        assert body["ar_as_of"].startswith("2026-10-26T09:00:00")

    def test_nulls_when_never_synced(self, world, api):
        body = api(VIEW).get(self._url(world.customer2)).json()
        assert body["customer_id"] == str(world.customer2.id)
        assert body["ar_outstanding"] is None and body["ar_as_of"] is None

    def test_another_companys_customer_is_404(self, world, api):
        client = api(VIEW)
        assert client.get(self._url(world.customer)).status_code == 200  # the route exists
        assert client.get(self._url(world.customer_b)).status_code == 404

    def test_403_without_the_view_slug(self, world, api):
        assert api().get(self._url(world.customer)).status_code == 403
        assert api(EXPORT).get(self._url(world.customer)).status_code == 403


# ===================================================================== S2-12 export
class TestExport:
    URL = "/api/v1/list-query/export"
    BODY = {"resource": "finance_billing_documents", "fields": [{"field_key": "doc_no"}]}

    def test_view_without_export_is_403(self, world, api):
        world.doc("IV-1")
        res = api(VIEW, raise_server_exceptions=False).post(self.URL, json=self.BODY)
        assert res.status_code == 403, res.text

    def test_both_slugs_get_the_company_scoped_rows(self, world, api):
        world.doc("IV-MINE")
        with company_scope(world.db, None):
            world.doc("IV-THEIRS", company_id=world.company_b)
        world.db.commit()
        res = api(VIEW, EXPORT, raise_server_exceptions=False).post(self.URL, json=self.BODY)
        assert res.status_code == 200, res.text
        exported = [row.get("doc_no") for row in res.json()["data"]]
        assert exported == ["IV-MINE"]
        assert res.json()["total"] == 1


# ===================================================================== fix round
class TestCancelledShowsNoOutstanding:
    """UAC S5-7: a cancelled document is shown with no outstanding."""

    def _cancelled(self, world, **kw):
        return world.doc(
            "IV-CANX", status="cancelled", outstanding="75.00", due_date="2026-11-01",
            as_of="2026-10-26 09:00:00", **kw,
        )

    def test_list_row_has_no_outstanding(self, world, api):
        self._cancelled(world)
        row = _data(api(VIEW).get(LIST))[0]
        assert row["outstanding"] is None
        assert row["due_date"] is None
        assert row["outstanding_as_of"] is None

    def test_sorting_by_outstanding_ranks_it_as_blank(self, world, api):
        """Its stored balance is the largest, but the cell is blank, so it sorts with the blanks."""
        world.doc("IV-OPEN", outstanding="10.00", as_of="2026-10-26 09:00:00")
        self._cancelled(world)
        rows = _data(api(VIEW).get(LIST, params={"sort": "outstanding", "dir": "desc"}))
        assert [r["doc_no"] for r in rows] == ["IV-OPEN", "IV-CANX"]

    def test_detail_has_no_outstanding(self, world, api):
        doc = self._cancelled(world)
        body = api(VIEW).get(f"{LIST}/{doc.id}").json()
        assert body["outstanding"] is None and body["due_date"] is None
        assert body["outstanding_as_of"] is None

    def test_related_cancelled_note_has_no_outstanding(self, world, api):
        iv = world.doc("IV-PARENT")
        world.doc(
            "CN-CANX", document_type="credit_note", status="cancelled", against=iv,
            outstanding="-20.00", as_of="2026-10-26 09:00:00",
        )
        notes = api(VIEW).get(f"{LIST}/{iv.id}").json()["related"]["notes"]
        assert notes[0]["outstanding"] is None

    def test_open_only_excludes_it(self, world, api):
        self._cancelled(world)
        world.doc("IV-OPEN", outstanding="10.00", as_of="2026-10-26 09:00:00")
        assert _nos(api(VIEW).get(LIST, params={"open_only": "true"})) == ["IV-OPEN"]


class TestRelatedNotesCurrency:
    def test_note_rows_carry_their_currency(self, world, api):
        iv = world.doc("IV-PARENT2")
        cn = world.doc("CN-USD", document_type="credit_note", against=iv)
        world.db.execute(
            text("UPDATE billing_documents SET currency_code = 'USD' WHERE id = :id"), {"id": cn.id}
        )
        world.db.expire_all()
        notes = api(VIEW).get(f"{LIST}/{iv.id}").json()["related"]["notes"]
        assert notes[0]["currency_code"] == "USD"


class TestListCustomerNameFallback:
    def test_linked_customer_name_fills_a_null_snapshot(self, world, api):
        doc = world.doc("IV-NONAME", customer=world.customer)
        world.db.execute(
            text("UPDATE billing_documents SET customer_name = NULL WHERE id = :id"), {"id": doc.id}
        )
        world.db.expire_all()
        row = _data(api(VIEW).get(LIST))[0]
        assert row["customer_name"] == world.customer.customer_name

    def test_own_snapshot_wins(self, world, api):
        world.doc("IV-SNAP", customer=world.customer, customer_name="Snapshot Name")
        assert _data(api(VIEW).get(LIST))[0]["customer_name"] == "Snapshot Name"


class TestExportHardening:
    URL = "/api/v1/list-query/export"
    BODY = {"resource": "finance_billing_documents", "fields": [{"field_key": "doc_no"}]}

    def test_malformed_customer_id_is_a_plain_400(self, world, api):
        world.doc("IV-1")
        res = api(VIEW, EXPORT, raise_server_exceptions=False).post(
            self.URL, json={**self.BODY, "customer_id": "not-a-uuid"}
        )
        assert res.status_code == 400, res.text
        assert "SELECT" not in res.text.upper()

    def test_malformed_record_ids_is_a_plain_400(self, world, api):
        world.doc("IV-1")
        res = api(VIEW, EXPORT, raise_server_exceptions=False).post(
            self.URL, json={**self.BODY, "record_ids": ["nope"]}
        )
        assert res.status_code == 400, res.text
        assert "SELECT" not in res.text.upper()

    @pytest.mark.parametrize(
        "extra",
        [
            {"customer_id": "urn:uuid:12345678-1234-5678-1234-567812345678"},
            {"record_ids": ["{12345678-1234-5678-1234-567812345678}"]},
        ],
    )
    def test_a_uuid_form_postgres_rejects_is_still_a_plain_answer(self, world, api, extra):
        """Python's UUID() accepts `urn:uuid:`, braces and missing hyphens; Postgres does not.
        The value is canonicalised before it reaches SQL, so this is never a 500 with SQL."""
        world.doc("IV-1")
        res = api(VIEW, EXPORT, raise_server_exceptions=False).post(
            self.URL, json={**self.BODY, **extra}
        )
        assert res.status_code in (200, 400), res.text
        assert "SELECT" not in res.text.upper()

    def test_an_unexpected_export_error_does_not_echo_its_text(self, world, api, monkeypatch):
        from app.services.list_query_export_service import ListQueryExportService

        def boom(self, body):
            raise RuntimeError("SELECT secret FROM finance.billing_documents")

        monkeypatch.setattr(ListQueryExportService, "export_rows", boom)
        res = api(VIEW, EXPORT, raise_server_exceptions=False).post(self.URL, json=self.BODY)
        assert res.status_code == 500
        assert "SELECT" not in res.text.upper()

    def test_export_without_view_is_403(self, world, api):
        world.doc("IV-1")
        res = api(EXPORT, raise_server_exceptions=False).post(self.URL, json=self.BODY)
        assert res.status_code == 403, res.text

    def test_export_needs_the_finance_module(self, world, api, monkeypatch):
        from app.config import settings
        from app.modules.runtime import guards

        world.doc("IV-1")
        client = api(VIEW, EXPORT, raise_server_exceptions=False)
        monkeypatch.setattr(settings, "module_guard_strict", True, raising=False)
        monkeypatch.setattr(guards, "_roles_bypass_module", lambda db, uid: False)
        monkeypatch.setattr(guards, "_all_modules_effectively_enabled", lambda db, tid: False)
        monkeypatch.setattr(guards, "is_module_enabled", lambda db, tid, key: False)
        res = client.post(self.URL, json=self.BODY)
        assert res.status_code == 403, res.text
