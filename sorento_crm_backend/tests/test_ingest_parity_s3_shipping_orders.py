"""RED tests for ingest parity standardisation, Phase S3 (shipping order rules).

UAC: documentation/plans/autocount/ingest-parity-standardisation-acceptance-criteria.md
     Phase S3, AC-P3-1 .. AC-P3-8.
PLAN: documentation/plans/autocount/PLAN-ingest-parity-standardisation.md sections 2.5, 2.6.

Substrate: `blank_session()` throughout. Unlike Phase S2's outstanding-upload
tests, none of the drivers used here (`SPOAllocationService.upsert_allocation`,
`ShippingOrderIngestService`, `outstanding_import_service._spo_lines_to_close`)
touch `outstanding_reader`/`import_field_alias`, so no migration-seeded data
is needed.

Model facts verified in code before relying on them:

* `app/tasks/import_tasks.process_spo_import` (the RQ task) parses rows, then
  hands each `(product, warehouse)` group to
  `SPOAllocationService.upsert_allocation(allocation_data, user_id,
  forward_match=False)` - THIS is "the function it calls with parsed rows",
  reused directly here (matching `tests/test_spo_import_upsert.py`'s own
  pattern) rather than driving the whole RQ task. After the group loop, the
  task calls `forward_match_grn_lines_for_spo_best_effort` once per
  `(spo_number, company_id)` touched - CORRECT already on the xlsx path; only
  `ShippingOrderIngestService` (grep: zero hits for `forward_match`) lacks it.
* `_spo_import_extract_container` (`app/tasks/import_tasks.py`) is NARROWER
  than the shared function AC-P3-2 wants: it is "text after the first space"
  only, so `"F-WHSU8488069 (MOCHA)"` -> `"(MOCHA)"` (wrong) and
  `"TRHU4104785"` (no space at all) -> `None` (wrong, no fallback to the
  whole string). The golden-set test therefore targets the NEW shared
  `app.services.rules.shipping_order_rules.extract_container_number`, not
  this narrower existing function.
* `SPOAllocation` (`app/models/procurement.py`) has NO `container_number`
  column (grep, zero hits) - confirmed structurally.
* `get_inbound_shipment_by_container_number` (`app/api/v1/external/utils.py`)
  already exists and is reusable by both writers (case-insensitive, ordered
  by `created_at` for a non-unique container).
* `SPOAllocationService.upsert_allocation`'s existing-row query explicitly
  filters `SPOAllocation.source_system.is_(None)` - it can ONLY adopt a
  pre-existing xlsx-era row, NEVER one an ESB push wrote (`source_system=
  'autocount'`). `outstanding_import_service._spo_lines_to_close` filters
  `source_system == SPO_UPLOAD_SOURCE` for the same reason - an ESB-written
  open row is invisible to the close-by-absence sweep. Both are AC-P3-5.
* `ShippingOrderIngestService._line_values` never sets `inbound_shipment_id`
  at all - `CanonicalShippingOrderLine`/`CanonicalShippingOrder` have no
  container-bearing field yet, so there is nothing to resolve a shipment
  from.
* `ShippingOrderIngestService._write_row` does a blind `setattr` for every
  line column, including `allocated_quantity` and `quantity_received` (the
  latter recomputed from the PUSHED payload's own `qty_received`, default
  `0`) - there is no received-quantity guard on this path at all, unlike
  `SPOAllocationService.upsert_allocation`'s `AllocationReceivedGuardError`
  (already correct on the xlsx path).
* `document_ingest_service`'s existing D5 refusal
  (`doc_family(payload.po_number) == FAMILY_SPO` -> refuse, "push under
  shipping_orders") is PREFIX-ONLY; `CanonicalPurchaseOrder` has no
  `is_shipping_order` field, so a payload naming one fails on `extra="forbid"`
  today rather than being refused for the DOCUMENTED reason.
* `CanonicalShippingOrder.status` is still required
  (`_CanonicalDocument.status: str = Field(..., ...)`).
* **Chosen module for the two PLAN-2.5 pure functions this file cannot yet
  exercise behaviourally** (`link_allocation_to_shipment`,
  `relink_allocations_for_container`): `app.services.rules
  .shipping_order_rules`, per the coordinator's instruction - stated here so
  the choice is visible to the coder.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.inventory import Warehouse
from app.models.procurement import InboundShipment
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.schemas.procurement import SPOAllocationCreate
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.document_ingest_service import DocumentIngestService
from app.services.master_ingest_service import IngestOutcome
from app.services.procurement_service import SPOAllocationService
from app.services.shipping_order_ingest_service import ShippingOrderIngestService

from tests._pg_fixture import blank_session, unique_code

MARKER = "ZZTIP3"

_GENERIC_EXCLUDE = {
    "id", "company_id", "created_at", "updated_at", "created_by",
    # Source-tracking columns, expected to differ by design (the ESB stamps
    # them, the xlsx writer never does) - same reasoning as excluding `source`
    # itself in the S0/S1/S2 files.
    "source_system", "source_ref", "source_doc_ref",
}


@pytest.fixture()
def db():
    with blank_session() as session:
        yield session


def _code(stem: str) -> str:
    return unique_code(f"{MARKER}{stem}")[:30]


def _seed_product(db) -> tuple[str, str]:
    """A product plus its ESB integration reference. Returns (product_id, ref)."""
    from app.services.integration_reference_service import IntegrationReferenceService

    category = ProductCategory(category_code=_code("CAT"), category_name="Cat")
    uom = UnitOfMeasure(uom_code=_code("UOM"), uom_name="Each")
    db.add_all([category, uom])
    db.flush()
    product = Product(
        product_code=_code("PRD"), product_name="Item",
        category_id=category.id, base_uom_id=uom.id, list_price=0,
    )
    db.add(product)
    db.flush()
    ref = f"DK-{product.product_code}"
    # Anchored: plan D14 is strict, an unanchored link on a scoped type now
    # raises ValueError. Every caller of this helper works inside the
    # incumbent company.
    IntegrationReferenceService(db, company_id=DEFAULT_COMPANY_ID).link(
        entity_type="products", entity_id=product.id, source_ref=ref
    )
    return str(product.id), ref


def _shipping_order_svc(db, company_id=DEFAULT_COMPANY_ID) -> ShippingOrderIngestService:
    return ShippingOrderIngestService(db, integration_id=None, company_id=company_id)


class TestAcP31ContainerNumberColumn:
    """D6: `spo_allocations` gains `container_number VARCHAR(100) NULL`."""

    def test_spo_allocations_has_a_container_number_column(self):
        from app.models.procurement import SPOAllocation

        assert "container_number" in SPOAllocation.__table__.columns, (
            "S3 must add spo_allocations.container_number - confirmed absent "
            "by reading app/models/procurement.py"
        )


class TestAcP32ExtractContainerNumberAndLinking:
    """D6: one shared `extract_container_number`, used by both writers, that
    handles every real form; both writers store it and link
    `inbound_shipment_id` via a shared `link_allocation_to_shipment` when a
    shipment with that container exists."""

    GOLDEN_SET = [
        ("F-WHSU8488069 (MOCHA)", "WHSU8488069"),
        ("TRHU4104785", "TRHU4104785"),
        ("12/03/2026 TCLU1234567", "TCLU1234567"),
        ("", None),
        (None, None),
    ]

    def test_extract_container_number_golden_set(self):
        from app.services.rules.shipping_order_rules import extract_container_number

        for raw, expected in self.GOLDEN_SET:
            assert extract_container_number(raw) == expected, raw

    def test_link_allocation_to_shipment_exists(self):
        from app.services.rules.shipping_order_rules import link_allocation_to_shipment  # noqa: F401

    def test_esb_shipping_order_payload_accepts_container_number(self, db):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        _product_id, product_ref = _seed_product(db)
        spo_number = _code("SPO")
        svc = _shipping_order_svc(db)
        result = svc.ingest(
            "shipping_orders",
            [
                {
                    "source_ref": f"DK-{spo_number}",
                    "spo_number": spo_number,
                    "status": "open",
                    "container_number": "F-WHSU8488069 (MOCHA)",
                    "lines": [
                        {
                            "source_ref": f"DK-{spo_number}-L1",
                            "product_ref": product_ref, "product_code": product_ref[3:],
                            "qty_ordered": "5",
                        }
                    ],
                }
            ],
        )
        assert result.records[0].outcome is IngestOutcome.CREATED, result.records[0].errors


class TestAcP33RelinkOnLaterShipment:
    """D6: a nightly / on-shipment-create relink fills `inbound_shipment_id`
    on allocations whose `container_number` now matches a shipment that did
    not exist yet when the allocation was written. Cannot be exercised
    behaviourally (the column it operates on does not exist - AC-P3-1), so
    this is the pure-function existence check, same module choice as
    AC-P3-2's `link_allocation_to_shipment`."""

    def test_relink_allocations_for_container_does_not_exist_yet(self):
        from app.services.rules.shipping_order_rules import relink_allocations_for_container  # noqa: F401


class TestAcP34ForwardMatchOnceAndReceivedGuard:
    """D7: a non-dry ESB shipping_orders batch runs
    `forward_match_grn_lines_for_spo_best_effort` once per SPO number touched,
    end of batch; an allocation with `quantity_received > 0` is never reduced
    below it by an ESB push (`received_locked`), the rest of the document
    lands."""

    def test_non_dry_esb_shipping_orders_batch_calls_forward_match_once(self, db, monkeypatch):
        """Security review (blocker 2), 2026-09-06: the forward-match sweep
        moved OUT of `ShippingOrderIngestService.ingest()` and into the
        route's post-commit hook slot (`app.api.v1.external.ingest
        ._run_document_hooks`) - `forward_match_grn_lines_for_spo` commits on
        success and rolls back on failure, so running it BEFORE the route's
        own commit let one exception mid-batch discard every not-yet-
        committed record while the route still answered 200.

        Drives `ingest()` + `db.commit()` + the hook directly (the shape
        `tests/test_ingest_documents_v2_hooks.py` exercises through a full
        `env.post` HTTP round trip) rather than pulling in that file's
        company/auth scaffolding - the assertion here is only about WHEN the
        call happens, which does not need an HTTP layer to prove. The
        intermediate `assert not calls` right after `ingest()` is the actual
        regression pin: it fails again the moment forward-match is called
        back inside `ingest()` itself.
        """
        import app.services.grn_spo_matching as grn_spo_matching
        from app.api.v1.external.ingest import _run_document_hooks

        calls: list[Any] = []
        monkeypatch.setattr(
            grn_spo_matching,
            "forward_match_grn_lines_for_spo_best_effort",
            lambda *a, **kw: calls.append((a, kw)),
        )

        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        _product_id, product_ref = _seed_product(db)
        spo_number = _code("SPO")
        svc = _shipping_order_svc(db)
        result = svc.ingest(
            "shipping_orders",
            [
                {
                    "source_ref": f"DK-{spo_number}",
                    "spo_number": spo_number,
                    "status": "open",
                    "lines": [
                        {
                            "source_ref": f"DK-{spo_number}-L1",
                            "product_ref": product_ref, "product_code": product_ref[3:],
                            "qty_ordered": "5",
                        },
                        {
                            "source_ref": f"DK-{spo_number}-L2",
                            "product_ref": product_ref, "product_code": product_ref[3:],
                            "qty_ordered": "3",
                        },
                    ],
                }
            ],
        )
        assert result.records[0].outcome is IngestOutcome.CREATED, result.records[0].errors
        assert not calls, (
            "forward-match must not run before the route's own commit - "
            "ingest() alone must never trigger it any more"
        )

        db.commit()
        _run_document_hooks(db, "shipping_orders", svc, actor=None)

        assert calls, (
            "expected forward_match_grn_lines_for_spo_best_effort to be called "
            "by the route's post-commit hook after a non-dry shipping_orders batch"
        )
        assert len(calls) == 1, f"expected exactly one call (batch end), got {len(calls)}"

    def test_esb_push_never_reduces_allocation_below_received_quantity(self, db):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        _product_id, product_ref = _seed_product(db)
        spo_number = _code("SPO")
        line_ref = f"DK-{spo_number}-L1"
        svc = _shipping_order_svc(db)

        result = svc.ingest(
            "shipping_orders",
            [
                {
                    "source_ref": f"DK-{spo_number}",
                    "spo_number": spo_number,
                    "status": "open",
                    "lines": [
                        {"source_ref": line_ref, "product_ref": product_ref, "product_code": product_ref[3:], "qty_ordered": "10"}
                    ],
                }
            ],
        )
        assert result.records[0].outcome is IngestOutcome.CREATED, result.records[0].errors

        # 5 units already received via a GRN - out of scope for this test, so
        # seeded directly rather than run through the receiving flow.
        db.execute(
            text("UPDATE spo_allocations SET quantity_received = 5 WHERE source_ref = :r"),
            {"r": line_ref},
        )
        db.flush()

        result2 = svc.ingest(
            "shipping_orders",
            [
                {
                    "source_ref": f"DK-{spo_number}",
                    "spo_number": spo_number,
                    "status": "open",
                    "lines": [
                        {"source_ref": line_ref, "product_ref": product_ref, "product_code": product_ref[3:], "qty_ordered": "3"}
                    ],
                }
            ],
        )
        record = result2.records[0]
        assert record.outcome is IngestOutcome.UPDATED, record.errors
        assert "received_locked" in record.warnings, record.warnings
        row = db.execute(
            text(
                "SELECT allocated_quantity, quantity_received FROM spo_allocations "
                "WHERE source_ref = :r"
            ),
            {"r": line_ref},
        ).first()
        assert row == (10, 5), "a received-locked line must be left unchanged, not overwritten"


class TestAcP35AdoptionAndCloseSweepAcrossSources:
    """D11: one SPO writer identity - the xlsx import adopts an ESB-written
    allocation by `(spo_number, product, location)`, and the upload's
    close-by-absence sweep considers rows of both `source_system` values.
    Today `upsert_allocation`'s match query and `_spo_lines_to_close`'s
    candidate query both filter to xlsx-only rows."""

    def test_reupload_of_an_esb_written_line_adopts_it_not_duplicates(self, db):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        product_id, product_ref = _seed_product(db)
        warehouse = Warehouse(warehouse_code=_code("WH"), warehouse_name="Main")
        db.add(warehouse)
        db.flush()
        shipment = InboundShipment(
            shipment_number=_code("SH"), shipping_container_number=_code("CONT"),
            shipment_date=date(2026, 1, 1), shipment_status="pending",
        )
        db.add(shipment)
        db.flush()

        spo_number = _code("SPO")
        svc = _shipping_order_svc(db)
        result = svc.ingest(
            "shipping_orders",
            [
                {
                    "source_ref": f"DK-{spo_number}",
                    "spo_number": spo_number,
                    "status": "open",
                    "lines": [
                        {
                            "source_ref": f"DK-{spo_number}-L1",
                            "product_ref": product_ref, "product_code": product_ref[3:],
                            "warehouse_code": warehouse.warehouse_code,
                            "qty_ordered": "20",
                        }
                    ],
                }
            ],
        )
        assert result.records[0].outcome is IngestOutcome.CREATED, result.records[0].errors

        # The xlsx import's own write function, re-uploading the SAME
        # (spo_number, product, warehouse) with a corrected quantity.
        proc = SPOAllocationService(db)
        allocation_data = SPOAllocationCreate(
            spo_number=spo_number, inbound_shipment_id=shipment.id,
            warehouse_id=warehouse.id, product_id=product_id, allocated_quantity=25,
        )
        proc.upsert_allocation(allocation_data, created_by=None, forward_match=False)

        count = db.execute(
            text(
                "SELECT count(*) FROM spo_allocations "
                "WHERE spo_number = :n AND product_id = :p AND warehouse_id = :w"
            ),
            {"n": spo_number, "p": product_id, "w": warehouse.id},
        ).scalar()
        assert count == 1, "the upload must adopt the ESB-written row, not duplicate it"

    def test_close_sweep_considers_esb_written_open_rows(self, db):
        from app.services.scm.outstanding_import_service import _spo_lines_to_close

        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        _product_id, product_ref = _seed_product(db)
        spo_number = _code("SPO")
        svc = _shipping_order_svc(db)
        result = svc.ingest(
            "shipping_orders",
            [
                {
                    "source_ref": f"DK-{spo_number}",
                    "spo_number": spo_number,
                    "status": "open",
                    "lines": [
                        {
                            "source_ref": f"DK-{spo_number}-L1",
                            "product_ref": product_ref, "product_code": product_ref[3:],
                            "qty_ordered": "8",
                        }
                    ],
                }
            ],
        )
        assert result.records[0].outcome is IngestOutcome.CREATED, result.records[0].errors
        row_id = db.execute(
            text("SELECT id FROM spo_allocations WHERE spo_number = :n"), {"n": spo_number}
        ).scalar()

        # The file no longer states ANYTHING for this SPO - every open row of
        # EITHER source should be a close candidate.
        to_close = _spo_lines_to_close(db, stated_keys=set())
        assert str(row_id) in {str(r.id) for r in to_close}, (
            "the close-by-absence sweep must also consider ESB-written open rows"
        )


class TestAcP36StatusOptionalOnShippingOrders:
    """D20: `status` optional on the shipping_orders payload; absent derives
    from lines via the shared `derive_document_status` (S2's own pure-function
    golden-cases test already covers the derivation logic itself - not
    repeated here). Today `status` is still required."""

    def test_esb_shipping_orders_payload_status_is_optional(self, db):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        _product_id, product_ref = _seed_product(db)
        spo_number = _code("SPO")
        svc = _shipping_order_svc(db)
        record = {
            "source_ref": f"DK-{spo_number}",
            "spo_number": spo_number,
            "lines": [
                {"source_ref": f"DK-{spo_number}-L1", "product_ref": product_ref, "product_code": product_ref[3:], "qty_ordered": "5"}
            ],
        }
        result = svc.ingest("shipping_orders", [record])
        assert result.records[0].outcome is IngestOutcome.CREATED, result.records[0].errors


class TestAcP37DocFamilyAcceptsFlagOrPrefix:
    """D6: the ESB's SPO family test accepts a document as SPO when the
    number starts with `SPO-` OR the payload says `is_shipping_order`. Today's
    `document_ingest_service` refusal (`doc_family(po_number) == FAMILY_SPO`)
    is prefix-only; `CanonicalPurchaseOrder` has no `is_shipping_order` field
    at all. The outstanding book's own `doc_family` (prefix-only, no flag
    concept) is correct as-is and is not re-tested."""

    def test_esb_po_with_is_shipping_order_flag_is_rejected_as_a_shipping_order(self, db):
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        _product_id, product_ref = _seed_product(db)
        po_number = _code("NOTSPO")  # deliberately NOT "SPO-" prefixed
        svc = DocumentIngestService(db, integration_id=None, company_id=DEFAULT_COMPANY_ID)
        result = svc.ingest(
            "purchase_orders",
            [
                {
                    "source_ref": f"DK-{po_number}",
                    "po_number": po_number,
                    "status": "open",
                    "is_shipping_order": True,
                    "lines": [
                        {
                            "source_ref": f"DK-{po_number}-L1",
                            "product_ref": product_ref, "product_code": product_ref[3:],
                            "qty_ordered": "5",
                        }
                    ],
                }
            ],
        )
        record = result.records[0]
        assert record.errors.get("po_number") == "shipping order; push under shipping_orders", (
            record.errors
        )


class TestAcP38XlsxVsEsbParity:
    """A scaled-down stand-in for the UAC's 6-line SPO fixture (three
    representative lines, distinct products, sharing ONE container/shipment
    for the whole document - `CanonicalShippingOrder.container_number` is
    header-level (D6, one container per document, applied to every line by
    `ShippingOrderIngestService._apply`), so the ESB half names it once on
    the payload rather than per line): through the xlsx import's own write
    function (`SPOAllocationService.upsert_allocation`) into company A, and
    through the ESB into company B. `inbound_shipment_id` is compared as
    presence-of-link (a boolean), not the raw id - the id itself is a
    company-scoped row and legitimately differs, same reasoning as every
    other `*_id` column excluded generically."""

    def test_three_line_spo_xlsx_vs_esb_parity(self, db):
        from app.models.company import Company
        from app.models.procurement import SPOAllocation
        from app.services.rules import shipping_order_rules

        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        other = Company(id=str(uuid.uuid4()), name=f"{MARKER} B", code=unique_code(MARKER)[:10])
        db.add(other)
        db.flush()
        company_b = str(other.id)

        spo_number = _code("SPOPAR")
        shared_container = _code("CONT")
        # `process_spo_import` (the real xlsx importer) reads the container
        # already cleaned by `_spo_import_extract_container`, i.e. the same
        # `extract_container_number` the ESB side runs - so the fixture's
        # xlsx half must state the cleaned form too, not the raw one.
        cleaned_container = shipping_order_rules.extract_container_number(shared_container)
        shipment_a = InboundShipment(
            shipment_number=_code("SHA"), shipping_container_number=shared_container,
            shipment_date=date(2026, 1, 1), shipment_status="pending",
        )
        db.add(shipment_a)
        db.flush()

        lines = []
        for i in range(3):
            category = ProductCategory(category_code=_code(f"CAT{i}"), category_name="Cat")
            uom = UnitOfMeasure(uom_code=_code(f"UOM{i}"), uom_name="Each")
            db.add_all([category, uom])
            db.flush()
            product_a = Product(
                product_code=_code(f"PRDA{i}"), product_name="Item",
                category_id=category.id, base_uom_id=uom.id, list_price=0,
            )
            db.add(product_a)
            db.flush()
            warehouse_a = Warehouse(warehouse_code=_code(f"WHA{i}"), warehouse_name="Main")
            db.add(warehouse_a)
            db.flush()
            lines.append(
                {"product_a": product_a, "warehouse_a": warehouse_a, "qty": 10 + i}
            )

        # Upload half, into company A - all three lines share the one shipment.
        proc = SPOAllocationService(db)
        for line in lines:
            proc.upsert_allocation(
                SPOAllocationCreate(
                    spo_number=spo_number,
                    inbound_shipment_id=shipment_a.id,
                    # `process_spo_import` (the real xlsx importer) always
                    # resolves and states the container explicitly alongside
                    # the shipment id it resolved from it - see
                    # app/tasks/import_tasks.py.
                    container_number=cleaned_container,
                    warehouse_id=line["warehouse_a"].id,
                    location_code=line["warehouse_a"].warehouse_code,
                    product_id=line["product_a"].id,
                    allocated_quantity=line["qty"],
                ),
                created_by=None,
                forward_match=False,
            )

        # ESB half, into company B - its own products/warehouses/shipment,
        # same codes/container so the fixture reads as "the same document".
        set_company_scope(db, frozenset({company_b}))
        from app.services.integration_reference_service import IntegrationReferenceService

        # Anchored to B: every row this half seeds belongs to company_b, and
        # plan D14 is strict - an unanchored link on a scoped type now raises
        # ValueError rather than inferring the company from the row.
        refs = IntegrationReferenceService(db, company_id=company_b)
        shipment_b = InboundShipment(
            shipment_number=_code("SHB"), shipping_container_number=shared_container,
            shipment_date=date(2026, 1, 1), shipment_status="pending",
        )
        db.add(shipment_b)
        db.flush()

        esb_lines = []
        for i, line in enumerate(lines):
            product_b = Product(
                product_code=line["product_a"].product_code, product_name="Item",
                category_id=None, base_uom_id=None, list_price=0,
            )
            # category/uom must exist in company B too.
            category_b = ProductCategory(category_code=_code(f"BCAT{i}"), category_name="Cat")
            uom_b = UnitOfMeasure(uom_code=_code(f"BUOM{i}"), uom_name="Each")
            db.add_all([category_b, uom_b])
            db.flush()
            product_b.category_id = category_b.id
            product_b.base_uom_id = uom_b.id
            db.add(product_b)
            db.flush()
            product_ref = f"DK-{product_b.product_code}"
            refs.link(entity_type="products", entity_id=product_b.id, source_ref=product_ref)

            warehouse_b = Warehouse(
                warehouse_code=line["warehouse_a"].warehouse_code, warehouse_name="Main"
            )
            db.add(warehouse_b)
            db.flush()
            esb_lines.append(
                {
                    "source_ref": f"DK-{spo_number}-L{i}",
                    "product_ref": product_ref, "product_code": product_ref[3:],
                    "warehouse_code": warehouse_b.warehouse_code,
                    "qty_ordered": str(line["qty"]),
                }
            )

        esb = _shipping_order_svc(db, company_id=company_b)
        result = esb.ingest(
            "shipping_orders",
            [
                {
                    "source_ref": f"DK-{spo_number}",
                    "spo_number": spo_number,
                    "status": "open",
                    "container_number": shared_container,
                    "lines": esb_lines,
                }
            ],
        )
        assert result.records[0].outcome is IngestOutcome.CREATED, result.records[0].errors

        columns = [
            c.name
            for c in SPOAllocation.__table__.columns
            if c.name not in _GENERIC_EXCLUDE and not c.name.endswith("_id")
        ]
        rows_a = (
            db.execute(
                text(
                    f"SELECT {', '.join(columns)}, inbound_shipment_id IS NOT NULL AS linked "
                    "FROM spo_allocations WHERE spo_number = :n AND company_id = :cid "
                    "ORDER BY allocated_quantity"
                ),
                {"n": spo_number, "cid": DEFAULT_COMPANY_ID},
            )
            .mappings()
            .all()
        )
        rows_b = (
            db.execute(
                text(
                    f"SELECT {', '.join(columns)}, inbound_shipment_id IS NOT NULL AS linked "
                    "FROM spo_allocations WHERE spo_number = :n AND company_id = :cid "
                    "ORDER BY allocated_quantity"
                ),
                {"n": spo_number, "cid": company_b},
            )
            .mappings()
            .all()
        )
        assert len(rows_a) == 3 and len(rows_b) == 3, (len(rows_a), len(rows_b))
        for row_a, row_b in zip(rows_a, rows_b):
            diff = {
                k: (row_a[k], row_b[k])
                for k in row_a.keys()
                if row_a[k] != row_b[k]
            }
            assert diff == {}, diff
