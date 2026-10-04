"""Review-guard tests from the lane's reviewer + security-reviewer pass on
`ingest-parity-standardisation` (behaviours found with NO test covering them).

UAC/PLAN: documentation/plans/autocount/ingest-parity-standardisation-acceptance-criteria.md,
documentation/plans/autocount/PLAN-ingest-parity-standardisation.md.

The coder is fixing these in parallel, so a test here may already be green when it runs -
each class/test says so explicitly rather than mixing a guard in with a red assertion
silently. Substrate: the `env` fixture (`blank_session()` + real `TestClient(app)`) reused
byte for byte from `tests/test_ingest_documents.py`, same as every other file in this UAC.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.master_ingest_service import IngestOutcome

from tests.test_ingest_documents import (
    INGEST_PO,
    INGEST_SO,
    MARKER as DOC_MARKER,
    _code_of_ref,
    _po_line,
    _po_record,
    _ref,
    _so_line,
    _so_record,
    env,  # noqa: F401 - pytest fixture, imported for reuse
)

__all__ = ["env"]

MARKER = "ZZTIPG"


# ============================================================== B1 ==========
class TestB1DroppedLineLeavesItsPersistedCounterpartAlone:
    """A line dropped for an unresolvable product must not cost the DOCUMENT's
    other, unrelated, already-persisted line: `_apply` used to only count
    `dropped` and never exclude that line's own `source_ref` from
    `_sync_lines`' leftover sweep - so a persisted line whose ONLY fault was
    "this push dropped a different line" got swept as a leftover (cancelled
    or deleted) exactly as if it had genuinely vanished from the document.
    Fixed (B1, review re-check 2026-09-06): `_sync_lines`/`_adopt_lines` now
    track dropped lines and reserve their would-be counterpart from the
    leftover sweep instead."""

    def test_by_ref_repush_with_one_line_now_unresolvable_leaves_both_persisted_lines(
        self, env
    ):
        line1 = _so_line(env, product_ref=env.product_ref, qty_ordered=10)
        line2 = _so_line(env, product_ref=env.product2_ref, qty_ordered=5)
        record = _so_record(env, lines=[line1, line2])
        res = env.post(INGEST_SO, [record])
        assert res.json()["records"][0]["outcome"] == "created", res.text
        header = env.header("sales_orders", record["source_ref"])
        before = env.so_lines(header["id"])
        assert len(before) == 2, before

        # Re-push the SAME document, same two line refs, but line 2 now
        # names an unresolvable product (built by hand, not via the
        # `_so_line` helper - its own `product_ref or env.product_ref`
        # silently falls back to a valid ref for `None`).
        bad_line2 = {
            "source_ref": line2["source_ref"],
            "product_code": f"{DOC_MARKER}-NOSUCHITEM",
            "qty_ordered": 5,
        }
        record2 = dict(record, lines=[line1, bad_line2])
        res2 = env.post(INGEST_SO, [record2])
        body = res2.json()["records"][0]
        assert body["outcome"] == "updated", body
        assert body.get("lines", {}).get("dropped") == 1, body

        after = env.so_lines(header["id"])
        assert len(after) == 2, (
            "the dropped line's PERSISTED counterpart from the first push must still "
            f"exist, unchanged: {after}"
        )
        line2_row = next((r for r in after if r["source_ref"] == line2["source_ref"]), None)
        assert line2_row is not None, "line 2's original row must not be deleted"
        assert line2_row["line_status"] not in ("cancelled",), (
            f"line 2's original row must not be cancelled either: {line2_row}"
        )

    def test_refless_adoption_repush_with_one_line_now_unresolvable_leaves_both_persisted_lines(
        self, env
    ):
        """Same defect via the OTHER route into `_sync_lines`' sweep: two
        xlsx-era (ref-less) lines already on the header, an ESB push naming
        one adoptable line and one now-unresolvable line - the unresolvable
        line is dropped BEFORE adoption runs, so its own would-be pool match
        never happens and the ESB push has nothing left to claim the other
        pool row, which the sweep then cancels/deletes even though nothing
        about IT changed."""
        from app.models.order import SalesOrder, SalesOrderLine

        so_number = f"{MARKER}-REFLESS-{uuid.uuid4().hex[:8]}"
        header = SalesOrder(
            id=str(uuid.uuid4()), so_number=so_number, status="open",
            company_id=env.company_a,
        )
        env.db.add(header)
        env.db.flush()
        product1_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        product2_id = env.refs.resolve(entity_type="products", source_ref=env.product2_ref)
        line1 = SalesOrderLine(
            id=str(uuid.uuid4()), sales_order_id=header.id, product_id=product1_id,
            qty_ordered=Decimal("10"), qty_delivered=Decimal("0"), line_status="open",
            source_ref=None, company_id=env.company_a,
        )
        line2 = SalesOrderLine(
            id=str(uuid.uuid4()), sales_order_id=header.id, product_id=product2_id,
            qty_ordered=Decimal("5"), qty_delivered=Decimal("0"), line_status="open",
            source_ref=None, company_id=env.company_a,
        )
        env.db.add_all([line1, line2])
        env.db.flush()
        env.db.commit()
        so_ref = _ref("SO")
        env.refs.link(entity_type="sales_orders", entity_id=header.id, source_ref=so_ref)

        good_line = {
            "source_ref": _ref("SOL"), "product_ref": env.product_ref, "product_code": _code_of_ref(env, env.product_ref), "qty_ordered": 10,
        }
        bad_line = {
            "source_ref": _ref("SOL"), "product_code": f"{DOC_MARKER}-NOSUCHITEM",
            "qty_ordered": 5,
        }
        record = _so_record(env, ref=so_ref, number=so_number, lines=[good_line, bad_line])

        res = env.post(INGEST_SO, [record])
        body = res.json()["records"][0]
        assert body["outcome"] == "updated", body
        assert body.get("lines", {}).get("dropped") == 1, body

        after = env.so_lines(header.id)
        assert len(after) == 2, (
            "line 2's persisted xlsx-era row must survive a push that dropped an "
            f"UNRELATED line, not be swept as an orphaned pool row: {after}"
        )
        statuses = {r["product_id"]: r["line_status"] for r in after}
        assert statuses.get(product2_id) != "cancelled", after


# ============================================================== B4 ==========
class TestB4OrderInquiryConflictRecordedOnBothPaths:
    """D22: an SO line's warehouse changing between what Order Inquiry set and
    what AutoCount now states is recorded in `order_inquiry_conflicts`
    (migration 476) - already true on the BY-REF path (`_sync_lines`); the
    ref-less ADOPTION path (`_adopt_lines`) does a blind `setattr` with no
    such recording at all (confirmed by reading `document_ingest_service.py`:
    zero references to `OrderInquiryConflict` inside `_adopt_lines`)."""

    def test_by_ref_path_already_records_the_conflict(self, env):
        """REGRESSION GUARD, not red - `_sync_lines`' by-ref branch already
        does this (migration 476/D22)."""
        warehouse_b = env.link_warehouse(env.company_a)
        line = _so_line(env, product_ref=env.product_ref, warehouse_ref=env.warehouse_ref)
        record = _so_record(env, lines=[line])
        res = env.post(INGEST_SO, [record])
        assert res.json()["records"][0]["outcome"] == "created", res.text
        header = env.header("sales_orders", record["source_ref"])
        so_line = env.so_lines(header["id"])[0]

        record2 = dict(record, lines=[dict(line, warehouse_ref=warehouse_b)])
        res2 = env.post(INGEST_SO, [record2])
        assert res2.json()["records"][0]["outcome"] == "updated", res2.text

        rows = env.db.execute(
            text(
                "SELECT previous_warehouse_id, new_warehouse_id, sales_order_line_id "
                "FROM order_inquiry_conflicts WHERE sales_order_line_id = :id"
            ),
            {"id": so_line["id"]},
        ).mappings().all()
        assert len(rows) == 1, rows
        old_wh = env.refs.resolve(entity_type="warehouses", source_ref=env.warehouse_ref)
        new_wh = env.refs.resolve(entity_type="warehouses", source_ref=warehouse_b)
        assert str(rows[0]["previous_warehouse_id"]) == str(old_wh)
        assert str(rows[0]["new_warehouse_id"]) == str(new_wh)

    def test_refless_adoption_path_records_the_conflict(self, env):
        from app.models.order import SalesOrder, SalesOrderLine

        warehouse_a_id = env.refs.resolve(entity_type="warehouses", source_ref=env.warehouse_ref)
        warehouse_b_ref = env.link_warehouse(env.company_a)
        warehouse_b_id = env.refs.resolve(entity_type="warehouses", source_ref=warehouse_b_ref)
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)

        so_number = f"{MARKER}-OICADOPT-{uuid.uuid4().hex[:8]}"
        header = SalesOrder(
            id=str(uuid.uuid4()), so_number=so_number, status="open",
            company_id=env.company_a,
        )
        env.db.add(header)
        env.db.flush()
        line = SalesOrderLine(
            id=str(uuid.uuid4()), sales_order_id=header.id, product_id=product_id,
            warehouse_id=warehouse_a_id, qty_ordered=Decimal("10"),
            qty_delivered=Decimal("0"), line_status="open", source_ref=None,
            company_id=env.company_a,
        )
        env.db.add(line)
        env.db.flush()
        env.db.commit()
        so_ref = _ref("SO")
        env.refs.link(entity_type="sales_orders", entity_id=header.id, source_ref=so_ref)

        # Same (product, outstanding) key, DIFFERENT warehouse - pass 1 of
        # `_adopt_lines` still claims it (warehouse is not part of pass 1's
        # match key when it differs; see its own `_row_key`/`_line_key`).
        incoming_line = {
            "source_ref": _ref("SOL"), "product_ref": env.product_ref, "product_code": _code_of_ref(env, env.product_ref),
            "warehouse_ref": warehouse_b_ref, "qty_ordered": 10,
        }
        record = _so_record(env, ref=so_ref, number=so_number, lines=[incoming_line])
        res = env.post(INGEST_SO, [record])
        body = res.json()["records"][0]
        assert body["outcome"] == "updated", body
        assert body.get("lines", {}).get("adopted") == 1, body

        rows = env.db.execute(
            text(
                "SELECT count(*) FROM order_inquiry_conflicts WHERE sales_order_line_id = :id"
            ),
            {"id": line.id},
        ).scalar()
        assert rows == 1, (
            "D22 must record the SAME conflict on the ref-less adoption path as it "
            f"does on the by-ref path - got {rows} rows"
        )


# ============================================================== B5 ==========
class TestB5MarketSegmentAndRegion:
    def test_masters_known_segment_lands(self, env):
        """REGRESSION GUARD, not red - already implemented
        (`master_ingest_service._customer_columns`)."""
        from app.services.master_ingest_service import MasterIngestService

        segment_code = f"{MARKER}-SEG-{uuid.uuid4().hex[:6]}".upper()
        _seed_market_segment(env.db, segment_code)
        svc = MasterIngestService(env.db, integration_id=None, company_id=env.company_a)
        code = _ref("CUSTMS")
        result = svc.ingest(
            "customers",
            [{
                "source_ref": _ref("DK"), "code": code, "name": "Segment Co",
                "market_segment_code": segment_code, "region": "Klang Valley",
            }],
        )
        record = result.records[0]
        assert record.outcome is IngestOutcome.CREATED, record.errors
        row = env.db.execute(
            text("SELECT market_segment_code, region FROM customers WHERE customer_code = :c"),
            {"c": code},
        ).mappings().first()
        assert row["market_segment_code"] == segment_code
        assert row["region"] == "Klang Valley"

    def test_masters_unknown_segment_spelling_is_null_with_a_warning(self, env):
        """REGRESSION GUARD, not red."""
        from app.services.master_ingest_service import MasterIngestService

        svc = MasterIngestService(env.db, integration_id=None, company_id=env.company_a)
        code = _ref("CUSTMSX")
        result = svc.ingest(
            "customers",
            [{
                "source_ref": _ref("DK"), "code": code, "name": "Segment Co",
                "market_segment_code": f"{MARKER}-NOSUCHSEG",
            }],
        )
        record = result.records[0]
        assert record.outcome is IngestOutcome.CREATED, record.errors
        assert "segment_unknown" in record.warnings, record.warnings
        segment = env.db.execute(
            text("SELECT market_segment_code FROM customers WHERE customer_code = :c"),
            {"c": code},
        ).scalar()
        assert segment is None

    def test_masters_hand_set_segment_is_never_overwritten_by_an_absent_field(self, env):
        """REGRESSION GUARD, not red - `_present`'s absent-vs-null rule already
        keeps `market_segment_code` off `columns` when the payload never sent it."""
        from app.services.master_ingest_service import MasterIngestService

        segment_code = f"{MARKER}-SEG2-{uuid.uuid4().hex[:6]}".upper()
        _seed_market_segment(env.db, segment_code)
        svc = MasterIngestService(env.db, integration_id=None, company_id=env.company_a)
        code = _ref("CUSTMSK")
        svc.ingest(
            "customers",
            [{"source_ref": _ref("DK"), "code": code, "name": "Segment Co",
              "market_segment_code": segment_code}],
        )
        svc.ingest(
            "customers",
            [{"source_ref": _ref("DK"), "code": code, "name": "Segment Co renamed"}],
        )
        segment = env.db.execute(
            text("SELECT market_segment_code FROM customers WHERE customer_code = :c"),
            {"c": code},
        ).scalar()
        assert segment == segment_code, "a hand-set segment must survive an update that omits it"

    def test_so_push_with_customer_segment_and_region_back_creates_a_customer_carrying_both(
        self, env
    ):
        """RED: `document_ingest_service.py` never reads `payload.customer_segment`/
        `customer_region` at all (grep, zero hits) even though
        `customer_rules.back_create_customer` already accepts `segment`/`region`
        kwargs - the SO caller simply never threads them through."""
        segment_code = f"{MARKER}-SEG3-{uuid.uuid4().hex[:6]}".upper()
        _seed_market_segment(env.db, segment_code)
        debtor_code = f"{MARKER}-NEWDEB-{uuid.uuid4().hex[:8]}".upper()
        record = _so_record(
            env,
            customer_code=debtor_code,
            customer_name=f"{MARKER} New Customer Sdn Bhd",
            customer_segment=segment_code,
            customer_region="Klang Valley",
        )
        res = env.post(INGEST_SO, [record])
        body = res.json()["records"][0]
        assert body["outcome"] == "created", body
        row = env.db.execute(
            text(
                "SELECT market_segment_code, region FROM customers WHERE customer_code = :c"
            ),
            {"c": debtor_code},
        ).mappings().first()
        assert row is not None, "the customer must be back-created"
        assert row["market_segment_code"] == segment_code, row
        assert row["region"] == "Klang Valley", row

    def test_so_push_with_unknown_customer_segment_lands_null_with_a_warning_on_the_record(
        self, env
    ):
        """RED: even once `customer_segment` is threaded through, nothing today
        folds it against `market_segments` or reports `segment_unknown` on the
        document record - `back_create_customer` just writes the raw string."""
        debtor_code = f"{MARKER}-NEWDEB2-{uuid.uuid4().hex[:8]}".upper()
        record = _so_record(
            env,
            customer_code=debtor_code,
            customer_name=f"{MARKER} New Customer 2 Sdn Bhd",
            customer_segment=f"{MARKER}-NOSUCHSEG",
        )
        res = env.post(INGEST_SO, [record])
        body = res.json()["records"][0]
        assert body["outcome"] == "created", body
        assert "segment_unknown" in body.get("warnings", []), body
        segment = env.db.execute(
            text("SELECT market_segment_code FROM customers WHERE customer_code = :c"),
            {"c": debtor_code},
        ).scalar()
        assert segment is None, segment


def _seed_market_segment(db, code: str) -> None:
    from app.models.access import MarketSegment

    db.add(MarketSegment(id=str(uuid.uuid4()), code=code, name=code, is_active=True))
    db.flush()


# ============================================================== B6 ==========
class TestB6ContainerLinking:
    def test_esb_allocation_with_no_matching_shipment_is_null_with_a_warning(self, env):
        """REGRESSION GUARD, not red -
        `shipping_order_ingest_service._write_row` already calls
        `shipping_order_rules.link_allocation_to_shipment` and warns
        `container_unresolved` on a miss."""
        from app.services.shipping_order_ingest_service import ShippingOrderIngestService

        svc = ShippingOrderIngestService(env.db, integration_id=None, company_id=env.company_a)
        container = f"{MARKER}-CONT-{uuid.uuid4().hex[:8]}".upper()
        spo_number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}".upper()
        result = svc.ingest(
            "shipping_orders",
            [{
                "source_ref": _ref("SPO"), "spo_number": spo_number, "status": "open",
                "container_number": container,
                "lines": [{
                    "source_ref": _ref("SPOL"), "product_ref": env.product_ref, "product_code": _code_of_ref(env, env.product_ref),
                    "warehouse_code": env.db.execute(
                        text("SELECT warehouse_code FROM warehouses WHERE id = :id"),
                        {"id": env.refs.resolve(
                            entity_type="warehouses", source_ref=env.warehouse_ref
                        )},
                    ).scalar(),
                    "qty_ordered": "10",
                }],
            }],
        )
        record = result.records[0]
        assert record.outcome is IngestOutcome.CREATED, record.errors
        assert "container_unresolved" in record.warnings, record.warnings
        row = env.db.execute(
            text(
                "SELECT inbound_shipment_id, container_number FROM spo_allocations "
                "WHERE spo_number = :n"
            ),
            {"n": spo_number},
        ).mappings().first()
        assert row["inbound_shipment_id"] is None
        assert row["container_number"] == container

    def test_creating_the_shipment_afterwards_links_the_leftover_allocation(self, env):
        """REGRESSION GUARD, not red - `InboundShipmentService.create_shipment`
        already calls `_relink_allocations_for_shipment` at the end."""
        from app.models.procurement import SPOAllocation
        from app.services.procurement_service import InboundShipmentService
        from app.schemas.procurement import InboundShipmentCreate

        container = f"{MARKER}-CONT2-{uuid.uuid4().hex[:8]}".upper()
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        alloc = SPOAllocation(
            id=str(uuid.uuid4()), spo_number=f"{MARKER}-SPO2-{uuid.uuid4().hex[:8]}",
            spo_line_number=1, product_id=product_id, container_number=container,
            allocated_quantity=10, quantity_received=0, line_status="open",
            company_id=env.company_a,
        )
        env.db.add(alloc)
        env.db.flush()
        env.db.commit()

        svc = InboundShipmentService(env.db)
        svc.create_shipment(
            InboundShipmentCreate(
                shipment_number=f"{MARKER}-SH-{uuid.uuid4().hex[:8]}",
                shipping_container_number=container,
                shipment_date=date(2026, 1, 1), shipment_status="pending",
            ),
            created_by=None,
        )
        env.db.refresh(alloc)
        assert alloc.inbound_shipment_id is not None, (
            "creating the shipment afterwards must link the leftover allocation"
        )

    def test_setting_the_container_via_update_shipment_links_the_leftover_allocation(
        self, env
    ):
        """RED: `InboundShipmentService.update_shipment` never calls
        `_relink_allocations_for_shipment` at all (confirmed by reading it) -
        setting a container on an EXISTING container-less shipment never
        links anything waiting on it."""
        from app.models.procurement import InboundShipment, SPOAllocation
        from app.services.procurement_service import InboundShipmentService
        from app.schemas.procurement import InboundShipmentUpdate

        container = f"{MARKER}-CONT3-{uuid.uuid4().hex[:8]}".upper()
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        alloc = SPOAllocation(
            id=str(uuid.uuid4()), spo_number=f"{MARKER}-SPO3-{uuid.uuid4().hex[:8]}",
            spo_line_number=1, product_id=product_id, container_number=container,
            allocated_quantity=10, quantity_received=0, line_status="open",
            company_id=env.company_a,
        )
        env.db.add(alloc)
        shipment = InboundShipment(
            id=str(uuid.uuid4()), shipment_number=f"{MARKER}-SH2-{uuid.uuid4().hex[:8]}",
            shipping_container_number=None, shipment_date=date(2026, 1, 1),
            shipment_status="pending", company_id=env.company_a,
        )
        env.db.add(shipment)
        env.db.flush()
        env.db.commit()

        svc = InboundShipmentService(env.db)
        svc.update_shipment(
            shipment.id,
            InboundShipmentUpdate(shipping_container_number=container),
            updated_by=None,
        )
        env.db.refresh(alloc)
        assert alloc.inbound_shipment_id == shipment.id, (
            "setting the container via update_shipment must link the leftover "
            f"allocation, same as create_shipment already does: {alloc.inbound_shipment_id!r}"
        )

    def test_relink_function_is_scoped_by_company_and_never_crosses(self, env):
        """REGRESSION GUARD, not red - `relink_allocations_for_container`
        already takes a `company_id` kwarg and filters both the shipment
        lookup and the allocation query by it (security review should-fix 3,
        already landed). No standalone nightly TASK exists yet in
        `app/tasks/` to wrap this call (grep, zero hits) - tested directly
        against the shared function per the coordinator's own fallback."""
        from app.models.company import Company
        from app.models.procurement import InboundShipment, SPOAllocation
        from app.services.rules.shipping_order_rules import relink_allocations_for_container

        other = Company(id=str(uuid.uuid4()), name=f"{MARKER} B", code=f"ZZR{uuid.uuid4().hex[:6]}")
        env.db.add(other)
        env.db.flush()
        company_b = str(other.id)

        container = f"{MARKER}-CONT4-{uuid.uuid4().hex[:8]}".upper()
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        alloc_b = SPOAllocation(
            id=str(uuid.uuid4()), spo_number=f"{MARKER}-SPOB-{uuid.uuid4().hex[:8]}",
            spo_line_number=1, product_id=product_id, container_number=container,
            allocated_quantity=10, quantity_received=0, line_status="open",
            company_id=company_b,
        )
        shipment_a = InboundShipment(
            id=str(uuid.uuid4()), shipment_number=f"{MARKER}-SHA-{uuid.uuid4().hex[:8]}",
            shipping_container_number=container, shipment_date=date(2026, 1, 1),
            shipment_status="pending", company_id=env.company_a,
        )
        env.db.add_all([alloc_b, shipment_a])
        env.db.flush()
        env.db.commit()

        count = relink_allocations_for_container(env.db, container, company_id=env.company_a)
        assert count == 0, "relinking scoped to company A must never touch company B's row"
        env.db.refresh(alloc_b)
        assert alloc_b.inbound_shipment_id is None, alloc_b.inbound_shipment_id


# ============================================================== S3 ==========
class TestS3DerivedStatusWiring:
    """All GUARDS, not red - `document_rules.derive_document_status` is already
    wired into `DocumentIngestService._apply` and `ShippingOrderIngestService`
    (both confirmed by reading the code), and the SO/PO status maps
    (`SALES_ORDER_STATUS_MAP`/`PURCHASE_ORDER_STATUS_MAP`) already carry the
    canonical->stored mapping this whole class exercises."""

    def test_so_no_status_all_settled_lands_closed(self, env):
        line = _so_line(env, qty_ordered=10, qty_delivered=10)
        ref = _ref("SO")
        res = env.post(INGEST_SO, [{
            "source_ref": ref, "so_number": f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}",
            "lines": [line],
        }])
        body = res.json()["records"][0]
        assert body["outcome"] == "created", body
        header = env.header("sales_orders", ref)
        assert header["status"] == "closed", header

    def test_so_no_status_outstanding_lands_open(self, env):
        line = _so_line(env, qty_ordered=10, qty_delivered=0)
        ref = _ref("SO")
        res = env.post(INGEST_SO, [{
            "source_ref": ref, "so_number": f"{MARKER}-SOB-{uuid.uuid4().hex[:8]}",
            "lines": [line],
        }])
        body = res.json()["records"][0]
        assert body["outcome"] == "created", body
        header = env.header("sales_orders", ref)
        assert header["status"] == "open", header

    def test_po_no_status_outstanding_lands_active(self, env):
        line = _po_line(env, qty_ordered=10, qty_received=0)
        ref = _ref("PO")
        res = env.post(INGEST_PO, [{
            "source_ref": ref, "po_number": f"{MARKER}-POA-{uuid.uuid4().hex[:8]}",
            "supplier_ref": env.supplier_ref, "lines": [line],
        }])
        body = res.json()["records"][0]
        assert body["outcome"] == "created", body
        header = env.header("purchase_orders", ref)
        assert header["status"] == "active", header

    def test_existing_draft_po_is_lifted_once_autocount_names_it(self, env):
        from app.models.procurement import PurchaseOrder

        po = PurchaseOrder(
            id=str(uuid.uuid4()), po_number=f"{MARKER}-DRAFT-{uuid.uuid4().hex[:8]}",
            status="draft", supplier_id=env.refs.resolve(
                entity_type="suppliers", source_ref=env.supplier_ref
            ), company_id=env.company_a,
        )
        env.db.add(po)
        env.db.flush()
        env.db.commit()
        ref = _ref("PO")
        env.refs.link(entity_type="purchase_orders", entity_id=po.id, source_ref=ref)

        line = _po_line(env, qty_ordered=10, qty_received=0)
        res = env.post(INGEST_PO, [{
            "source_ref": ref, "po_number": po.po_number,
            "supplier_ref": env.supplier_ref, "lines": [line],
        }])
        body = res.json()["records"][0]
        assert body["outcome"] == "updated", body
        env.db.refresh(po)
        assert po.status == "active", (
            f"a draft PO must be lifted the moment AutoCount states it: {po.status!r}"
        )

    def test_existing_cancelled_status_is_preserved_even_with_outstanding_lines(self, env):
        from app.models.procurement import PurchaseOrder

        po = PurchaseOrder(
            id=str(uuid.uuid4()), po_number=f"{MARKER}-CANC-{uuid.uuid4().hex[:8]}",
            status="cancelled", supplier_id=env.refs.resolve(
                entity_type="suppliers", source_ref=env.supplier_ref
            ), company_id=env.company_a,
        )
        env.db.add(po)
        env.db.flush()
        env.db.commit()
        ref = _ref("PO")
        env.refs.link(entity_type="purchase_orders", entity_id=po.id, source_ref=ref)

        line = _po_line(env, qty_ordered=10, qty_received=0)
        res = env.post(INGEST_PO, [{
            "source_ref": ref, "po_number": po.po_number,
            "supplier_ref": env.supplier_ref, "lines": [line],
        }])
        body = res.json()["records"][0]
        assert body["outcome"] == "updated", body
        env.db.refresh(po)
        assert po.status == "cancelled", po.status

    def test_spo_no_status_all_received_lands_closed(self, env):
        from app.services.shipping_order_ingest_service import ShippingOrderIngestService

        svc = ShippingOrderIngestService(env.db, integration_id=None, company_id=env.company_a)
        spo_number = f"{MARKER}-SPOC-{uuid.uuid4().hex[:8]}".upper()
        warehouse_code = env.db.execute(
            text("SELECT warehouse_code FROM warehouses WHERE id = :id"),
            {"id": env.refs.resolve(entity_type="warehouses", source_ref=env.warehouse_ref)},
        ).scalar()
        result = svc.ingest(
            "shipping_orders",
            [{
                "source_ref": _ref("SPO"), "spo_number": spo_number,
                "lines": [{
                    "source_ref": _ref("SPOL"), "product_ref": env.product_ref, "product_code": _code_of_ref(env, env.product_ref),
                    "warehouse_code": warehouse_code, "qty_ordered": "10",
                    "qty_received": "10",
                }],
            }],
        )
        record = result.records[0]
        assert record.outcome is IngestOutcome.CREATED, record.errors
        status = env.db.execute(
            text("SELECT line_status FROM spo_allocations WHERE spo_number = :n"),
            {"n": spo_number},
        ).scalar()
        assert status == "closed", status


# ============================================================== S6/S8 =======
class TestS8UploadVsEsbParityScaledMix:
    """A scaled-down stand-in for the UAC's 30-document fixture (the coordinator's
    listed mix: open, partial, closed, cancelled, one unknown-product line, one
    unknown-location line, one new customer named, one unclassified agent) - 8
    documents, one per case, same reduction-in-scope pattern every parity test
    in this UAC already documents in its own docstring. Diffs
    `sales_order_lines` keyed by (so_number, product code, warehouse code),
    excluding ids/timestamps/source_system/source_ref/company_id.

    REGRESSION GUARD, not red - this actually PASSES today across all 8 cases
    (verified empirically, not assumed). Kept as the coordinator's own
    "make AC-P2-7 a real parity test" ask, and as a guard against a future
    change silently breaking any one of the 8 cases."""

    def test_eight_document_mix_lands_the_same_shape_on_both_channels(self, env):
        from app.models.base import set_company_scope
        from app.models.company import Company
        from app.models.product import Product, ProductCategory, UnitOfMeasure
        from app.services.document_ingest_service import DocumentIngestService

        other = Company(id=str(uuid.uuid4()), name=f"{MARKER} B", code=f"ZZS{uuid.uuid4().hex[:6]}")
        env.db.add(other)
        env.db.flush()
        company_b = str(other.id)

        def _push_a(number, lines, **extra):
            record = _so_record(env, number=number, lines=lines, **extra)
            res = env.post(INGEST_SO, [record])
            assert res.status_code == 200, res.text
            return res.json()["records"][0]

        cases = []
        # open
        n1 = f"{MARKER}-OPEN-{uuid.uuid4().hex[:6]}"
        cases.append((n1, [_so_line(env, qty_ordered=10, qty_delivered=0)], {}))
        # partial
        n2 = f"{MARKER}-PART-{uuid.uuid4().hex[:6]}"
        cases.append((n2, [_so_line(env, qty_ordered=10, qty_delivered=4)], {"status": "partial"}))
        # closed (fully delivered, no status stated)
        n3 = f"{MARKER}-CLOS-{uuid.uuid4().hex[:6]}"
        cases.append((n3, [_so_line(env, qty_ordered=10, qty_delivered=10)], {"status": None}))
        # cancelled
        n4 = f"{MARKER}-CANC-{uuid.uuid4().hex[:6]}"
        cases.append((n4, [_so_line(env, qty_ordered=10, qty_delivered=0)], {"status": "cancelled"}))

        results_a = {}
        for number, lines, extra in cases:
            extra = dict(extra)
            status = extra.pop("status", "open")
            payload = {"status": status} if status is not None else {}
            record = _so_record(env, number=number, lines=lines, **payload)
            res = env.post(INGEST_SO, [record])
            assert res.status_code == 200, res.text
            results_a[number] = res.json()["records"][0]

        # unknown-product line (co-exists with a resolvable one)
        n5 = f"{MARKER}-BADPRD-{uuid.uuid4().hex[:6]}"
        good = _so_line(env)
        bad = {"source_ref": _ref("SOL"), "product_code": f"{DOC_MARKER}-NOSUCH", "qty_ordered": 3}
        results_a[n5] = _push_a(n5, [good, bad])

        # unknown-location line
        n6 = f"{MARKER}-BADLOC-{uuid.uuid4().hex[:6]}"
        loc_line = _so_line(env, warehouse_code=f"{DOC_MARKER}-NOSUCHLOC")
        results_a[n6] = _push_a(n6, [loc_line])

        # new customer named by code+name
        n7 = f"{MARKER}-NEWCUST-{uuid.uuid4().hex[:6]}"
        new_debtor = f"{MARKER}-DEB-{uuid.uuid4().hex[:6]}".upper()
        results_a[n7] = _push_a(
            n7, [_so_line(env)], customer_code=new_debtor, customer_name="New Co"
        )

        # unclassified agent (no demand class ladder answers) - agent_code new,
        # no order_type stated; assert it lands rather than refuses (D23).
        n8 = f"{MARKER}-UNCLASS-{uuid.uuid4().hex[:6]}"
        new_agent = f"{MARKER}-AGT-{uuid.uuid4().hex[:6]}".upper()
        results_a[n8] = _push_a(n8, [_so_line(env)], agent_code=new_agent)

        for number, result in results_a.items():
            assert result["outcome"] == "created", (number, result)

        # ---- ESB half, into company B: same 8 documents, same shape ----
        set_company_scope(env.db, frozenset({company_b}))
        category = ProductCategory(category_code=f"{MARKER}B", category_name="Cat")
        uom = UnitOfMeasure(uom_code=f"{MARKER}BU", uom_name="Each")
        env.db.add_all([category, uom])
        env.db.flush()
        product_a_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        product_code = env.db.execute(
            text("SELECT product_code FROM products WHERE id = :id"), {"id": product_a_id}
        ).scalar()
        product_b = Product(
            product_code=product_code, product_name="Item",
            category_id=category.id, base_uom_id=uom.id, list_price=0,
        )
        env.db.add(product_b)
        env.db.flush()
        esb = DocumentIngestService(env.db, integration_id=None, company_id=company_b)
        product_ref_b = f"DK-{product_code}-B"
        esb.refs.link(entity_type="products", entity_id=product_b.id, source_ref=product_ref_b)

        def _esb_push(so_number, lines, **extra):
            record = {
                "source_ref": f"DK-{so_number}", "so_number": so_number, "lines": lines,
            }
            record.update(extra)
            result = esb.ingest("sales_orders", [record])
            assert result.records[0].outcome is IngestOutcome.CREATED, result.records[0].errors

        _esb_push(n1, [{
            "source_ref": f"DK-{n1}-L1", "product_ref": product_ref_b, "product_code": product_code, "qty_ordered": "10",
        }])
        _esb_push(n2, [{
            "source_ref": f"DK-{n2}-L1", "product_ref": product_ref_b, "product_code": product_code, "qty_ordered": "10",
            "qty_delivered": "4",
        }], status="partial")
        _esb_push(n3, [{
            "source_ref": f"DK-{n3}-L1", "product_ref": product_ref_b, "product_code": product_code, "qty_ordered": "10",
            "qty_delivered": "10",
        }])
        _esb_push(n4, [{
            "source_ref": f"DK-{n4}-L1", "product_ref": product_ref_b, "product_code": product_code, "qty_ordered": "10",
        }], status="cancelled")
        _esb_push(n5, [
            {"source_ref": f"DK-{n5}-L1", "product_ref": product_ref_b, "product_code": product_code, "qty_ordered": "10"},
            {"source_ref": f"DK-{n5}-L2", "product_code": f"{DOC_MARKER}-NOSUCH",
             "qty_ordered": "3"},
        ])
        _esb_push(n6, [{
            "source_ref": f"DK-{n6}-L1", "product_ref": product_ref_b, "product_code": product_code, "qty_ordered": "10",
            "warehouse_code": f"{DOC_MARKER}-NOSUCHLOC",
        }])
        _esb_push(n7, [{
            "source_ref": f"DK-{n7}-L1", "product_ref": product_ref_b, "product_code": product_code, "qty_ordered": "10",
        }], customer_code=new_debtor, customer_name="New Co")
        _esb_push(n8, [{
            "source_ref": f"DK-{n8}-L1", "product_ref": product_ref_b, "product_code": product_code, "qty_ordered": "10",
        }], agent_code=new_agent)

        exclude = {
            "id", "sales_order_id", "product_id", "warehouse_id", "created_at",
            "updated_at", "created_by", "source_system", "source_ref", "company_id",
        }
        for number in (n1, n2, n3, n4, n5, n6, n7, n8):
            rows_a = env.db.execute(
                text(
                    "SELECT sol.* FROM sales_order_lines sol "
                    "JOIN sales_orders so ON so.id = sol.sales_order_id "
                    "WHERE so.so_number = :n AND so.company_id = :cid "
                    "ORDER BY sol.qty_ordered, sol.id"
                ),
                {"n": number, "cid": env.company_a},
            ).mappings().all()
            rows_b = env.db.execute(
                text(
                    "SELECT sol.* FROM sales_order_lines sol "
                    "JOIN sales_orders so ON so.id = sol.sales_order_id "
                    "WHERE so.so_number = :n AND so.company_id = :cid "
                    "ORDER BY sol.qty_ordered, sol.id"
                ),
                {"n": number, "cid": company_b},
            ).mappings().all()
            assert len(rows_a) == len(rows_b), (number, len(rows_a), len(rows_b))
            for row_a, row_b in zip(rows_a, rows_b):
                diff = {
                    k: (row_a[k], row_b[k])
                    for k in row_a.keys()
                    if k not in exclude and row_a[k] != row_b[k]
                }
                assert diff == {}, (number, diff)


class TestAcP24RealRow:
    """AC-P2-4's own "real row" ask: after a non-dry SO batch + hook, one
    `planning_change_batches` row with one `planning_change_rows` row per
    changed line. NOT PRODUCIBLE against a plain core SO: read
    `app/models/planning_change.py` - `PlanningChangeRow.project_sales_order_id`
    is a NOT NULL FK into `projects.sales_orders` (the Project Sales module's
    own mirror table), and nothing in `app/models/project_so.py` links a
    `projects.sales_orders` row back to a core `sales_orders` row by id (grep
    for `core_sales_order_id`-shaped column, zero hits). The batch this ESB
    hook builds (`planning_change_service.build_batch`, fed by
    `DocumentIngestService._capture_planning_diff_before/_after`) is fed by a
    core `sales_orders` diff and can only ever produce rows for SOs that ALSO
    have a Project Sales mirror - which an ESB-only SO, by construction, does
    not. This is evidence, not a red test: forcing a project mirror into
    existence here would test the Project Sales module's own linkage, not
    D10/AC-P2-4."""

    def test_evidence_only_no_forced_assertion(self):
        pytest.skip(
            "AC-P2-4's planning_change_rows shape is project-keyed "
            "(project_sales_order_id NOT NULL) and cannot be produced from a "
            "core-only SO document - see this class's own docstring for the "
            "code read that established this."
        )
