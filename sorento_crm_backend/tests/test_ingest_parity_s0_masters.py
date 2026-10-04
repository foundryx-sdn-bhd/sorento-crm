"""RED tests for ingest parity standardisation, Phase S0 (masters ingest hygiene).

UAC: documentation/plans/autocount/ingest-parity-standardisation-acceptance-criteria.md
     Phase S0, AC-P0-1 .. AC-P0-7.
PLAN: documentation/plans/autocount/PLAN-ingest-parity-standardisation.md sections 2.3, 2.4.

No implementation for this slice exists yet beyond what `master_ingest_service.py`
already does for Phase C. Every test below targets a SPECIFIC, already-read gap in
that module:

  AC-P0-1  `_lookup_id` for `customers` matches on `customer_code` ALONE
           (`normalized_code=False`), never `(code, name)` - so a push naming an
           existing code but a different name can adopt/rename the wrong row.
  AC-P0-2  Every optional field on every canonical schema is a plain default
           (`Optional[X] = None` or a bare `bool = True` / `int = 0`), and every
           `_xxx_columns` builder writes it unconditionally - so an OMITTED field
           reads identically to an explicit `null` and both clear the stored value.
  AC-P0-3  Same absent-vs-null gap for `units_of_measure.decimal_places`
           (`int = Field(0, ...)`, not Optional) and `sales_agents.person_label`.
  AC-P0-4  `_supplier_columns` never writes `contact_name`/`address_line1`/
           `address_line2`/`city`/`state`/`postal_code`/`country` at all; and both
           `_customer_columns` (credit_limit/payment_terms_days) and
           `_supplier_columns` (payment_terms_code, which currently raises
           `MissingReference` -> RETRYABLE) have no `deprecated_field` warning
           vocabulary yet.
  AC-P0-5  `CanonicalSupplier.payment_terms_days` defaults to `None`, not `30`,
           and the raw INSERT writes that `None` literally - no Python/ORM default
           kicks in for a hand-built INSERT statement.
  AC-P0-6  `_apply` writes exclusively via `text()` raw SQL, which bypasses the
           ORM entirely - so none of the global `before_flush` audit listener, the
           `after_insert`/`after_update` embedding-queue listener, or `updated_at`
           stamping ever fire; `_sales_agent_columns` also deliberately omits
           `source`, so an ESB-created agent lands on the column's `'manual'`
           server default.
  AC-P0-7  The parity fixture: the same record through the manual service and
           through the ESB ingest (into two separate companies) should be
           byte-identical apart from ids/timestamps/source - and is not, today,
           because of the AC-P0-2/AC-P0-4 gaps above.

Substrate: `tests._pg_fixture.blank_session()` - a real Postgres schema built from
the live models, freshly created and rolled back per test, so nothing here reads
or leaks into the shared dev database. Two companies where the AC calls for two.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

import app.services.embedding_change_listener as embedding_change_listener_module
import app.services.embedding_events as embedding_events_module
from app.models.audit import AuditLog
from app.models.base import set_company_scope
from app.models.company import Company
from app.models.integration_reference import IntegrationReference
from app.models.inventory import Warehouse
from app.models.order import Customer
from app.models.procurement import Supplier
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.sales_agent import SalesAgent
from app.schemas.inventory import WarehouseCreate, WarehouseUpdate
from app.schemas.order import CustomerCreate, CustomerUpdate
from app.schemas.procurement import SupplierCreate, SupplierUpdate
from app.schemas.product import (
    ProductCategoryCreate,
    ProductCategoryUpdate,
    ProductCreate,
    ProductUpdate,
    UnitOfMeasureCreate,
    UnitOfMeasureUpdate,
)
from app.services.audit_service import register_audit_listeners
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.embedding_change_listener import register_embedding_change_listeners
from app.services.inventory_service import WarehouseService
from app.services.master_ingest_service import IngestOutcome, MasterIngestService
from app.services.order_service import CustomerService
from app.services.procurement_service import SupplierService
from app.services.product_service import ProductCategoryService, ProductService, UnitOfMeasureService
from app.services.scm.sales_agent_service import normalize_code as normalize_agent_code

from tests._pg_fixture import blank_session, unique_code

MARKER = "ZZTIP"

# Generic exclusion set for AC-P0-7's column diff, per the UAC: "empty apart from
# ids, timestamps and source". `id`-suffixed FK columns (category_id, base_uom_id,
# brand_id, manager_id, pool_warehouse_id, ...) are genuinely different UUIDs per
# company even for the "same" referenced row, so they are ids too.
_GENERIC_EXCLUDE = {"id", "company_id", "created_at", "updated_at", "source", "created_by"}


@pytest.fixture(autouse=True)
def _listeners():
    """Both global listener sets, exactly as the app registers them at startup
    (idempotent). AC-P0-6 needs both to have anything to observe."""
    register_company_scope_listeners()
    register_audit_listeners()
    register_embedding_change_listeners()


@pytest.fixture()
def db():
    with blank_session() as session:
        yield session


@pytest.fixture()
def company_b(db) -> str:
    company = Company(id=str(uuid.uuid4()), name=f"{MARKER} B", code=unique_code(MARKER)[:10])
    db.add(company)
    db.flush()
    return str(company.id)


def _esb(db, company_id: str) -> MasterIngestService:
    return MasterIngestService(db, integration_id=None, company_id=company_id)


def _code(stem: str) -> str:
    return unique_code(f"{MARKER}{stem}")[:30]


def _mapped_columns(model) -> list[str]:
    return [
        c.name
        for c in model.__table__.columns
        if c.name not in _GENERIC_EXCLUDE and not c.name.endswith("_id")
    ]


def _row_values(db, table: str, code_column: str, code: str, company_id: str, columns: list[str]):
    cols = ", ".join(columns)
    row = db.execute(
        text(f"SELECT {cols} FROM {table} WHERE {code_column} = :c AND company_id = :cid"),
        {"c": code, "cid": company_id},
    ).mappings().first()
    return dict(row) if row else None


class TestAcP01CustomerIdentityCodeAndName:
    """D13: customer identity on the masters push is the lower-trimmed (code,
    name) pair, never the code alone. `_lookup_id("customers", ...)` today
    matches on `customer_code` only, so it cannot tell two same-code,
    different-name rows apart.
    """

    def test_pushing_an_existing_name_updates_that_row_and_leaves_the_sibling(self, db):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("CUST1")
        alpha = Customer(customer_code=code, customer_name="ALPHA", email="alpha@old.com")
        beta = Customer(customer_code=code, customer_name="BETA", email="beta@old.com")
        db.add_all([alpha, beta])
        db.flush()
        alpha_id, beta_id = str(alpha.id), str(beta.id)

        svc = _esb(db, DEFAULT_COMPANY_ID)
        result = svc.ingest(
            "customers",
            [{"source_ref": f"DK-{code}", "code": code, "name": "BETA", "email": "beta@new.com"}],
        )
        assert result.updated == 1, result.records[0].errors

        alpha_row = db.execute(
            text("SELECT customer_name, email FROM customers WHERE id = :i"), {"i": alpha_id}
        ).first()
        beta_row = db.execute(
            text("SELECT customer_name, email FROM customers WHERE id = :i"), {"i": beta_id}
        ).first()
        assert alpha_row == ("ALPHA", "alpha@old.com"), "ALPHA must be untouched"
        assert beta_row == ("BETA", "beta@new.com"), "BETA is the row that should have updated"

    def test_pushing_a_new_name_under_a_known_code_creates_a_third_row_never_renames(self, db):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("CUST2")
        db.add_all(
            [
                Customer(customer_code=code, customer_name="ALPHA"),
                Customer(customer_code=code, customer_name="BETA"),
            ]
        )
        db.flush()

        svc = _esb(db, DEFAULT_COMPANY_ID)
        result = svc.ingest(
            "customers", [{"source_ref": f"DK-{code}-G", "code": code, "name": "GAMMA"}]
        )

        assert result.created == 1, result.records[0].errors
        names = set(
            db.execute(
                text("SELECT lower(customer_name) FROM customers WHERE customer_code = :c"),
                {"c": code},
            ).scalars()
        )
        assert names == {"alpha", "beta", "gamma"}, "neither existing row may be renamed"

    def test_integration_reference_links_the_row_matched_by_name_not_an_arbitrary_one(self, db):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("CUST3")
        alpha = Customer(customer_code=code, customer_name="ALPHA")
        beta = Customer(customer_code=code, customer_name="BETA")
        db.add_all([alpha, beta])
        db.flush()
        beta_id = str(beta.id)

        svc = _esb(db, DEFAULT_COMPANY_ID)
        ref = f"DK-{code}-B"
        svc.ingest("customers", [{"source_ref": ref, "code": code, "name": "BETA"}])

        linked = (
            db.query(IntegrationReference)
            .filter_by(entity_type="customers", source_ref=ref)
            .one()
        )
        assert str(linked.entity_id) == beta_id


class TestAcP02AbsentUntouchedNullCleared:
    """D14: absent = untouched, null = cleared, on every optional column of every
    master; `is_active` on UPDATE is written only when the payload sends it.
    """

    def test_warehouse_location_and_is_active_absent_untouched_null_cleared(self, db):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("WH1")
        db.add(
            Warehouse(warehouse_code=code, warehouse_name="Main", location="Rack 4", is_active=False)
        )
        db.flush()

        svc = _esb(db, DEFAULT_COMPANY_ID)
        result = svc.ingest(
            "warehouses", [{"source_ref": f"DK-{code}", "code": code, "name": "Main"}]
        )
        assert result.updated == 1, result.records[0].errors

        row = db.execute(
            text("SELECT location, is_active FROM warehouses WHERE warehouse_code = :c"),
            {"c": code},
        ).first()
        assert row == ("Rack 4", False), "omitted fields must stay untouched, not cleared/reset"

        svc.ingest(
            "warehouses",
            [{"source_ref": f"DK-{code}", "code": code, "name": "Main", "location": None}],
        )
        location = db.execute(
            text("SELECT location FROM warehouses WHERE warehouse_code = :c"), {"c": code}
        ).scalar()
        assert location is None, "an explicit null must clear the field"

    def test_blank_string_clears_like_a_blank_cell(self, db):
        """D14 third state (ESB session, 2026-09-06): the ESB sends `""` for a
        mapped AutoCount field that is blank (it omits None, never sends null).
        The xlsx import treats a blank cell as "clear it", so a blank string on
        the push must land as NULL too - never as a stored empty string, which
        would be the one column shape the upload can never produce."""
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("WHB")
        db.add(Warehouse(warehouse_code=code, warehouse_name="Main", location="Rack 4"))
        db.flush()

        svc = _esb(db, DEFAULT_COMPANY_ID)
        result = svc.ingest(
            "warehouses",
            [{"source_ref": f"DK-{code}", "code": code, "name": "Main", "location": "   "}],
        )
        assert result.updated == 1, result.records[0].errors
        location = db.execute(
            text("SELECT location FROM warehouses WHERE warehouse_code = :c"), {"c": code}
        ).scalar()
        assert location is None, "a blank string must clear the field, same as a blank cell"

        # A required identity field stays refused when blank - "" is not a
        # way to omit `code` or `name`.
        result = svc.ingest(
            "warehouses", [{"source_ref": f"DK-{code}", "code": code, "name": ""}]
        )
        assert result.failed == 1, result.records[0].errors

    def test_customer_email_preserved_when_omitted(self, db):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("CUSTE")
        db.add(Customer(customer_code=code, customer_name="Test Sdn Bhd", email="keep@example.com"))
        db.flush()

        svc = _esb(db, DEFAULT_COMPANY_ID)
        result = svc.ingest(
            "customers", [{"source_ref": f"DK-{code}", "code": code, "name": "Test Sdn Bhd"}]
        )
        assert result.updated == 1, result.records[0].errors
        email = db.execute(
            text("SELECT email FROM customers WHERE customer_code = :c"), {"c": code}
        ).scalar()
        assert email == "keep@example.com"

    def test_category_description_preserved_when_omitted(self, db):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("CAT1")
        db.add(ProductCategory(category_code=code, category_name="Fasteners", description="Keep me"))
        db.flush()

        svc = _esb(db, DEFAULT_COMPANY_ID)
        result = svc.ingest(
            "product_categories", [{"source_ref": f"DK-{code}", "code": code, "name": "Fasteners"}]
        )
        assert result.updated == 1, result.records[0].errors
        description = db.execute(
            text("SELECT description FROM product_categories WHERE category_code = :c"), {"c": code}
        ).scalar()
        assert description == "Keep me"

    def test_uom_description_preserved_when_omitted(self, db):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("UOM1")
        db.add(UnitOfMeasure(uom_code=code, uom_name="Kilogram", description="Keep me"))
        db.flush()

        svc = _esb(db, DEFAULT_COMPANY_ID)
        result = svc.ingest(
            "units_of_measure", [{"source_ref": f"DK-{code}", "code": code, "name": "Kilogram"}]
        )
        assert result.updated == 1, result.records[0].errors
        description = db.execute(
            text("SELECT description FROM units_of_measure WHERE uom_code = :c"), {"c": code}
        ).scalar()
        assert description == "Keep me"

    def test_product_create_without_list_price_defaults_to_zero(self, db):
        """Live fix, 2026-09-06: `products.list_price` is NOT NULL with no
        server-side default - a create whose payload never sends `list_price`
        at all used to reach the DB as a bare `NotNullViolation` (43 records
        on `:8042`) instead of the same 0 the manual create form's own
        bulk-import fallback already applies for a blank price cell."""
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        cat_code, uom_code = _code("P2CAT"), _code("P2UOM")
        code = _code("P2PRD")
        svc = _esb(db, DEFAULT_COMPANY_ID)

        result = svc.ingest(
            "products",
            [
                {
                    "source_ref": f"DK-{code}",
                    "code": code,
                    "name": "Item",
                    "category_code": cat_code,
                    "uom_code": uom_code,
                }
            ],
        )
        assert result.records[0].outcome is IngestOutcome.CREATED, result.records[0].errors
        list_price = db.execute(
            text("SELECT list_price FROM products WHERE product_code = :c"), {"c": code}
        ).scalar()
        assert list_price == Decimal("0")

    def test_product_update_with_list_price_null_resets_to_zero(self, db):
        """An explicit `null` on a NOT NULL column with no server default
        maps to the column's own create default (D14: "null = cleared" can
        only mean cleared TO something on a NOT NULL column), never a
        `NotNullViolation`."""
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        cat_code, uom_code = _code("P2CATB"), _code("P2UOMB")
        code = _code("P2PRDB")
        svc = _esb(db, DEFAULT_COMPANY_ID)
        svc.ingest(
            "products",
            [
                {
                    "source_ref": f"DK-{code}",
                    "code": code,
                    "name": "Item",
                    "category_code": cat_code,
                    "uom_code": uom_code,
                    "list_price": "12.50",
                }
            ],
        )

        result = svc.ingest(
            "products",
            [{"source_ref": f"DK-{code}", "code": code, "name": "Item", "list_price": None}],
        )
        assert result.records[0].outcome is IngestOutcome.UPDATED, result.records[0].errors
        list_price = db.execute(
            text("SELECT list_price FROM products WHERE product_code = :c"), {"c": code}
        ).scalar()
        assert list_price == Decimal("0")

    def test_product_update_without_list_price_preserves_existing(self, db):
        """Absent stays absent (D14): a re-sync that never mentions the price
        at all must not reset it to the create default - only an explicit
        `null` does that."""
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        cat_code, uom_code = _code("P2CATC"), _code("P2UOMC")
        code = _code("P2PRDC")
        svc = _esb(db, DEFAULT_COMPANY_ID)
        svc.ingest(
            "products",
            [
                {
                    "source_ref": f"DK-{code}",
                    "code": code,
                    "name": "Item",
                    "category_code": cat_code,
                    "uom_code": uom_code,
                    "list_price": "12.50",
                }
            ],
        )

        result = svc.ingest(
            "products",
            [{"source_ref": f"DK-{code}", "code": code, "name": "Item Renamed"}],
        )
        assert result.records[0].outcome is IngestOutcome.UPDATED, result.records[0].errors
        list_price = db.execute(
            text("SELECT list_price FROM products WHERE product_code = :c"), {"c": code}
        ).scalar()
        assert list_price == Decimal("12.50")


class TestAcP03NumericAndLabelPreservedOnOmit:
    """D14, continued: `units_of_measure.decimal_places` and
    `sales_agents.person_label` must also survive an omitting push."""

    def test_uom_decimal_places_preserved_when_omitted(self, db):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("UOMKG")
        db.add(UnitOfMeasure(uom_code=code, uom_name="Kilogram", decimal_places=3))
        db.flush()

        svc = _esb(db, DEFAULT_COMPANY_ID)
        result = svc.ingest(
            "units_of_measure", [{"source_ref": f"DK-{code}", "code": code, "name": "Kilogram"}]
        )
        assert result.updated == 1, result.records[0].errors
        decimal_places = db.execute(
            text("SELECT decimal_places FROM units_of_measure WHERE uom_code = :c"), {"c": code}
        ).scalar()
        assert decimal_places == 3

    def test_sales_agent_person_label_preserved_when_omitted(self, db):
        code = _code("AGT1")
        db.add(SalesAgent(sales_agent=code, person_label="Ah Seng"))
        db.flush()

        svc = _esb(db, DEFAULT_COMPANY_ID)
        result = svc.ingest("sales_agents", [{"source_ref": f"DK-{code}", "code": code}])
        assert result.updated == 1, result.records[0].errors
        # `sales_agents` stores the code upper-trimmed on create/adopt
        # (tests/test_ingest_sales_agents.py AC-A2-1/A2-3), so the lookup must
        # match on the SAME normalised form - `unique_code()` routinely mints
        # a-f hex, and an un-normalised lookup misses non-deterministically.
        person_label = db.execute(
            text("SELECT person_label FROM sales_agents WHERE sales_agent = :c"),
            {"c": normalize_agent_code(code)},
        ).scalar()
        assert person_label == "Ah Seng"


class TestAcP04SupplierAddressBlockAndDeprecatedFields:
    """D15: the supplier contact/address block must land. The
    accepted-and-ignored-with-warning `deprecated_field` behaviour for
    customer credit_limit/payment_terms_days and supplier payment_terms_code
    (this class's own docstring said so through S0-S3) is superseded
    2026-09-06 by S4's contract 2.1 end state (AC-P0-4/D15): the three
    fields are REMOVED from the canonical schemas entirely, so a payload
    naming one now fails validation with a field-named error, never
    accepted, never retryable."""

    def test_supplier_contact_and_address_fields_land(self, db):
        # S2 (`PLAN-local-supplier-oi-routing.md`, AC-2.10): `country` is resolved to
        # `country_id`, never written as free text - `suppliers.country` is dropped. The
        # blank scratch schema is `create_all`, not a migrated database, so it carries no
        # migration seed (the ISO list); the one row this ingest needs to resolve against
        # is seeded here, the same way AC-2.1's own test seeds it for the countries suite.
        from app.models.country import Country

        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        my = Country(id=str(uuid.uuid4()), code="MY", name="Malaysia")
        db.add(my)
        db.flush()

        code = _code("SUP1")
        svc = _esb(db, DEFAULT_COMPANY_ID)
        result = svc.ingest(
            "suppliers",
            [
                {
                    "source_ref": f"DK-{code}",
                    "code": code,
                    "name": "Acme Sdn Bhd",
                    "contact_name": "Tan Ah Kow",
                    "address_line1": "1 Jalan Test",
                    "address_line2": "Taman Test",
                    "city": "Kuala Lumpur",
                    "state": "Selangor",
                    "postal_code": "50000",
                    "country": "Malaysia",
                }
            ],
        )
        assert result.created == 1, result.records[0].errors
        # ::text, the suite's idiom (test_plan_row_decision.py, test_m4_cash.py): psycopg2
        # returns a raw uuid column as `uuid.UUID`, which never equals the ORM's `str` id.
        row = db.execute(
            text(
                "SELECT contact_name, address_line1, address_line2, city, state, "
                "postal_code, country_id::text FROM suppliers WHERE supplier_code = :c"
            ),
            {"c": code},
        ).first()
        assert row == (
            "Tan Ah Kow",
            "1 Jalan Test",
            "Taman Test",
            "Kuala Lumpur",
            "Selangor",
            "50000",
            str(my.id),
        )

    def test_customer_deprecated_fields_are_dropped_never_retryable(self, db):
        # Superseded 2026-09-06 (S4, AC-P0-4/D15 end state) - see the class
        # docstring. Owner decision 1 Oct 2026: an undeclared key is dropped (name
        # logged by the route), never refused, so the record lands without it.
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("CUSTD")
        svc = _esb(db, DEFAULT_COMPANY_ID)
        result = svc.ingest(
            "customers",
            [
                {
                    "source_ref": f"DK-{code}",
                    "code": code,
                    "name": "Deprecated Fields Sdn Bhd",
                    "credit_limit": "15000.50",
                    "payment_terms_days": 45,
                }
            ],
        )
        record = result.records[0]
        assert record.outcome is IngestOutcome.CREATED, record.errors
        assert result.retryable == 0

    def test_supplier_payment_terms_code_is_dropped_never_retryable(self, db):
        # Superseded 2026-09-06 (S4, AC-P0-4/D15 end state) - see the class
        # docstring. Dropped, not refused (owner decision, 1 Oct 2026).
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("SUPD")
        svc = _esb(db, DEFAULT_COMPANY_ID)
        result = svc.ingest(
            "suppliers",
            [
                {
                    "source_ref": f"DK-{code}",
                    "code": code,
                    "name": "Deprecated Terms Sdn Bhd",
                    "payment_terms_code": "NET-30",
                }
            ],
        )
        record = result.records[0]
        assert record.outcome is IngestOutcome.CREATED, record.errors
        assert result.retryable == 0


class TestAcP05SupplierPaymentTermsDefault:
    """D14: a supplier the ESB creates without `payment_terms_days` gets the
    manual-create default (30), not NULL."""

    def test_supplier_created_without_payment_terms_days_defaults_to_30(self, db):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("SUP30")
        svc = _esb(db, DEFAULT_COMPANY_ID)
        result = svc.ingest(
            "suppliers", [{"source_ref": f"DK-{code}", "code": code, "name": "No Terms Sdn Bhd"}]
        )
        assert result.created == 1, result.records[0].errors
        terms = db.execute(
            text("SELECT payment_terms_days FROM suppliers WHERE supplier_code = :c"), {"c": code}
        ).scalar()
        assert terms == 30


class TestAcP06AuditEmbeddingUpdatedAtAndAgentSource:
    """D18: the masters writer moves to the ORM upsert path, so audit, embedding
    and `updated_at` all fire; an ESB-created sales agent carries
    `source='autocount'`. Today `_apply` writes exclusively via raw `text()` SQL,
    which no ORM-level listener ever sees."""

    def test_audit_log_row_exists_for_esb_customer_create_and_update(self, db):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("CAUD")
        svc = _esb(db, DEFAULT_COMPANY_ID)
        result = svc.ingest(
            "customers", [{"source_ref": f"DK-{code}", "code": code, "name": "Audit Me"}]
        )
        assert result.created == 1, result.records[0].errors
        entity_id = result.records[0].entity_id

        create_rows = (
            db.query(AuditLog)
            .filter_by(entity_type="customer", entity_id=entity_id, action="CREATE")
            .count()
        )
        assert create_rows == 1

        svc.ingest(
            "customers", [{"source_ref": f"DK-{code}", "code": code, "name": "Audit Me Updated"}]
        )
        update_rows = (
            db.query(AuditLog)
            .filter_by(entity_type="customer", entity_id=entity_id, action="UPDATE")
            .count()
        )
        assert update_rows == 1

    def test_audit_log_row_exists_for_esb_supplier_create(self, db):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("SAUD")
        svc = _esb(db, DEFAULT_COMPANY_ID)
        result = svc.ingest(
            "suppliers", [{"source_ref": f"DK-{code}", "code": code, "name": "Audit Supplier"}]
        )
        assert result.created == 1, result.records[0].errors
        entity_id = result.records[0].entity_id

        create_rows = (
            db.query(AuditLog)
            .filter_by(entity_type="suppliers", entity_id=entity_id, action="CREATE")
            .count()
        )
        assert create_rows == 1

    def test_updated_at_is_stamped_on_an_esb_update(self, db):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("UPDAT")
        svc = _esb(db, DEFAULT_COMPANY_ID)
        svc.ingest("customers", [{"source_ref": f"DK-{code}", "code": code, "name": "V1"}])
        svc.ingest("customers", [{"source_ref": f"DK-{code}", "code": code, "name": "V2"}])

        updated_at = db.execute(
            text("SELECT updated_at FROM customers WHERE customer_code = :c"), {"c": code}
        ).scalar()
        assert updated_at is not None

    def test_embedding_enqueued_for_esb_customer_create(self, db, monkeypatch):
        calls = []
        monkeypatch.setattr(
            embedding_change_listener_module,
            "_queue_from_mapper",
            lambda *a, **kw: calls.append((a, kw)),
        )
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("CEMB")
        svc = _esb(db, DEFAULT_COMPANY_ID)
        result = svc.ingest(
            "customers", [{"source_ref": f"DK-{code}", "code": code, "name": "Embed Me"}]
        )
        assert result.created == 1, result.records[0].errors
        assert calls, "expected the customer embedding change listener to enqueue an event"

    def test_embedding_enqueued_for_esb_product_create(self, db, monkeypatch):
        mapper_calls = []
        event_calls = []
        monkeypatch.setattr(
            embedding_change_listener_module,
            "_queue_from_mapper",
            lambda *a, **kw: mapper_calls.append((a, kw)),
        )
        monkeypatch.setattr(
            embedding_events_module,
            "publish_embedding_event",
            lambda *a, **kw: event_calls.append((a, kw)),
        )
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        cat_code = _code("PECAT")
        uom_code = _code("PEUOM")
        svc = _esb(db, DEFAULT_COMPANY_ID)
        svc.ingest(
            "product_categories", [{"source_ref": f"DK-{cat_code}", "code": cat_code, "name": "Cat"}]
        )
        svc.ingest(
            "units_of_measure", [{"source_ref": f"DK-{uom_code}", "code": uom_code, "name": "Each"}]
        )
        code = _code("PEMB")
        result = svc.ingest(
            "products",
            [
                {
                    "source_ref": f"DK-{code}",
                    "code": code,
                    "name": "Embed Product",
                    "category_code": cat_code,
                    "uom_code": uom_code,
                }
            ],
        )
        assert result.created == 1, result.records[0].errors
        assert mapper_calls or event_calls, "expected a product embedding event to be enqueued"

    def test_esb_created_sales_agent_carries_source_autocount(self, db):
        code = _code("AGTSRC")
        svc = _esb(db, DEFAULT_COMPANY_ID)
        result = svc.ingest("sales_agents", [{"source_ref": f"DK-{code}", "code": code}])
        assert result.created == 1, result.records[0].errors
        # Same normalised-lookup reasoning as
        # TestAcP03NumericAndLabelPreservedOnOmit.test_sales_agent_person_label_preserved_when_omitted.
        source = db.execute(
            text("SELECT source FROM sales_agents WHERE sales_agent = :c"),
            {"c": normalize_agent_code(code)},
        ).scalar()
        assert source == "autocount"


class TestAcP07ManualVsEsbParity:
    """The same fixture (one record, then a second push changing two fields and
    omitting one) through the manual create/update service into company A and
    through the ESB masters ingest into company B; a column-by-column diff
    (everything on the model's table except id/company_id/created_at/updated_at/
    source/created_by/*_id) must be empty.

    `sales_agents` is deliberately excluded: it is a SHARED table (no
    `company_id`), so "one company vs another" does not apply to it.
    """

    def test_warehouse_manual_vs_esb_parity(self, db, company_b):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("PARWH")
        manual = WarehouseService(db).create_warehouse(
            WarehouseCreate(warehouse_code=code, warehouse_name="Main", location="Rack 1")
        )
        esb = _esb(db, company_b)
        esb.ingest(
            "warehouses",
            [{"source_ref": f"DK-{code}", "code": code, "name": "Main", "location": "Rack 1"}],
        )

        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        WarehouseService(db).update_warehouse(
            manual.id, WarehouseUpdate(warehouse_name="Main Updated", is_active=False)
        )
        esb.ingest(
            "warehouses",
            [{"source_ref": f"DK-{code}", "code": code, "name": "Main Updated", "is_active": False}],
        )

        columns = _mapped_columns(Warehouse)
        manual_row = _row_values(db, "warehouses", "warehouse_code", code, DEFAULT_COMPANY_ID, columns)
        esb_row = _row_values(db, "warehouses", "warehouse_code", code, company_b, columns)
        diff = {k: (manual_row.get(k), esb_row.get(k)) for k in columns if manual_row.get(k) != esb_row.get(k)}
        assert diff == {}, diff

    def test_supplier_manual_vs_esb_parity(self, db, company_b):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("PARSUP")
        manual = SupplierService(db).create_supplier(
            SupplierCreate(
                supplier_code=code,
                supplier_name="Acme",
                contact_name="Tan",
                email="acme@example.com",
                phone_number="0123456789",
                address_line1="1 Jalan",
                city="KL",
                state="Selangor",
                postal_code="50000",
                country="Malaysia",
                payment_terms_days=30,
            )
        )
        esb = _esb(db, company_b)
        esb.ingest(
            "suppliers",
            [
                {
                    "source_ref": f"DK-{code}",
                    "code": code,
                    "name": "Acme",
                    "contact_name": "Tan",
                    "email": "acme@example.com",
                    "phone_number": "0123456789",
                    "address_line1": "1 Jalan",
                    "city": "KL",
                    "state": "Selangor",
                    "postal_code": "50000",
                    "country": "Malaysia",
                    "payment_terms_days": 30,
                }
            ],
        )

        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        SupplierService(db).update_supplier(
            manual.id, SupplierUpdate(supplier_name="Acme Sdn Bhd", city="Petaling Jaya")
        )
        esb.ingest(
            "suppliers",
            [
                {
                    "source_ref": f"DK-{code}",
                    "code": code,
                    "name": "Acme Sdn Bhd",
                    "city": "Petaling Jaya",
                }
            ],
        )

        columns = _mapped_columns(Supplier)
        manual_row = _row_values(db, "suppliers", "supplier_code", code, DEFAULT_COMPANY_ID, columns)
        esb_row = _row_values(db, "suppliers", "supplier_code", code, company_b, columns)
        diff = {k: (manual_row.get(k), esb_row.get(k)) for k in columns if manual_row.get(k) != esb_row.get(k)}
        assert diff == {}, diff

    def test_category_manual_vs_esb_parity(self, db, company_b):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("PARCAT")
        manual = ProductCategoryService(db).create_category(
            ProductCategoryCreate(category_code=code, category_name="Fasteners", description="Bolts and nuts")
        )
        esb = _esb(db, company_b)
        esb.ingest(
            "product_categories",
            [{"source_ref": f"DK-{code}", "code": code, "name": "Fasteners", "description": "Bolts and nuts"}],
        )

        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        ProductCategoryService(db).update_category(
            manual.id, ProductCategoryUpdate(category_name="Fasteners & Screws", is_active=False)
        )
        esb.ingest(
            "product_categories",
            [{"source_ref": f"DK-{code}", "code": code, "name": "Fasteners & Screws", "is_active": False}],
        )

        columns = _mapped_columns(ProductCategory)
        manual_row = _row_values(db, "product_categories", "category_code", code, DEFAULT_COMPANY_ID, columns)
        esb_row = _row_values(db, "product_categories", "category_code", code, company_b, columns)
        diff = {k: (manual_row.get(k), esb_row.get(k)) for k in columns if manual_row.get(k) != esb_row.get(k)}
        assert diff == {}, diff

    def test_uom_manual_vs_esb_parity(self, db, company_b):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("PARUOM")
        manual = UnitOfMeasureService(db).create_uom(
            UnitOfMeasureCreate(uom_code=code, uom_name="Kilogram", decimal_places=3, description="Metric mass")
        )
        esb = _esb(db, company_b)
        esb.ingest(
            "units_of_measure",
            [
                {
                    "source_ref": f"DK-{code}",
                    "code": code,
                    "name": "Kilogram",
                    "decimal_places": 3,
                    "description": "Metric mass",
                }
            ],
        )

        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        UnitOfMeasureService(db).update_uom(
            manual.id, UnitOfMeasureUpdate(uom_name="Kilograms", is_active=False)
        )
        esb.ingest(
            "units_of_measure",
            [{"source_ref": f"DK-{code}", "code": code, "name": "Kilograms", "is_active": False}],
        )

        columns = _mapped_columns(UnitOfMeasure)
        manual_row = _row_values(db, "units_of_measure", "uom_code", code, DEFAULT_COMPANY_ID, columns)
        esb_row = _row_values(db, "units_of_measure", "uom_code", code, company_b, columns)
        diff = {k: (manual_row.get(k), esb_row.get(k)) for k in columns if manual_row.get(k) != esb_row.get(k)}
        assert diff == {}, diff

    def test_customer_manual_vs_esb_parity(self, db, company_b):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        code = _code("PARCUST")
        manual = CustomerService(db).create_customer(
            CustomerCreate(
                customer_code=code,
                customer_name="Test Sdn Bhd",
                email="t@example.com",
                phone_number="0123456789",
            )
        )
        esb = _esb(db, company_b)
        esb.ingest(
            "customers",
            [
                {
                    "source_ref": f"DK-{code}",
                    "code": code,
                    "name": "Test Sdn Bhd",
                    "email": "t@example.com",
                    "phone_number": "0123456789",
                }
            ],
        )

        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        CustomerService(db).update_customer(
            manual.id, CustomerUpdate(customer_name="Test Sdn Bhd (Updated)", is_active=False)
        )
        esb.ingest(
            "customers",
            [{"source_ref": f"DK-{code}", "code": code, "name": "Test Sdn Bhd (Updated)", "is_active": False}],
        )

        columns = _mapped_columns(Customer)
        manual_row = _row_values(db, "customers", "customer_code", code, DEFAULT_COMPANY_ID, columns)
        esb_row = _row_values(db, "customers", "customer_code", code, company_b, columns)
        diff = {k: (manual_row.get(k), esb_row.get(k)) for k in columns if manual_row.get(k) != esb_row.get(k)}
        assert diff == {}, diff

    def test_product_manual_vs_esb_parity(self, db, company_b):
        # D24 (captain 2026-09-06): `product_name` is the AutoCount item code
        # and `description` the AutoCount text on BOTH channels. The ESB
        # writer forces that on every push (`name` maps to `description`
        # when no `description` is sent); the manual side here types the
        # same convention, so the two rows can be compared column for column.
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        cat_code = _code("PARPCAT")
        uom_code = _code("PARPUOM")
        category_a = ProductCategoryService(db).create_category(
            ProductCategoryCreate(category_code=cat_code, category_name="Parity Cat")
        )
        uom_a = UnitOfMeasureService(db).create_uom(
            UnitOfMeasureCreate(uom_code=uom_code, uom_name="Each")
        )
        code = _code("PARPRD")
        manual = ProductService(db).create_product(
            ProductCreate(
                product_code=code,
                product_name=code,
                description="Parity Product",
                category_id=category_a.id,
                base_uom_id=uom_a.id,
                list_price=Decimal("10.00"),
            ),
            created_by=None,
        )

        esb = _esb(db, company_b)
        esb.ingest(
            "product_categories",
            [{"source_ref": f"DK-{cat_code}", "code": cat_code, "name": "Parity Cat"}],
        )
        esb.ingest(
            "units_of_measure", [{"source_ref": f"DK-{uom_code}", "code": uom_code, "name": "Each"}]
        )
        esb.ingest(
            "products",
            [
                {
                    "source_ref": f"DK-{code}",
                    "code": code,
                    "name": "Parity Product",
                    "category_code": cat_code,
                    "uom_code": uom_code,
                    "list_price": "10.00",
                }
            ],
        )

        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        ProductService(db).update_product(
            manual.id,
            ProductUpdate(description="Parity Product Updated", list_price=Decimal("12.50")),
            updated_by=None,
        )
        esb.ingest(
            "products",
            [
                {
                    "source_ref": f"DK-{code}",
                    "code": code,
                    "name": "Parity Product Updated",
                    "category_code": cat_code,
                    "uom_code": uom_code,
                    "list_price": "12.50",
                }
            ],
        )

        columns = _mapped_columns(Product)
        manual_row = _row_values(db, "products", "product_code", code, DEFAULT_COMPANY_ID, columns)
        esb_row = _row_values(db, "products", "product_code", code, company_b, columns)
        diff = {k: (manual_row.get(k), esb_row.get(k)) for k in columns if manual_row.get(k) != esb_row.get(k)}
        assert diff == {}, diff
