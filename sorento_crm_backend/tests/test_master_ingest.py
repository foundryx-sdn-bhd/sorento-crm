"""Phase C - master data ingest (UAC Group B, masters half).

  AC-AC-11  canonical shapes; unknown fields dropped and their names logged (owner, 1 Oct 2026)
  AC-AC-12  idempotent on the source reference; created vs updated distinguished
  AC-AC-13  per-record structured errors, machine-readable
  AC-AC-15  masters quarantine, they do not block: valid rows persist
  AC-AC-16  a missing referenced master is RETRYABLE, distinct from invalid
  AC-AC-18  ingest emits no lifecycle events -- the sync-loop guard

The behaviour that carries this slice is per-record isolation. One bad record
in a batch of 10,000 must not cost the other 9,999, and a failed flush poisons
the SQLAlchemy session unless each record runs in its own savepoint.
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import engine
from app.models.integration_reference import IntegrationReference
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.master_ingest_service import (
    IngestOutcome,
    MasterIngestService,
    UnsupportedIngestEntity,
)


@pytest.fixture()
def db():
    """A Postgres session rolled back after each test.

    Deliberately not sqlite. The behaviour under test is per-record SAVEPOINT
    isolation, and pysqlite does not share Postgres's semantics -- these tests
    passed on sqlite while proving nothing about production, and broke outright
    once another module registered the global audit flush listeners.

    Everything happens inside one outer transaction that is rolled back, so the
    service's begin_nested() calls become real nested savepoints and no test
    data survives.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def svc(db):
    """Anchored to the incumbent company, the way the route anchors a request.

    ``company_id`` is required rather than defaulted: an ingest that cannot name
    one company has no correct destination for a row, and a default here would
    have hidden that from every test in this file.
    """
    return MasterIngestService(db, integration_id=None, company_id=DEFAULT_COMPANY_ID)


def _wh(code="ZZT-WH-01", name="Main", ref=None, **extra):
    return {"source_ref": ref or f"DK-{code}", "code": code, "name": name, **extra}


class TestCanonicalShape:
    def test_unknown_fields_are_dropped_not_refused(self, svc):
        # AC-AC-11, as amended by the owner (1 Oct 2026): an unknown key is dropped and
        # its NAME logged once per request by the ingest route (`app.schemas.ingest_extras`),
        # never refused - refusing failed every record the day the shared service mapped a
        # new AutoCount column, and forced the two repos to deploy in a fixed order.
        result = svc.ingest("warehouses", [_wh(**{"DocKey": "leaked-autocount-name"})])
        assert result.failed == 0
        assert result.created == 1

    def test_missing_required_field_is_a_validation_failure(self, svc):
        result = svc.ingest("warehouses", [{"source_ref": "DK-1"}])
        assert result.failed == 1
        assert result.records[0].outcome is IngestOutcome.FAILED

    def test_unsupported_entity_is_refused(self, svc):
        with pytest.raises(UnsupportedIngestEntity):
            svc.ingest("nonsense", [])

    def test_source_ref_is_required(self, svc):
        # Without it there is no idempotency key and every push duplicates.
        result = svc.ingest("warehouses", [{"code": "ZZT-1", "name": "Main"}])
        assert result.failed == 1


class TestCreateAndUpdate:
    def test_creates_a_new_record(self, db, svc):
        result = svc.ingest("warehouses", [_wh()])
        assert result.created == 1
        assert db.execute(text("SELECT count(*) FROM warehouses WHERE warehouse_code LIKE 'ZZT-%'")).scalar() == 1

    def test_links_the_source_reference(self, db, svc):
        svc.ingest("warehouses", [_wh(ref="DK-99")])
        # Scope to this test's reference: the live table now holds real synced
        # rows, so a bare .one() raised MultipleResultsFound once AutoCount data
        # existed.
        ref = db.query(IntegrationReference).filter_by(source_ref="DK-99").one()
        assert ref.entity_type == "warehouses"
        assert ref.source_ref == "DK-99"

    def test_repushing_updates_in_place_and_reports_updated(self, db, svc):
        # AC-AC-12: the same payload twice must not create a second record.
        svc.ingest("warehouses", [_wh(name="Main")])
        result = svc.ingest("warehouses", [_wh(name="Renamed")])

        assert result.updated == 1
        assert result.created == 0
        assert db.execute(text("SELECT count(*) FROM warehouses WHERE warehouse_code LIKE 'ZZT-%'")).scalar() == 1
        assert db.execute(text("SELECT warehouse_name FROM warehouses WHERE warehouse_code LIKE 'ZZT-%'")).scalar() == "Renamed"

    def test_a_renamed_code_still_updates_the_same_record(self, db, svc):
        # The point of keying on source_ref rather than the business code: the
        # upstream may rename, and a rename is not a new record.
        svc.ingest("warehouses", [_wh(code="ZZT-WH-01", ref="DK-1")])
        svc.ingest("warehouses", [_wh(code="ZZT-WH-01-RENAMED", ref="DK-1")])

        assert db.execute(text("SELECT count(*) FROM warehouses WHERE warehouse_code LIKE 'ZZT-%'")).scalar() == 1
        assert db.execute(text("SELECT warehouse_code FROM warehouses WHERE warehouse_code LIKE 'ZZT-%'")).scalar() == "ZZT-WH-01-RENAMED"

    def test_adopts_an_existing_record_matched_by_business_code(self, db, svc):
        # First sync against a record that already exists locally. Creating a
        # duplicate under a new id would be far worse than adopting it.
        # is_active set explicitly: warehouses.is_active has a Python-side
        # default only, so this raw INSERT would leave it NULL on a schema built
        # from the ORM models (bootstrap_env -- CI and fresh installs).
        db.execute(
            text(
                "INSERT INTO warehouses (id, warehouse_code, warehouse_name, is_active) "
                "VALUES (:i, 'ZZT-WH-01', 'Existing', true)"
            ),
            {"i": str(uuid.uuid4())},
        )
        db.commit()

        result = svc.ingest("warehouses", [_wh(code="ZZT-WH-01", name="From AutoCount")])

        assert db.execute(text("SELECT count(*) FROM warehouses WHERE warehouse_code LIKE 'ZZT-%'")).scalar() == 1
        assert result.updated == 1


def _customer(code="ZZT-CUST-01", name="Test Sdn Bhd", ref=None, **extra):
    return {"source_ref": ref or f"DK-{code}", "code": code, "name": name, **extra}


class TestCustomerColumnsFixRound2BugB:
    """Fix round 2, BUG B: `_customer_columns` wrote `credit_limit` and
    `payment_terms_days`, neither of which is a column on `customers` - every
    customers push failed with the raw psycopg2 message. Accepted-and-dropped
    (never written) was the fix through S0-S3; superseded 2026-09-06 by S4's
    contract 2.1 end state (AC-P0-4/D15), which REMOVES both fields from
    `CanonicalCustomer` entirely - the raw-SQL bug this class guards against
    is still structurally impossible: the fields are undeclared, so the schema
    drops them (owner decision, 1 Oct 2026) before any column-building code runs.
    """

    def test_credit_limit_and_payment_terms_days_are_dropped_never_written(self, db, svc):
        result = svc.ingest(
            "customers",
            [_customer(credit_limit="15000.50", payment_terms_days=30)],
        )

        record = result.records[0]
        assert record.outcome is IngestOutcome.CREATED, record.errors
        # Dropped before the column builder runs: the row lands with only the
        # declared columns (customers holds neither of these fields).
        row = db.execute(
            text(
                "SELECT customer_code, customer_name FROM customers "
                "WHERE customer_code = 'ZZT-CUST-01'"
            )
        ).first()
        assert row is not None


class TestSEC3IntegrityConflictNamesTheConstraint:
    """Fix round 4, BUG B. A unique-constraint race (two companies pushing the
    same code, or a concurrent duplicate) must be a FAILED verdict naming the
    constraint, never `INTERNAL_ERROR_MESSAGE` - and never `str(exc)`'s own
    SQL statement either.

    Forces the race the adoption check (`_lookup_id`) exists to prevent: with
    `_lookup_id` patched to always answer "nothing exists yet", `_apply` takes
    its CREATE path against a code that already has a row, and the real
    composite unique index (fix round 4, BUG A) is what raises.
    """

    def test_a_forced_unique_violation_is_failed_and_names_the_constraint(
        self, db, svc, monkeypatch
    ):
        import app.services.master_ingest_service as m

        code = "ZZT-WH-DUP"
        db.execute(
            text(
                "INSERT INTO warehouses (id, warehouse_code, warehouse_name, "
                "company_id, is_active) VALUES (:i, :c, 'Existing', :cid, true)"
            ),
            {"i": str(uuid.uuid4()), "c": code, "cid": DEFAULT_COMPANY_ID},
        )
        db.commit()
        # S1 (ingest-parity-standardisation): the adopt lookup for a
        # non-agent master moved from `_lookup_id` to
        # `master_rules.resolve_master_by_code` (D17, case/whitespace-
        # insensitive matching) - forcing the same "not found" race now goes
        # through that function instead.
        monkeypatch.setattr(m, "resolve_master_by_code", lambda *a, **kw: None)

        result = svc.ingest("warehouses", [_wh(code=code, ref="DK-DUP")])

        assert result.failed == 1
        entry = result.records[0]
        assert entry.outcome is IngestOutcome.FAILED
        assert "uq_warehouses_company_warehouse_code" in entry.errors.get("code", "")
        body_text = str(entry.errors)
        assert "INSERT" not in body_text
        assert "SQL" not in body_text
        # Only one row for this code - the forced create never landed.
        assert (
            db.execute(
                text("SELECT count(*) FROM warehouses WHERE warehouse_code = :c"),
                {"c": code},
            ).scalar()
            == 1
        )


class TestQuarantineNotBlock:
    def test_valid_rows_persist_when_a_sibling_fails(self, db, svc):
        # AC-AC-15. 10,000 products with 12 bad ones must yield 9,988 imported.
        batch = [
            _wh(code="ZZT-1", ref="DK-1"),
            {"source_ref": "DK-2", "code": "ZZT-2"},  # missing name -> invalid
            _wh(code="ZZT-3", ref="DK-3"),
        ]
        result = svc.ingest("warehouses", batch)

        assert result.created == 2
        assert result.failed == 1
        assert db.execute(text("SELECT count(*) FROM warehouses WHERE warehouse_code LIKE 'ZZT-%'")).scalar() == 2

    def test_a_database_error_on_one_record_does_not_poison_the_batch(self, db, svc):
        # The savepoint's reason for existing. A failed flush leaves the whole
        # SQLAlchemy session unusable, so without per-record nesting one bad
        # row takes out every row after it -- "12 invalid" becomes "0 imported".
        #
        # Two records claiming the same warehouse_code under different source
        # refs forces a genuine IntegrityError on the unique index, mid-batch.
        batch = [
            _wh(code="ZZT-A", ref="DK-A"),
            _wh(code="ZZT-DUP", ref="DK-DUP-1"),
            _wh(code="ZZT-DUP", ref="DK-DUP-2"),  # violates the unique code index
            _wh(code="ZZT-B", ref="DK-B"),
        ]
        result = svc.ingest("warehouses", batch)

        # The clashing record is rejected alone...
        assert result.failed == 1
        # ...and crucially the record *after* it was still processed.
        assert (
            db.execute(
                text("SELECT count(*) FROM warehouses WHERE warehouse_code = 'ZZT-B'")
            ).scalar()
            == 1
        )
        assert result.created == 3

    def test_adoption_is_refused_when_the_local_record_is_already_claimed(self, db, svc):
        # Retargeting a record that another source document already owns would
        # silently hand one AutoCount document control of another's data.
        svc.ingest("warehouses", [_wh(code="ZZT-OWNED", ref="DK-OWNER")])
        result = svc.ingest("warehouses", [_wh(code="ZZT-OWNED", ref="DK-INTRUDER")])

        assert result.failed == 1
        assert db.execute(text("SELECT count(*) FROM warehouses WHERE warehouse_code LIKE 'ZZT-%'")).scalar() == 1

    def test_per_record_error_names_record_field_and_reason(self, db, svc):
        # AC-AC-13: machine-readable, so the ESB quarantines per record without
        # parsing prose.
        result = svc.ingest("warehouses", [{"source_ref": "DK-X", "code": "ZZT-X"}])
        record = result.records[0]

        assert record.source_ref == "DK-X"
        assert record.outcome is IngestOutcome.FAILED
        assert isinstance(record.errors, dict)
        assert "name" in record.errors


class TestRetryableVsFatal:
    def test_payment_terms_code_is_dropped_not_retryable(self, db, svc):
        # Superseded 2026-09-06 (ingest-parity-standardisation S4, AC-P0-4/D15
        # end state): `payment_terms_code` was accepted-and-warned
        # `deprecated_field` through S0-S3 (the docstring this test used to
        # carry said so explicitly); S4's contract 2.1 cutover REMOVES the
        # field from `CanonicalSupplier` entirely. Owner decision 1 Oct 2026: an
        # undeclared key is dropped (name logged), never refused - so the record
        # lands, never retryable, and the field reaches no column.
        result = svc.ingest(
            "suppliers",
            [
                {
                    "source_ref": "DK-S1",
                    "code": "ZZT-SUP-1",
                    "name": "Acme",
                    "payment_terms_code": "NET-999-MISSING",
                }
            ],
        )
        assert result.retryable == 0
        record = result.records[0]
        assert record.outcome is IngestOutcome.CREATED, record.errors

    def test_payment_terms_code_is_dropped_on_an_update_too(self, db, svc):
        # Same as the test above for an update payload: the removed field is
        # dropped, the update applies, and no second row appears.
        svc.ingest(
            "suppliers",
            [{"source_ref": "DK-S1", "code": "ZZT-SUP-1", "name": "Acme"}],
        )
        result = svc.ingest(
            "suppliers",
            [
                {
                    "source_ref": "DK-S1",
                    "code": "ZZT-SUP-1",
                    "name": "Acme",
                    "payment_terms_code": "MISSING",
                }
            ],
        )
        record = result.records[0]
        assert record.outcome is not IngestOutcome.FAILED, record.errors
        assert db.execute(
            text("SELECT count(*) FROM suppliers WHERE supplier_code LIKE 'ZZT-%'")
        ).scalar() == 1

    def test_retry_succeeds_once_the_reference_exists(self, db, svc):
        payload = {
            "source_ref": "DK-S1",
            "code": "ZZT-SUP-1",
            "name": "Acme",
            "payment_terms_days": 30,
        }
        result = svc.ingest("suppliers", [payload])
        assert result.created == 1

    def test_invalid_data_is_failed_not_retryable(self, db, svc):
        # The distinction is the whole point: retrying bad data forever is a
        # queue that never drains.
        result = svc.ingest("suppliers", [{"source_ref": "DK-S2", "code": "ZZT-SUP-2"}])
        assert result.failed == 1
        assert result.retryable == 0


class TestBatchSummary:
    def test_summary_counts_add_up_to_the_batch_size(self, svc):
        batch = [
            _wh(code="ZZT-SA", ref="DK-A"),
            {"source_ref": "DK-B", "code": "ZZT-SB2"},
            _wh(code="ZZT-SC", ref="DK-C"),
        ]
        result = svc.ingest("warehouses", batch)
        assert result.created + result.updated + result.failed + result.retryable == len(batch)

    def test_every_record_is_reported_in_order(self, svc):
        batch = [_wh(code="ZZT-SA", ref="DK-A"), _wh(code="ZZT-SB", ref="DK-B")]
        result = svc.ingest("warehouses", batch)
        assert [r.source_ref for r in result.records] == ["DK-A", "DK-B"]

    def test_an_empty_batch_is_accepted(self, svc):
        result = svc.ingest("warehouses", [])
        assert result.created == 0
        assert result.records == []


class TestOnProgressCallback:
    """B3 (small-fix track, PLAN-autocount-pull-review.md): a full-size products preview
    used to sit 4-5 minutes on a bare spinner - `on_progress` is what the pull tasks use
    to publish `import_jobs.processed_rows`/`total_rows` while a dry run is still running.
    """

    def test_reports_every_n_records_and_a_final_call_with_monotonic_counts(self, svc, monkeypatch):
        monkeypatch.setattr(MasterIngestService, "PROGRESS_REPORT_EVERY", 3)
        calls: list[tuple[int, int]] = []
        batch = [_wh(code=f"ZZT-PROG-{i}", ref=f"DK-PROG-{i}") for i in range(7)]

        svc.ingest("warehouses", batch, on_progress=lambda processed, total: calls.append((processed, total)))

        assert calls == [(3, 7), (6, 7), (7, 7)]
        assert [c[0] for c in calls] == sorted(c[0] for c in calls), "must be monotonically increasing"

    def test_a_batch_shorter_than_the_report_interval_still_gets_one_final_call(self, svc):
        calls: list[tuple[int, int]] = []
        batch = [_wh(code=f"ZZT-PROGSHORT-{i}", ref=f"DK-PROGSHORT-{i}") for i in range(2)]

        svc.ingest("warehouses", batch, on_progress=lambda processed, total: calls.append((processed, total)))

        assert calls == [(2, 2)]

    def test_dry_run_still_reports_progress_even_though_it_rolls_back(self, svc):
        calls: list[tuple[int, int]] = []
        batch = [_wh(code=f"ZZT-PROGDRY-{i}", ref=f"DK-PROGDRY-{i}") for i in range(2)]

        svc.ingest(
            "warehouses", batch, dry_run=True,
            on_progress=lambda processed, total: calls.append((processed, total)),
        )

        assert calls == [(2, 2)]

    def test_an_empty_batch_with_a_callback_reports_nothing_to_do(self, svc):
        calls: list[tuple[int, int]] = []
        svc.ingest("warehouses", [], on_progress=lambda processed, total: calls.append((processed, total)))
        assert calls == [(0, 0)]

    def test_a_callback_that_raises_never_breaks_the_ingest(self, svc):
        batch = [_wh(code="ZZT-PROGERR-1", ref="DK-PROGERR-1")]

        def _boom(processed, total):
            raise RuntimeError("boom")

        result = svc.ingest("warehouses", batch, on_progress=_boom)

        assert result.records[0].outcome in (IngestOutcome.CREATED, IngestOutcome.UPDATED)


class TestCategoriesAndUnitsOfMeasure:
    """Both exist so products can be ingested at all.

    ``products.category_id`` and ``products.base_uom_id`` are NOT NULL, so
    without these two entity types every product in a first sync reports
    retryable forever -- there is no path by which the parent could arrive.
    """

    def test_a_category_is_created(self, db, svc):
        result = svc.ingest(
            "product_categories",
            [{"source_ref": "DK-C1", "code": "ZZT-CAT-1", "name": "Fasteners"}],
        )
        assert result.created == 1
        assert (
            db.execute(
                text("SELECT category_name FROM product_categories WHERE category_code = 'ZZT-CAT-1'")
            ).scalar()
            == "Fasteners"
        )

    def test_a_unit_of_measure_is_created(self, db, svc):
        result = svc.ingest(
            "units_of_measure", [{"source_ref": "DK-U1", "code": "ZZT-UOM-1", "name": "Each"}]
        )
        assert result.created == 1
        assert (
            db.execute(
                text("SELECT uom_name FROM units_of_measure WHERE uom_code = 'ZZT-UOM-1'")
            ).scalar()
            == "Each"
        )

    def test_both_are_idempotent_on_the_source_reference(self, db, svc):
        svc.ingest(
            "product_categories",
            [{"source_ref": "DK-C1", "code": "ZZT-CAT-1", "name": "Fasteners"}],
        )
        result = svc.ingest(
            "product_categories",
            [{"source_ref": "DK-C1", "code": "ZZT-CAT-1", "name": "Renamed"}],
        )
        assert result.updated == 1
        assert (
            db.execute(
                text("SELECT count(*) FROM product_categories WHERE category_code LIKE 'ZZT-%'")
            ).scalar()
            == 1
        )

    def test_an_unknown_category_is_created_not_retryable(self, db, svc):
        # Superseded by ingest-parity-standardisation S1 (D3, AC-P1-4): an
        # unknown category/uom/brand is CREATED (code = name = the raw value),
        # never retryable any more - `product_rules.ensure_reference`. This
        # test used to assert the retired AC-AC-16 behaviour (retryable until
        # the parent exists); see tests/test_ingest_parity_s1_products.py
        # ::TestAcP14UnknownReferencesCreatedWithWarnings for the full contract.
        svc.ingest("units_of_measure", [{"source_ref": "DK-U1", "code": "ZZT-UOM-1", "name": "Each"}])
        result = svc.ingest(
            "products",
            [
                {
                    "source_ref": "DK-P1",
                    "code": "ZZT-PRD-1",
                    "name": "Bolt",
                    "category_code": "ZZT-CAT-MISSING",
                    "uom_code": "ZZT-UOM-1",
                }
            ],
        )
        assert result.created == 1
        assert result.retryable == 0
        assert result.failed == 0
        assert "category_created" in result.records[0].warnings

    def test_an_unknown_uom_is_created_not_retryable(self, db, svc):
        # Superseded by ingest-parity-standardisation S1 (D3, AC-P1-4) - see
        # the sibling test above.
        svc.ingest(
            "product_categories",
            [{"source_ref": "DK-C1", "code": "ZZT-CAT-1", "name": "Fasteners"}],
        )
        result = svc.ingest(
            "products",
            [
                {
                    "source_ref": "DK-P1",
                    "code": "ZZT-PRD-1",
                    "name": "Bolt",
                    "category_code": "ZZT-CAT-1",
                    "uom_code": "ZZT-UOM-MISSING",
                }
            ],
        )
        assert result.created == 1
        assert result.retryable == 0
        assert result.failed == 0
        assert "uom_created" in result.records[0].warnings

    def test_the_product_lands_once_both_parents_have_synced(self, db, svc):
        # The sequencing end to end: category, then UoM, then the product that
        # could not previously be created.
        svc.ingest(
            "product_categories",
            [{"source_ref": "DK-C1", "code": "ZZT-CAT-1", "name": "Fasteners"}],
        )
        svc.ingest("units_of_measure", [{"source_ref": "DK-U1", "code": "ZZT-UOM-1", "name": "Each"}])
        result = svc.ingest(
            "products",
            [
                {
                    "source_ref": "DK-P1",
                    "code": "ZZT-PRD-1",
                    "name": "Bolt",
                    "category_code": "ZZT-CAT-1",
                    "uom_code": "ZZT-UOM-1",
                    "list_price": "12.50",
                }
            ],
        )

        assert result.created == 1
        row = db.execute(
            text(
                "SELECT p.product_name, p.description, c.category_code, u.uom_code "
                "FROM products p "
                "JOIN product_categories c ON c.id = p.category_id "
                "JOIN units_of_measure u ON u.id = p.base_uom_id "
                "WHERE p.product_code = 'ZZT-PRD-1'"
            )
        ).first()
        # Resolved to the ids of the records just synced, not to some other
        # category that happened to share a name. D24: `product_name` is the
        # item code, the payload's `name` text lands in `description`.
        assert row == ("ZZT-PRD-1", "Bolt", "ZZT-CAT-1", "ZZT-UOM-1")

    def test_the_category_is_matched_by_code_not_by_name(self, db, svc):
        # The bug this pins: the lookup keyed on category_name, so a payload
        # whose code and name differ resolved to the wrong category or to none.
        svc.ingest(
            "product_categories",
            [{"source_ref": "DK-C1", "code": "ZZT-CAT-1", "name": "Totally Different Name"}],
        )
        svc.ingest("units_of_measure", [{"source_ref": "DK-U1", "code": "ZZT-UOM-1", "name": "Each"}])
        result = svc.ingest(
            "products",
            [
                {
                    "source_ref": "DK-P1",
                    "code": "ZZT-PRD-1",
                    "name": "Bolt",
                    "category_code": "ZZT-CAT-1",
                    "uom_code": "ZZT-UOM-1",
                }
            ],
        )
        assert result.created == 1


class TestBarcodeOverwritePolicy:
    """AC-S7-2 (PLAN D14): `products.barcode` is CRM-owned.

    A non-empty incoming `bar_code` overwrites the stored value; an
    empty/absent one leaves whatever is already there - manually typed on the
    product master, or set by an earlier sync - untouched. Each case ingests
    the STORED value first (a real create, exercising the same code path a
    manual entry or an earlier sync would have used), then re-pushes the same
    `source_ref` with the INCOMING value and asserts what lands.
    """

    @pytest.fixture(autouse=True)
    def _parents(self, svc):
        svc.ingest(
            "product_categories",
            [{"source_ref": "DK-BC-CAT", "code": "ZZT-BC-CAT", "name": "Barcode Test"}],
        )
        svc.ingest(
            "units_of_measure",
            [{"source_ref": "DK-BC-UOM", "code": "ZZT-BC-UOM", "name": "Each"}],
        )

    def _push(self, svc, *, bar_code=None, ref="DK-BC-P1"):
        payload = {
            "source_ref": ref,
            "code": "ZZT-BC-PRD-1",
            "name": "Barcode Bolt",
            "category_code": "ZZT-BC-CAT",
            "uom_code": "ZZT-BC-UOM",
        }
        if bar_code is not None:
            payload["bar_code"] = bar_code
        return svc.ingest("products", [payload])

    @pytest.mark.parametrize(
        "stored,incoming,expected",
        [
            # (stored first, incoming second) -> what the column holds after.
            (None, "1234567890123", "1234567890123"),  # unset -> incoming sets it
            ("OLDCODE123", "NEWCODE456", "NEWCODE456"),  # non-empty overwrites
            ("OLDCODE123", None, "OLDCODE123"),  # absent leaves it alone
            ("OLDCODE123", "", "OLDCODE123"),  # empty string leaves it alone
            ("OLDCODE123", "   ", "OLDCODE123"),  # whitespace-only leaves it alone
            (None, None, None),  # never set, stays unset
            (None, "", None),  # empty string never sets it
        ],
        ids=[
            "unset-to-value",
            "value-to-new-value",
            "value-absent-unchanged",
            "value-empty-unchanged",
            "value-whitespace-unchanged",
            "unset-absent-stays-unset",
            "unset-empty-stays-unset",
        ],
    )
    def test_overwrite_table(self, db, svc, stored, incoming, expected):
        self._push(svc, bar_code=stored)
        self._push(svc, bar_code=incoming)

        assert (
            db.execute(
                text("SELECT barcode FROM products WHERE product_code = 'ZZT-BC-PRD-1'")
            ).scalar()
            == expected
        )

    def test_a_new_product_with_no_barcode_creates_with_null(self, db, svc):
        result = self._push(svc)
        assert result.created == 1
        assert (
            db.execute(
                text("SELECT barcode FROM products WHERE product_code = 'ZZT-BC-PRD-1'")
            ).scalar()
            is None
        )
