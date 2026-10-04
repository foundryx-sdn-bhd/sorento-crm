"""Group A4 - deletions on the ingest surface.

  AC-A4-1  the envelope: per-ref verdicts + a summary, 413 over the cap, 422 with
           no source_refs array, and the ingest route it shares a prefix with
           still ingests
  AC-A4-2  a ref that does not resolve, or resolves into another company, is
           `not_found` and nothing is touched
  AC-A4-3  a master with no dependents is `deleted` - row gone, reference gone
  AC-A4-4  a customer an order points at is `deactivated`, and the order keeps
           its customer_id
  AC-A4-5  a product a line points at is `deactivated` by is_discontinued, with
           is_active left alone
  AC-A4-6  a document whose LINE is referenced is `deactivated` (cancelled, lines
           cancelled); one with no external referrer is `deleted` with its lines
  AC-A4-7  dry_run reports the same verdicts and writes nothing
  AC-A4-8  the entity's `.delete` slug is required on top of the ingest guard
  AC-A4-9  one record that errors does not cost the rest of the batch its verdict

The dependent probe is the reason this is not simply a DELETE. Half the foreign
keys pointing at these tables are `ON DELETE SET NULL`, so a bare delete of a
customer would "succeed" by orphaning every sales order that names them - the
orders survive with a NULL customer and nothing says who they were for. So the
catalogue is asked who points here first, and anything pointing means the row is
taken out of use instead of removed.

Substrate: the blank scratch schema, so a row can be counted as gone rather than
merely absent from a filtered read, and so `projects.sales_orders` (an identically
named table) is present for the probe to find. Every code is minted under a
`ZZTDEL` marker.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

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
from app.models.scm import OrderLinkClaim
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.integration_reference_service import IntegrationReferenceService

from ._pg_fixture import blank_session, unique_code

MARKER = "ZZTDEL"

_USER_ID = "6c9cad20-1111-4222-8333-4444555566e1"
_ROLE_ID = "6c9cad20-2222-4222-8333-4444555566e2"


def _url(entity: str) -> str:
    return f"/api/v1/external/ingest/{entity}/deletions"


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
    """Two companies and a seed helper per shape the deletion has to tell apart.

    Every seeded row is LINKED through ``IntegrationReferenceService``: a source
    ref is the only thing a deletion payload can name, so an unlinked row is
    unreachable by this endpoint by construction.

    Each helper commits. A dry run makes the SERVICE call ``rollback()``, which
    under ``create_savepoint`` unwinds to wherever the session's transaction
    began - and that would take the fixture's own seeds with it, making "the dry
    run wrote nothing" indistinguishable from "the fixture lost its data". The
    outer transaction still discards everything at teardown.
    """

    def __init__(self, client: TestClient, db):
        self.client = client
        self.db = db
        self.company_a = DEFAULT_COMPANY_ID
        # Anchored to A; a helper seeding into B builds its OWN anchored
        # instance (see `_link`) - plan D14 is strict, the constructor's own
        # anchor is what gets stored, never inferred from the entity's row.
        self.refs = IntegrationReferenceService(db, company_id=self.company_a)

        suffix = uuid.uuid4().hex[:8]
        other = Company(id=str(uuid.uuid4()), name=f"{MARKER} B {suffix}", code=f"ZL{suffix}")
        db.add(other)
        db.flush()
        self.company_b = str(other.id)
        self.company_a_code = db.execute(
            text("SELECT code FROM companies WHERE id = :id"), {"id": self.company_a}
        ).scalar()

        self._category = ProductCategory(
            category_code=unique_code(MARKER), category_name=f"{MARKER} category"
        )
        self._uom = UnitOfMeasure(uom_code=unique_code(MARKER), uom_name=f"{MARKER} unit")
        db.add_all([self._category, self._uom])
        db.flush()
        db.commit()

    # ------------------------------------------------------------- seed helpers
    def _link(self, entity_type: str, entity_id: str, stem: str, *, company_id: str = None) -> str:
        source_ref = _ref(stem)
        svc = (
            self.refs
            if company_id is None or company_id == self.company_a
            else IntegrationReferenceService(self.db, company_id=company_id)
        )
        svc.link(entity_type=entity_type, entity_id=str(entity_id), source_ref=source_ref)
        self.db.commit()
        return source_ref

    def warehouse(self, company_id: str = None) -> tuple[str, str]:
        anchor = company_id or self.company_a
        row = Warehouse(
            # Globally unique in the model (pre-305 drift), so no code is reused
            # across the two companies in this suite.
            warehouse_code=f"{MARKER}WH{uuid.uuid4().hex[:6]}",
            warehouse_name=f"{MARKER} depot",
            company_id=anchor,
        )
        self.db.add(row)
        self.db.flush()
        return self._link("warehouses", row.id, "LOC", company_id=anchor), str(row.id)

    def customer(self, company_id: str = None) -> tuple[str, str]:
        anchor = company_id or self.company_a
        row = Customer(
            customer_code=unique_code(MARKER),
            customer_name=f"{MARKER} customer",
            company_id=anchor,
        )
        self.db.add(row)
        self.db.flush()
        return self._link("customers", row.id, "DEBTOR", company_id=anchor), str(row.id)

    def product(self, company_id: str = None) -> tuple[str, str]:
        anchor = company_id or self.company_a
        row = Product(
            product_code=unique_code(MARKER),
            product_name=f"{MARKER} product",
            category_id=self._category.id,
            base_uom_id=self._uom.id,
            list_price=10,
            company_id=anchor,
        )
        self.db.add(row)
        self.db.flush()
        return self._link("products", row.id, "ITEM", company_id=anchor), str(row.id)

    def supplier(self) -> str:
        row = Supplier(
            supplier_code=unique_code(MARKER),
            supplier_name=f"{MARKER} supplier",
            company_id=self.company_a,
        )
        self.db.add(row)
        self.db.flush()
        return str(row.id)

    def sales_order(self, *, customer_id: str = None, product_id: str = None) -> tuple[str, str, str]:
        """A linked sales order with one line. Returns (source_ref, id, line id)."""
        if product_id is None:
            _, product_id = self.product()
        header = SalesOrder(
            so_number=f"{MARKER}-SO-{uuid.uuid4().hex[:8]}",
            customer_id=customer_id,
            status="open",
            company_id=self.company_a,
        )
        self.db.add(header)
        self.db.flush()
        line = SalesOrderLine(
            sales_order_id=header.id,
            product_id=product_id,
            qty_ordered=5,
            qty_delivered=0,
            line_status="open",
            company_id=self.company_a,
        )
        self.db.add(line)
        self.db.flush()
        return self._link("sales_orders", header.id, "SO"), str(header.id), str(line.id)

    def purchase_order(self, *, product_id: str = None) -> tuple[str, str, str]:
        """A linked purchase order with one line. Returns (source_ref, id, line id)."""
        if product_id is None:
            _, product_id = self.product()
        header = PurchaseOrder(
            po_number=f"{MARKER}-PO-{uuid.uuid4().hex[:8]}",
            supplier_id=self.supplier(),
            status="active",
            company_id=self.company_a,
        )
        self.db.add(header)
        self.db.flush()
        line = PurchaseOrderLine(
            purchase_order_id=header.id,
            product_id=product_id,
            qty_ordered=7,
            qty_received=0,
            line_status="open",
            company_id=self.company_a,
        )
        self.db.add(line)
        self.db.flush()
        return self._link("purchase_orders", header.id, "PO"), str(header.id), str(line.id)

    def claim(self, *, so_line_id: str = None, po_line_id: str = None) -> None:
        """A row in `scm.order_link_claim` pointing at a document LINE.

        The cheapest referrer of either line table: two text numbers and a
        source. It is also a realistic one - the claim outlives the document it
        names, which is exactly why the line must not be hard-deleted out from
        under it.
        """
        self.db.add(
            OrderLinkClaim(
                so_number=f"{MARKER}-{uuid.uuid4().hex[:6]}",
                po_number=f"{MARKER}-{uuid.uuid4().hex[:6]}",
                source="manual",
                so_line_id=so_line_id,
                po_line_id=po_line_id,
                company_id=self.company_a,
            )
        )
        self.db.flush()
        self.db.commit()

    # -------------------------------------------------------------- call helper
    def delete(
        self,
        entity: str,
        source_refs: list[str],
        *,
        company_code=None,
        dry_run: bool = False,
        body: dict = None,
    ):
        payload = body if body is not None else {"source_refs": source_refs}
        if company_code is not False:
            payload = {"companyCode": company_code or self.company_a_code, **payload}
        return self.client.post(
            f"{_url(entity)}?dry_run=true" if dry_run else _url(entity), json=payload
        )

    # -------------------------------------------------------------- read helpers
    def row(self, table: str, entity_id: str):
        """A row read WITHOUT the ORM scope filter - which company it is in is
        one of the things under test."""
        return (
            self.db.execute(
                text(f"SELECT * FROM {table} WHERE id = :id"), {"id": str(entity_id)}
            )
            .mappings()
            .first()
        )

    def line_count(self, table: str, fk: str, header_id: str) -> int:
        return self.db.execute(
            text(f"SELECT count(*) FROM {table} WHERE {fk} = :id"), {"id": str(header_id)}
        ).scalar()

    def ref_count(self, source_ref: str) -> int:
        return self.db.execute(
            text("SELECT count(*) FROM integration_references WHERE source_ref = :r"),
            {"r": source_ref},
        ).scalar()

    def marker_ref_count(self) -> int:
        return self.db.execute(
            text("SELECT count(*) FROM integration_references WHERE source_ref LIKE :p"),
            {"p": f"{MARKER}:%"},
        ).scalar()


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
            # The real integration principal: an X-API-Key call arrives scoped to
            # ALL companies, and the anchor is what narrows it.
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


def _by_ref(body: dict) -> dict:
    return {r["source_ref"]: r for r in body["records"]}


# ============================================================ envelope (AC-A4-1)
class TestEnvelope:
    def test_a_batch_returns_a_verdict_per_ref_and_a_summary(self, env):
        """AC-A4-1. Three shapes in one call: deletable, dependent, unknown."""
        free_ref, free_id = env.warehouse()
        held_ref, held_id = env.customer()
        env.sales_order(customer_id=held_id)
        unknown = _ref("GONE")

        # Two entities cannot share one call (the entity is in the path), so the
        # summary is asserted per call and the mixed shapes are covered by the
        # verdict tests below.
        res = env.delete("warehouses", [free_ref, unknown])

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["dry_run"] is False
        assert body["summary"] == {
            "total": 2,
            "deleted": 1,
            "deactivated": 0,
            "not_found": 1,
            "failed": 0,
        }
        verdicts = _by_ref(body)
        assert verdicts[free_ref]["outcome"] == "deleted"
        assert verdicts[free_ref]["entity_id"] == free_id
        assert verdicts[unknown]["outcome"] == "not_found"
        assert verdicts[unknown]["entity_id"] is None

    def test_a_batch_above_the_cap_is_refused(self, env):
        res = env.delete("warehouses", [f"{MARKER}:X:{i}" for i in range(1001)])
        assert res.status_code == 413
        assert res.json()["code"] == "BATCH_TOO_LARGE"

    def test_a_body_without_a_source_refs_array_is_422(self, env):
        res = env.delete("warehouses", [], body={})
        assert res.status_code == 422
        assert res.json()["code"] == "INVALID_BODY"

    def test_a_source_refs_that_is_not_a_list_is_422(self, env):
        res = env.delete("warehouses", [], body={"source_refs": "LOC:1"})
        assert res.status_code == 422
        assert res.json()["code"] == "INVALID_BODY"

    def test_no_anchor_is_422(self, env):
        ref, entity_id = env.warehouse()
        res = env.client.post(_url("warehouses"), json={"source_refs": [ref]})
        assert res.status_code == 422
        assert res.json()["code"] == "COMPANY_ANCHOR_REQUIRED"
        assert env.row("warehouses", entity_id) is not None

    def test_an_unknown_entity_is_404(self, env):
        res = env.delete("unicorns", [_ref("X")])
        assert res.status_code == 404

    def test_the_ingest_route_still_ingests_alongside_the_deletions_route(self, env):
        """AC-A4-1. `/{entity}` takes ONE path segment, so `/warehouses/deletions`
        cannot be swallowed by it and cannot swallow it."""
        code = f"{MARKER}WH{uuid.uuid4().hex[:6]}"
        res = env.client.post(
            "/api/v1/external/ingest/warehouses",
            json={
                "companyCode": env.company_a_code,
                "records": [
                    {"source_ref": _ref("LOC"), "code": code, "name": f"{MARKER} new depot"}
                ],
            },
        )
        assert res.status_code == 200, res.text
        assert res.json()["summary"]["created"] == 1, res.text
        assert (
            env.db.execute(
                text("SELECT count(*) FROM warehouses WHERE warehouse_code = :c"), {"c": code}
            ).scalar()
            == 1
        )


# =========================================================== not found (AC-A4-2)
class TestNotFound:
    def test_an_unknown_ref_is_not_found(self, env):
        res = env.delete("warehouses", [_ref("NOPE")])
        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "not_found"
        assert res.json()["summary"]["not_found"] == 1

    def test_a_ref_in_another_company_is_not_found_and_its_row_stands(self, env):
        """AC-A4-2. `integration_references` is global, so B's ref resolves under
        A's anchor. Deleting there would be a cross-company write wearing the
        clothes of a routine tidy-up."""
        ref, entity_id = env.warehouse(env.company_b)

        res = env.delete("warehouses", [ref])

        assert res.json()["records"][0]["outcome"] == "not_found"
        row = env.row("warehouses", entity_id)
        assert row is not None
        assert row["is_active"] is True
        assert env.ref_count(ref) == 1


# ============================================================== delete (AC-A4-3)
class TestHardDelete:
    def test_a_warehouse_with_no_dependents_is_deleted(self, env):
        ref, entity_id = env.warehouse()

        res = env.delete("warehouses", [ref])

        assert res.json()["records"][0]["outcome"] == "deleted"
        assert env.row("warehouses", entity_id) is None
        # The mapping goes with the row: leaving it would make the next push
        # believe the record is present and 'update' something that is gone.
        assert env.ref_count(ref) == 0

    def test_a_sales_order_with_no_referrers_is_deleted_with_its_lines(self, env):
        """AC-A4-6. The order's OWN line table is not a dependent - it is part of
        the document, and it cascades."""
        ref, header_id, line_id = env.sales_order()

        res = env.delete("sales_orders", [ref])

        assert res.json()["records"][0]["outcome"] == "deleted", res.text
        assert env.row("sales_orders", header_id) is None
        assert env.row("sales_order_lines", line_id) is None
        assert env.ref_count(ref) == 0

    def test_a_purchase_order_with_no_referrers_is_deleted_with_its_lines(self, env):
        ref, header_id, line_id = env.purchase_order()

        res = env.delete("purchase_orders", [ref])

        assert res.json()["records"][0]["outcome"] == "deleted", res.text
        assert env.row("purchase_orders", header_id) is None
        assert env.row("purchase_order_lines", line_id) is None
        assert env.ref_count(ref) == 0


# ========================================================== deactivate (AC-A4-4)
class TestDeactivate:
    def test_a_customer_an_order_points_at_is_deactivated(self, env):
        """AC-A4-4. `sales_orders.customer_id` is ON DELETE SET NULL, so a bare
        DELETE would report success and leave the order with no customer at all.
        That is the whole reason the probe exists."""
        ref, customer_id = env.customer()
        _, order_id, _ = env.sales_order(customer_id=customer_id)

        res = env.delete("customers", [ref])

        assert res.json()["records"][0]["outcome"] == "deactivated", res.text
        row = env.row("customers", customer_id)
        assert row is not None
        assert row["is_active"] is False
        assert str(env.row("sales_orders", order_id)["customer_id"]) == customer_id
        # Still AutoCount's row, so the next push finds it rather than creating a
        # second one.
        assert env.ref_count(ref) == 1

    def test_a_product_on_a_line_is_discontinued_not_deactivated(self, env):
        """AC-A4-5. `is_active` is not the lever on a product: placeholder rows
        orders reference have to stay active (product.py), so the retirement flag
        is `is_discontinued`."""
        ref, product_id = env.product()
        env.sales_order(product_id=product_id)

        # Products delete by code only (owner rulings, 3 Oct): the shared
        # service sends the `codes` map.
        code = env.row("products", product_id)["product_code"]
        res = env.delete("products", [ref], body={"source_refs": [ref], "codes": {ref: code}})

        assert res.json()["records"][0]["outcome"] == "deactivated", res.text
        row = env.row("products", product_id)
        assert row["is_discontinued"] is True
        assert row["is_active"] is True

    def test_a_second_deletion_of_a_dependent_row_still_reports_deactivated(self, env):
        """AC-A4-4. Idempotent: the ESB re-drains its queue, and an already
        deactivated row must not come back as failed."""
        ref, customer_id = env.customer()
        env.sales_order(customer_id=customer_id)

        assert env.delete("customers", [ref]).json()["records"][0]["outcome"] == "deactivated"
        again = env.delete("customers", [ref])

        assert again.json()["records"][0]["outcome"] == "deactivated"
        assert env.row("customers", customer_id)["is_active"] is False

    def test_a_sales_order_whose_line_is_claimed_is_cancelled(self, env):
        """AC-A4-6. The claim points at the LINE, not the header, so the header's
        own referrer list says nothing. The line table's referrers are what
        answer, and a document has no `is_active` - `cancelled` is its retirement."""
        ref, header_id, line_id = env.sales_order()
        env.claim(so_line_id=line_id)

        res = env.delete("sales_orders", [ref])

        assert res.json()["records"][0]["outcome"] == "deactivated", res.text
        assert env.row("sales_orders", header_id)["status"] == "cancelled"
        assert env.row("sales_order_lines", line_id)["line_status"] == "cancelled"
        assert env.ref_count(ref) == 1

    def test_a_purchase_order_whose_line_is_claimed_is_cancelled(self, env):
        ref, header_id, line_id = env.purchase_order()
        env.claim(po_line_id=line_id)

        res = env.delete("purchase_orders", [ref])

        assert res.json()["records"][0]["outcome"] == "deactivated", res.text
        assert env.row("purchase_orders", header_id)["status"] == "cancelled"
        assert env.row("purchase_order_lines", line_id)["line_status"] == "cancelled"
        assert env.ref_count(ref) == 1


# ============================================================= dry run (AC-A4-7)
class TestDryRun:
    def test_a_dry_run_reports_the_verdicts_and_writes_nothing(self, env):
        free_ref, free_id = env.warehouse()
        held_ref, held_id = env.customer()
        _, order_id, _ = env.sales_order(customer_id=held_id)
        unknown = _ref("GONE")
        refs_before = env.marker_ref_count()

        deletes = env.delete("warehouses", [free_ref, unknown], dry_run=True)
        deactivates = env.delete("customers", [held_ref], dry_run=True)

        assert deletes.json()["dry_run"] is True
        assert _by_ref(deletes.json())[free_ref]["outcome"] == "deleted"
        assert _by_ref(deletes.json())[unknown]["outcome"] == "not_found"
        assert deactivates.json()["records"][0]["outcome"] == "deactivated"

        assert env.row("warehouses", free_id) is not None
        assert env.row("customers", held_id)["is_active"] is True
        assert str(env.row("sales_orders", order_id)["customer_id"]) == held_id
        assert env.marker_ref_count() == refs_before


# ======================================================== batch safety (AC-A4-9)
class TestBatchSafety:
    def test_one_record_that_errors_does_not_cost_the_others_their_verdict(
        self, env, monkeypatch
    ):
        """AC-A4-9. Per-record savepoints, the same promise the ingest makes: a
        batch is not a transaction, so 1 bad row must not take out 999 good ones."""
        from app.services import deletion_service as module

        first_ref, first_id = env.customer()
        boom_ref, boom_id = env.customer()
        env.sales_order(customer_id=boom_id)  # forces the deactivate arm
        last_ref, last_id = env.customer()

        original = module.DeletionService._deactivate

        def _explode(self, entity_type, entity_id):
            if str(entity_id) == boom_id:
                raise RuntimeError(f"{MARKER} deactivate blew up")
            return original(self, entity_type, entity_id)

        monkeypatch.setattr(module.DeletionService, "_deactivate", _explode)

        res = env.delete("customers", [first_ref, boom_ref, last_ref])

        assert res.status_code == 200, res.text
        verdicts = _by_ref(res.json())
        assert verdicts[first_ref]["outcome"] == "deleted"
        assert verdicts[last_ref]["outcome"] == "deleted"
        assert verdicts[boom_ref]["outcome"] == "failed"
        # SEC3 (review round 1): a non-domain exception's own message is
        # never echoed to the caller - it is logged server-side instead.
        assert verdicts[boom_ref]["errors"]["_"] == "internal error; see server logs"
        assert MARKER not in verdicts[boom_ref]["errors"]["_"]
        assert res.json()["summary"] == {
            "total": 3,
            "deleted": 2,
            "deactivated": 0,
            "not_found": 0,
            "failed": 1,
        }
        assert env.row("customers", first_id) is None
        assert env.row("customers", last_id) is None
        # The failed record changed nothing at all.
        assert env.row("customers", boom_id)["is_active"] is True


# =============================================================== guard (AC-A4-8)
class TestPermissionGuard:
    """The real RBAC guard, on an empty schema of its own.

    A stub handler behind the real dependencies: what is under test is the gate.
    The route carries BOTH - the router's ingest guard (`.edit`) and its own
    `.delete` guard - because deleting through the ESB is a different act from
    writing, and an integration trusted to sync must not be able to remove.
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

        @api.post(
            "/ingest/{entity}/deletions",
            dependencies=[
                Depends(
                    require_external_permission_for_path(ingest_module.INGEST_PERMISSIONS)
                ),
                Depends(
                    require_external_permission_for_path(ingest_module.DELETE_PERMISSIONS)
                ),
            ],
        )
        def _delete_stub(entity: str):
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
            "edit": ingest_module.INGEST_PERMISSIONS["warehouses"],
            "delete": ingest_module.DELETE_PERMISSIONS["warehouses"],
        }
        perms = {}
        for slug in slugs.values():
            perm = UserPermission(slug=slug, name=slug)
            guard_db.add(perm)
            guard_db.flush()
            perms[slug] = perm

        issued = {}
        for label, held in (
            ("syncer", [slugs["edit"]]),
            ("remover", [slugs["edit"], slugs["delete"]]),
            ("deleter_only", [slugs["delete"]]),
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
        assert guard_client.post("/ingest/warehouses/deletions").status_code == 401

    def test_the_edit_slug_alone_is_403_naming_the_delete_slug(self, guard_client, keys):
        res = guard_client.post(
            "/ingest/warehouses/deletions", headers={"X-API-Key": keys["syncer"]}
        )
        assert res.status_code == 403
        assert "inventory.warehouses.delete" in res.text

    def test_the_delete_slug_alone_is_403_naming_the_edit_slug(self, guard_client, keys):
        res = guard_client.post(
            "/ingest/warehouses/deletions", headers={"X-API-Key": keys["deleter_only"]}
        )
        assert res.status_code == 403
        assert "inventory.warehouses.edit" in res.text

    def test_holding_both_slugs_passes(self, guard_client, keys):
        res = guard_client.post(
            "/ingest/warehouses/deletions", headers={"X-API-Key": keys["remover"]}
        )
        assert res.status_code == 200, res.text
