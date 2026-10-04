"""Group A3 - sales orders and purchase orders on the ingest surface.

  AC-A3-1   a SO record creates the header + its lines, stamped with the anchor
  AC-A3-2   a re-push updates, deletes and creates lines by their own source_ref
  AC-A3-3   first sync adopts an unclaimed so_number; a claimed one is failed
  AC-A3-4   an unknown product_ref makes the WHOLE record retryable
  AC-A3-5   an absent customer_ref is NULL; an unknown one is retryable
  AC-A3-6   every canonical status maps to the documented Sorento value
  AC-A3-7   cancelled is an update - the rows stay
  AC-A3-8   dry_run writes nothing, for a create and for an update
  AC-A3-9   read-back answers in canonical names, with lines[]
  AC-A3-10  the same, against purchase_orders with supplier_ref
  AC-A3-11  the scm.*.edit slug is required to write, .view to read

A document is not a master, and three of its properties are the reason this has
its own service rather than a seventh EntitySpec:

* it OWNS its lines. The push is authoritative over the whole document, so a
  line the payload no longer carries is a line the customer cancelled, and it
  goes - including a ref-less line an earlier extract import created.
* it POINTS at masters. Five of its fields are integration references to rows
  the ESB pushed earlier, so a document arriving before its product is a
  sequencing artefact (retryable), never bad data.
* its status is a VOCABULARY, not a string: five canonical words map onto two
  different Sorento vocabularies, and an unmapped word is refused rather than
  stored, because `status` decides whether the demand is still open.

Substrate: the blank scratch schema, so `public.sales_orders` can be counted
without a production row in the way, and so the `projects` schema's IDENTICALLY
NAMED tables can be asserted untouched. Every code is minted under a `ZZTDOC`
marker.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

# MUST be the first app import - resolves the circular import in
# app.modules.runtime.guards.
from app.main import app  # noqa: E402

from app.api.v1.external import ingest as ingest_module
from app.api.v1.external.permissions import require_external_permission_for_path
from app.models.company import Company
from app.models.inventory import Warehouse
from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, Supplier
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.project_so import ProjectSalesOrder
from app.models.sales_agent import SalesAgent
from app.models.scm import LoadingPlan, LoadingPlanLine
from app.models.stock_transfer import (
    TRANSFER_KIND_POOL,
    TRANSFER_PROPOSED,
    StockTransfer,
)
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.integration_reference_service import IntegrationReferenceService

from ._pg_fixture import blank_session, unique_code

MARKER = "ZZTDOC"

INGEST_SO = "/api/v1/external/ingest/sales_orders"
INGEST_PO = "/api/v1/external/ingest/purchase_orders"
READ_SO = "/api/v1/external/read/sales_orders"
READ_PO = "/api/v1/external/read/purchase_orders"

_USER_ID = "5b8b9c10-1111-4222-8333-4444555566d1"
_ROLE_ID = "5b8b9c10-2222-4222-8333-4444555566d2"


def _ref(stem: str) -> str:
    return f"{MARKER}:{stem}:{uuid.uuid4().hex[:8]}"


def _seed_principal(db) -> None:
    from app.models.user import User, UserRole, UserRoleAssignment

    db.add(
        UserRole(
            id=_ROLE_ID,
            slug="superadmin",
            name=f"{MARKER} Superadmin",
            description="",
            is_protected=True,
            is_default=False,
        )
    )
    db.flush()
    db.add(
        User(
            id=_USER_ID,
            name=f"{MARKER} admin",
            email=f"{MARKER.lower()}-admin@test.com",
            password="x",
            status="active",
        )
    )
    db.flush()
    db.add(UserRoleAssignment(user_id=_USER_ID, role_id=_ROLE_ID))
    db.flush()


class _Env:
    """Two companies, and one linked master of every kind the documents point at.

    Every master is linked through ``IntegrationReferenceService`` at seed time,
    because that is the only thing a document payload can name: the ESB holds no
    Sorento ids, so `product_ref` resolves through the reference table or it does
    not resolve at all.
    """

    def __init__(self, client: TestClient, db):
        self.client = client
        self.db = db
        self.company_a = DEFAULT_COMPANY_ID
        # Anchored to A: every seed helper below defaults to company_a, and a
        # company-scoped `link`/`resolve` with no anchor now raises ValueError
        # (plan D14, strict). A helper seeding into B constructs its OWN
        # anchored instance instead of reusing this one - see `_link`.
        self.refs = IntegrationReferenceService(db, company_id=self.company_a)

        suffix = uuid.uuid4().hex[:8]
        other = Company(id=str(uuid.uuid4()), name=f"{MARKER} B {suffix}", code=f"ZD{suffix}")
        db.add(other)
        db.flush()
        self.company_b = str(other.id)
        self.company_b_code = other.code
        self.company_a_code = db.execute(
            text("SELECT code FROM companies WHERE id = :id"), {"id": self.company_a}
        ).scalar()

        self._category = ProductCategory(
            category_code=unique_code(MARKER), category_name=f"{MARKER} category"
        )
        self._uom = UnitOfMeasure(uom_code=unique_code(MARKER), uom_name=f"{MARKER} unit")
        db.add_all([self._category, self._uom])
        db.flush()

        self.product_ref = self.link_product(self.company_a)
        self.product2_ref = self.link_product(self.company_a)
        self.warehouse_ref = self.link_warehouse(self.company_a)
        self.customer_ref = self.link_customer(self.company_a)
        self.supplier_ref = self.link_supplier(self.company_a)
        self.agent_ref = self.link_agent()
        # Committed, not just flushed. A dry run makes the SERVICE call
        # `rollback()`, and under `create_savepoint` that unwinds to wherever the
        # session's transaction began - which would take these seeds with it and
        # make "the dry run wrote nothing" indistinguishable from "the fixture
        # lost its own data". The outer transaction still discards everything.
        db.commit()

    # ------------------------------------------------------------- seed helpers
    def _link(self, entity_type: str, entity_id: str, stem: str, *, company_id: str = None) -> str:
        """Link through the anchor the row actually belongs to.

        `self.refs` is anchored to company_a; a row seeded into company_b (or
        any other company) needs its OWN anchored instance - plan D14 is
        strict, so the constructor's anchor is what gets stored, not the
        entity's own company (see `_anchor_for_link`).
        """
        source_ref = _ref(stem)
        svc = (
            self.refs
            if company_id is None or company_id == self.company_a
            else IntegrationReferenceService(self.db, company_id=company_id)
        )
        svc.link(entity_type=entity_type, entity_id=str(entity_id), source_ref=source_ref)
        return source_ref

    def link_product(self, company_id: str) -> str:
        row = Product(
            product_code=unique_code(MARKER),
            product_name=f"{MARKER} product",
            category_id=self._category.id,
            base_uom_id=self._uom.id,
            list_price=10,
            company_id=company_id,
        )
        self.db.add(row)
        self.db.flush()
        return self._link("products", row.id, "ITEM", company_id=company_id)

    def link_warehouse(self, company_id: str) -> str:
        row = Warehouse(
            # Globally unique in the model (pre-305 drift), so no code is ever
            # reused across the two companies in this suite.
            warehouse_code=f"{MARKER}WH{uuid.uuid4().hex[:6]}",
            warehouse_name=f"{MARKER} depot",
            company_id=company_id,
        )
        self.db.add(row)
        self.db.flush()
        return self._link("warehouses", row.id, "LOC", company_id=company_id)

    def link_customer(self, company_id: str) -> str:
        row = Customer(
            customer_code=unique_code(MARKER),
            customer_name=f"{MARKER} customer",
            company_id=company_id,
        )
        self.db.add(row)
        self.db.flush()
        return self._link("customers", row.id, "DEBTOR", company_id=company_id)

    def link_supplier(self, company_id: str) -> str:
        row = Supplier(
            supplier_code=unique_code(MARKER),
            supplier_name=f"{MARKER} supplier",
            company_id=company_id,
        )
        self.db.add(row)
        self.db.flush()
        return self._link("suppliers", row.id, "CREDITOR", company_id=company_id)

    def loading_plan_line(self, po_line_id: str) -> str:
        """A `scm.loading_plan_line` pointing at a purchase-order LINE.

        `po_line_id` is ON DELETE **CASCADE** (`app/models/scm.py`), so a hard
        delete of the line does not merely orphan this row, it destroys it - and
        the plan a buyer sent a supplier loses the line it was built from.
        """
        supplier = Supplier(
            supplier_code=unique_code(MARKER),
            supplier_name=f"{MARKER} supplier",
            company_id=self.company_a,
        )
        self.db.add(supplier)
        self.db.flush()
        plan = LoadingPlan(supplier_id=supplier.id, company_id=self.company_a)
        self.db.add(plan)
        self.db.flush()
        row = LoadingPlanLine(
            plan_id=plan.id, po_line_id=po_line_id, company_id=self.company_a
        )
        self.db.add(row)
        self.db.flush()
        return str(row.id)

    def stock_transfer(self, so_line_id: str) -> str:
        """A `projects.stock_transfers` row pointing at a sales-order LINE.

        `so_line_id` is ON DELETE SET NULL, so a hard delete of the line SUCCEEDS
        and silently detaches a movement of stock that has already happened.
        """
        mirror = ProjectSalesOrder(
            provisional_ref=f"{MARKER}-{uuid.uuid4().hex[:8]}",
            company_id=self.company_a,
        )
        self.db.add(mirror)
        self.db.flush()
        row = StockTransfer(
            transfer_no=f"{MARKER}-TR-{uuid.uuid4().hex[:6]}",
            so_line_id=so_line_id,
            project_sales_order_id=mirror.id,
            product_id=self.refs.resolve(
                entity_type="products", source_ref=self.product_ref
            ),
            from_warehouse_id=self.refs.resolve(
                entity_type="warehouses", source_ref=self.warehouse_ref
            ),
            to_warehouse_id=self.refs.resolve(
                entity_type="warehouses", source_ref=self.link_warehouse(self.company_a)
            ),
            qty=1,
            kind=TRANSFER_KIND_POOL,
            state=TRANSFER_PROPOSED,
            company_id=self.company_a,
        )
        self.db.add(row)
        self.db.flush()
        return str(row.id)

    def link_agent(self) -> str:
        row = SalesAgent(sales_agent=f"{MARKER}-{uuid.uuid4().hex[:6].upper()}")
        self.db.add(row)
        self.db.flush()
        return self._link("sales_agents", row.id, "AGENT")

    # -------------------------------------------------------------- read helpers
    def post(self, url: str, records: list[dict], *, company_code=None, dry_run=False):
        return self.client.post(
            f"{url}?dry_run=true" if dry_run else url,
            json={"companyCode": company_code or self.company_a_code, "records": records},
        )

    def read(self, url: str, source_refs: list[str], *, company_code=None):
        return self.client.post(
            url,
            json={
                "companyCode": company_code or self.company_a_code,
                "source_refs": source_refs,
            },
        )

    def refs_for(self, company_id: str) -> IntegrationReferenceService:
        """A service anchored to ``company_id`` - `self.refs` is fixed to
        company_a, so a caller reading back a record pushed under company_b
        needs its own anchored instance (plan D14: resolve() on a scoped type
        only sees that anchor's own rows)."""
        if company_id == self.company_a:
            return self.refs
        return IntegrationReferenceService(self.db, company_id=company_id)

    def header(self, table: str, source_ref: str, *, company_id: str = None):
        """The header a ref points at, read WITHOUT the ORM scope filter.

        Which company the row landed in is one of the things under test, so the
        filter that hides the answer is not welcome here. ``company_id``
        defaults to company_a - pass the company the record was actually
        pushed under (plan D14: `resolve()` on a scoped type only resolves
        within its own anchor, so reading back a company_b record needs
        company_id=env.company_b).
        """
        refs = self.refs_for(company_id) if company_id is not None else self.refs
        entity_id = refs.resolve(entity_type=table, source_ref=source_ref)
        if entity_id is None:
            return None
        return (
            self.db.execute(
                text(f"SELECT * FROM {table} WHERE id = :id"), {"id": entity_id}
            )
            .mappings()
            .first()
        )

    def lines(self, table: str, fk: str, header_id: str):
        return (
            self.db.execute(
                text(f"SELECT * FROM {table} WHERE {fk} = :id ORDER BY created_at, id"),
                {"id": str(header_id)},
            )
            .mappings()
            .all()
        )

    def so_lines(self, header_id):
        return self.lines("sales_order_lines", "sales_order_id", header_id)

    def po_lines(self, header_id):
        return self.lines("purchase_order_lines", "purchase_order_id", header_id)

    def counts(self) -> dict[str, int]:
        """Header, line and reference counts, for the "nothing was written" cases.

        Scoped to this suite's marker on the reference table; the document tables
        are empty on the scratch schema, so a bare count is honest there.
        """
        return {
            "so": self.db.execute(
                select(func.count()).select_from(SalesOrder.__table__)
            ).scalar(),
            "so_lines": self.db.execute(
                select(func.count()).select_from(SalesOrderLine.__table__)
            ).scalar(),
            "po": self.db.execute(
                select(func.count()).select_from(PurchaseOrder.__table__)
            ).scalar(),
            "po_lines": self.db.execute(
                select(func.count()).select_from(PurchaseOrderLine.__table__)
            ).scalar(),
            "refs": self.db.execute(
                text(
                    "SELECT count(*) FROM integration_references WHERE source_ref LIKE :p"
                ),
                {"p": f"{MARKER}:%"},
            ).scalar(),
        }


@pytest.fixture
def env():
    from app.dependencies import (  # safe: app.main is already loaded
        get_current_user,
        get_current_user_or_api_key,
        get_db,
        get_external_api_user,
    )
    from app.models.base import set_company_scope
    from app.services.company_scope_resolver import apply_company_scope

    with blank_session() as db:
        _seed_principal(db)

        def _override_get_db():
            yield db

        def _override_user():
            return {"id": _USER_ID, "email": f"{MARKER.lower()}-admin@test.com"}

        def _override_company_scope():
            # The real n8n principal: an X-API-Key call with no contact identity
            # arrives scoped to ALL companies. The anchor is what narrows it.
            set_company_scope(db, None)
            return None

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = _override_user
        app.dependency_overrides[get_current_user_or_api_key] = _override_user
        app.dependency_overrides[get_external_api_user] = _override_user
        app.dependency_overrides[apply_company_scope] = _override_company_scope
        try:
            with TestClient(app) as client:
                yield _Env(client, db)
        finally:
            app.dependency_overrides.clear()


def _so_record(env, *, ref=None, number=None, lines=None, **extra) -> dict:
    record = {
        "source_ref": ref or _ref("SO"),
        "so_number": number or f"{MARKER}-SO-{uuid.uuid4().hex[:8]}",
        "status": "open",
        "lines": lines if lines is not None else [_so_line(env)],
    }
    record.update(extra)
    return record


def _code_of_ref(env, product_ref: str) -> str:
    from sqlalchemy import text as _text

    pid = env.refs.resolve(entity_type="products", source_ref=product_ref)
    return env.db.execute(
        _text("SELECT product_code FROM products WHERE id = :i"), {"i": str(pid)}
    ).scalar()


def _with_product_code(env, line: dict) -> dict:
    """Real AutoCount lines always send ItemCode next to the product ref, and
    products match by code only (owner rulings, 3 Oct): add the linked
    product's code unless the caller chose one (or sent none on purpose with
    `product_code=None`)."""
    if "product_code" in line:
        if line["product_code"] is None:
            del line["product_code"]
        return line
    ref = line.get("product_ref")
    if ref:
        from sqlalchemy import text as _text

        pid = env.refs.resolve(entity_type="products", source_ref=ref)
        if pid is not None:
            line["product_code"] = env.db.execute(
                _text("SELECT product_code FROM products WHERE id = :i"), {"i": str(pid)}
            ).scalar()
    return line


def _so_line(env, *, ref=None, product_ref=None, **extra) -> dict:
    line = {
        "source_ref": ref or _ref("SOL"),
        "product_ref": product_ref or env.product_ref,
        "qty_ordered": 10,
    }
    line.update(extra)
    return _with_product_code(env, line)


def _po_record(env, *, ref=None, number=None, lines=None, **extra) -> dict:
    record = {
        "source_ref": ref or _ref("PO"),
        "po_number": number or f"{MARKER}-PO-{uuid.uuid4().hex[:8]}",
        "status": "open",
        "lines": lines if lines is not None else [_po_line(env)],
    }
    record.update(extra)
    return record


def _po_line(env, *, ref=None, product_ref=None, **extra) -> dict:
    line = {
        "source_ref": ref or _ref("POL"),
        "product_ref": product_ref or env.product_ref,
        "qty_ordered": 4,
    }
    line.update(extra)
    return _with_product_code(env, line)


# ============================================================== create (AC-A3-1)
class TestCreate:
    def test_a_sales_order_creates_its_header_and_lines_under_the_anchor(self, env):
        """AC-A3-1. Every row carries the anchor company, header and lines alike.

        The lines are inserted by the service, not by the ORM cascade of a
        parent the caller stamped, so "the header is in the right company" says
        nothing about them - hence the assertion on each.
        """
        line_a = _so_line(env, warehouse_ref=env.warehouse_ref, unit_price="12.50", uom="PCS")
        line_b = _so_line(env, product_ref=env.product2_ref, qty_ordered=3, qty_delivered=3)
        record = _so_record(
            env,
            lines=[line_a, line_b],
            customer_ref=env.customer_ref,
            sales_agent_ref=env.agent_ref,
            doc_date="2026-08-30",
            requested_delivery_date="2026-09-15",
            internal_note="Site A",
        )

        res = env.post(INGEST_SO, [record])

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["summary"]["created"] == 1, body
        entry = body["records"][0]
        assert entry["outcome"] == "created"

        header = env.header("sales_orders", record["source_ref"])
        assert header is not None
        assert header["so_number"] == record["so_number"]
        assert str(header["company_id"]) == env.company_a
        assert header["source_system"] == "autocount"
        assert header["source_ref"] == record["source_ref"]
        assert header["source_doc_no"] == record["so_number"]
        assert header["status"] == "open"
        assert header["internal_note"] == "Site A"
        assert str(header["order_date"]) == "2026-08-30"
        assert str(header["requested_delivery_date"]) == "2026-09-15"
        assert header["customer_id"] is not None
        assert header["sales_agent_id"] is not None

        lines = env.so_lines(header["id"])
        assert len(lines) == 2
        assert {l["source_ref"] for l in lines} == {line_a["source_ref"], line_b["source_ref"]}
        for line in lines:
            assert str(line["company_id"]) == env.company_a
            assert line["source_system"] == "autocount"
        by_ref = {l["source_ref"]: l for l in lines}
        first = by_ref[line_a["source_ref"]]
        assert first["qty_ordered"] == 10
        assert first["unit_price"] == Decimal("12.50")
        assert first["uom"] == "PCS"
        assert first["warehouse_id"] is not None
        assert first["line_status"] == "open"
        # Fully delivered, so this one is closed on arrival.
        assert by_ref[line_b["source_ref"]]["line_status"] == "closed"

    def test_the_projects_schema_table_of_the_same_name_is_untouched(self, env):
        """AC-A3-1. `projects.sales_orders` exists and is a DIFFERENT table.

        Unqualified raw SQL resolves by search_path, and the projects module owns
        seven bare names that also exist in core. Writing a document into the
        module's copy would be invisible to every SCM screen and would corrupt
        project sales at the same time, so the count is pinned rather than
        assumed.
        """
        from app.models.project_so import ProjectSalesOrder

        before = env.db.execute(
            select(func.count()).select_from(ProjectSalesOrder.__table__)
        ).scalar()

        res = env.post(INGEST_SO, [_so_record(env)])
        assert res.json()["summary"]["created"] == 1, res.text

        after = env.db.execute(
            select(func.count()).select_from(ProjectSalesOrder.__table__)
        ).scalar()
        assert after == before

    def test_the_header_is_linked_so_the_next_push_is_an_update(self, env):
        record = _so_record(env)
        env.post(INGEST_SO, [record])

        again = env.post(INGEST_SO, [record])

        assert again.json()["records"][0]["outcome"] == "updated", again.text
        assert env.counts()["so"] == 1


# ========================================================== line upsert (AC-A3-2)
class TestLineUpsert:
    def test_a_re_push_updates_deletes_and_creates_lines_by_their_own_ref(self, env):
        """AC-A3-2. The push is authoritative over the whole document.

        Line 1 keeps its id on purpose: allocations, transfers and plan decisions
        point at a line id, so replacing every line wholesale on every sync -
        which is what the stale autocount branch did - would break those links
        weekly for lines nobody changed.
        """
        keep = _so_line(env, qty_ordered=10)
        drop = _so_line(env, product_ref=env.product2_ref, qty_ordered=5)
        record = _so_record(env, lines=[keep, drop])
        env.post(INGEST_SO, [record])

        header = env.header("sales_orders", record["source_ref"])
        original = {l["source_ref"]: str(l["id"]) for l in env.so_lines(header["id"])}

        added = _so_line(env, qty_ordered=7)
        second = dict(record, lines=[dict(keep, qty_ordered=12, qty_delivered=2), added])
        res = env.post(INGEST_SO, [second])

        assert res.json()["records"][0]["outcome"] == "updated", res.text
        lines = env.so_lines(header["id"])
        assert {l["source_ref"] for l in lines} == {keep["source_ref"], added["source_ref"]}
        by_ref = {l["source_ref"]: l for l in lines}
        assert str(by_ref[keep["source_ref"]]["id"]) == original[keep["source_ref"]]
        assert by_ref[keep["source_ref"]]["qty_ordered"] == 12
        assert by_ref[keep["source_ref"]]["qty_delivered"] == 2

    def test_a_ref_less_line_from_an_earlier_import_is_removed(self, env):
        """AC-A3-2. The extract importer wrote lines with no source_ref at all.

        Keeping them would double the demand on the first AutoCount sync: the
        same physical line would count once under its old ref-less row and once
        under the pushed one.
        """
        record = _so_record(env)
        env.post(INGEST_SO, [record])
        header = env.header("sales_orders", record["source_ref"])

        product_id = env.db.execute(
            text("SELECT product_id FROM sales_order_lines WHERE sales_order_id = :h LIMIT 1"),
            {"h": str(header["id"])},
        ).scalar()
        legacy_id = str(uuid.uuid4())
        env.db.execute(
            text(
                "INSERT INTO sales_order_lines "
                "(id, sales_order_id, product_id, qty_ordered, qty_delivered, "
                " line_status, company_id) "
                "VALUES (:id, :h, :p, 4, 0, 'open', :c)"
            ),
            {"id": legacy_id, "h": str(header["id"]), "p": product_id, "c": env.company_a},
        )
        env.db.flush()
        assert len(env.so_lines(header["id"])) == 2

        env.post(INGEST_SO, [record])

        lines = env.so_lines(header["id"])
        assert legacy_id not in {str(l["id"]) for l in lines}
        assert len(lines) == 1


    def test_a_line_a_loading_plan_points_at_is_cancelled_not_deleted(self, env):
        """AC-A3-2, the arm that used to lose data.

        `scm.loading_plan_line.po_line_id` is ON DELETE CASCADE, so deleting the
        purchase-order line takes the plan row with it - and the first sync of an
        ADOPTED document deletes EVERY pre-existing line, because none of them
        carries a source_ref yet. So a line something points at is cancelled in
        place: out of the demand, still there for whatever needs it.
        """
        keep = _po_line(env)
        drop = _po_line(env, product_ref=env.product2_ref, qty_ordered=5)
        record = _po_record(env, lines=[keep, drop])
        env.post(INGEST_PO, [record])
        header = env.header("purchase_orders", record["source_ref"])
        by_ref = {l["source_ref"]: l for l in env.po_lines(header["id"])}
        dropped_id = str(by_ref[drop["source_ref"]]["id"])
        plan_line_id = env.loading_plan_line(dropped_id)

        res = env.post(INGEST_PO, [dict(record, lines=[keep])])

        assert res.json()["records"][0]["outcome"] == "updated", res.text
        lines = {l["source_ref"]: l for l in env.po_lines(header["id"])}
        assert set(lines) == {keep["source_ref"], drop["source_ref"]}
        cancelled = lines[drop["source_ref"]]
        assert str(cancelled["id"]) == dropped_id
        assert cancelled["line_status"] == "cancelled"
        # Untouched otherwise: this row is now the evidence the plan was built
        # from, and restating its quantity would falsify that.
        assert cancelled["qty_ordered"] == 5
        # Through the ORM, so the read lands in the scratch schema the request
        # wrote into; `scm.`-qualified raw SQL would count rows in the REAL one.
        assert (
            env.db.query(func.count())
            .select_from(LoadingPlanLine)
            .filter(LoadingPlanLine.id == plan_line_id)
            .scalar()
            == 1
        )
        assert lines[keep["source_ref"]]["line_status"] == "open"

    def test_a_line_a_stock_transfer_points_at_is_cancelled_not_deleted(self, env):
        """The SET NULL half. `stock_transfers.so_line_id` does not refuse the
        delete - it lets it through and detaches a movement of stock that has
        already physically happened, which no error anywhere reports."""
        keep = _so_line(env)
        drop = _so_line(env, product_ref=env.product2_ref, qty_ordered=5)
        record = _so_record(env, lines=[keep, drop])
        env.post(INGEST_SO, [record])
        header = env.header("sales_orders", record["source_ref"])
        by_ref = {l["source_ref"]: l for l in env.so_lines(header["id"])}
        dropped_id = str(by_ref[drop["source_ref"]]["id"])
        transfer_id = env.stock_transfer(dropped_id)

        res = env.post(INGEST_SO, [dict(record, lines=[keep])])

        assert res.json()["records"][0]["outcome"] == "updated", res.text
        lines = {l["source_ref"]: l for l in env.so_lines(header["id"])}
        assert set(lines) == {keep["source_ref"], drop["source_ref"]}
        assert str(lines[drop["source_ref"]]["id"]) == dropped_id
        assert lines[drop["source_ref"]]["line_status"] == "cancelled"
        still_attached = (
            env.db.query(StockTransfer.so_line_id)
            .filter(StockTransfer.id == transfer_id)
            .scalar()
        )
        assert str(still_attached) == dropped_id


# ============================================================= adoption (AC-A3-3)
class TestAdoption:
    def test_an_unclaimed_so_number_in_the_same_company_is_adopted(self, env):
        """AC-A3-3. The extract importer created these rows first.

        Creating a second `SO-000123` instead would be a duplicate the unique
        index refuses outright, and where it did not, two rows of demand for one
        order.
        """
        number = f"{MARKER}-SO-{uuid.uuid4().hex[:8]}"
        existing = SalesOrder(
            id=str(uuid.uuid4()),
            so_number=number,
            status="open",
            company_id=env.company_a,
            source_system="import",
        )
        env.db.add(existing)
        env.db.flush()

        record = _so_record(env, number=number)
        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", res.text
        assert entry["entity_id"] == str(existing.id)
        header = env.header("sales_orders", record["source_ref"])
        assert str(header["id"]) == str(existing.id)
        # Adoption takes ownership: the row is AutoCount's from here on.
        assert header["source_system"] == "autocount"
        assert header["source_ref"] == record["source_ref"]
        assert env.counts()["so"] == 1

    def test_the_same_so_number_in_another_company_is_not_adopted(self, env):
        """AC-A1-6 for a document. Adoption is by NUMBER, and `so_number` is
        unique per company only (`uq_sales_orders_company_so_number`, migration
        305) - so two companies routinely hold `SO-000123`, and an unscoped
        adoption would hand company A's push to company B's order.

        The model carried the pre-305 GLOBAL `unique=True` until this fix, so a
        schema built from the models could not hold one number twice at all and
        this case was untestable rather than passing.
        """
        number = f"{MARKER}-SO-{uuid.uuid4().hex[:8]}"
        theirs = SalesOrder(
            id=str(uuid.uuid4()),
            so_number=number,
            status="open",
            company_id=env.company_b,
            source_system="import",
        )
        env.db.add(theirs)
        env.db.flush()

        record = _so_record(env, number=number)
        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert entry["entity_id"] != str(theirs.id)
        rows = (
            env.db.execute(
                text(
                    "SELECT id, company_id, source_system FROM sales_orders "
                    "WHERE so_number = :n"
                ),
                {"n": number},
            )
            .mappings()
            .all()
        )
        by_company = {str(row["company_id"]): row for row in rows}
        assert set(by_company) == {env.company_a, env.company_b}
        # B's row is untouched: still its own, still not AutoCount's.
        assert str(by_company[env.company_b]["id"]) == str(theirs.id)
        assert by_company[env.company_b]["source_system"] == "import"

    def test_the_same_po_number_in_another_company_is_not_adopted(self, env):
        """The purchase-order half, for the same reason and the same model fix
        (`uq_purchase_orders_company_po_number`)."""
        number = f"{MARKER}-PO-{uuid.uuid4().hex[:8]}"
        theirs = PurchaseOrder(
            id=str(uuid.uuid4()),
            po_number=number,
            status="active",
            company_id=env.company_b,
            source_system="import",
        )
        env.db.add(theirs)
        env.db.flush()

        record = _po_record(env, number=number)
        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert entry["entity_id"] != str(theirs.id)
        rows = (
            env.db.execute(
                text(
                    "SELECT id, company_id, source_system FROM purchase_orders "
                    "WHERE po_number = :n"
                ),
                {"n": number},
            )
            .mappings()
            .all()
        )
        by_company = {str(row["company_id"]): row for row in rows}
        assert set(by_company) == {env.company_a, env.company_b}
        assert str(by_company[env.company_b]["id"]) == str(theirs.id)
        assert by_company[env.company_b]["source_system"] == "import"

    def test_a_number_already_claimed_by_another_ref_is_failed(self, env):
        """AC-A3-3. Two AutoCount documents claiming one Sorento order is a
        conflict a human has to settle, not something to silently retarget."""
        first = _so_record(env)
        env.post(INGEST_SO, [first])

        clash = _so_record(env, number=first["so_number"])
        res = env.post(INGEST_SO, [clash])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", res.text
        assert "source_ref" in entry["errors"]
        assert env.counts()["so"] == 1

    def test_a_header_ref_linked_to_another_company_creates_a_new_row_here(self, env):
        """AC-19b (autocount-brands-ingest BL-056, D15) - was the document
        mirror of AC-A1-7 ('failed', 'outside this company anchor').

        BL-056 scopes `integration_references` by `company_id`, so a ref
        linked only under B is invisible to a `resolve()` scoped to A - the
        same answer as a ref that was never linked at all. The push under A
        therefore proceeds through the ordinary adopt-by-number/create path
        and lands a brand NEW header row in A; B's row and its own reference
        stay exactly as they were.
        """
        theirs = SalesOrder(
            id=str(uuid.uuid4()),
            so_number=f"{MARKER}-SO-{uuid.uuid4().hex[:8]}",
            status="open",
            company_id=env.company_b,
        )
        env.db.add(theirs)
        env.db.flush()
        source_ref = _ref("SO")
        # env.refs is anchored to company_a; theirs belongs to company_b, so
        # linking it through env.refs would store company_a as the anchor
        # (D14: the constructor's anchor wins, never the entity's own row).
        IntegrationReferenceService(env.db, company_id=env.company_b).link(
            entity_type="sales_orders", entity_id=str(theirs.id), source_ref=source_ref
        )

        res = env.post(INGEST_SO, [_so_record(env, ref=source_ref)])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text

        rows = (
            env.db.execute(
                text("SELECT id, company_id, so_number FROM sales_orders WHERE id != :b"),
                {"b": str(theirs.id)},
            )
            .mappings()
            .all()
        )
        # company_id comes back a uuid.UUID; compare with str() on both sides.
        new_rows = [r for r in rows if str(r["company_id"]) == env.company_a]
        assert len(new_rows) == 1, "exactly one new header must land in A"

        # B's row is untouched - same id, same status, no lines stolen.
        b_row = env.db.execute(
            text("SELECT company_id, status FROM sales_orders WHERE id = :i"),
            {"i": str(theirs.id)},
        ).mappings().first()
        assert str(b_row["company_id"]) == env.company_b
        assert env.so_lines(theirs.id) == []


# ============================================================ retryable (AC-A3-4)
class TestUnresolvedReferences:
    def test_an_unknown_product_ref_drops_the_line_not_the_whole_record(self, env):
        """AC-A3-4 superseded 2026-09-06 (ingest-parity-standardisation D9,
        AC-P2-3): a document with SOME resolvable lines is no longer refused
        outright over one unresolved product - the unresolved line is DROPPED
        and counted (`lines.dropped`) and the rest of the document lands, the
        same rule `outstanding_import_service._resolve` already applied to an
        unknown item code. See `tests/test_ingest_parity_s2_documents.py
        ::TestAcP23UnknownProductDroppedUnknownLocationKept` for the new
        contract test.
        """
        record = _so_record(
            env,
            lines=[_so_line(env), _so_line(env, product_ref="ITEM:NOT-SYNCED-YET")],
        )

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert entry.get("lines", {}).get("dropped") == 1, entry

    def test_an_unknown_warehouse_ref_lands_null_with_a_warning(self, env):
        """v2 deviation (D10, AC-V1-7b): superseded 2026-09-05. A warehouse is
        optional and an unlocated line is a supported state, so an unresolved
        SENT `warehouse_ref` is no longer retryable - the line lands with a
        NULL `warehouse_id` and a `warehouse_unresolved` warning instead. See
        `tests/test_ingest_documents_v2_resolution.py::TestWarehouseUnresolvedIsAWarningNotARetry`.
        """
        record = _so_record(
            env, lines=[_so_line(env, warehouse_ref="LOC:NOT-SYNCED-YET")]
        )

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert "warehouse_unresolved" in entry.get("warnings", [])
        header = env.header("sales_orders", record["source_ref"])
        assert env.so_lines(header["id"])[0]["warehouse_id"] is None

    def test_an_absent_customer_ref_leaves_the_fk_null(self, env):
        """AC-A3-5. An order whose debtor Sorento does not hold is still an order;
        the FK is simply empty."""
        record = _so_record(env)

        res = env.post(INGEST_SO, [record])

        assert res.json()["records"][0]["outcome"] == "created", res.text
        header = env.header("sales_orders", record["source_ref"])
        assert header["customer_id"] is None
        assert header["sales_agent_id"] is None

    def test_an_unknown_customer_ref_is_retryable(self, env):
        """AC-A3-5. Present-but-unknown is a sequencing artefact: the customer
        push has not drained yet. Silently NULLing it would lose the attribution
        with no signal that it happened."""
        before = env.counts()
        record = _so_record(env, customer_ref="DEBTOR:NOT-SYNCED-YET")

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "retryable", res.text
        assert "customer_ref" in entry["errors"]
        assert env.counts() == before

    def test_a_master_ref_pointing_into_another_company_is_retryable(self, env):
        """AC-19b (autocount-brands-ingest BL-056, D15) - was 'failed', 'outside
        this company anchor'.

        A ladder ref (customer_ref here) scoped to B is invisible to a
        `resolve()` scoped to A - not a usable row and not a cross-company
        write either, just a ref that resolves to nothing under this anchor.
        That is exactly the sequencing shape `customer_ref: DEBTOR:NOT-SYNCED-
        YET` already gets, so it is `retryable`, named in `errors`, and
        nothing is written.
        """
        foreign_customer = env.link_customer(env.company_b)
        before = env.counts()
        record = _so_record(env, customer_ref=foreign_customer)

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "retryable", res.text
        assert "customer_ref" in entry["errors"]
        assert env.counts() == before


# =============================================================== status (AC-A3-6)
class TestStatusVocabulary:
    @pytest.mark.parametrize(
        "canonical,stored,read_back",
        [
            ("open", "open", "open"),
            # D6a: partial is stored open (PLAN-autocount-document-ingest-v2) - the
            # map is no longer injective, so the round trip is NOT the same word:
            # a canonical `partial` reads back `open`, same as a canonical `open`.
            ("partial", "open", "open"),
            ("fulfilled", "fulfilled", "fulfilled"),
            ("closed", "closed", "closed"),
            ("cancelled", "cancelled", "cancelled"),
        ],
    )
    def test_every_canonical_sales_order_status_maps_and_reads_back(
        self, env, canonical, stored, read_back
    ):
        """AC-A3-6. Five canonical words, two Sorento vocabularies. The map is the
        contract the shared service codes against, and it round-trips."""
        record = _so_record(env, status=canonical)

        res = env.post(INGEST_SO, [record])
        assert res.json()["records"][0]["outcome"] == "created", res.text

        assert env.header("sales_orders", record["source_ref"])["status"] == stored
        back = env.read(READ_SO, [record["source_ref"]]).json()["records"][0]
        assert back["status"] == read_back

    @pytest.mark.parametrize(
        "canonical,stored",
        [
            ("open", "active"),
            ("partial", "partial"),
            ("fulfilled", "received"),
            ("closed", "closed"),
            ("cancelled", "cancelled"),
        ],
    )
    def test_every_canonical_purchase_order_status_maps_and_reads_back(
        self, env, canonical, stored
    ):
        record = _po_record(env, status=canonical)

        res = env.post(INGEST_PO, [record])
        assert res.json()["records"][0]["outcome"] == "created", res.text

        assert env.header("purchase_orders", record["source_ref"])["status"] == stored
        back = env.read(READ_PO, [record["source_ref"]]).json()["records"][0]
        assert back["status"] == canonical

    def test_an_unknown_status_is_failed_and_names_the_field(self, env):
        """AC-A3-6. Storing an unmapped word would leave a demand row nothing can
        classify - and `status` is what decides whether it is still demand."""
        before = env.counts()
        record = _so_record(env, status="shipped")

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", res.text
        assert "status" in entry["errors"]
        assert "shipped" in entry["errors"]["status"]
        assert env.counts() == before

    def test_the_status_is_matched_case_insensitively(self, env):
        record = _so_record(env, status="Partial")

        res = env.post(INGEST_SO, [record])

        assert res.json()["records"][0]["outcome"] == "created", res.text
        # D6a: partial is stored open (PLAN-autocount-document-ingest-v2)
        assert (
            env.header("sales_orders", record["source_ref"])["status"]
            == "open"
        )

    def test_cancelled_on_a_re_push_keeps_the_rows(self, env):
        """AC-A3-7. Cancellation is an UPDATE.

        Deleting instead would erase the history of an order that existed, and
        the deletion endpoint (A4) is where removal is asked for explicitly.
        """
        record = _so_record(env, lines=[_so_line(env), _so_line(env, product_ref=env.product2_ref)])
        env.post(INGEST_SO, [record])
        header = env.header("sales_orders", record["source_ref"])
        line_ids = {str(l["id"]) for l in env.so_lines(header["id"])}

        res = env.post(INGEST_SO, [dict(record, status="cancelled")])

        assert res.json()["records"][0]["outcome"] == "updated", res.text
        assert env.header("sales_orders", record["source_ref"])["status"] == "cancelled"
        lines = env.so_lines(header["id"])
        assert {str(l["id"]) for l in lines} == line_ids
        assert {l["line_status"] for l in lines} == {"cancelled"}


# ============================================================== dry run (AC-A3-8)
class TestDryRun:
    def test_a_dry_run_create_writes_nothing(self, env):
        before = env.counts()
        record = _so_record(env)

        res = env.post(INGEST_SO, [record], dry_run=True)

        body = res.json()
        assert body["dry_run"] is True
        assert body["records"][0]["outcome"] == "created", res.text
        assert env.counts() == before

    def test_a_dry_run_update_writes_nothing_and_reports_the_diff(self, env):
        """AC-A3-8. The diff is the reason the preview exists: an adoption
        overwrites a row somebody typed in by hand."""
        record = _so_record(env, internal_note="first")
        env.post(INGEST_SO, [record])
        header = env.header("sales_orders", record["source_ref"])
        before = env.counts()

        res = env.post(
            INGEST_SO, [dict(record, internal_note="second", status="partial")], dry_run=True
        )

        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", res.text
        assert entry["diff"]["internal_note"] == {"current": "first", "incoming": "second"}
        # D6a: partial is stored open (PLAN-autocount-document-ingest-v2) - the
        # existing row is already open, so restating it as canonical `partial`
        # writes the SAME stored value and is correctly absent from the diff,
        # the same as pushing `open` onto an already-open row would be.
        assert "status" not in entry["diff"], entry["diff"]
        after = env.header("sales_orders", record["source_ref"])
        assert after["internal_note"] == "first"
        assert after["status"] == "open"
        assert str(after["id"]) == str(header["id"])
        assert env.counts() == before

    def test_a_dry_run_does_not_touch_the_lines(self, env):
        record = _so_record(env)
        env.post(INGEST_SO, [record])
        header = env.header("sales_orders", record["source_ref"])
        line_ids = {str(l["id"]) for l in env.so_lines(header["id"])}

        env.post(INGEST_SO, [dict(record, lines=[_so_line(env, qty_ordered=99)])], dry_run=True)

        lines = env.so_lines(header["id"])
        assert {str(l["id"]) for l in lines} == line_ids
        assert lines[0]["qty_ordered"] == 10


# ============================================================= read-back (AC-A3-9)
class TestReadBack:
    def test_the_sales_order_reads_back_in_canonical_names_with_its_lines(self, env):
        """AC-A3-9. The ESB renders a diff between what it holds and this, so the
        answer is in ITS vocabulary - refs, not Sorento ids, and `doc_date`, not
        `order_date`."""
        line = _so_line(
            env,
            warehouse_ref=env.warehouse_ref,
            qty_delivered=4,
            unit_price="12.50",
            discount="0",
            line_total="125",
            uom="PCS",
            required_date="2026-09-15",
        )
        record = _so_record(
            env,
            lines=[line],
            customer_ref=env.customer_ref,
            sales_agent_ref=env.agent_ref,
            doc_date="2026-08-30",
            requested_delivery_date="2026-09-15",
            internal_note="Site A",
            status="partial",
        )
        env.post(INGEST_SO, [record])

        res = env.read(READ_SO, [record["source_ref"]])

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["not_found"] == []
        got = body["records"][0]
        assert got["source_ref"] == record["source_ref"]
        assert got["entity_id"]
        assert got["so_number"] == record["so_number"]
        assert got["customer_ref"] == env.customer_ref
        assert got["sales_agent_ref"] == env.agent_ref
        assert got["doc_date"] == "2026-08-30"
        assert got["requested_delivery_date"] == "2026-09-15"
        # D6a: partial is stored open (PLAN-autocount-document-ingest-v2)
        assert got["status"] == "open"
        assert got["internal_note"] == "Site A"

        assert len(got["lines"]) == 1
        got_line = got["lines"][0]
        assert got_line["source_ref"] == line["source_ref"]
        assert got_line["entity_id"]
        assert got_line["product_ref"] == env.product_ref
        assert got_line["warehouse_ref"] == env.warehouse_ref
        assert got_line["qty_ordered"] == 10
        assert got_line["qty_delivered"] == 4
        assert got_line["unit_price"] == 12.5
        assert got_line["line_total"] == 125
        assert got_line["uom"] == "PCS"
        assert got_line["required_date"] == "2026-09-15"

    def test_an_unlinked_master_reads_back_as_a_null_ref(self, env):
        """A locally created customer has no integration reference, and inventing
        one would send the ESB a ref it cannot resolve."""
        record = _so_record(env)
        env.post(INGEST_SO, [record])

        got = env.read(READ_SO, [record["source_ref"]]).json()["records"][0]

        assert got["customer_ref"] is None
        assert got["sales_agent_ref"] is None

    def test_an_unknown_ref_is_reported_not_found(self, env):
        res = env.read(READ_SO, [_ref("SO")])

        body = res.json()
        assert body["records"] == []
        assert len(body["not_found"]) == 1

    def test_a_ref_in_another_company_reads_as_not_found(self, env):
        """AC-A1-8 for documents: another company's row must read exactly like a
        row that is not there, so the caller acts on one answer rather than two."""
        record = _so_record(env)
        env.post(INGEST_SO, [record])

        res = env.read(READ_SO, [record["source_ref"]], company_code=env.company_b_code)

        body = res.json()
        assert body["records"] == []
        assert body["not_found"] == [record["source_ref"]]

    def test_the_purchase_order_reads_back_with_supplier_and_currency(self, env):
        line = _po_line(
            env,
            warehouse_ref=env.warehouse_ref,
            qty_received=1,
            unit_cost="9.99",
            uom="CTN",
            currency="MYR",
            expected_date="2026-10-01",
        )
        record = _po_record(
            env,
            lines=[line],
            supplier_ref=env.supplier_ref,
            issue_date="2026-08-30",
            expected_date="2026-10-01",
            currency="MYR",
        )
        env.post(INGEST_PO, [record])

        got = env.read(READ_PO, [record["source_ref"]]).json()["records"][0]

        assert got["po_number"] == record["po_number"]
        assert got["supplier_ref"] == env.supplier_ref
        assert got["issue_date"] == "2026-08-30"
        assert got["expected_date"] == "2026-10-01"
        assert got["currency"] == "MYR"
        assert got["status"] == "open"
        got_line = got["lines"][0]
        assert got_line["product_ref"] == env.product_ref
        assert got_line["warehouse_ref"] == env.warehouse_ref
        assert got_line["qty_ordered"] == 4
        assert got_line["qty_received"] == 1
        assert got_line["unit_cost"] == 9.99
        assert got_line["currency"] == "MYR"
        assert got_line["expected_date"] == "2026-10-01"


# ====================================================== purchase orders (AC-A3-10)
class TestPurchaseOrders:
    def test_a_purchase_order_creates_its_header_and_lines_under_the_anchor(self, env):
        line = _po_line(env, warehouse_ref=env.warehouse_ref, qty_received=4, unit_cost="9.99")
        record = _po_record(
            env, lines=[line], supplier_ref=env.supplier_ref, currency="MYR", status="open"
        )

        res = env.post(INGEST_PO, [record])

        assert res.json()["summary"]["created"] == 1, res.text
        header = env.header("purchase_orders", record["source_ref"])
        assert header["po_number"] == record["po_number"]
        assert str(header["company_id"]) == env.company_a
        assert header["status"] == "active"
        assert header["currency"] == "MYR"
        assert header["supplier_id"] is not None
        assert header["source_system"] == "autocount"

        lines = env.po_lines(header["id"])
        assert len(lines) == 1
        assert str(lines[0]["company_id"]) == env.company_a
        assert lines[0]["qty_ordered"] == 4
        assert lines[0]["qty_received"] == 4
        assert lines[0]["unit_cost"] == Decimal("9.99")
        # Received in full, so the line is closed.
        assert lines[0]["line_status"] == "closed"

    def test_a_re_push_upserts_purchase_order_lines(self, env):
        keep = _po_line(env, qty_ordered=4)
        drop = _po_line(env, product_ref=env.product2_ref, qty_ordered=2)
        record = _po_record(env, lines=[keep, drop], supplier_ref=env.supplier_ref)
        env.post(INGEST_PO, [record])
        header = env.header("purchase_orders", record["source_ref"])
        original = {l["source_ref"]: str(l["id"]) for l in env.po_lines(header["id"])}

        added = _po_line(env, qty_ordered=6)
        res = env.post(
            INGEST_PO, [dict(record, lines=[dict(keep, qty_ordered=8), added])]
        )

        assert res.json()["records"][0]["outcome"] == "updated", res.text
        lines = env.po_lines(header["id"])
        assert {l["source_ref"] for l in lines} == {keep["source_ref"], added["source_ref"]}
        by_ref = {l["source_ref"]: l for l in lines}
        assert str(by_ref[keep["source_ref"]]["id"]) == original[keep["source_ref"]]
        assert by_ref[keep["source_ref"]]["qty_ordered"] == 8

    def test_an_unknown_supplier_ref_is_retryable_and_writes_nothing(self, env):
        before = env.counts()
        record = _po_record(env, supplier_ref="CREDITOR:NOT-SYNCED-YET")

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "retryable", res.text
        assert "supplier_ref" in entry["errors"]
        assert env.counts() == before

    def test_an_unclaimed_po_number_is_adopted(self, env):
        number = f"{MARKER}-PO-{uuid.uuid4().hex[:8]}"
        existing = PurchaseOrder(
            id=str(uuid.uuid4()), po_number=number, status="draft", company_id=env.company_a
        )
        env.db.add(existing)
        env.db.flush()

        res = env.post(INGEST_PO, [_po_record(env, number=number)])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", res.text
        assert entry["entity_id"] == str(existing.id)
        assert env.counts()["po"] == 1


# ============================================================== batch behaviour
class TestBatch:
    def test_one_bad_record_does_not_take_out_the_batch(self, env):
        """The per-record SAVEPOINT, on the document path. A failed flush leaves
        the session unusable, so without it one bad order loses every order after
        it in the file."""
        good = _so_record(env)
        bad = _so_record(env, status="nonsense")
        also_good = _so_record(env)

        res = env.post(INGEST_SO, [good, bad, also_good])

        body = res.json()
        assert [r["outcome"] for r in body["records"]] == ["created", "failed", "created"]
        assert body["summary"]["created"] == 2
        assert env.counts()["so"] == 2

    def test_duplicate_line_refs_inside_one_record_are_a_validation_failure(self, env):
        """Two lines with one DtlKey cannot both be upserted onto it, and picking
        one silently would drop a quantity the customer ordered."""
        line = _so_line(env)
        record = _so_record(env, lines=[line, dict(line)])

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", res.text
        assert entry["errors"]

    def test_an_unknown_document_entity_is_still_a_404(self, env):
        # `delivery_orders` was this test's unknown entity until contract 2.7 (#1354 S2)
        # made it a real one; a document type the surface still does not serve stands in.
        res = env.client.post(
            "/api/v1/external/ingest/stock_adjustments",
            json={"companyCode": env.company_a_code, "records": []},
        )
        assert res.status_code == 404, res.text


# ================================================================ guard (AC-A3-11)
class TestPermissionGuard:
    """The real RBAC guard, on an empty schema of its own.

    A stub handler behind the real dependency: what is under test is the gate,
    and running the ingest here would need the whole document schema for no gain.
    """

    @pytest.fixture()
    def guard_db(self):
        from app.models.integration import Integration, IntegrationApiKey
        from app.models.user import (
            User,
            UserPermission,
            UserRole,
            UserRoleAssignment,
            UserRolePermission,
        )
        from tests._pg_fixture import pg_empty_schema

        with pg_empty_schema(
            [
                User.__table__,
                UserRole.__table__,
                UserRoleAssignment.__table__,
                UserPermission.__table__,
                UserRolePermission.__table__,
                Integration.__table__,
                IntegrationApiKey.__table__,
            ]
        ) as session:
            yield session

    @pytest.fixture()
    def guard_client(self, guard_db):
        from app.dependencies import get_db as app_get_db

        api = FastAPI()

        @api.post("/ingest/{entity}")
        def _ingest_stub(
            entity: str,
            _: dict = Depends(
                require_external_permission_for_path(ingest_module.INGEST_PERMISSIONS)
            ),
        ):
            return {"ok": entity}

        @api.post("/read/{entity}")
        def _read_stub(
            entity: str,
            _: dict = Depends(
                require_external_permission_for_path(ingest_module.READ_PERMISSIONS)
            ),
        ):
            return {"ok": entity}

        def _override_db():
            yield guard_db

        api.dependency_overrides[app_get_db] = _override_db
        return TestClient(api, raise_server_exceptions=False)

    @pytest.fixture()
    def keys(self, guard_db):
        from app.models.integration import Integration
        from app.models.user import (
            User,
            UserPermission,
            UserRole,
            UserRoleAssignment,
            UserRolePermission,
        )
        from app.services.integration_key_service import IntegrationKeyService

        slugs = {
            "edit": ingest_module.INGEST_PERMISSIONS["sales_orders"],
            "view": ingest_module.READ_PERMISSIONS["sales_orders"],
            "po_edit": ingest_module.INGEST_PERMISSIONS["purchase_orders"],
        }
        perms = {}
        for slug in slugs.values():
            perm = UserPermission(slug=slug, name=slug)
            guard_db.add(perm)
            guard_db.flush()
            perms[slug] = perm

        issued = {}
        for label, held in (
            ("editor", [slugs["edit"], slugs["po_edit"]]),
            ("viewer", [slugs["view"]]),
        ):
            user = User(
                email=f"{MARKER.lower()}-{label}@integrations.local",
                name=f"Integration: {label}",
                status="ACTIVE",
                is_integration=True,
            )
            guard_db.add(user)
            guard_db.flush()
            role = UserRole(slug=f"{MARKER.lower()}_{label}", name=f"{MARKER} {label}")
            guard_db.add(role)
            guard_db.flush()
            guard_db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
            for slug in held:
                guard_db.add(UserRolePermission(role_id=role.id, permission_id=perms[slug].id))
            guard_db.flush()
            integration = Integration(
                name=f"{MARKER}-{label}",
                type="autocount_esb",
                act_as_user_id=user.id,
                is_active=True,
            )
            guard_db.add(integration)
            guard_db.flush()
            issued[label] = IntegrationKeyService(guard_db).issue_key(integration)
        return issued

    def test_no_key_is_401(self, guard_client, keys):
        assert guard_client.post("/ingest/sales_orders").status_code == 401

    def test_a_key_without_the_edit_slug_is_403_naming_it(self, guard_client, keys):
        res = guard_client.post("/ingest/sales_orders", headers={"X-API-Key": keys["viewer"]})
        assert res.status_code == 403
        assert "scm.sales_orders.edit" in res.text

    def test_the_edit_slug_passes_for_both_documents(self, guard_client, keys):
        for entity in ("sales_orders", "purchase_orders"):
            res = guard_client.post(
                f"/ingest/{entity}", headers={"X-API-Key": keys["editor"]}
            )
            assert res.status_code == 200, res.text

    def test_reading_takes_the_view_slug_not_the_edit_one(self, guard_client, keys):
        res = guard_client.post("/read/sales_orders", headers={"X-API-Key": keys["viewer"]})
        assert res.status_code == 200, res.text

    def test_purchase_order_ingest_needs_its_own_slug(self, guard_client, keys):
        res = guard_client.post(
            "/ingest/purchase_orders", headers={"X-API-Key": keys["viewer"]}
        )
        assert res.status_code == 403
        assert "scm.purchase_orders.edit" in res.text
