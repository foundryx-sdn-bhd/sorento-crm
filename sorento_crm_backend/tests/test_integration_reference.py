"""Phase B -- the integration reference table (UAC Group D).

Replaces the plan's per-table `source_system`/`source_ref` columns with a single
polymorphic mapping table: the business tables stay untouched, and adding
another entity type needs no DDL.

  AC-AC-21  consumed entities carry a source system and reference
  AC-AC-22  source_ref holds AutoCount's stable DocKey, never the mutable DocNo
  AC-AC-23  no row is left in a state that breaks a later sync

Two properties carry the design, because a polymorphic table has no foreign
keys to lean on:

  * **entity_type is an allowlist.** It reaches a table name, and it arrives
    from an ingest payload. An unchecked value is an injection surface.
  * **A reference whose target has been deleted must not resolve.** Nothing
    cascades, so a stale row would otherwise make ingest 'update' a record that
    no longer exists.

Runs against Postgres, not sqlite. The previous sqlite version created stub
`products (id VARCHAR)` tables, so it exercised the service against a schema
that does not exist anywhere -- it could not have caught a uuid-vs-varchar
mismatch, which is precisely the bug TestIdentifierTyping exists to pin.
"""
import uuid

import pytest
from sqlalchemy import text

from app.models.integration_reference import IntegrationReference
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.integration_reference_service import (
    SUPPORTED_ENTITY_TYPES,
    IntegrationReferenceService,
    UnsupportedEntityType,
)
from tests._pg_fixture import pg_session, unique_code


@pytest.fixture()
def db():
    with pg_session() as session:
        yield session


@pytest.fixture()
def svc(db):
    # Anchored to the incumbent company: every functional test here exercises
    # a company-scoped type (`products`, `orders`) - plan D14 is strict, so an
    # unanchored `link`/`resolve` on one now raises ValueError. None of the
    # fixture rows below (`_product`, `_order`) set their own `company_id`, so
    # the real seeded incumbent (migration 302 in production, seeded by hand
    # on this lane's private DB) is the one real company id available here.
    return IntegrationReferenceService(db, company_id=DEFAULT_COMPANY_ID)


def _refs(db):
    """Only the references this test created.

    The table is shared with real data, so a bare count() would be answering a
    different question than the one asked.
    """
    return db.query(IntegrationReference).filter(
        IntegrationReference.source_ref.like("ZZT-%")
    )


def _product(db, pid=None):
    """A real product row, with the two NOT NULL parents it requires.

    is_active is set explicitly: the model declares a Python-side default only,
    not a server_default, so a raw INSERT (as opposed to an ORM add) leaves it
    NULL. The local prod-copy DB happens to carry a migration-added default and
    forgave that; a schema built straight from the ORM models (bootstrap_env, so
    CI and any fresh install) does not.
    """
    pid = pid or uuid.uuid4()
    cat_id, uom_id = uuid.uuid4(), uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO product_categories (id, category_code, category_name, is_active) "
            "VALUES (:i, :c, 'Test Category', true)"
        ),
        {"i": cat_id, "c": unique_code("CAT")},
    )
    db.execute(
        text(
            "INSERT INTO units_of_measure (id, uom_code, uom_name, is_active) "
            "VALUES (:i, :c, 'Each', true)"
        ),
        {"i": uom_id, "c": unique_code("UOM")},
    )
    db.execute(
        text(
            "INSERT INTO products (id, product_code, product_name, category_id, base_uom_id, "
            "list_price, is_active) "
            "VALUES (:i, :c, 'Test Product', :cat, :uom, 0, true)"
        ),
        {"i": pid, "c": unique_code("PRD"), "cat": cat_id, "uom": uom_id},
    )
    db.flush()
    return str(pid)


def _integration(db):
    iid = uuid.uuid4()
    db.execute(
        text("INSERT INTO integrations (id, name, type) VALUES (:i, :n, 'esb')"),
        {"i": iid, "n": unique_code("INT")},
    )
    db.flush()
    return str(iid)


def _order(db):
    # Insert via the ORM, not raw SQL: orders carries a dozen NOT NULL columns
    # whose defaults are Python-side only (is_cancelled, kpi_warning, the amount
    # totals, ...). A raw INSERT skips them and fails on a schema built straight
    # from the ORM models (bootstrap_env -- CI and fresh installs); the ORM
    # applies every default.
    from app.models.order import Order

    oid = str(uuid.uuid4())
    db.add(Order(id=oid, order_number=unique_code("ORD")))
    db.flush()
    return oid


class TestEntityTypeAllowlist:
    def test_the_consumed_entities_are_supported(self):
        assert SUPPORTED_ENTITY_TYPES == {
            "products",
            "product_categories",
            "units_of_measure",
            "stock",
            "warehouses",
            "suppliers",
            "customers",
            # Group A2: the salesperson master joined the ingest surface, so its
            # refs need somewhere to live. `sales_agents` is also a real table
            # name, which the allowlist's own SQL interpolation depends on.
            "sales_agents",
            # Group A3: the documents joined the ingest surface, so their headers
            # need a mapping to be idempotent by. Both are real PUBLIC table
            # names, which the allowlist's SQL interpolation depends on - the
            # `projects` schema holds tables of the same two names and they are
            # NOT these.
            "sales_orders",
            "purchase_orders",
            "picking_headers",
            "picking_lines",
            "orders",
            "order_lines",
            # autocount-brands-ingest AC-15: brands joined the ingest surface.
            "brands",
            # Finance S0 (#1309): billing document headers, in the `finance` schema.
            "billing_documents",
            # AC-BILLING S5 (contract 2.10): a debtor ref, pointing at the customer's row.
            "ar_outstanding",
        }

    def test_every_supported_type_names_a_real_table(self, db):
        # The allowlist is interpolated into SQL as a table name. A typo would
        # surface as a runtime error on the ingest path rather than here.
        existing = {
            r[0]
            for r in db.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
            )
        }
        # Module-schema types are reached through their Table, never the bare name
        # (`_module_table`), so they are checked against the model instead.
        from app.services.integration_reference_service import _module_table

        module_types = {t for t in SUPPORTED_ENTITY_TYPES if _module_table(t) is not None}
        assert module_types == {"billing_documents"}
        assert _module_table("billing_documents").fullname == "finance.billing_documents"
        from app.services.integration_reference_service import _TABLE_OF

        # A type that points at another type's rows is checked as that type's table.
        assert SUPPORTED_ENTITY_TYPES - module_types - set(_TABLE_OF) <= existing
        assert set(_TABLE_OF.values()) <= existing

    def test_unknown_entity_type_is_rejected_on_link(self, svc):
        with pytest.raises(UnsupportedEntityType):
            svc.link(entity_type="nonsense", entity_id="x", source_ref="ZZT-1")

    def test_unknown_entity_type_is_rejected_on_lookup(self, svc):
        with pytest.raises(UnsupportedEntityType):
            svc.resolve(entity_type="nonsense", source_ref="ZZT-1")

    def test_a_sql_injection_attempt_is_rejected_not_executed(self, db, svc):
        # entity_type reaches a table name and arrives from an ingest payload.
        with pytest.raises(UnsupportedEntityType):
            svc.resolve(entity_type="products; DROP TABLE products; --", source_ref="ZZT-1")
        # On Postgres this assertion is worth making: the sqlite version dropped
        # a stub table nobody cared about.
        assert db.execute(text("SELECT to_regclass('public.products')")).scalar() is not None


class TestLinking:
    def test_link_records_the_mapping(self, db, svc):
        pid = _product(db)
        ref = svc.link(entity_type="products", entity_id=pid, source_ref="ZZT-100")

        assert ref.entity_type == "products"
        assert ref.entity_id == pid
        assert ref.source_ref == "ZZT-100"
        assert ref.source_system == "autocount"

    def test_relinking_the_same_source_ref_updates_in_place(self, db, svc):
        # AC-AC-12 idempotency rests on this: a re-push must not duplicate.
        pid = _product(db)
        svc.link(entity_type="products", entity_id=pid, source_ref="ZZT-100")
        svc.link(entity_type="products", entity_id=pid, source_ref="ZZT-100")

        assert _refs(db).count() == 1

    def test_doc_no_is_stored_for_display_but_is_not_the_key(self, db, svc):
        # AC-AC-22: DocNo is mutable (AutoCount exposes NewDocNo), so a rename
        # must still resolve to the same local row.
        pid = _product(db)
        svc.link(
            entity_type="products", entity_id=pid, source_ref="ZZT-100", source_doc_no="PO-0001"
        )
        svc.link(
            entity_type="products", entity_id=pid, source_ref="ZZT-100", source_doc_no="PO-9999"
        )

        assert svc.resolve(entity_type="products", source_ref="ZZT-100") == pid
        assert _refs(db).one().source_doc_no == "PO-9999"

    def test_records_which_integration_wrote_it(self, db, svc):
        pid = _product(db)
        integration_id = _integration(db)
        ref = svc.link(
            entity_type="products",
            entity_id=pid,
            source_ref="ZZT-1",
            integration_id=integration_id,
        )
        db.flush()
        assert ref.integration_id == integration_id

    def test_the_integration_must_actually_exist(self, db, svc):
        # integration_id is a real FK to integrations.id. The sqlite version of
        # this test passed the string "int-9" and was perfectly happy, so it
        # asserted nothing about a column Postgres types as uuid.
        pid = _product(db)
        with pytest.raises(Exception):
            svc.link(
                entity_type="products",
                entity_id=pid,
                source_ref="ZZT-2",
                integration_id=str(uuid.uuid4()),
            )
            db.flush()

    def test_same_source_ref_may_exist_for_different_entity_types(self, db, svc):
        # DocKey is only unique within an AutoCount entity, so a product and an
        # order can legitimately share the value.
        pid, oid = _product(db), _order(db)

        svc.link(entity_type="products", entity_id=pid, source_ref="ZZT-SHARED")
        svc.link(entity_type="orders", entity_id=oid, source_ref="ZZT-SHARED")

        assert _refs(db).filter(IntegrationReference.source_ref == "ZZT-SHARED").count() == 2


class TestUniqueness:
    def test_two_local_rows_cannot_claim_the_same_source_ref(self, db, svc):
        # Otherwise one AutoCount document maps to two Sorento records and the
        # next sync silently updates whichever it finds first.
        a, b = _product(db), _product(db)
        svc.link(entity_type="products", entity_id=a, source_ref="ZZT-1")
        with pytest.raises(Exception):
            svc.link(entity_type="products", entity_id=b, source_ref="ZZT-1")
            db.flush()

    def test_one_local_row_cannot_have_two_source_refs(self, db, svc):
        # A record has one origin. Two would make "where did this come from?"
        # unanswerable and ownership rules ambiguous.
        pid = _product(db)
        svc.link(entity_type="products", entity_id=pid, source_ref="ZZT-1")
        with pytest.raises(Exception):
            svc.link(entity_type="products", entity_id=pid, source_ref="ZZT-2")
            db.flush()


class TestResolution:
    def test_resolves_a_source_ref_to_its_local_id(self, db, svc):
        pid = _product(db)
        svc.link(entity_type="products", entity_id=pid, source_ref="ZZT-100")
        assert svc.resolve(entity_type="products", source_ref="ZZT-100") == pid

    def test_unknown_source_ref_resolves_to_none(self, svc):
        assert svc.resolve(entity_type="products", source_ref="ZZT-never-seen") is None

    def test_origin_of_a_linked_record_is_reported(self, db, svc):
        pid = _product(db)
        svc.link(entity_type="products", entity_id=pid, source_ref="ZZT-100")
        origin = svc.origin_of(entity_type="products", entity_id=pid)
        assert origin is not None
        assert origin.source_system == "autocount"

    def test_absence_of_a_reference_means_locally_created(self, db, svc):
        # The design decision that removes the backfill: no row == manual.
        # ~110k existing records would otherwise need a reference saying nothing.
        pid = _product(db)
        assert svc.origin_of(entity_type="products", entity_id=pid) is None
        assert svc.is_externally_sourced(entity_type="products", entity_id=pid) is False


class TestIdentifierTyping:
    """Most consumed tables key on Postgres ``uuid``, but entity_id is varchar
    because it addresses many tables with differing key types.

    Only meaningful on Postgres. Under sqlite every id was a VARCHAR anyway, so
    these assertions held trivially and proved nothing.
    """

    def test_a_uuid_input_round_trips_as_its_string_form(self, db, svc):
        pid = uuid.UUID(_product(db))
        svc.link(entity_type="products", entity_id=pid, source_ref="ZZT-UUID")

        resolved = svc.resolve(entity_type="products", source_ref="ZZT-UUID")
        assert resolved == str(pid)
        # The trap this pins: a caller holding the UUID object must compare as
        # strings. Believing these differ would make ingest treat an existing
        # record as new and create the duplicate the table exists to prevent.
        assert resolved != pid
        assert str(resolved) == str(pid)

    def test_the_string_id_still_matches_a_real_uuid_column(self, db, svc):
        # The comparison the service actually performs against a uuid-keyed
        # table. A varchar/uuid mismatch would raise here, not silently miss.
        pid = _product(db)
        svc.link(entity_type="products", entity_id=pid, source_ref="ZZT-UUID3")
        assert svc.resolve(entity_type="products", source_ref="ZZT-UUID3") == pid

    def test_origin_lookup_accepts_either_form(self, db, svc):
        pid = _product(db)
        svc.link(entity_type="products", entity_id=pid, source_ref="ZZT-UUID2")

        assert svc.origin_of(entity_type="products", entity_id=uuid.UUID(pid)) is not None
        assert svc.origin_of(entity_type="products", entity_id=pid) is not None


class TestOrphanHandling:
    def test_a_reference_to_a_deleted_record_does_not_resolve(self, db, svc):
        # Nothing cascades in a polymorphic table, so this is the guard that
        # stops ingest 'updating' a row that no longer exists.
        pid = _product(db)
        svc.link(entity_type="products", entity_id=pid, source_ref="ZZT-100")
        db.execute(text("DELETE FROM products WHERE id = :i"), {"i": pid})
        db.flush()

        assert svc.resolve(entity_type="products", source_ref="ZZT-100") is None

    def test_the_orphaned_reference_is_cleaned_up_on_discovery(self, db, svc):
        # Lazily, on the path that notices -- not via a scheduled sweep, since
        # the scheduler is opt-in and defaults off in this codebase.
        pid = _product(db)
        svc.link(entity_type="products", entity_id=pid, source_ref="ZZT-100")
        db.execute(text("DELETE FROM products WHERE id = :i"), {"i": pid})
        db.flush()

        svc.resolve(entity_type="products", source_ref="ZZT-100")
        assert _refs(db).count() == 0

    def test_cleanup_frees_the_source_ref_for_relinking(self, db, svc):
        # Re-importing a deleted document must succeed rather than tripping the
        # unique index on a reference nobody can reach.
        pid = _product(db)
        svc.link(entity_type="products", entity_id=pid, source_ref="ZZT-100")
        db.execute(text("DELETE FROM products WHERE id = :i"), {"i": pid})
        db.flush()
        svc.resolve(entity_type="products", source_ref="ZZT-100")

        replacement = _product(db)
        svc.link(entity_type="products", entity_id=replacement, source_ref="ZZT-100")
        assert svc.resolve(entity_type="products", source_ref="ZZT-100") == replacement

    def test_unlink_removes_the_mapping(self, db, svc):
        pid = _product(db)
        svc.link(entity_type="products", entity_id=pid, source_ref="ZZT-100")
        svc.unlink(entity_type="products", entity_id=pid)
        assert _refs(db).count() == 0
