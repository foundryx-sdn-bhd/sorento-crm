"""PL-STATUS-ROLLUP: the packing-list header status follows its lines.

Production report (12 Oct 2026), packing list TCLU8176397: three lines of 529, each
showing Received with SPO allocated 529 and Received 529 on the detail page, while the
Packing Lists page still said In Transit.

The detail page recomputes each line's status from the VISIBLE SPO allocation total
(`packing_lists.get_packing_list`, retired-and-never-received allocations left out per
PLAN-hide-retired-spo-lines), but the STORED `line_status` that the header rollup, the
list page and its Received/Open filters all read was computed from the unfiltered
total. One retired allocation AutoCount stopped naming doubles a product's allocated
figure (1058 against 529 received), so the stored line said `partially_received`, the
header never rolled up, and the list kept the container In Transit.

Fixture: that shape with made-up numbers - three products, 529 shipped / allocated /
received each, and one retired never-received 529 allocation left on the first product.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.company import Company
from app.models.inventory import Warehouse
from app.models.procurement import (
    InboundShipment,
    InboundShipmentLine,
    PickingHeader,
    PickingLine,
    SPOAllocation,
)
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.company_scope import register_company_scope_listeners
from app.services.procurement_service import InboundShipmentService

from ._pg_fixture import blank_session, unique_code

QTY = 529


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope_listeners()


@pytest.fixture
def db():
    with blank_session() as session:
        company_id = str(uuid.uuid4())
        code = unique_code("PLR")[:20]
        session.add(Company(id=company_id, name="Rollup Co", code=code))
        session.flush()
        set_company_scope(session, frozenset({company_id}))
        session.info["test_company_id"] = company_id
        session.info["test_company_code"] = code
        yield session


def _product(db) -> str:
    category_id, uom_id = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(ProductCategory(id=category_id, category_code=unique_code("C")[:50], category_name="C"))
    db.add(UnitOfMeasure(id=uom_id, uom_code=unique_code("U")[:20], uom_name="Each"))
    db.flush()
    product_id = str(uuid.uuid4())
    code = unique_code("P")[:50]
    db.add(
        Product(
            id=product_id, product_code=code, product_name=code,
            category_id=category_id, base_uom_id=uom_id, list_price=10, is_active=True,
        )
    )
    db.flush()
    return product_id


def _container(db, received: list[int], *, header: str = "in_transit", retired_on_first: bool = False) -> dict:
    """One container, one line per entry in `received` (529 shipped and allocated each,
    that many received through an approved GRN). The stored line figures start at the
    in-transit defaults so the refresh under test is what writes them."""
    warehouse_id = str(uuid.uuid4())
    db.add(Warehouse(id=warehouse_id, warehouse_code=unique_code("WH")[:50], warehouse_name="WH"))
    shipment_id = str(uuid.uuid4())
    container = unique_code("TCLU")[:100]
    db.add(
        InboundShipment(
            id=shipment_id,
            shipment_number=unique_code("SHP")[:50],
            shipping_container_number=container,
            shipment_date=date(2026, 9, 8),
            estimated_arrival_date=date(2026, 9, 19),
            shipment_status=header,
        )
    )
    db.flush()
    line_ids: list[str] = []
    for index, recv in enumerate(received):
        product_id = _product(db)
        line_id = str(uuid.uuid4())
        line_ids.append(line_id)
        db.add(
            InboundShipmentLine(
                id=line_id, shipment_id=shipment_id, product_id=product_id,
                quantity_shipped=QTY, cartons_count=1,
                spo_allocated_quantity=0, quantity_received=0, line_status="in_transit",
            )
        )
        spo_number = f"SPO-2026/09-{uuid.uuid4().hex[:6]}"
        allocation_id = str(uuid.uuid4())
        db.add(
            SPOAllocation(
                id=allocation_id, spo_number=spo_number, inbound_shipment_id=shipment_id,
                warehouse_id=warehouse_id, product_id=product_id,
                allocated_quantity=QTY, quantity_received=recv,
            )
        )
        if index == 0 and retired_on_first:
            # The line AutoCount stopped naming: retired, never received - hidden
            # from every listing (`spo_supply.visible_line_clauses`).
            db.add(
                SPOAllocation(
                    id=str(uuid.uuid4()), spo_number=f"{spo_number}-OLD", inbound_shipment_id=shipment_id,
                    warehouse_id=warehouse_id, product_id=product_id,
                    allocated_quantity=QTY, quantity_received=0,
                    retired_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
                )
            )
        if recv:
            header_id = str(uuid.uuid4())
            db.add(
                PickingHeader(
                    id=header_id, picking_number=unique_code("GR")[:50], picking_type="goods_received",
                    picking_date=date(2026, 9, 25), picking_status="approved",
                    inspection_status="pending", spo_number=spo_number,
                )
            )
            db.flush()
            db.add(
                PickingLine(
                    id=str(uuid.uuid4()), picking_header_id=header_id, spo_allocation_id=allocation_id,
                    product_id=product_id, quantity_expected=QTY, quantity_picked=recv,
                    destination_warehouse_id=warehouse_id,
                )
            )
    db.commit()
    return {"shipment_id": shipment_id, "line_ids": line_ids, "container": container}


def _stored(db, shipment_id: str) -> tuple[str, list[tuple[int, int, str]]]:
    header = db.execute(
        text("SELECT shipment_status FROM inbound_shipments WHERE id = :id"), {"id": shipment_id}
    ).scalar()
    lines = db.execute(
        text(
            "SELECT spo_allocated_quantity, quantity_received, line_status FROM inbound_shipment_lines "
            "WHERE shipment_id = :id ORDER BY created_at, id"
        ),
        {"id": shipment_id},
    ).all()
    return header, sorted((int(a or 0), int(r or 0), s) for a, r, s in lines)


def _listed_status(db, shipment_id: str) -> str:
    result = InboundShipmentService(db).list_shipments(limit=200)
    return {str(s.id): s.shipment_status for s in result["data"]}[shipment_id]


class TestOwnerCase:
    def test_every_line_received_rolls_the_header_up_despite_a_retired_allocation(self, db):
        seeded = _container(db, [QTY, QTY, QTY], retired_on_first=True)

        InboundShipmentService(db).refresh_shipment_line_statuses(seeded["shipment_id"])

        header, lines = _stored(db, seeded["shipment_id"])
        assert [status for _a, _r, status in lines] == ["received"] * 3
        assert header == "fully_received"

    def test_the_purchasing_figure_still_counts_the_retired_allocation(self, db):
        """Only the STATUS follows the visible total. `spo_allocated_quantity` feeds
        `container_request_service.PL_UNALLOCATED_SQL` and the allocation suggestion
        arithmetic, and narrowing it is a purchasing decision this lane does not take."""
        seeded = _container(db, [QTY, QTY, QTY], retired_on_first=True)

        InboundShipmentService(db).refresh_shipment_line_statuses(seeded["shipment_id"])

        _header, lines = _stored(db, seeded["shipment_id"])
        assert sorted(alloc for alloc, _r, _s in lines) == [QTY, QTY, 2 * QTY]

    def test_the_list_page_reads_received(self, db):
        seeded = _container(db, [QTY, QTY, QTY], retired_on_first=True)
        InboundShipmentService(db).refresh_shipment_line_statuses(seeded["shipment_id"])

        assert _listed_status(db, seeded["shipment_id"]) == "fully_received"


class TestPartial:
    def test_some_lines_received_rolls_the_header_to_partially_received(self, db):
        seeded = _container(db, [QTY, 0, 0])

        InboundShipmentService(db).refresh_shipment_line_statuses(seeded["shipment_id"])

        header, _lines = _stored(db, seeded["shipment_id"])
        assert header == "partially_received"
        assert _listed_status(db, seeded["shipment_id"]) == "partially_received"

    def test_a_short_receipt_on_one_line_is_partially_received(self, db):
        seeded = _container(db, [100, 0])

        InboundShipmentService(db).refresh_shipment_line_statuses(seeded["shipment_id"])

        assert _stored(db, seeded["shipment_id"])[0] == "partially_received"

    def test_nothing_received_leaves_in_transit(self, db):
        seeded = _container(db, [0, 0])

        InboundShipmentService(db).refresh_shipment_line_statuses(seeded["shipment_id"])

        assert _stored(db, seeded["shipment_id"])[0] == "in_transit"

    def test_a_received_header_whose_receipt_went_away_falls_back_to_in_transit(self, db):
        seeded = _container(db, [0, 0], header="fully_received")

        InboundShipmentService(db).refresh_shipment_line_statuses(seeded["shipment_id"])

        assert _stored(db, seeded["shipment_id"])[0] == "in_transit"

    def test_arrived_at_port_is_kept_until_a_receipt_lands(self, db):
        seeded = _container(db, [0, 0], header="arrived_at_port")

        InboundShipmentService(db).refresh_shipment_line_statuses(seeded["shipment_id"])

        assert _stored(db, seeded["shipment_id"])[0] == "arrived_at_port"

    @pytest.mark.parametrize("header", ["closed", "draft"])
    def test_closed_and_draft_headers_are_never_rolled(self, db, header):
        seeded = _container(db, [QTY, 0], header=header)

        InboundShipmentService(db).refresh_shipment_line_statuses(seeded["shipment_id"])

        assert _stored(db, seeded["shipment_id"])[0] == header


def _backfill(db, *, apply: bool = False) -> tuple[int, str]:
    from scripts.oneoff import refresh_container_received as script

    lines: list[str] = []
    code = script.run(db, db.info["test_company_code"], stale_headers=True, apply=apply, out=lines.append)
    return code, "\n".join(lines)


class TestStaleHeaderBackfill:
    """`refresh_container_received.py --stale-headers`: dry-run by default, and a
    second run after --apply finds nothing left to change."""

    def test_dry_run_reports_the_owner_case_and_writes_nothing(self, db):
        seeded = _container(db, [QTY, QTY, QTY], retired_on_first=True)

        code, out = _backfill(db)

        assert code == 0, out
        assert "DRY-RUN (no writes)" in out
        assert seeded["shipment_id"] in out
        assert "header in_transit -> fully_received  CHANGED" in out
        assert "headers to change 1" in out
        assert _stored(db, seeded["shipment_id"])[0] == "in_transit"

    def test_apply_fixes_it_and_a_rerun_changes_nothing(self, db):
        seeded = _container(db, [QTY, QTY, QTY], retired_on_first=True)

        code, out = _backfill(db, apply=True)
        assert code == 0, out
        assert _stored(db, seeded["shipment_id"])[0] == "fully_received"

        code, again = _backfill(db)
        assert code == 0, again
        assert seeded["shipment_id"] not in again
        assert "lines to change 0, headers to change 0" in again

    def test_a_header_behind_lines_already_stored_received_is_selected(self, db):
        """Not reachable through --all-open: every stored line already says received."""
        seeded = _container(db, [QTY, QTY])
        InboundShipmentService(db).refresh_shipment_line_statuses(seeded["shipment_id"])
        db.execute(
            text("UPDATE inbound_shipments SET shipment_status = 'in_transit' WHERE id = :id"),
            {"id": seeded["shipment_id"]},
        )
        db.commit()

        code, out = _backfill(db, apply=True)

        assert code == 0, out
        assert "header in_transit -> fully_received  CHANGED" in out
        assert _stored(db, seeded["shipment_id"])[0] == "fully_received"

    def test_a_closed_header_is_not_selected(self, db):
        seeded = _container(db, [QTY, 0], header="closed")

        code, out = _backfill(db, apply=True)

        assert code == 0, out
        assert seeded["shipment_id"] not in out
        assert _stored(db, seeded["shipment_id"])[0] == "closed"


class TestOwnerReadOnlyQueries:
    """The SELECTs handed to the owner (`documentation/plans/scm/
    pl-status-rollup-readonly.sql`) find the pre-fix states they describe."""

    @staticmethod
    def _queries() -> list[str]:
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / "documentation/plans/scm/pl-status-rollup-readonly.sql"
        statements = []
        for chunk in path.read_text().split(";"):
            body = "\n".join(l for l in chunk.splitlines() if not l.strip().startswith("--")).strip()
            if body:
                statements.append(body)
        assert len(statements) == 4
        return statements

    def _stale(self, db, seeded, line_status: str) -> None:
        """Put the container back the way main left it: header in_transit, stored lines
        carrying `line_status`, received figures stored."""
        db.execute(
            text("UPDATE inbound_shipments SET shipment_status = 'in_transit' WHERE id = :id"),
            {"id": seeded["shipment_id"]},
        )
        db.execute(
            text("UPDATE inbound_shipment_lines SET quantity_received = :q, line_status = :s WHERE id = :id"),
            {"q": QTY, "s": line_status, "id": seeded["line_ids"][0]},
        )
        db.commit()

    def test_q1_counts_a_header_behind_lines_all_stored_received(self, db):
        seeded = _container(db, [QTY, QTY])
        InboundShipmentService(db).refresh_shipment_line_statuses(seeded["shipment_id"])
        before = db.execute(text(self._queries()[0])).scalar()
        self._stale(db, seeded, "received")

        assert db.execute(text(self._queries()[0])).scalar() == before + 1

    def test_q2_lists_the_owner_case_and_q3_shows_its_retired_allocation(self, db):
        seeded = _container(db, [QTY, QTY, QTY], retired_on_first=True)
        InboundShipmentService(db).refresh_shipment_line_statuses(seeded["shipment_id"])
        self._stale(db, seeded, "partially_received")

        containers = [row[0] for row in db.execute(text(self._queries()[1])).all()]
        assert seeded["container"] in containers

        q3 = self._queries()[2].replace("'TCLU8176397'", ":c")
        rows = db.execute(text(q3), {"c": seeded["container"].upper()}).mappings().all()
        assert sum(1 for r in rows if r["retired_at"] is not None) == 1


class TestListReadRollup:
    """`list_shipments` rolls the header up from the STORED line statuses on read, with
    no refresh in between: the stored header is forced stale by raw UPDATE."""

    def _stale_header(self, db, line_statuses: list[str]) -> str:
        seeded = _container(db, [0] * len(line_statuses))
        for line_id, status in zip(seeded["line_ids"], line_statuses):
            db.execute(
                text("UPDATE inbound_shipment_lines SET line_status = :s WHERE id = :id"),
                {"s": status, "id": line_id},
            )
        db.commit()
        return seeded["shipment_id"]

    def test_all_stored_received_reads_fully_received(self, db):
        shipment_id = self._stale_header(db, ["received", "received"])

        assert _listed_status(db, shipment_id) == "fully_received"
        assert _stored(db, shipment_id)[0] == "in_transit"

    def test_some_stored_receipt_reads_partially_received(self, db):
        shipment_id = self._stale_header(db, ["partially_received", "allocated"])

        assert _listed_status(db, shipment_id) == "partially_received"

    def test_no_stored_receipt_keeps_the_header(self, db):
        shipment_id = self._stale_header(db, ["allocated", "in_transit"])

        assert _listed_status(db, shipment_id) == "in_transit"


def test_q4_counts_the_lines_the_status_change_can_reach(db):
    queries = TestOwnerReadOnlyQueries._queries()
    before = db.execute(text(queries[3])).mappings().one()
    _container(db, [QTY, QTY, QTY], retired_on_first=True)

    after = db.execute(text(queries[3])).mappings().one()
    assert after["lines_in_scope"] == before["lines_in_scope"] + 1
    assert after["containers_in_scope"] == before["containers_in_scope"] + 1
    assert after["over_shipped_in_scope"] == before["over_shipped_in_scope"]
