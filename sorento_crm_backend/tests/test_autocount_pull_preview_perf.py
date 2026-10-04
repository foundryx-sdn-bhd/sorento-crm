"""RED tests for the AutoCount pull preview perf lane (T1-T8, round 2 T9-T11).

Plan: documentation/plans/autocount/PLAN-autocount-pull-preview-perf.md
UAC:  documentation/plans/autocount/autocount-pull-preview-perf-acceptance-criteria.md

Nothing here exists yet: `MasterIngestService._diff` returning `{}`/populated on a
REAL (non dry-run) run, `_update` being skipped when the diff is empty, the
per-batch reference cache in `product_rules.ensure_reference` /
`resolve_default_uom`, and `_apply_products`' own `unchanged` summary count are
ALL the coder's deliverable for this slice.

Round 2 (captain-authorised, the C1-C3 clone measurement still missed the
<=90s target and a cProfile pass named the actual dominant cost): T9-T11 pin
`MasterIngestService`'s new per-batch bulk preload for products - one pass
over the batch's own codes/refs at the start of `ingest()`, instead of the
same `resolve_master_by_code`/`IntegrationReferenceService.resolve`/
`origin_of` queries running once per record. T9/T10 are RED until the
preload exists at all; T11 (the failed-insert guard, T6's own analogue for
this new map) is a guard - it exercises the SAME rollback-cleanup shape T6
already proves for the C2 reference cache, just for the new code-to-id map.

Substrate: `tests._pg_fixture.blank_session()` (Postgres, own blank schema, own
seeded chain) for every test except T4, which drives `app.tasks.
autocount_pull_tasks._apply_products` directly and therefore reuses the
`task_db` / `_FakeFoundryX` / `_patch_foundryx` / `_canonical_row` / `_header`
substrate from `tests.test_autocount_pull_sr1` and `_seed_apply_job` /
`_set_rows_page` / `_job_rows` from `tests.test_autocount_pull_sr3` - both
proven fixtures, imported rather than copied.

Company/category/brand/uom/product chains are seeded fresh per test (own
company anchor `DEFAULT_COMPANY_ID`, own category/brand/uom/product rows) -
CI's database starts empty.

Confirmed by an actual red run (22 Sep 2026, `sorento_buc_ci`): T1, T2, T3, T4,
T5 and T8 are RED today, each for the feature this plan is about, not a fixture
bug:

* T1 - a real re-push of an unchanged product still issues one
  `UPDATE ... products SET updated_by=..., updated_at=...`; `_update` is not
  yet skipped on an empty diff.
* T2 - `RecordResult.diff` is `None` on a real (non dry-run) ingest; `_diff`
  still early-returns `None` unless `self._dry_run`.
* T3 - same root cause as T2, pinned for both the changed and unchanged case.
* T4 - `_apply_products`' summary counts the unchanged record inside
  `updated` (2, not 1) and has no `unchanged` key at all; the outcome writer
  also gives it a "Product updated" row today.
* T5 - `product_categories`/`brands` are each queried twice for two records
  sharing one code (once per record) - no per-batch reference cache exists
  yet in `product_rules.ensure_reference`.
* T8 - a 5-row dry-run of an all-unchanged batch issues 5 `UPDATE products`
  statements before rolling them all back at the very end - the whole
  perf problem this plan cuts.

T6 and T7 are GUARD tests: both already pass today, unaffected by this slice,
and are kept as regression fences for the coder's cache (T6: a category
created by a record that then fails must never be handed to the next record
from a cache; T7: adoption still links the reference even when nothing about
the row changes).
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from decimal import Decimal

import pytest
from sqlalchemy import event, text

from app.models.procurement import ProductSupplier, Supplier
from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure
from app.services.audit_service import register_audit_listeners
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.integration_reference_service import IntegrationReferenceService
from app.services.master_ingest_service import IngestOutcome, MasterIngestService
from app.services.rules import product_rules

from tests._pg_fixture import blank_session, unique_code
from tests.test_autocount_pull_sr1 import (  # noqa: F401 - task_db is a fixture
    _FakeFoundryX,
    _canonical_row,
    _header,
    _patch_foundryx,
    task_db,
)
from tests.test_autocount_pull_sr3 import _job_rows, _seed_apply_job, _set_rows_page

MARKER = "PVPERF"


@pytest.fixture(autouse=True)
def _listeners():
    """Both global listener sets, exactly as the app registers them at startup.

    Registered explicitly (rather than relying on some other test file having
    already imported `app.main` first in this process) so T1/T2/T8's audit
    assertions are meaningful whether this file runs alone or as part of the
    full suite.
    """
    register_company_scope_listeners()
    register_audit_listeners()


@pytest.fixture()
def db():
    with blank_session() as session:
        yield session


@contextmanager
def _capture_sql(db):
    """Every statement issued on `db`'s own connection while the block runs."""
    calls: list[str] = []
    connection = db.get_bind()

    def _capture(conn, cursor, statement, *_a, **_kw):
        calls.append(statement)

    event.listen(connection, "before_cursor_execute", _capture)
    try:
        yield calls
    finally:
        event.remove(connection, "before_cursor_execute", _capture)


def _row(**overrides) -> dict:
    code = overrides.pop("code", None) or unique_code(MARKER)
    defaults = {
        "source_ref": f"{MARKER}:{code}:{uuid.uuid4().hex[:6]}",
        "code": code,
        "name": f"{MARKER} name {code}",
        "description": f"{MARKER} description {code}",
        "category_code": unique_code(f"{MARKER}CAT"),
        "uom_code": unique_code(f"{MARKER}UOM"),
        "brand_code": unique_code(f"{MARKER}BRD"),
        "list_price": "10.00",
        "is_active": True,
    }
    defaults.update(overrides)
    return defaults


#: `blank_session()` runs every table through a `schema_translate_map`, so the
#: real SQL SQLAlchemy emits against the test schema names the table
#: `zzs_blank_<pid>_<hex>.products`, never bare `products` - matching helpers
#: below tolerate an optional `<schema>.` prefix so they see the same
#: statement shape this suite emits AND the unqualified one production's
#: default-schema connection emits. A previous, bare-`startswith("update
#: products")` version silently matched nothing against the qualified name and
#: passed for the wrong reason - caught by inspecting the raw captured SQL
#: before trusting any of these tests as red or green.
import re


def _update_statements(calls: list[str]) -> list[str]:
    pattern = re.compile(r"^\s*update\s+(?:\S+\.)?products\b", re.IGNORECASE)
    return [s for s in calls if pattern.match(s)]


def _audit_insert_statements(calls: list[str]) -> list[str]:
    pattern = re.compile(r"^\s*insert\s+into\s+(?:\S+\.)?audit_logs\b", re.IGNORECASE)
    return [s for s in calls if pattern.match(s)]


def _select_statements(calls: list[str], table: str) -> list[str]:
    pattern = re.compile(
        rf"^\s*select\b.*\bfrom\s+(?:\S+\.)?{re.escape(table)}\b",
        re.IGNORECASE | re.DOTALL,
    )
    return [s for s in calls if pattern.match(s)]


def _product_row(db, product_id) -> dict:
    return dict(
        db.execute(
            text(
                "SELECT description, list_price, updated_at, updated_by "
                "FROM products WHERE id = :id"
            ),
            {"id": product_id},
        ).mappings().first()
    )


def _audit_count(db, product_id) -> int:
    # Product.__audit_entity_type__ = "product" (singular) overrides the
    # __tablename__ default every other entity's audit rows use.
    return db.execute(
        text("SELECT count(*) FROM audit_logs WHERE entity_type = 'product' AND entity_id = :id"),
        {"id": str(product_id)},
    ).scalar()


# =================================================================== T1 / T3
class TestUnchangedRealIngestIsANoWrite:
    """PP-1: a real (non dry-run) ingest of a product whose incoming columns
    all equal the stored row must not touch it at all."""

    def test_t1_unchanged_real_ingest_issues_no_update_no_audit_row(self, db):
        row = _row()
        user_1 = str(uuid.uuid4())
        user_2 = str(uuid.uuid4())

        created = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID, stamp_user_id=user_1).ingest(
            "products", [row]
        )
        assert created.created == 1, created.as_dict()
        product_id = created.records[0].entity_id
        db.commit()

        before = _product_row(db, product_id)
        audit_before = _audit_count(db, product_id)

        with _capture_sql(db) as calls:
            again = MasterIngestService(
                db, company_id=DEFAULT_COMPANY_ID, stamp_user_id=user_2
            ).ingest("products", [dict(row)])

        entry = again.records[0]
        assert entry.outcome is IngestOutcome.UPDATED, entry
        assert _update_statements(calls) == [], calls

        after = _product_row(db, product_id)
        assert after["updated_at"] == before["updated_at"], (before, after)
        assert str(after["updated_by"]) == user_1, after
        assert _audit_count(db, product_id) == audit_before


class TestDiffOnRealRun:
    """PP-3: `RecordResult.diff` is populated on real (non dry-run) runs too -
    `{}` for unchanged, a field map for changed."""

    def test_t3_diff_empty_when_unchanged_populated_when_changed(self, db):
        row = _row()
        MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest("products", [row])
        db.commit()

        unchanged = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest(
            "products", [dict(row)]
        )
        assert unchanged.records[0].diff == {}, unchanged.records[0].diff

        changed_row = dict(row)
        changed_row["list_price"] = "99.99"
        changed = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest(
            "products", [changed_row]
        )
        entry = changed.records[0]
        assert entry.diff, entry.diff
        assert entry.diff["list_price"]["incoming"] == Decimal("99.99"), entry.diff


# ========================================================================= T2
class TestChangedRealIngestStillWrites:
    """PP-2: a real ingest of a product with at least one differing column
    still updates it, bumps `updated_at`, stamps `updated_by` and writes the
    audit row - the ordinary write path must survive the T1 optimisation."""

    def test_t2_changed_real_ingest_updates_bumps_stamps_and_audits(self, db):
        row = _row()
        user_1 = str(uuid.uuid4())
        user_2 = str(uuid.uuid4())

        created = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID, stamp_user_id=user_1).ingest(
            "products", [row]
        )
        product_id = created.records[0].entity_id
        db.commit()

        changed_row = dict(row)
        changed_row["description"] = f"{row['description']} CHANGED"

        with _capture_sql(db) as calls:
            again = MasterIngestService(
                db, company_id=DEFAULT_COMPANY_ID, stamp_user_id=user_2
            ).ingest("products", [changed_row])

        entry = again.records[0]
        assert entry.outcome is IngestOutcome.UPDATED, entry
        assert entry.diff is not None and "description" in entry.diff, entry.diff
        assert entry.diff["description"]["incoming"] == changed_row["description"], entry.diff

        assert len(_update_statements(calls)) == 1, calls

        after = _product_row(db, product_id)
        assert after["description"] == changed_row["description"], after
        assert str(after["updated_by"]) == user_2, after

        audit_updates = db.execute(
            text(
                "SELECT count(*) FROM audit_logs WHERE entity_type = 'product' "
                "AND entity_id = :id AND action = 'UPDATE'"
            ),
            {"id": str(product_id)},
        ).scalar()
        assert audit_updates == 1, audit_updates


# ========================================================================= T5
class TestReferenceCachePerBatch:
    """PP-5: category / brand lookups are resolved once per distinct code per
    batch, not once per record."""

    def test_t5_category_and_brand_resolved_once_for_two_sharing_records(self, db):
        cat = ProductCategory(
            category_code=unique_code(MARKER), category_name="cat", company_id=DEFAULT_COMPANY_ID
        )
        brand = Brand(
            brand_code=unique_code(MARKER), brand_name="brand", company_id=DEFAULT_COMPANY_ID
        )
        uom = UnitOfMeasure(
            uom_code=unique_code(MARKER), uom_name="uom", company_id=DEFAULT_COMPANY_ID
        )
        db.add_all([cat, brand, uom])
        db.flush()
        db.commit()

        rows = [
            _row(category_code=cat.category_code, brand_code=brand.brand_code, uom_code=uom.uom_code),
            _row(category_code=cat.category_code, brand_code=brand.brand_code, uom_code=uom.uom_code),
        ]

        with _capture_sql(db) as calls:
            result = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest("products", rows)

        assert result.created == 2, result.as_dict()
        category_selects = _select_statements(calls, "product_categories")
        brand_selects = _select_statements(calls, "brands")
        assert len(category_selects) == 1, category_selects
        assert len(brand_selects) == 1, brand_selects


# ========================================================================= T6
class TestFailedRecordNeverPoisonsTheCache:
    """PP-6: a reference CREATED inside a record that then fails must never be
    served to the next record from the cache - the create was rolled back
    with the rest of that record's savepoint, so the id is dead.

    GUARD: already green today (no cache exists yet to poison) - kept as a
    regression fence for the coder's per-batch cache."""

    def test_t6_category_created_by_a_failed_record_is_resolved_fresh_by_the_next(
        self, db, monkeypatch
    ):
        existing_cat = ProductCategory(
            category_code=unique_code(MARKER), category_name="existing", company_id=DEFAULT_COMPANY_ID
        )
        uom = UnitOfMeasure(
            uom_code=unique_code(MARKER), uom_name="uom", company_id=DEFAULT_COMPANY_ID
        )
        db.add_all([existing_cat, uom])
        db.flush()

        db.commit()

        shared_new_cat_code = unique_code(f"{MARKER}NEWCAT")
        # Record A: a new product whose category is created + flushed inside its own
        # savepoint, then a synthetic post-write failure (the old cross-source
        # ReferenceConflict no longer exists for products - owner rulings, 3 Oct).
        record_a = _row(category_code=shared_new_cat_code, uom_code=uom.uom_code)
        # Record B: a genuinely new product, sharing the SAME (now rolled-back)
        # category code - must resolve/create it again, never be handed A's
        # dead id.
        record_b = _row(category_code=shared_new_cat_code, uom_code=uom.uom_code)

        real_hooks = MasterIngestService._post_write_product_hooks
        calls = {"n": 0}

        def _flaky_hooks(self, entity_type, product_id):
            calls["n"] += 1
            if calls["n"] == 1:
                raise Exception("synthetic T6 failure")
            return real_hooks(self, entity_type, product_id)

        monkeypatch.setattr(MasterIngestService, "_post_write_product_hooks", _flaky_hooks)

        result = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest(
            "products", [record_a, record_b]
        )

        entry_a, entry_b = result.records
        assert entry_a.outcome is IngestOutcome.FAILED, entry_a
        assert entry_b.outcome is IngestOutcome.CREATED, entry_b

        cat_row = db.execute(
            text("SELECT id FROM product_categories WHERE category_code = :c"),
            {"c": shared_new_cat_code},
        ).mappings().first()
        assert cat_row is not None, "record B must have (re)created the category"

        product_b_row = db.execute(
            text("SELECT category_id FROM products WHERE id = :id"), {"id": entry_b.entity_id}
        ).mappings().first()
        assert str(product_b_row["category_id"]) == str(cat_row["id"])


# ========================================================================= T7
class TestAdoptionLinksEvenWithNoColumnChanges:
    """PP-7: adoption (ref miss, code hit, no origin) still links the
    reference even when nothing about the row actually changes.

    GUARD: already green today (`_link` runs unconditionally in the adopt
    branch) - kept as a regression fence against a C1 change that skips
    linking along with the write when the diff is empty."""

    def test_t7_adoption_by_code_on_an_unchanged_row_writes_no_reference(self, db):
        cat = ProductCategory(
            category_code=unique_code(MARKER), category_name="cat", company_id=DEFAULT_COMPANY_ID
        )
        uom = UnitOfMeasure(
            uom_code=unique_code(MARKER), uom_name="uom", company_id=DEFAULT_COMPANY_ID
        )
        db.add_all([cat, uom])
        db.flush()

        code = unique_code(MARKER)
        description = f"{MARKER} description {code}"
        product = Product(
            product_code=code,
            product_name=code,
            description=description,
            category_id=cat.id,
            base_uom_id=uom.id,
            list_price=Decimal("10.00"),
            company_id=DEFAULT_COMPANY_ID,
        )
        db.add(product)
        db.commit()

        new_ref = f"{MARKER}:{uuid.uuid4().hex[:8]}"
        row = {
            "source_ref": new_ref,
            "code": code,
            "name": code,
            "description": description,
            "list_price": "10.00",
        }

        result = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest("products", [row])

        entry = result.records[0]
        assert entry.outcome is IngestOutcome.UPDATED, entry
        assert entry.entity_id == str(product.id), entry

        # Ingest never writes a product reference now (owner ruling 2, 3 Oct).
        ref_row = db.execute(
            text(
                "SELECT entity_id FROM integration_references "
                "WHERE source_ref = :r AND entity_type = 'products'"
            ),
            {"r": new_ref},
        ).mappings().first()
        assert ref_row is None, "adoption by code must not write a product reference"

        after = db.execute(
            text("SELECT description, list_price FROM products WHERE id = :id"), {"id": product.id}
        ).mappings().first()
        assert after["description"] == description
        assert after["list_price"] == Decimal("10.00")


# ========================================================================= T8
class TestDryRunUnchangedBatchIsAlsoANoWrite:
    """PP-1's dry-run half: a preview of an all-unchanged batch must not
    issue writes it is only going to roll back anyway."""

    def test_t8_dry_run_of_five_unchanged_products_issues_no_writes(self, db):
        rows = [_row() for _ in range(5)]
        created = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest("products", rows)
        assert created.created == 5, created.as_dict()
        db.commit()

        with _capture_sql(db) as calls:
            preview = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest(
                "products", [dict(r) for r in rows], dry_run=True
            )

        assert preview.dry_run is True
        assert all(r.outcome is IngestOutcome.UPDATED for r in preview.records), preview.as_dict()
        assert all(r.diff == {} for r in preview.records), preview.as_dict()

        assert _update_statements(calls) == [], calls
        assert _audit_insert_statements(calls) == [], calls


# ========================================================================= T4
class TestApplyProductsSummaryDistinguishesUnchanged:
    """PP-4: the pull apply summary distinguishes `updated` from `unchanged`,
    and an unchanged record gets no "Product updated" outcome row."""

    def test_t4_one_changed_one_unchanged_one_created(self, task_db, monkeypatch):
        from app.models.job import ImportJob
        from app.tasks.autocount_pull_tasks import _apply_products

        db, _factory = task_db
        fake = _FakeFoundryX()
        _patch_foundryx(monkeypatch, fake, db)

        job_user = str(uuid.uuid4())
        created_code = f"{MARKER}-T4CREATED-{uuid.uuid4().hex[:6]}"
        changed_code = f"{MARKER}-T4CHANGED-{uuid.uuid4().hex[:6]}"
        unchanged_code = f"{MARKER}-T4UNCHANGED-{uuid.uuid4().hex[:6]}"

        changed_row = _canonical_row(changed_code, list_price="10.00")
        unchanged_row = _canonical_row(unchanged_code, list_price="20.00")

        # Seed the "changed" and "unchanged" products with a REAL prior ingest -
        # `unchanged_row` will be re-pushed byte-identical below, `changed_row`
        # with one different value.
        MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest(
            "products", [changed_row, unchanged_row]
        )
        db.commit()

        changed_row_pushed = dict(changed_row)
        changed_row_pushed["list_price"] = "15.00"
        created_row = _canonical_row(created_code)

        rows = [created_row, changed_row_pushed, dict(unchanged_row)]
        snapshot_id = f"{MARKER}-snap-{uuid.uuid4().hex[:8]}"
        fake.status = (200, {**_header(rows), "snapshotId": snapshot_id})
        _set_rows_page(fake, snapshot_id, rows)

        job_id = _seed_apply_job(
            db, user_id=job_user, company_id=DEFAULT_COMPANY_ID, entity="products",
            snapshot_id=snapshot_id,
        )
        job = db.query(ImportJob).filter(ImportJob.id == job_id).first()

        summary = _apply_products(db, job, snapshot_id)

        assert summary["created"] == 1, summary
        assert summary["updated"] == 1, summary
        assert summary["unchanged"] == 1, summary
        assert summary["retryable"] == 0, summary

        written = _job_rows(db, job_id)
        outcomes = sorted(w["outcome"] for w in written)
        assert outcomes == ["created", "updated"], written


def _code_resolution_selects(calls: list[str]) -> list[str]:
    """The bulk (or, pre-round-2, per-record) `upper(btrim(product_code))`
    match `resolve_master_by_code`/the new batch preload both use - distinct
    in shape from `_read_product_row`'s plain `WHERE id = :id` SELECT (C3,
    round 1), which never mentions `product_code` inside an `upper(btrim(`
    call at all."""
    pattern = re.compile(r"upper\(\s*btrim\(", re.IGNORECASE)
    return [
        s for s in calls
        if pattern.search(s) and re.search(r"product_code", s, re.IGNORECASE)
    ]


# ========================================================================= T9
class TestBulkCodePreloadIsOneSelect:
    """PP-9 round 2: code resolution for the WHOLE batch is one SELECT, not
    one per record - the lever the round-1 clone measurement's own profile
    named (see the PR body)."""

    def test_t9_three_existing_products_issue_one_code_resolution_select(self, db):
        rows = [_row() for _ in range(3)]
        created = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest("products", rows)
        assert created.created == 3, created.as_dict()
        db.commit()

        with _capture_sql(db) as calls:
            again = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest(
                "products", [dict(r) for r in rows]
            )

        assert all(r.outcome is IngestOutcome.UPDATED for r in again.records), again.as_dict()
        assert len(_code_resolution_selects(calls)) == 1, calls



# ======================================================================== T10
class TestBulkCodePreloadSeesSameBatchCreates:
    """PP-9 round 2: a product CREATED earlier in the same batch is found by
    a later duplicate-code record through the preload MAP (`_insert`
    maintains it) - never an EXTRA per-record code-resolution query for that
    later record specifically, and never a second CREATE."""

    def test_t10_later_duplicate_code_record_adopts_via_the_map_not_a_query(self, db):
        code = unique_code(MARKER)
        record_a = _row(code=code, source_ref=f"{MARKER}:A:{uuid.uuid4().hex[:6]}")
        record_b = _row(code=code, source_ref=f"{MARKER}:B:{uuid.uuid4().hex[:6]}")

        with _capture_sql(db) as calls:
            result = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest(
                "products", [record_a, record_b]
            )

        entry_a, entry_b = result.records
        assert entry_a.outcome is IngestOutcome.CREATED, entry_a
        assert entry_b.outcome is IngestOutcome.UPDATED, entry_b
        assert entry_b.entity_id == entry_a.entity_id, (entry_a, entry_b)

        # TWO code-resolution SELECTs for the whole batch: the upfront
        # preload (which found nothing for either record - the code did not
        # exist yet) plus record A's own adopt-check, equally unavoidable
        # (nothing could have preloaded a code before A itself created it).
        # NOT three: record B's own adopt-by-code match comes from the map
        # `_insert` updated when A was processed - a third SELECT here would
        # mean B queried for itself too, exactly what the map exists to skip.
        assert len(_code_resolution_selects(calls)) == 2, calls

        product_count = db.execute(
            text("SELECT count(*) FROM products WHERE upper(btrim(product_code)) = :c"),
            {"c": code.upper()},
        ).scalar()
        assert product_count == 1, "must never create a second product for one code"


# ======================================================================== T11
class TestFailedInsertNeverPoisonsTheCodePreloadMap:
    """PP-9 round 2: T6's own guard, re-run for the NEW code-to-id preload map
    instead of `product_rules.ensure_reference`'s cache - a product CREATED
    by a record whose savepoint later rolls back must never be served to the
    next same-code record from the map.

    The post-`_insert` failure is injected (`monkeypatch`) rather than
    triggered through a naturally-occurring conflict: unlike a master
    reference (T6), a product's own post-insert `_link` conflict paths are
    already pre-empted by the EARLIER `resolve()` lookup (same entity_type,
    same source_ref, checked before create/adopt is ever decided) - by
    design, so there is no organic "insert succeeds, source_ref conflicts
    right after" case for THIS entity_type to construct. The fault injection
    isolates exactly the thing this test is about: whatever fails after
    `_insert`, the savepoint rollback and the map cleanup must stay in sync.
    """

    def test_t11_rolled_back_insert_leaves_no_code_map_entry(self, db, monkeypatch):
        from app.services.master_ingest_service import MasterIngestService as _MIS

        shared_new_code = unique_code(f"{MARKER}NEWCODE")
        record_a = _row(code=shared_new_code)
        record_b = _row(code=shared_new_code)

        real_hooks = _MIS._post_write_product_hooks
        calls = {"n": 0}

        def _flaky_hooks(self, entity_type, product_id):
            calls["n"] += 1
            if calls["n"] == 1:
                # record_a only - a synthetic post-insert failure (the product
                # `_link` step is gone, owner ruling 2, 3 Oct), injected where
                # the rollback revert of the preload `code_to_id` map matters.
                raise Exception("synthetic T11 failure")
            return real_hooks(self, entity_type, product_id)

        monkeypatch.setattr(_MIS, "_post_write_product_hooks", _flaky_hooks)

        result = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest(
            "products", [record_a, record_b]
        )

        entry_a, entry_b = result.records
        assert entry_a.outcome is IngestOutcome.FAILED, entry_a
        assert entry_b.outcome is IngestOutcome.CREATED, entry_b

        rows = db.execute(
            text("SELECT id FROM products WHERE upper(btrim(product_code)) = :c"),
            {"c": shared_new_code.upper()},
        ).mappings().all()
        assert len(rows) == 1, rows
        assert str(rows[0]["id"]) == entry_b.entity_id


# ============================================================ T12 (fix round)
class TestSecondSameBatchAdopterSeesTheFirstLink:
    """B1 (both reviewers, round-1 review): `_link` never wrote the ref it
    just created into `preload.origin_by_entity`, so a SECOND record in the
    same batch that resolves the SAME product by a normalize-equal code (a
    different casing/whitespace, a different source_ref) reads the STALE
    preloaded `None` origin, takes the unclaimed-adopt branch instead of the
    code-wins one, and its own `_link` call then raises `ReferenceConflict`
    against the ref record A just wrote - FAILED, where main (no preload)
    would have re-queried and seen the link.

    Also exercises B2 in the same scenario: once B1 is fixed, record B
    reaches `_post_write_product_hooks` for the SAME product id record A's
    hook just linked to the default supplier THIS batch - `default_supplier_
    lead_time.get(product_id)` (no sentinel) reads Python's bare `None`
    default and treats it as "confirmed no link", inserting a SECOND
    `product_suppliers` row and violating
    `uq_product_suppliers_product_id_supplier_id`.
    """

    def test_t12_two_records_same_normalized_code_one_batch(self, db):
        cat = ProductCategory(
            category_code=unique_code(MARKER), category_name="cat", company_id=DEFAULT_COMPANY_ID
        )
        uom = UnitOfMeasure(
            uom_code=unique_code(MARKER), uom_name="uom", company_id=DEFAULT_COMPANY_ID
        )
        db.add_all([cat, uom])
        db.flush()

        supplier = Supplier(
            supplier_code=unique_code(MARKER), supplier_name="T12 Supplier",
            company_id=DEFAULT_COMPANY_ID,
        )
        db.add(supplier)
        db.flush()

        code = unique_code(MARKER)
        description = f"{MARKER} description {code}"
        product = Product(
            product_code=code,
            product_name=code,
            description=description,
            category_id=cat.id,
            base_uom_id=uom.id,
            list_price=Decimal("10.00"),
            company_id=DEFAULT_COMPANY_ID,
        )
        db.add(product)
        db.commit()

        record_a = {
            "source_ref": f"{MARKER}:A:{uuid.uuid4().hex[:8]}",
            "code": code,
            "name": code,
            "description": description,
            "list_price": "10.00",
        }
        record_b = {
            "source_ref": f"{MARKER}:B:{uuid.uuid4().hex[:8]}",
            "code": f" {code.lower()} ",
            "name": code,
            "description": description,
            "list_price": "10.00",
        }

        result = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest(
            "products", [record_a, record_b]
        )

        entry_a, entry_b = result.records
        assert entry_a.outcome is IngestOutcome.UPDATED, entry_a
        assert entry_b.outcome is IngestOutcome.UPDATED, entry_b
        assert entry_a.entity_id == str(product.id)
        assert entry_b.entity_id == str(product.id)

        supplier_rows = db.execute(
            text("SELECT count(*) FROM product_suppliers WHERE product_id = :pid"),
            {"pid": str(product.id)},
        ).scalar()
        assert supplier_rows == 1, supplier_rows

        ref_rows = db.execute(
            text(
                "SELECT count(*) FROM integration_references "
                "WHERE entity_type = 'products' AND entity_id = :pid"
            ),
            {"pid": str(product.id)},
        ).scalar()
        assert ref_rows == 0, ref_rows  # ingest writes no product references


# ============================================================ T13 (fix round)
class TestCreatedThenDuplicateCodeSameBatch:
    """B2: a product CREATED earlier in the same batch, then adopted again by
    a later duplicate-code record via the preload's own `code_to_id` map
    (T10's own shape) - the SECOND record's `_post_write_product_hooks` must
    see the FIRST record's freshly-created `product_suppliers` link, not
    attempt a second insert."""

    def test_t13_created_then_duplicate_code_same_batch(self, db):
        supplier = Supplier(
            supplier_code=unique_code(MARKER), supplier_name="T13 Supplier",
            company_id=DEFAULT_COMPANY_ID,
        )
        db.add(supplier)
        db.commit()

        code = unique_code(MARKER)
        record_a = _row(code=code, source_ref=f"{MARKER}:A:{uuid.uuid4().hex[:8]}")
        record_b = _row(code=code, source_ref=f"{MARKER}:B:{uuid.uuid4().hex[:8]}")

        result = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest(
            "products", [record_a, record_b]
        )

        entry_a, entry_b = result.records
        assert entry_a.outcome is IngestOutcome.CREATED, entry_a
        assert entry_b.outcome is IngestOutcome.UPDATED, entry_b
        assert entry_b.entity_id == entry_a.entity_id

        supplier_rows = db.execute(
            text("SELECT count(*) FROM product_suppliers WHERE product_id = :pid"),
            {"pid": entry_a.entity_id},
        ).scalar()
        assert supplier_rows == 1, supplier_rows


# ============================================================ T14 (fix round)
class TestRenamedCodeIsANewProductNotARename:
    """B2: a product resolved by its SOURCE_REF (renamed code - the incoming
    code no longer matches the stored one, so the batch's own code preload
    never covers this id) already has an EXISTING `product_suppliers` row -
    `default_supplier_lead_time.get(product_id)` must fall through to the
    real per-record query rather than reading Python's bare `None` default
    as "confirmed no link" and inserting a duplicate."""

    def test_t14_renamed_code_creates_a_new_product_and_leaves_the_old_one(self, db):
        cat = ProductCategory(
            category_code=unique_code(MARKER), category_name="cat", company_id=DEFAULT_COMPANY_ID
        )
        uom = UnitOfMeasure(
            uom_code=unique_code(MARKER), uom_name="uom", company_id=DEFAULT_COMPANY_ID
        )
        db.add_all([cat, uom])
        db.flush()

        supplier = Supplier(
            supplier_code=unique_code(MARKER), supplier_name="T14 Supplier",
            company_id=DEFAULT_COMPANY_ID,
        )
        db.add(supplier)
        db.flush()

        old_code = unique_code(MARKER)
        description = f"{MARKER} description {old_code}"
        product = Product(
            product_code=old_code,
            product_name=old_code,
            description=description,
            category_id=cat.id,
            base_uom_id=uom.id,
            list_price=Decimal("10.00"),
            company_id=DEFAULT_COMPANY_ID,
        )
        db.add(product)
        db.flush()

        source_ref = f"{MARKER}:{uuid.uuid4().hex[:8]}"
        IntegrationReferenceService(db, company_id=DEFAULT_COMPANY_ID).link(
            entity_type="products", entity_id=str(product.id), source_ref=source_ref,
        )
        db.add(
            ProductSupplier(
                product_id=product.id, supplier_id=supplier.id, standard_lead_time_days=45
            )
        )
        db.commit()

        new_code = unique_code(f"{MARKER}RENAMED")
        row = {
            "source_ref": source_ref,
            "code": new_code,
            "name": new_code,
            "description": description,
            "list_price": "10.00",
            "category_code": cat.category_code,
        }

        result = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest("products", [row])

        entry = result.records[0]
        # Code is identity (owner rulings, 3 Oct): a renamed item is a NEW
        # product; the old one is untouched and keeps its single supplier link.
        assert entry.outcome is IngestOutcome.CREATED, entry
        assert entry.entity_id != str(product.id)
        old = db.execute(
            text("SELECT product_code FROM products WHERE id = :pid"), {"pid": str(product.id)}
        ).scalar()
        assert old == old_code

        supplier_rows = db.execute(
            text("SELECT standard_lead_time_days FROM product_suppliers WHERE product_id = :pid"),
            {"pid": str(product.id)},
        ).mappings().all()
        assert len(supplier_rows) == 1, supplier_rows


# ==================================================== Group 2 (fix round)
class TestAsDictDiffOnlyOnDryRun:
    """PP-10 (captain's contract ruling, fix round): `RecordResult.diff` is
    now populated on a REAL run too (T2/T3), but `IngestResult.as_dict()` -
    the shape `/api/v1/ingest/*` actually returns - must serialize it ONLY
    for a dry run, so a real push's wire response stays byte-identical to
    main."""

    def test_real_run_as_dict_carries_no_diff_key_dry_run_does(self, db):
        row = _row()
        MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest("products", [row])
        db.commit()

        changed_row = dict(row)
        changed_row["list_price"] = "77.00"
        real = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest(
            "products", [changed_row]
        )
        assert real.dry_run is False
        assert real.records[0].diff, real.records[0].diff  # populated in-process
        assert "diff" not in real.as_dict()["records"][0], real.as_dict()

        preview_row = dict(changed_row)
        preview_row["list_price"] = "88.00"
        preview = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest(
            "products", [preview_row], dry_run=True
        )
        assert preview.dry_run is True
        assert "diff" in preview.as_dict()["records"][0], preview.as_dict()


# ================================================== Group 3 (fix round)
class TestPreloadFailureRecoversViaRollback:
    """Group 3: `_build_product_preload`'s own except swallowed the
    exception but left the session's transaction ABORTED (a real DB error,
    not a Python one, leaves Postgres refusing every further statement until
    a ROLLBACK) - every record's own `self.db.begin_nested()` then raised
    `PendingRollbackError`, which is UNCAUGHT there and killed the whole
    batch, not just the preload optimisation."""

    def test_preload_db_error_recovers_and_the_batch_still_ingests(self, db, monkeypatch):
        rows = [_row() for _ in range(2)]

        real_resolve = product_rules.resolve_default_supplier_id
        calls = {"n": 0}

        def _broken_once_then_real(db, settings):
            calls["n"] += 1
            if calls["n"] == 1:
                # A genuine Postgres error (division by zero), not a Python
                # one - exactly what actually aborts a transaction; a bare
                # `raise ValueError(...)` would not reproduce the bug this
                # pins. Only the FIRST call (inside the preload build) fails
                # - `_post_write_product_hooks`'s own per-record fallback
                # call to this same function must succeed normally, or this
                # test would be pinning ITS OWN monkeypatch, not the preload
                # recovery.
                db.execute(text("SELECT 1/0"))
                return None
            return real_resolve(db, settings)

        monkeypatch.setattr(product_rules, "resolve_default_supplier_id", _broken_once_then_real)

        result = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest("products", rows)
        assert result.created == 2, result.as_dict()


class TestRefCacheResetPerBatch:
    """Group 3: `self._ref_cache` must be a FRESH dict per `ingest()` call -
    `MasterIngestService` instances are already one-per-batch in every real
    caller, but this pins the reset explicitly so a future caller that
    re-runs `ingest()` on the SAME instance can never see a stale id from an
    earlier, unrelated batch."""

    def test_ref_cache_resets_between_two_ingest_calls_on_one_instance(self, db):
        # A brand-new category/uom/brand is CREATED, never cached (only a
        # FOUND reference is - see `ensure_reference`'s own docstring), so
        # the batch needs an EXISTING one to actually populate the cache.
        cat = ProductCategory(
            category_code=unique_code(MARKER), category_name="cat", company_id=DEFAULT_COMPANY_ID
        )
        uom = UnitOfMeasure(
            uom_code=unique_code(MARKER), uom_name="uom", company_id=DEFAULT_COMPANY_ID
        )
        db.add_all([cat, uom])
        db.commit()

        service = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID)
        service.ingest(
            "products", [_row(category_code=cat.category_code, uom_code=uom.uom_code)]
        )
        assert service._ref_cache, "the first batch should have cached something"

        service._ref_cache[("sentinel", "marker", "zzt-should-not-survive")] = "dead"
        service.ingest(
            "products", [_row(category_code=cat.category_code, uom_code=uom.uom_code)]
        )
        assert ("sentinel", "marker", "zzt-should-not-survive") not in service._ref_cache


class TestDiscontinuedWatermarkInPreviewDiff:
    """Group 3 (both reviewers): a discontinued -> live product's diff must
    show the watermark reset (`discontinued_notified_at` /
    `discontinued_notify_batch_id`), or an operator's preview silently omits
    a real write the real sync makes."""

    def test_dry_run_diff_names_the_discontinued_watermark_reset(self, db):
        row = _row(description=f"**** {MARKER} discontinued model")
        created = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest("products", [row])
        product_id = created.records[0].entity_id
        db.commit()

        db.execute(
            text(
                "UPDATE products SET discontinued_notified_at = now(), "
                "discontinued_notify_batch_id = :b WHERE id = :id"
            ),
            {"b": str(uuid.uuid4()), "id": product_id},
        )
        db.commit()

        live_row = dict(row)
        live_row["description"] = f"{MARKER} back in stock"

        preview = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest(
            "products", [live_row], dry_run=True
        )
        diff = preview.records[0].diff
        assert diff is not None
        assert "discontinued_notified_at" in diff, diff
        assert diff["discontinued_notified_at"]["incoming"] is None, diff


class TestDefaultUomResolvedOncePerBatch:
    """Group 3: three records with a BLANK `uom_code` and a configured
    `system_settings.default_uom_id` must resolve that default ONCE for the
    batch, not once per record - `resolve_default_uom`'s own `system_settings`
    read is already cached via `_system_settings()`; this pins the
    `units_of_measure` VALIDATION query too."""

    def test_three_blank_uom_records_issue_one_units_of_measure_select(self, db):
        cat = ProductCategory(
            category_code=unique_code(MARKER), category_name="cat", company_id=DEFAULT_COMPANY_ID
        )
        configured_uom = UnitOfMeasure(
            uom_code=unique_code(MARKER), uom_name="Configured", company_id=DEFAULT_COMPANY_ID
        )
        db.add_all([cat, configured_uom])
        db.flush()

        from app.models.user import SystemSetting

        settings = db.query(SystemSetting).first()
        if settings is None:
            settings = SystemSetting(id=str(uuid.uuid4()))
            db.add(settings)
        settings.default_uom_id = configured_uom.id
        db.commit()

        rows = [
            _row(category_code=cat.category_code, uom_code="")
            for _ in range(3)
        ]

        with _capture_sql(db) as calls:
            result = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest(
                "products", rows
            )

        assert result.created == 3, result.as_dict()
        assert all(r.entity_id for r in result.records)
        uom_rows = db.execute(
            text("SELECT base_uom_id FROM products WHERE id::text = ANY(:ids)"),
            {"ids": [r.entity_id for r in result.records]},
        ).mappings().all()
        assert all(str(row["base_uom_id"]) == str(configured_uom.id) for row in uom_rows)

        uom_selects = _select_statements(calls, "units_of_measure")
        assert len(uom_selects) == 1, calls


# ============================================== T9b/T15 (Group 4, fix round)
class TestBulkCodePreloadCoversUnlinkedExistingProducts:
    """T9 strengthened (reviewer kill test): the round-1 T9 only pins the
    "already synced, resync" shape, where the origin preload ALSO answers
    without a query. This is the "ref miss, code hit" shape - a product that
    exists locally but was never actually linked - which is the one the
    adopt branch's own code lookup exists for; without the code preload map
    this costs 1 (batch) + 3 (per record) SELECTs instead of 1."""

    def test_t9b_three_unlinked_existing_products_still_issue_one_code_select(self, db):
        cat = ProductCategory(
            category_code=unique_code(MARKER), category_name="cat", company_id=DEFAULT_COMPANY_ID
        )
        uom = UnitOfMeasure(
            uom_code=unique_code(MARKER), uom_name="uom", company_id=DEFAULT_COMPANY_ID
        )
        db.add_all([cat, uom])
        db.flush()

        products = []
        for _ in range(3):
            code = unique_code(MARKER)
            product = Product(
                product_code=code,
                product_name=code,
                description=f"{MARKER} description {code}",
                category_id=cat.id,
                base_uom_id=uom.id,
                list_price=Decimal("5.00"),
                company_id=DEFAULT_COMPANY_ID,
            )
            db.add(product)
            products.append(product)
        db.commit()

        rows = [
            {
                "source_ref": f"{MARKER}:{uuid.uuid4().hex[:8]}",
                "code": p.product_code,
                "name": p.product_code,
                "description": p.description,
                "list_price": "5.00",
            }
            for p in products
        ]

        with _capture_sql(db) as calls:
            result = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest(
                "products", rows
            )

        assert all(r.outcome is IngestOutcome.UPDATED for r in result.records), result.as_dict()
        assert len(_code_resolution_selects(calls)) == 1, calls


class TestBulkCodePreloadChunking:
    """T15: with the chunk size forced down to 2, a 5-record batch still
    preloads EVERY code (3 bulk SELECTs, ceil(5/2)) and issues zero
    per-record code-resolution fallback queries."""

    def test_t15_chunked_preload_covers_every_code_zero_per_record_selects(self, db, monkeypatch):
        monkeypatch.setattr(MasterIngestService, "_PRELOAD_CHUNK_SIZE", 2)

        cat = ProductCategory(
            category_code=unique_code(MARKER), category_name="cat", company_id=DEFAULT_COMPANY_ID
        )
        uom = UnitOfMeasure(
            uom_code=unique_code(MARKER), uom_name="uom", company_id=DEFAULT_COMPANY_ID
        )
        db.add_all([cat, uom])
        db.flush()

        products = []
        for _ in range(5):
            code = unique_code(MARKER)
            product = Product(
                product_code=code,
                product_name=code,
                description=f"{MARKER} description {code}",
                category_id=cat.id,
                base_uom_id=uom.id,
                list_price=Decimal("5.00"),
                company_id=DEFAULT_COMPANY_ID,
            )
            db.add(product)
            products.append(product)
        db.commit()

        rows = [
            {
                "source_ref": f"{MARKER}:{uuid.uuid4().hex[:8]}",
                "code": p.product_code,
                "name": p.product_code,
                "description": p.description,
                "list_price": "5.00",
            }
            for p in products
        ]

        with _capture_sql(db) as calls:
            result = MasterIngestService(db, company_id=DEFAULT_COMPANY_ID).ingest(
                "products", rows
            )

        assert all(r.outcome is IngestOutcome.UPDATED for r in result.records), result.as_dict()
        # ceil(5/2) = 3 chunked bulk SELECTs, and (the whole point) zero
        # per-record fallback queries - every code was covered by the preload.
        assert len(_code_resolution_selects(calls)) == 3, calls
