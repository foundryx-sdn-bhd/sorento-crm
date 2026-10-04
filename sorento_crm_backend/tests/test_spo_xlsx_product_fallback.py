"""RED tests for SPO-XLSX-SUPERSEDE round 2: Excel rows with no destination
superseded by the AutoCount split lines, and no false "incoming".

UAC: documentation/plans/autocount/spo-xlsx-product-fallback-acceptance-criteria.md (AC-F1..AC-F10).
PLAN: documentation/plans/autocount/PLAN-spo-xlsx-product-fallback.md (D31..D36).

The fixture is the owner's production case (PL GCXU6137164, SPO-2026/08-0074,
SRTWCX8605-S-RL-PJ), scaled to nothing: the Excel upload wrote one row of 95
(on the PL, location HQ, no warehouse) and one of 4 (no PL); an approved GR
picked 22 @ BRW-IB + 73 @ BRW-NTC against the 95 row and 4 @ BRW-NTC against
the 4 row; AutoCount then states two lines, BRW-IB 22 and BRW-NTC 77, both on
the PL and both fully received. Shipped 99.

Substrate reused from `tests/test_ingest_shipping_orders.py` (the `env`
fixture and the record builders), the same way `test_spo_xlsx_supersede.py`
reuses it.
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import text

from app.models.procurement import (
    InboundShipment,
    InboundShipmentLine,
    PickingHeader,
    PickingLine,
    SPOAllocation,
)
from tests._pg_fixture import unique_code
from tests.test_ingest_shipping_orders import (
    MARKER,
    _seed_legacy_row,
    _spo_line,
    _spo_record,
    _spo_rows,
    env,  # noqa: F401 - pytest fixture, imported for reuse
)

__all__ = ["env"]


# --------------------------------------------------------------------- fixture
@dataclass
class _Case:
    number: str
    product_id: str
    ib_ref: str
    ntc_ref: str
    ib_id: str
    ntc_id: str
    shipment_id: str
    shipment_line_id: str
    container: str
    excel_95: SPOAllocation
    excel_4: SPOAllocation
    pick_ib_22: PickingLine
    pick_ntc_73: PickingLine
    pick_ntc_4: PickingLine


def _container() -> str:
    # `extract_container_number` keys on four letters + seven digits.
    return f"ZZTU{random.randint(10**6, 10**7 - 1)}"


def _resolve_wh(env, ref: str) -> str:
    return env.refs.resolve(entity_type="warehouses", source_ref=ref)


def _pick(env, header_id, allocation_id, product_id, warehouse_id, qty) -> PickingLine:
    line = PickingLine(
        id=str(uuid.uuid4()),
        company_id=env.company_a,
        picking_header_id=header_id,
        spo_allocation_id=allocation_id,
        product_id=product_id,
        source_warehouse_id=warehouse_id,
        quantity_expected=qty,
        quantity_picked=qty,
    )
    env.db.add(line)
    env.db.flush()
    return line


def _owner_case(
    env, *, excel_warehouse: bool = False, with_receipts: bool = True, number: str | None = None
) -> _Case:
    """The owner's shape BEFORE any AutoCount push: two Excel rows, one GR."""
    ib_ref = env.link_warehouse(env.company_a)
    ntc_ref = env.link_warehouse(env.company_a)
    ib_id, ntc_id = _resolve_wh(env, ib_ref), _resolve_wh(env, ntc_ref)
    product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
    container = _container()
    shipment = InboundShipment(
        id=str(uuid.uuid4()),
        company_id=env.company_a,
        shipment_number=unique_code(f"{MARKER}-PL"),
        shipping_container_number=container,
        shipment_date=date(2026, 9, 1),
        estimated_arrival_date=date(2026, 9, 20),
        shipment_status="pending",
    )
    env.db.add(shipment)
    env.db.flush()
    shipment_line = InboundShipmentLine(
        id=str(uuid.uuid4()),
        company_id=env.company_a,
        shipment_id=shipment.id,
        product_id=product_id,
        quantity_shipped=99,
        quantity_received=0,
        line_status="in_transit",
    )
    env.db.add(shipment_line)
    env.db.flush()

    number = number or f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
    received_95, received_4 = (95, 4) if with_receipts else (0, 0)
    excel_95 = _seed_legacy_row(
        env,
        spo_number=number,
        spo_line_number=1,
        location_code="HQ",
        allocated_quantity=95,
        quantity_received=received_95,
        line_status="closed" if with_receipts else "open",
        inbound_shipment_id=shipment.id,
    )
    excel_4 = _seed_legacy_row(
        env,
        spo_number=number,
        spo_line_number=2,
        location_code="HQ",
        allocated_quantity=4,
        quantity_received=received_4,
        line_status="closed" if with_receipts else "open",
    )
    if excel_warehouse:
        # A destination AutoCount never names: the keyed pass finds no counterpart
        # (kept, not D26a-locked), so only the fallback could ever pair these rows.
        other_id = _resolve_wh(env, env.link_warehouse(env.company_a))
        excel_95.warehouse_id = other_id
        excel_4.warehouse_id = other_id
        env.db.flush()

    picks = [None, None, None]
    if with_receipts:
        header = PickingHeader(
            id=str(uuid.uuid4()),
            company_id=env.company_a,
            picking_number=unique_code(f"{MARKER}-GR"),
            picking_type="goods_received",
            picking_status="approved",
            spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        picks = [
            _pick(env, header.id, excel_95.id, product_id, ib_id, 22),
            _pick(env, header.id, excel_95.id, product_id, ntc_id, 73),
            _pick(env, header.id, excel_4.id, product_id, ntc_id, 4),
        ]
    env.db.commit()
    return _Case(
        number=number,
        product_id=product_id,
        ib_ref=ib_ref,
        ntc_ref=ntc_ref,
        ib_id=ib_id,
        ntc_id=ntc_id,
        shipment_id=str(shipment.id),
        shipment_line_id=str(shipment_line.id),
        container=container,
        excel_95=excel_95,
        excel_4=excel_4,
        pick_ib_22=picks[0],
        pick_ntc_73=picks[1],
        pick_ntc_4=picks[2],
    )


def _autocount_record(env, case: _Case, *, ib_qty=22, ntc_qty=77, received=True) -> dict:
    return _spo_record(
        env,
        number=case.number,
        container_number=case.container,
        supplier_ref=env.supplier_ref,
        lines=[
            _spo_line(
                env,
                warehouse_ref=case.ib_ref,
                qty_ordered=ib_qty,
                qty_received=ib_qty if received else 0,
                line_number=1,
            ),
            _spo_line(
                env,
                warehouse_ref=case.ntc_ref,
                qty_ordered=ntc_qty,
                qty_received=ntc_qty if received else 0,
                line_number=2,
            ),
        ],
    )


def _push(env, record: dict, *, may_delete: bool = True):
    """The route's two halves: the ingest, then its post-commit shipment refresh."""
    from app.services.procurement_service import InboundShipmentService
    from app.services.shipping_order_ingest_service import ShippingOrderIngestService

    svc = ShippingOrderIngestService(
        env.db, integration_id=None, company_id=env.company_a, may_delete=may_delete
    )
    result = svc.ingest("shipping_orders", [record])
    env.db.commit()
    inbound = InboundShipmentService(env.db)
    for shipment_id in sorted(svc.shipment_ids_touched):
        inbound.refresh_shipment_line_statuses(shipment_id)
    return result.records[0]


def _row_by_wh(rows, warehouse_id: str):
    matches = [r for r in rows if str(r["warehouse_id"] or "") == str(warehouse_id)]
    assert len(matches) == 1, rows
    return matches[0]


def _picked_on(env, allocation_id) -> int:
    return int(
        env.db.execute(
            text(
                "SELECT coalesce(sum(quantity_picked), 0) FROM picking_lines "
                "WHERE spo_allocation_id = :a"
            ),
            {"a": str(allocation_id)},
        ).scalar()
    )


def _pl_figures(env, case: _Case) -> tuple[int, int, str]:
    """(SPO allocated, received, line status) exactly as the PL detail computes them
    (`api/v1/procurement/packing_lists.py`: the visible allocation total, the received
    reader, and the status recomputed from those two - the persisted `line_status`
    stays unfiltered by design, AC-H17 as narrowed)."""
    from sqlalchemy import func

    from app.services.procurement_service import (
        InboundShipmentService,
        compute_inbound_shipment_line_status,
    )
    from app.services.scm import spo_supply

    service = InboundShipmentService(env.db)
    service.refresh_shipment_line_statuses(case.shipment_id)
    allocated = (
        env.db.query(func.coalesce(func.sum(SPOAllocation.allocated_quantity), 0))
        .filter(
            SPOAllocation.inbound_shipment_id == case.shipment_id,
            SPOAllocation.product_id == case.product_id,
            *spo_supply.visible_line_clauses(),
        )
        .scalar()
    )
    received = service.get_received_quantities_by_product(case.shipment_id).get(
        str(case.product_id), 0
    )
    shipped = env.db.execute(
        text("SELECT quantity_shipped FROM inbound_shipment_lines WHERE id = :id"),
        {"id": case.shipment_line_id},
    ).scalar()
    status = compute_inbound_shipment_line_status(int(shipped), int(allocated), int(received))
    return int(allocated), int(received), status


def _incoming_shipments(env, case: _Case) -> list[dict]:
    from app.services.incoming_stock_service import IncomingStockService

    result = IncomingStockService(env.db).incoming_for_product(product_id=case.product_id)
    return [
        shipment
        for product in result.get("data", [])
        for shipment in product["shipments"]
        if shipment["shipping_container_number"] == case.container
    ]


def _seed_pre_repair_state(env, case: _Case) -> list[SPOAllocation]:
    """What production holds today: the push appended the AutoCount lines and
    left the Excel rows (closed, GR picks still on them)."""
    rows = []
    for spo_line_number, (wh_id, wh_code_ref, qty) in enumerate(
        ((case.ib_id, case.ib_ref, 22), (case.ntc_id, case.ntc_ref, 77)), start=3
    ):
        code = env.db.execute(
            text("SELECT warehouse_code FROM warehouses WHERE id = :id"), {"id": wh_id}
        ).scalar()
        row = SPOAllocation(
            company_id=env.company_a,
            spo_number=case.number,
            spo_line_number=spo_line_number,
            product_id=case.product_id,
            warehouse_id=wh_id,
            location_code=code,
            allocated_quantity=qty,
            quantity_received=qty,
            stated_received=qty,
            receipt_status="fully_received",
            line_status="closed",
            source_system="autocount",
            source_ref=f"{MARKER}:DTL-{uuid.uuid4().hex[:8]}",
            source_doc_ref=f"{MARKER}:DOC-{case.number}",
            inbound_shipment_id=case.shipment_id,
            container_number=case.container,
        )
        env.db.add(row)
        rows.append(row)
    env.db.flush()
    env.db.commit()
    return rows


# ============================================================================ #
# AC-F1 / AC-F2 / AC-F3 / AC-F4: the owner case, first push, delete grant
# ============================================================================ #
class TestAcF1OwnerCaseFirstPush:
    def test_excel_rows_are_superseded_by_the_autocount_split_lines(self, env):
        """AC-F1. RED today: the Excel group `(p, loc:HQ)` meets no AutoCount
        key, so both Excel rows are kept (closed) beside the two new lines -
        four rows, and no `superseded` count on the verdict."""
        case = _owner_case(env)
        entry = _push(env, _autocount_record(env, case))

        assert entry.lines.get("superseded") == 2, entry.lines
        rows = _spo_rows(env, case.number)
        assert len(rows) == 2, [(r["allocated_quantity"], r["source_ref"]) for r in rows]
        ib = _row_by_wh(rows, case.ib_id)
        ntc = _row_by_wh(rows, case.ntc_id)
        assert (ib["allocated_quantity"], ib["quantity_received"]) == (22, 22)
        assert (ntc["allocated_quantity"], ntc["quantity_received"]) == (77, 77)
        assert ib["line_status"] == ntc["line_status"] == "closed"
        assert ib["receipt_status"] == ntc["receipt_status"] == "fully_received"

    def test_receipt_picks_move_to_the_autocount_lines_split_by_capacity(self, env):
        """AC-F2. RED today: the picks stay on the kept Excel rows."""
        case = _owner_case(env)
        _push(env, _autocount_record(env, case))

        rows = _spo_rows(env, case.number)
        ib = _row_by_wh(rows, case.ib_id)
        ntc = _row_by_wh(rows, case.ntc_id)
        assert _picked_on(env, ib["id"]) == 22
        assert _picked_on(env, ntc["id"]) == 77
        assert _picked_on(env, case.excel_95.id) == 0
        assert _picked_on(env, case.excel_4.id) == 0
        total = env.db.execute(
            text(
                "SELECT coalesce(sum(pl.quantity_picked), 0) FROM picking_lines pl "
                "JOIN picking_headers ph ON ph.id = pl.picking_header_id "
                "WHERE ph.spo_number = :n"
            ),
            {"n": case.number},
        ).scalar()
        assert int(total) == 99

    def test_pl_shows_allocated_99_received_99_received(self, env):
        """AC-F3. RED today: allocated 194, received 95, partially received."""
        case = _owner_case(env)
        _push(env, _autocount_record(env, case))
        assert _pl_figures(env, case) == (99, 99, "received")

    def test_chatbot_does_not_report_the_pl_as_incoming(self, env):
        """AC-F4. RED today: "Incoming 4, BRW-IB (22), BRW-NTC (77)"."""
        case = _owner_case(env)
        _push(env, _autocount_record(env, case))
        assert _incoming_shipments(env, case) == []


# ============================================================================ #
# AC-F5 / AC-F6: the fallback needs reconciling quantities and no warehouse
# ============================================================================ #
class TestAcF5FallbackGuards:
    def test_quantities_that_do_not_reconcile_keep_the_excel_rows(self, env):
        """AC-F5 (guard, green before and after): 95 + 4 against 22 + 80."""
        case = _owner_case(env)
        entry = _push(env, _autocount_record(env, case, ntc_qty=80))
        assert "superseded" not in entry.lines, entry.lines
        ids = {str(r["id"]) for r in _spo_rows(env, case.number)}
        assert {str(case.excel_95.id), str(case.excel_4.id)} <= ids
        assert _picked_on(env, case.excel_95.id) == 95

    def test_fewer_lines_than_rows_keep_the_excel_rows_too(self, env):
        """AC-F5, the other direction (review M4b): 95 + 4 against 22 + 73."""
        case = _owner_case(env)
        entry = _push(env, _autocount_record(env, case, ntc_qty=73))
        assert "superseded" not in entry.lines, entry.lines
        ids = {str(r["id"]) for r in _spo_rows(env, case.number)}
        assert {str(case.excel_95.id), str(case.excel_4.id)} <= ids

    def test_a_repush_never_fallback_pairs_a_new_line(self, env):
        """Review B1: the first push keeps the HQ rows (22 + 80 does not
        reconcile); a re-push adding a new, unreceived NTC 99 line must not pair
        them with it (99 == 95 + 4) and call it fully received."""
        case = _owner_case(env)
        record = _autocount_record(env, case, ntc_qty=80)
        _push(env, record)
        record["lines"].append(
            _spo_line(
                env, warehouse_ref=case.ntc_ref, qty_ordered=99, qty_received=0, line_number=3
            )
        )
        entry = _push(env, record)

        assert "superseded" not in entry.lines, entry.lines
        rows = _spo_rows(env, case.number)
        new_line = [r for r in rows if r["source_ref"] == record["lines"][2]["source_ref"]]
        assert len(new_line) == 1
        assert int(new_line[0]["quantity_received"] or 0) == 0
        assert new_line[0]["line_status"] == "open"
        assert _picked_on(env, case.excel_95.id) == 95

    def test_an_excel_row_naming_a_warehouse_is_never_fallback_paired(self, env):
        """AC-F6 (guard): the Excel rows name a warehouse AutoCount never
        names. The quantities reconcile (95 + 4 = 22 + 77), but a row with a
        destination is a statement about where the goods went, so the product
        fallback must not pair it with lines elsewhere."""
        case = _owner_case(env, excel_warehouse=True)
        entry = _push(env, _autocount_record(env, case))
        assert "superseded" not in entry.lines, entry.lines
        ids = {str(r["id"]) for r in _spo_rows(env, case.number)}
        assert {str(case.excel_95.id), str(case.excel_4.id)} <= ids


# ============================================================================ #
# AC-F7: no `.delete` grant - rows retired, not left visible
# ============================================================================ #
class TestAcF7ClosedOnlySupersedeRetires:
    def test_closed_only_supersede_retires_the_excel_rows(self, env):
        """AC-F7. RED today: kept (no supersede at all); and even a keyed
        closed-only supersede leaves the row visible with its receipt."""
        case = _owner_case(env)
        entry = _push(env, _autocount_record(env, case), may_delete=False)

        assert entry.lines.get("superseded") == 2, entry.lines
        by_id = {str(r["id"]): r for r in _spo_rows(env, case.number)}
        for excel in (case.excel_95, case.excel_4):
            row = by_id[str(excel.id)]
            assert row["line_status"] == "closed"
            assert row["retired_at"] is not None
            assert int(row["quantity_received"] or 0) == 0
            assert "superseded by" in (row["allocation_notes"] or "")
        # Security review N1: the zeroed receipt is frozen in the declared column.
        assert int(by_id[str(case.excel_95.id)]["stated_received"]) == 95
        assert int(by_id[str(case.excel_4.id)]["stated_received"]) == 4
        assert _picked_on(env, case.excel_95.id) == 0
        assert _pl_figures(env, case) == (99, 99, "received")


# ============================================================================ #
# AC-F8: future GRs land on the AutoCount lines
# ============================================================================ #
class TestAcF8FutureGrsLandOnAutocountLines:
    def _pool_ids(self, env, case):
        from app.services.grn_spo_matching import build_allocation_pool

        pool = build_allocation_pool(
            env.db,
            product_id=case.product_id,
            spo_number=case.number,
            company_id=env.company_a,
        )
        return {entry.allocation_id: entry.available for entry in pool}

    def test_pool_offers_only_autocount_lines_after_a_delete_supersede(self, env):
        case = _owner_case(env, with_receipts=False)
        _push(env, _autocount_record(env, case, received=False))
        rows = _spo_rows(env, case.number)
        ib = _row_by_wh(rows, case.ib_id)
        ntc = _row_by_wh(rows, case.ntc_id)
        assert self._pool_ids(env, case) == {str(ib["id"]): 22, str(ntc["id"]): 77}

    def test_pool_offers_only_autocount_lines_after_a_closed_only_supersede(self, env):
        """RED today: the retired-but-kept Excel rows (95 + 4, never received)
        are older, so FIFO would draw the next GR against them."""
        case = _owner_case(env, with_receipts=False)
        _push(env, _autocount_record(env, case, received=False), may_delete=False)
        rows = [r for r in _spo_rows(env, case.number) if r["source_ref"]]
        ib = _row_by_wh(rows, case.ib_id)
        ntc = _row_by_wh(rows, case.ntc_id)
        assert self._pool_ids(env, case) == {str(ib["id"]): 22, str(ntc["id"]): 77}


# ============================================================================ #
# AC-F9: no false incoming even before the repair runs
# ============================================================================ #
class TestAcF9NoFalseIncomingOnReceivedLines:
    def test_pre_repair_pl_reads_received_and_chatbot_is_silent(self, env):
        """AC-F9. RED today: received 95 (the 4 hangs off a PL-less row) so the
        line reads partially received and the chatbot says incoming 4."""
        case = _owner_case(env)
        _seed_pre_repair_state(env, case)
        allocated, received, _status = _pl_figures(env, case)
        # The duplicate allocation is real data until the repair removes it, and the
        # PL keeps saying so; what must stop is the false "still to come".
        assert (allocated, received) == (194, 99)
        assert _incoming_shipments(env, case) == []

    @staticmethod
    def _partly_received(env):
        case = _owner_case(env, with_receipts=False)
        ib_row, ntc_row = _seed_pre_repair_state(env, case)
        ntc_row.quantity_received = 0
        ntc_row.stated_received = None
        ntc_row.receipt_status = "pending"
        ntc_row.line_status = "open"
        env.db.delete(case.excel_95)
        env.db.delete(case.excel_4)
        env.db.commit()
        _pl_figures(env, case)  # refresh the stored line figures
        ntc_code = env.db.execute(
            text("SELECT warehouse_code FROM warehouses WHERE id = :id"), {"id": case.ntc_id}
        ).scalar()
        owed = [
            {
                "warehouse_code": ntc_code,
                "warehouse_name": f"{MARKER} depot",
                "allocated_quantity": 77,
            }
        ]
        return case, owed

    def test_incoming_list_shows_only_the_warehouse_still_owed(self, env):
        """Review M9: the n8n list endpoint emits the same outstanding list."""
        from app.services.incoming_stock_service import IncomingStockService

        case, owed = self._partly_received(env)
        result = IncomingStockService(env.db).incoming_list(shipment_ids=[case.shipment_id])
        lines = [line for ship in result["data"] for line in ship["lines"]]
        assert len(lines) == 1, result
        assert lines[0]["warehouse_allocations"] == owed

    def test_shipment_incoming_products_shows_only_the_warehouse_still_owed(self, env):
        """Review M10: the per-shipment drill-down emits the same outstanding list."""
        from app.services.incoming_stock_service import IncomingStockService

        case, owed = self._partly_received(env)
        result = IncomingStockService(env.db).shipment_incoming_products(case.shipment_id)
        products = result["data"]["products"]
        assert len(products) == 1, result
        assert products[0]["warehouse_allocations"] == owed

    def test_partly_received_pl_lists_only_the_warehouse_still_owed(self, env):
        """AC-F9. AutoCount IB 22 received, NTC 77 not yet: the chatbot still
        lists the PL (77 to come) but only BRW-NTC, never BRW-IB (22).
        RED today: both warehouses listed at their full allocation."""
        case = _owner_case(env, with_receipts=False)
        ib_row, ntc_row = _seed_pre_repair_state(env, case)
        ntc_row.quantity_received = 0
        ntc_row.stated_received = None
        ntc_row.receipt_status = "pending"
        ntc_row.line_status = "open"
        # The Excel rows were never on the books for this variant.
        env.db.delete(case.excel_95)
        env.db.delete(case.excel_4)
        env.db.commit()

        _pl_figures(env, case)  # refresh the stored line figures
        shipments = _incoming_shipments(env, case)
        assert len(shipments) == 1, shipments
        assert shipments[0]["remaining_incoming_quantity"] == 77
        ntc_code = env.db.execute(
            text("SELECT warehouse_code FROM warehouses WHERE id = :id"), {"id": case.ntc_id}
        ).scalar()
        assert shipments[0]["warehouse_allocations"] == [
            {
                "warehouse_code": ntc_code,
                "warehouse_name": f"{MARKER} depot",
                "allocated_quantity": 77,
            }
        ]
        # The gap arithmetic still measures the full allocation: 99 shipped, 99 allocated.
        assert shipments[0]["unallocated_quantity"] is None


# ============================================================================ #
# AC-F10: the repair script
# ============================================================================ #
class TestAcF10RepairScript:
    def _run(self, env, *, dry_run):
        from scripts import dedupe_spo_xlsx_superseded as script

        return script.run(env.db, env.company_a, dry_run=dry_run)

    def test_dry_run_writes_nothing_and_reports_the_scope(self, env, capsys):
        """RED today: the Excel group has no keyed counterpart, so the script
        reports nothing for this SPO and has no PL count at all."""
        case = _owner_case(env)
        _seed_pre_repair_state(env, case)
        before = sorted(
            (str(r["id"]), r["quantity_received"]) for r in _spo_rows(env, case.number)
        )

        summary = self._run(env, dry_run=True)

        out = capsys.readouterr().out
        assert case.number in out
        assert summary["documents"] >= 1
        assert summary["shipments"] >= 1
        after = sorted(
            (str(r["id"]), r["quantity_received"]) for r in _spo_rows(env, case.number)
        )
        assert after == before
        assert _picked_on(env, case.excel_95.id) == 95

    def test_apply_repairs_the_owner_case_and_is_idempotent(self, env):
        case = _owner_case(env)
        ib_row, ntc_row = _seed_pre_repair_state(env, case)
        assert _pl_figures(env, case)[0] == 194  # the owner's screen, reproduced

        self._run(env, dry_run=False)

        rows = _spo_rows(env, case.number)
        assert {str(r["id"]) for r in rows} == {str(ib_row.id), str(ntc_row.id)}
        assert _picked_on(env, ib_row.id) == 22
        assert _picked_on(env, ntc_row.id) == 77
        assert _pl_figures(env, case) == (99, 99, "received")
        assert _incoming_shipments(env, case) == []

        second = self._run(env, dry_run=False)
        assert case.number not in str(second)
        assert {str(r["id"]) for r in _spo_rows(env, case.number)} == {
            str(ib_row.id),
            str(ntc_row.id),
        }


# ============================================================================ #
# D33 guard: a supersede that would lose a receipt aborts
# ============================================================================ #
class TestD33ConservationGuard:
    def test_replacement_holding_less_than_the_removed_receipt_raises(self, env):
        import pytest

        from app.services.rules.shipping_order_rules import (
            SupersedeNotConserved,
            assert_supersede_conserved,
        )

        with pytest.raises(SupersedeNotConserved):
            assert_supersede_conserved(env.db, [], 99, 99, 95)

    def test_a_carry_that_drops_a_remainder_raises(self, env):
        import pytest

        from app.services.rules.shipping_order_rules import (
            SupersedeNotConserved,
            assert_supersede_conserved,
        )

        with pytest.raises(SupersedeNotConserved):
            assert_supersede_conserved(env.db, [], 99, 95, 99)

    def test_a_pick_still_on_a_removed_row_raises(self, env):
        import pytest

        from app.services.rules.shipping_order_rules import (
            SupersedeNotConserved,
            assert_supersede_conserved,
        )

        case = _owner_case(env)
        with pytest.raises(SupersedeNotConserved):
            assert_supersede_conserved(env.db, [str(case.excel_95.id)], 95, 95, 95)

    def test_a_conserved_supersede_passes(self, env):
        from app.services.rules.shipping_order_rules import assert_supersede_conserved

        case = _owner_case(env, with_receipts=False)
        assert_supersede_conserved(env.db, [str(case.excel_95.id)], 0, 0, 0)

    def test_a_push_that_would_strand_a_pick_lands_nothing(self, env, monkeypatch):
        """End to end: if the pick move ever failed, the record FAILS and the
        Excel rows keep their receipt - nothing is zeroed or deleted."""
        from app.services.rules import shipping_order_rules

        monkeypatch.setattr(
            shipping_order_rules, "repoint_picking_lines_by_capacity", lambda *a, **k: 0
        )
        monkeypatch.setattr(
            shipping_order_rules, "repoint_allocation_dependants", lambda *a, **k: 0
        )
        case = _owner_case(env)
        entry = _push(env, _autocount_record(env, case), may_delete=False)

        assert entry.outcome.value == "failed", entry
        by_id = {str(r["id"]): r for r in _spo_rows(env, case.number)}
        assert int(by_id[str(case.excel_95.id)]["quantity_received"]) == 95
        assert by_id[str(case.excel_95.id)]["retired_at"] is None
        assert _picked_on(env, case.excel_95.id) == 95


# ============================================================================ #
# Security review round: S1 cap, S3 abort, N2 acceptance split
# ============================================================================ #
class TestSecurityReviewFixes:
    def test_stated_receipt_above_the_line_never_marks_more_received(self, env):
        """S1: a pushed TransferedQty of 500 on a 77 line counts as 77."""
        case = _owner_case(env, with_receipts=False)
        ib_row, ntc_row = _seed_pre_repair_state(env, case)
        ib_row.stated_received = 0
        ib_row.quantity_received = 0
        ntc_row.stated_received = 500
        env.db.delete(case.excel_95)
        env.db.delete(case.excel_4)
        env.db.commit()
        _allocated, received, _status = _pl_figures(env, case)
        assert received == 77

    def test_a_pick_split_across_two_lines_splits_its_acceptance(self, env):
        """N2: a 30-pick at IB (25 accepted) over IB 22 + NTC 77 becomes 22 + 8,
        accepted 22 + 3; the sum still accepts 25 and no chunk accepts more than
        it picked."""
        case = _owner_case(env)
        case.pick_ib_22.quantity_picked = 30
        case.pick_ib_22.quantity_expected = 30
        case.pick_ib_22.qty_accepted = 25
        case.pick_ntc_73.quantity_picked = 65
        case.pick_ntc_73.quantity_expected = 65
        env.db.commit()
        _push(env, _autocount_record(env, case))

        rows = _spo_rows(env, case.number)
        ib = _row_by_wh(rows, case.ib_id)
        ntc = _row_by_wh(rows, case.ntc_id)
        assert _picked_on(env, ib["id"]) == 22
        assert _picked_on(env, ntc["id"]) == 77
        chunks = env.db.execute(
            text(
                "SELECT spo_allocation_id, quantity_picked, quantity_expected, qty_accepted "
                "FROM picking_lines WHERE picking_header_id = :h AND source_warehouse_id = :w "
                "ORDER BY quantity_picked DESC"
            ),
            {"h": str(case.pick_ib_22.picking_header_id), "w": case.ib_id},
        ).all()
        assert [(str(a), p, e, q) for a, p, e, q in chunks] == [
            (str(ib["id"]), 22, 22, 22),
            (str(ntc["id"]), 8, 8, 3),
        ]

    def test_repair_aborts_a_document_whose_carry_would_not_conserve(self, env, monkeypatch, capsys):
        """S3: the guard refuses the document, it is named and counted, nothing of
        it is written, and the sweep finishes with a summary."""
        from app.services.rules import shipping_order_rules
        from scripts import dedupe_spo_xlsx_superseded as script

        real = shipping_order_rules.distribute_received

        def lossy(total, allocated):
            shares = real(total, allocated)
            shares[-1] = max(shares[-1] - 1, 0)
            return shares

        monkeypatch.setattr(shipping_order_rules, "distribute_received", lossy)
        case = _owner_case(env)
        _seed_pre_repair_state(env, case)

        for dry_run in (True, False):
            summary = script.run(env.db, env.company_a, dry_run=dry_run)
            assert summary["aborted"] >= 1
            assert f"{case.number}: ABORTED" in capsys.readouterr().out
        ids = {str(r["id"]) for r in _spo_rows(env, case.number)}
        assert {str(case.excel_95.id), str(case.excel_4.id)} <= ids
        assert _picked_on(env, case.excel_95.id) == 95


# ============================================================================ #
# Reviewer round: B2 pool, capacity, overflow, expected, dtl_key, locked lines
# ============================================================================ #
def _pool(env, case, *, exclude=()):
    from app.services.grn_spo_matching import build_allocation_pool

    return {
        entry.allocation_id: entry.available
        for entry in build_allocation_pool(
            env.db,
            product_id=case.product_id,
            spo_number=case.number,
            company_id=env.company_a,
            exclude_header_ids=exclude,
        )
    }


def _header(env, case) -> str:
    header = PickingHeader(
        id=str(uuid.uuid4()),
        company_id=env.company_a,
        picking_number=unique_code(f"{MARKER}-GR"),
        picking_type="goods_received",
        picking_status="approved",
        spo_number=case.number,
    )
    env.db.add(header)
    env.db.flush()
    return str(header.id)


class TestReviewerRound:
    def test_reimport_finds_a_retired_line_that_still_carries_its_picks(self, env):
        """Review B2: a retired AutoCount line with a pick from GRN H stays in
        the pool when H is re-imported, so the re-import does not draw a second
        copy of the receipt onto the live sibling."""
        case = _owner_case(env, with_receipts=False)
        ib_row, ntc_row = _seed_pre_repair_state(env, case)
        env.db.delete(case.excel_95)
        env.db.delete(case.excel_4)
        header_id = _header(env, case)
        _pick(env, header_id, ib_row.id, case.product_id, case.ib_id, 10)
        ib_row.retired_at = datetime.now(timezone.utc)
        env.db.commit()

        pool = _pool(env, case, exclude=[header_id])
        # Its own header's pick is excluded, so it offers its full 22 again.
        assert pool.get(str(ib_row.id)) == 22

    def test_repair_respects_existing_picks_and_overflows_onto_the_last_line(self, env):
        """Review M5 + M6: the IB line already carries a 10 pick from another GRN,
        so it takes only 12 more; what no line has room for lands on the LAST
        line (NTC), never the first."""
        from scripts import dedupe_spo_xlsx_superseded as script

        case = _owner_case(env)
        ib_row, ntc_row = _seed_pre_repair_state(env, case)
        _pick(env, _header(env, case), ib_row.id, case.product_id, case.ib_id, 10)
        env.db.commit()

        script.run(env.db, env.company_a, dry_run=False)

        assert _picked_on(env, ib_row.id) == 22
        assert _picked_on(env, ntc_row.id) == 87

    def _split_ib_pick(self, env, *, expected, dtl_key=None):
        case = _owner_case(env)
        case.pick_ib_22.quantity_picked = 30
        case.pick_ib_22.quantity_expected = expected
        case.pick_ib_22.dtl_key = dtl_key
        case.pick_ntc_73.quantity_picked = 65
        case.pick_ntc_73.quantity_expected = 65
        env.db.commit()
        _push(env, _autocount_record(env, case))
        rows = _spo_rows(env, case.number)
        ib = _row_by_wh(rows, case.ib_id)
        ntc = _row_by_wh(rows, case.ntc_id)
        chunks = env.db.execute(
            text(
                "SELECT spo_allocation_id, quantity_picked, quantity_expected "
                "FROM picking_lines WHERE picking_header_id = :h AND source_warehouse_id = :w "
                "ORDER BY quantity_picked DESC"
            ),
            {"h": str(case.pick_ib_22.picking_header_id), "w": case.ib_id},
        ).all()
        return ib, ntc, [(str(a), p, e) for a, p, e in chunks]

    def test_a_short_receipt_keeps_its_shortfall_on_the_last_chunk(self, env):
        """Review M7: expected 32, picked 30, split 22 / 8 -> expected 22 / 10."""
        ib, ntc, chunks = self._split_ib_pick(env, expected=32)
        assert chunks == [(str(ib["id"]), 22, 22), (str(ntc["id"]), 8, 10)]

    def test_an_over_pick_never_yields_a_negative_expectation(self, env):
        """Review should-fix 3: expected 25, picked 30 -> expected 22 / 3."""
        ib, ntc, chunks = self._split_ib_pick(env, expected=25)
        assert chunks == [(str(ib["id"]), 22, 22), (str(ntc["id"]), 8, 3)]

    def test_an_autocount_grn_line_is_never_split(self, env):
        """Review should-fix 1: a pick carrying a `dtl_key` moves whole."""
        ib, _ntc, chunks = self._split_ib_pick(env, expected=30, dtl_key=987654321)
        assert chunks == [(str(ib["id"]), 30, 30)]

    def test_a_locked_groups_line_is_never_handed_to_the_fallback(self):
        """Review should-fix 2 (pure): row A at IB 30/30 is D26a-locked by the
        single IB 10 line; HQ row B (10) must not take that line."""
        from types import SimpleNamespace

        from app.services.rules.shipping_order_rules import plan_xlsx_supersede

        def row(row_id, warehouse_id, location, allocated, received, number):
            return SimpleNamespace(
                id=row_id, product_id="P", warehouse_id=warehouse_id,
                location_code=location, allocated_quantity=allocated,
                quantity_received=received, spo_line_number=number,
                inbound_shipment_id=None, storage_zone_id=None, uom_id=None,
                quantity_rejected=0, allocation_notes=None,
            )

        plan = plan_xlsx_supersede(
            [{"product_id": "P", "warehouse_id": "IB", "location_code": "IB",
              "allocated_quantity": 10, "line_number": 1}],
            [row("A", "IB", "IB", 30, 30, 1), row("B", None, "HQ", 10, 0, 2)],
        )
        assert plan.groups == ()
        assert [g.reason for g in plan.locked_groups] == ["received_locked"]

    def test_a_pick_on_a_rejected_grn_holds_no_capacity(self, env):
        """Review should-fix 4: a 10 pick on a REJECTED GRN does not use the IB
        line's room, so the Excel 22 @ IB lands there whole."""
        from scripts import dedupe_spo_xlsx_superseded as script

        case = _owner_case(env)
        ib_row, ntc_row = _seed_pre_repair_state(env, case)
        rejected = _header(env, case)
        env.db.execute(
            text("UPDATE picking_headers SET picking_status = 'rejected' WHERE id = :id"),
            {"id": rejected},
        )
        _pick(env, rejected, ib_row.id, case.product_id, case.ib_id, 10)
        env.db.commit()

        script.run(env.db, env.company_a, dry_run=False)

        moved_to_ib = env.db.execute(
            text(
                "SELECT coalesce(sum(quantity_picked), 0) FROM picking_lines "
                "WHERE spo_allocation_id = :a AND picking_header_id <> :h"
            ),
            {"a": str(ib_row.id), "h": rejected},
        ).scalar()
        assert int(moved_to_ib) == 22


# ============================================================================ #
# --spo: the owner's one-SPO production repair (SPO-2026/08-0074)
# ============================================================================ #
OWNER_SPO = "SPO-2026/08-0074"


class TestScopedRepair:
    def test_spo_option_limits_the_sweep_to_the_named_document(self, env):
        """Only SPO-2026/08-0074 changes; another broken SPO in the same company
        is left exactly as it was."""
        from scripts import dedupe_spo_xlsx_superseded as script

        target = _owner_case(env, number=OWNER_SPO)
        target_ib, target_ntc = _seed_pre_repair_state(env, target)
        other = _owner_case(env)
        _seed_pre_repair_state(env, other)
        other_before = sorted(
            (str(r["id"]), r["quantity_received"]) for r in _spo_rows(env, other.number)
        )

        summary = script.run(env.db, env.company_a, dry_run=False, spo_numbers=[OWNER_SPO])

        assert summary["documents"] == 1
        assert {str(r["id"]) for r in _spo_rows(env, OWNER_SPO)} == {
            str(target_ib.id),
            str(target_ntc.id),
        }
        assert sorted(
            (str(r["id"]), r["quantity_received"]) for r in _spo_rows(env, other.number)
        ) == other_before
        assert _picked_on(env, other.excel_95.id) == 95

    def test_spo_option_never_crosses_the_company_guard(self, env):
        """The same number under ANOTHER company is not touched by a run scoped
        to company A."""
        from scripts import dedupe_spo_xlsx_superseded as script

        summary = script.run(env.db, env.company_b, dry_run=False, spo_numbers=[OWNER_SPO])
        assert summary["documents"] == 0

    def test_dry_run_prints_the_per_spo_plan(self, env, capsys):
        """Rows to delete (ids + quantities), quantity carried per line, links
        moved - printed in a dry run, nothing written."""
        from scripts import dedupe_spo_xlsx_superseded as script

        case = _owner_case(env, number=OWNER_SPO)
        ib_row, ntc_row = _seed_pre_repair_state(env, case)

        script.run(env.db, env.company_a, dry_run=True, spo_numbers=[OWNER_SPO])

        out = capsys.readouterr().out
        assert f"{OWNER_SPO}:" in out
        assert f"delete {case.excel_95.id} (allocated 95, received 95)" in out
        assert f"delete {case.excel_4.id} (allocated 4, received 4)" in out
        assert f"carry 22 -> {ib_row.id}" in out
        assert f"carry 77 -> {ntc_row.id}" in out
        assert "links moved 3" in out
        assert {str(r["id"]) for r in _spo_rows(env, OWNER_SPO)} >= {
            str(case.excel_95.id),
            str(case.excel_4.id),
        }

    def test_cli_accepts_a_repeatable_spo_option(self):
        from scripts import dedupe_spo_xlsx_superseded as script

        args = script.build_parser().parse_args(
            ["--company", "SRT", "--spo", OWNER_SPO, "--spo", "SPO-2026/09-0001"]
        )
        assert args.spo == [OWNER_SPO, "SPO-2026/09-0001"]
        assert args.apply is False


# ============================================================================ #
# Owner ruling "just follow AutoCount": orphan Excel rows (product AutoCount
# does not list on the document at all) - SPO-2026/08-0074 L23/L26 shape
# ============================================================================ #
def _orphan_row(env, case: _Case, *, line: int, received: int = 0) -> SPOAllocation:
    """An Excel row for a product the AutoCount line-set never names
    (SRTWCY8605 beside AutoCount's SRTWCY8605-PJ): 99 ordered, `received`."""
    product_id = env.refs.resolve(entity_type="products", source_ref=env.product2_ref)
    row = SPOAllocation(
        company_id=env.company_a,
        spo_number=case.number,
        spo_line_number=line,
        product_id=product_id,
        location_code="HQ",
        allocated_quantity=99,
        quantity_received=received,
        receipt_status="pending" if received < 99 else "fully_received",
        line_status="open",
        source_system="scm_upload",
    )
    env.db.add(row)
    env.db.flush()
    env.db.commit()
    return row


class TestOrphanExcelRows:
    def _run(self, env, *, dry_run):
        from scripts import dedupe_spo_xlsx_superseded as script

        return script.run(env.db, env.company_a, dry_run=dry_run, spo_numbers=[OWNER_SPO])

    def test_an_unreceived_unlinked_orphan_is_removed(self, env, capsys):
        case = _owner_case(env, number=OWNER_SPO)
        ib_row, ntc_row = _seed_pre_repair_state(env, case)
        orphan_23 = _orphan_row(env, case, line=23)
        orphan_26 = _orphan_row(env, case, line=26)

        dry = self._run(env, dry_run=True)
        out = capsys.readouterr().out
        assert f"orphan delete {orphan_23.id}" in out
        assert f"orphan delete {orphan_26.id}" in out
        assert dry["orphans_removed"] == 2
        assert {str(orphan_23.id), str(orphan_26.id)} <= {
            str(r["id"]) for r in _spo_rows(env, OWNER_SPO)
        }

        summary = self._run(env, dry_run=False)
        assert summary["orphans_removed"] == 2
        # 2 superseded Excel rows + 2 orphans gone; only AutoCount remains.
        assert {str(r["id"]) for r in _spo_rows(env, OWNER_SPO)} == {
            str(ib_row.id),
            str(ntc_row.id),
        }

    def test_an_orphan_with_a_receipt_is_blocked(self, env, capsys):
        case = _owner_case(env, number=OWNER_SPO)
        _seed_pre_repair_state(env, case)
        orphan = _orphan_row(env, case, line=23, received=5)

        summary = self._run(env, dry_run=False)

        out = capsys.readouterr().out
        assert f"ORPHAN-BLOCKED {orphan.id}" in out
        assert "received 5" in out
        assert summary["orphans_blocked"] == 1
        assert str(orphan.id) in {str(r["id"]) for r in _spo_rows(env, OWNER_SPO)}

    def test_an_orphan_with_a_link_is_blocked(self, env, capsys):
        case = _owner_case(env, number=OWNER_SPO)
        _seed_pre_repair_state(env, case)
        orphan = _orphan_row(env, case, line=26)
        from app.models.scm import OrderLinkClaim

        env.db.add(
            OrderLinkClaim(
                company_id=env.company_a,
                so_number=f"{MARKER}-SO-{uuid.uuid4().hex[:8]}",
                po_number=OWNER_SPO,
                source="autocount",
                spo_allocation_id=orphan.id,
            )
        )
        env.db.commit()

        summary = self._run(env, dry_run=False)

        out = capsys.readouterr().out
        assert f"ORPHAN-BLOCKED {orphan.id}" in out
        assert "picks 0, claims 1, order-inquiry links 0" in out
        assert summary["orphans_blocked"] == 1
        assert str(orphan.id) in {str(r["id"]) for r in _spo_rows(env, OWNER_SPO)}


# ============================================================================ #
# Owner ruling (after #1411): follow AutoCount ACROSS warehouses in the repair.
# SPO-2026/08-0074 L1-6 shape: Excel rows at BRW, AutoCount lines at BRW-NTC.
# ============================================================================ #
class TestFollowAutocountAcrossWarehouses:
    def _run(self, env, *, dry_run=False):
        from scripts import dedupe_spo_xlsx_superseded as script

        return script.run(env.db, env.company_a, dry_run=dry_run, spo_numbers=[OWNER_SPO])

    def test_excel_rows_at_another_warehouse_are_superseded_onto_the_autocount_lines(self, env, capsys):
        case = _owner_case(env, excel_warehouse=True, number=OWNER_SPO)
        ib_row, ntc_row = _seed_pre_repair_state(env, case)
        for row in (ib_row, ntc_row):  # AutoCount has not stated the receipt yet
            row.quantity_received = 0
            row.stated_received = None
            row.line_status = "open"
            row.receipt_status = "pending"
        env.db.commit()
        excel_ids = {str(case.excel_95.id), str(case.excel_4.id)}

        summary = self._run(env)

        assert summary["rows_removed"] == 2, capsys.readouterr().out
        rows = _spo_rows(env, OWNER_SPO)
        assert not excel_ids & {str(r["id"]) for r in rows}
        ib = _row_by_wh(rows, case.ib_id)
        ntc = _row_by_wh(rows, case.ntc_id)
        assert (ib["quantity_received"], ib["line_status"]) == (22, "closed")
        assert (ntc["quantity_received"], ntc["line_status"]) == (77, "closed")
        assert _picked_on(env, ib["id"]) == 22
        assert _picked_on(env, ntc["id"]) == 77

    def test_autocount_lines_too_small_for_the_receipt_keep_the_excel_rows(self, env):
        case = _owner_case(env, excel_warehouse=True, number=OWNER_SPO)
        ib_row, ntc_row = _seed_pre_repair_state(env, case)
        ntc_row.allocated_quantity = 50  # 22 + 50 < 99 already received
        env.db.commit()
        excel_ids = {str(case.excel_95.id), str(case.excel_4.id)}

        self._run(env)

        assert excel_ids <= {str(r["id"]) for r in _spo_rows(env, OWNER_SPO)}
        assert _picked_on(env, case.excel_95.id) == 95

    def test_the_ingest_push_still_never_pairs_across_warehouses(self, env):
        """The ruling is for the repair; a live push keeps D31 (warehouse-less
        rows, exact quantities) - AC-F6 is unchanged."""
        case = _owner_case(env, excel_warehouse=True)
        entry = _push(env, _autocount_record(env, case))
        assert "superseded" not in entry.lines, entry.lines
