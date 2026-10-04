"""RED tests for the SPO first-push supersede fix.

UAC: documentation/plans/autocount/spo-xlsx-supersede-acceptance-criteria.md (AC-X1..AC-X27).
PLAN: documentation/plans/autocount/PLAN-spo-xlsx-supersede.md (D25..D30).

Written BEFORE any implementation exists (Phase 2, test-first): `_split_rows` /
`_apply_scoped` in `app.services.shipping_order_ingest_service` carry none of
D25 (first-push supersede), D26 (receipt-carry-per-group) or D27
(links-move-rows-go) yet, so every test below is expected to fail against the
current tree, for the reason recorded in its own docstring - not for an
unrelated fixture defect.

Round 2 (AC-X13..AC-X22, security-review + reviewer rounds, 2026-09-07) adds
D25a (per-group, xlsx-only supersede eligibility), D26a (group-total guard +
merged-shipment warning), D28a (group-aware recompute for
`source_system='autocount'` rows), D30 (supersede needs `.delete`) and D27a
(shipment line-status refresh) - none of which exist in the tree this file is
written against either, same test-first rule.

Substrate reused wholesale from `tests/test_ingest_shipping_orders.py`
(imported, not copied, per the tester brief): the `env` fixture (itself
re-exported from `tests/test_ingest_documents.py`), `_spo_record`, `_spo_line`,
`_spo_rows`, `_seed_legacy_row` (extended there with an `inbound_shipment_id`
kwarg for AC-X6 and a `source_system` kwarg for AC-X13), `INGEST_SPO`,
`READ_SPO`, `MARKER`.

AC-X10, AC-X20 and AC-X21 (the dedupe script) are NOT here: they live in
`tests/test_dedupe_spo_xlsx_superseded.py`. AC-X12 is folded into this file as
a light contract-listing check since it needs no fixture beyond an HTTP GET.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text

from app.models.inventory import StorageZone, Warehouse
from app.models.procurement import (
    InboundShipment,
    PickingHeader,
    PickingLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SPOAllocation,
)
from app.models.project_so import (
    IV_ORDER_BACK,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRow,
    ProjectSalesOrder,
)
from app.models.scm import OrderLinkClaim

from tests._pg_fixture import unique_code
from tests.test_ingest_shipping_orders import (
    INGEST_SPO,
    MARKER,
    READ_SPO,
    _seed_legacy_row,
    _spo_line,
    _spo_record,
    _spo_rows,
    env,  # noqa: F401 - pytest fixture, imported for reuse
)

__all__ = ["env"]

_SUPERSEDE_LOGGER = "app.services.shipping_order_ingest_service"


# --------------------------------------------------------------------- helpers
def _warehouse_code(env, warehouse_ref: str) -> str:
    wh_id = env.refs.resolve(entity_type="warehouses", source_ref=warehouse_ref)
    return env.db.execute(
        text("SELECT warehouse_code FROM warehouses WHERE id = :id"), {"id": wh_id}
    ).scalar()


def _now() -> datetime:
    """A tz-aware timestamp for seeding `retired_at` (round 5, D28d) -
    silently dropped by the ORM until the column exists (same trick as
    `stated_received` in round 4); once it lands, this is a real value."""
    return datetime.now(timezone.utc)


def _picking_line_pointing_at(env, allocation_id: str, *, qty_picked: int = 5) -> PickingLine:
    """One approved GRN line drawing against `allocation_id` (AC-X2/AC-X8)."""
    header = PickingHeader(
        id=str(uuid.uuid4()),
        company_id=env.company_a,
        picking_number=unique_code(MARKER),
        picking_type="goods_received",
        picking_status="approved",
    )
    env.db.add(header)
    env.db.flush()
    product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
    line = PickingLine(
        id=str(uuid.uuid4()),
        company_id=env.company_a,
        picking_header_id=header.id,
        spo_allocation_id=allocation_id,
        product_id=product_id,
        quantity_expected=qty_picked,
        quantity_picked=qty_picked,
    )
    env.db.add(line)
    env.db.flush()
    return line


def _order_link_claim_pointing_at(env, allocation_id: str, spo_number: str) -> OrderLinkClaim:
    claim = OrderLinkClaim(
        company_id=env.company_a,
        so_number=f"{MARKER}-SO-{uuid.uuid4().hex[:8]}",
        po_number=spo_number,
        source="autocount",
        spo_allocation_id=allocation_id,
    )
    env.db.add(claim)
    env.db.flush()
    return claim


def _order_inquiry_link_pointing_at(
    env, allocation_id: str, *, qty: int = 10, null_company: bool = False
) -> OrderInquiryLink:
    """One `projects.order_inquiry_links` row on `allocation_id` (AC-X2/AC-X5).

    Mirrors `tests/scm/test_spo_allocation_documents.py::_project_link`'s
    chain, trimmed to what this fixture needs: no `Project` row (`project_id`
    is nullable on `ProjectSalesOrder`, and `env.stock_transfer` already seeds
    one the same way for the same reason). `null_company` (AC-X19, S4) leaves
    the LINK ROW's own `company_id` NULL - the column is nullable - while its
    parent chain (`ProjectSalesOrder`/`OrderInquiry`/`OrderInquiryRow`) stays
    scoped normally; only the dependant `repoint_allocation_dependants` reads
    is what AC-X19 is about.

    Forced NULL via a raw UPDATE, not by passing `company_id=None` to the
    constructor: `company_scope`'s `before_insert` auto-stamp treats an
    explicit `None` exactly like an omitted value - a live check confirmed
    it silently overwrites it with the ambient scope's own company on
    flush, which would make this fixture "pass" for the wrong reason (the
    dependant never actually ends up NULL at all).
    """
    pso = ProjectSalesOrder(
        id=str(uuid.uuid4()),
        provisional_ref=f"{MARKER}-PSO-{uuid.uuid4().hex[:8]}",
        company_id=env.company_a,
    )
    env.db.add(pso)
    env.db.flush()
    inquiry = OrderInquiry(
        id=str(uuid.uuid4()), project_sales_order_id=pso.id, company_id=env.company_a
    )
    env.db.add(inquiry)
    env.db.flush()
    from decimal import Decimal

    row = OrderInquiryRow(
        id=str(uuid.uuid4()),
        order_inquiry_id=inquiry.id,
        qty=Decimal(str(qty)),
        verb=IV_ORDER_BACK,
        company_id=env.company_a,
    )
    env.db.add(row)
    env.db.flush()
    link = OrderInquiryLink(
        id=str(uuid.uuid4()),
        row_id=row.id,
        spo_allocation_id=allocation_id,
        qty=Decimal(str(qty)),
        company_id=env.company_a,
    )
    env.db.add(link)
    env.db.flush()
    if null_company:
        env.db.execute(
            text("UPDATE order_inquiry_links SET company_id = NULL WHERE id = :id"),
            {"id": link.id},
        )
        env.db.flush()
    return link


def _seed_principal_with_permissions(env, slugs: list[str]) -> str:
    """A fresh user + role holding EXACTLY `slugs` - never superadmin (AC-X17,
    D30). `check_user_has_permission` bypasses every check for a superadmin
    role, and `env`'s own principal always is one, so a genuinely narrower
    principal has to be a different user entirely.
    """
    from app.models.user import (
        User,
        UserPermission,
        UserRole,
        UserRoleAssignment,
        UserRolePermission,
    )

    user_id = str(uuid.uuid4())
    suffix = user_id[:8]
    user = User(
        id=user_id,
        name=f"{MARKER} principal {suffix}",
        email=f"{MARKER.lower()}-{suffix}@integrations.local",
        password="x",
        status="active",
    )
    env.db.add(user)
    env.db.flush()
    role = UserRole(
        id=str(uuid.uuid4()),
        slug=f"{MARKER.lower()}-role-{suffix}",
        name=f"{MARKER} role {suffix}",
    )
    env.db.add(role)
    env.db.flush()
    env.db.add(UserRoleAssignment(user_id=user_id, role_id=role.id))
    env.db.flush()
    for slug in slugs:
        perm = UserPermission(id=str(uuid.uuid4()), slug=slug, name=slug)
        env.db.add(perm)
        env.db.flush()
        env.db.add(UserRolePermission(role_id=role.id, permission_id=perm.id))
    env.db.flush()
    env.db.commit()
    return user_id


@contextmanager
def _as_principal(user_id: str):
    """Swap `get_external_api_user`'s override for one call (AC-X17).

    `env`'s own override always answers the fixed superadmin dict; this
    temporarily answers `user_id` instead, for the one `env.post` call inside
    the `with` block, then restores whatever was there.
    """
    from app.dependencies import get_external_api_user
    from app.main import app as fastapi_app

    def _override():
        return {"id": user_id, "email": f"{MARKER.lower()}-principal@integrations.local"}

    previous = fastapi_app.dependency_overrides.get(get_external_api_user)
    fastapi_app.dependency_overrides[get_external_api_user] = _override
    try:
        yield
    finally:
        if previous is not None:
            fastapi_app.dependency_overrides[get_external_api_user] = previous
        else:
            fastapi_app.dependency_overrides.pop(get_external_api_user, None)


# ============================================================================ #
# AC-X1 (D25, D26)
# ============================================================================ #
class TestAcX1FirstPushSupersedesAClosedXlsxRow:
    def test_a_closed_xlsx_row_is_replaced_by_the_pushed_two_line_set(self, env):
        """AC-X1. One xlsx row (allocated 47, received 47, closed) for
        product P at location L; the first push names two lines for P at L
        (29 + 18, both qty_received 0). Expected: exactly two rows, both
        `source_ref` set, line 1 received 29 closed, line 2 received 18
        closed, both `fully_received`, the xlsx row gone, verdict `created` /
        `lines.created 2` / `lines.superseded 1`.

        RED today because `_split_rows` puts a CLOSED ref-less row into
        `already_closed` (S4), which never enters the adoption pool - both
        incoming lines are therefore created as brand new rows and the xlsx
        row is left exactly as it was, so the SPO ends up holding THREE rows
        (not two) and `entry["lines"]` carries no `superseded` key at all.
        This is the exact production incident (every xlsx SPO duplicated on
        its first ESB push).
        """
        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        legacy = _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=47,
            quantity_received=47,
            line_status="closed",
        )

        line1 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0, line_number=1
        )
        line2 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0, line_number=2
        )
        record = _spo_record(
            env, number=number, lines=[line1, line2], supplier_ref=env.supplier_ref
        )

        res = env.post(INGEST_SPO, [record])

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        lines_summary = entry.get("lines") or {}
        assert lines_summary.get("created") == 2, lines_summary
        assert lines_summary.get("superseded") == 1, lines_summary

        rows = _spo_rows(env, number)
        assert len(rows) == 2, "the xlsx row must be gone, leaving exactly the two pushed lines"
        assert all(r["source_ref"] for r in rows)
        by_ref = {r["source_ref"]: r for r in rows}
        first = by_ref[line1["source_ref"]]
        second = by_ref[line2["source_ref"]]
        assert first["quantity_received"] == 29
        assert first["line_status"] == "closed"
        assert first["receipt_status"] == "fully_received"
        assert second["quantity_received"] == 18
        assert second["line_status"] == "closed"
        assert second["receipt_status"] == "fully_received"
        assert str(legacy.id) not in {str(r["id"]) for r in rows}


# ============================================================================ #
# AC-X2 (D27)
# ============================================================================ #
class TestAcX2LinksMoveToTheFirstIncomingLine:
    def test_picking_line_claim_and_order_inquiry_link_repoint_to_line_1(self, env):
        """AC-X2. AC-X1's xlsx row is referenced by one `picking_lines` row,
        one `scm.order_link_claim` row and one `projects.order_inquiry_links`
        row. After the push all three must point at the row for line 1 (the
        first line of the group by `line_number`), none NULL, none pointing
        at a deleted id.

        RED today for the same structural reason as AC-X1: the xlsx row is
        never touched (S4 excludes a closed ref-less row from adoption), so
        none of the three dependants move - they still point at the OLD
        (untouched) xlsx row's id, and that id is a THIRD, still-open row
        the assertion `len(rows) == 2` below never gets past.
        """
        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        legacy = _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=47,
            quantity_received=47,
            line_status="closed",
        )
        picking_line = _picking_line_pointing_at(env, legacy.id)
        claim = _order_link_claim_pointing_at(env, legacy.id, number)
        link = _order_inquiry_link_pointing_at(env, legacy.id)

        line1 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0, line_number=1
        )
        line2 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0, line_number=2
        )
        record = _spo_record(
            env, number=number, lines=[line1, line2], supplier_ref=env.supplier_ref
        )

        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text

        rows = {r["source_ref"]: r for r in _spo_rows(env, number)}
        assert len(rows) == 2, "AC-X1 must land first for this to hold"
        target_id = str(rows[line1["source_ref"]]["id"])

        picking_line_alloc = env.db.execute(
            text("SELECT spo_allocation_id FROM picking_lines WHERE id = :id"),
            {"id": picking_line.id},
        ).scalar()
        claim_alloc = env.db.execute(
            text("SELECT spo_allocation_id FROM order_link_claim WHERE id = :id"),
            {"id": claim.id},
        ).scalar()
        link_alloc = env.db.execute(
            text("SELECT spo_allocation_id FROM order_inquiry_links WHERE id = :id"),
            {"id": link.id},
        ).scalar()

        assert picking_line_alloc is not None and str(picking_line_alloc) == target_id
        assert claim_alloc is not None and str(claim_alloc) == target_id
        assert link_alloc is not None and str(link_alloc) == target_id


# ============================================================================ #
# AC-X3 (D26)
# ============================================================================ #
class TestAcX3PartialReceiptCarriesAcrossTheGroup:
    def test_a_partially_received_xlsx_row_distributes_its_received_qty_in_line_order(
        self, env
    ):
        """AC-X3. The xlsx row received 30 of 47 (open); after the same
        two-line push, line 1 is received 29 closed, line 2 received 1 open
        (30 - 29 carried over), `receipt_status` fully_received / pending.

        RED today for the same structural reason - no carry exists, so both
        new lines land at `quantity_received=0` (their own pushed value),
        both `open`.
        """
        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=47,
            quantity_received=30,
            line_status="open",
        )

        line1 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0, line_number=1
        )
        line2 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0, line_number=2
        )
        record = _spo_record(
            env, number=number, lines=[line1, line2], supplier_ref=env.supplier_ref
        )

        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text

        rows = {r["source_ref"]: r for r in _spo_rows(env, number)}
        first = rows[line1["source_ref"]]
        second = rows[line2["source_ref"]]
        assert first["quantity_received"] == 29, first
        assert first["line_status"] == "closed", first
        assert first["receipt_status"] == "fully_received", first
        assert second["quantity_received"] == 1, second
        assert second["line_status"] == "open", second
        assert second["receipt_status"] == "pending", second


# ============================================================================ #
# AC-X5 (D27)
# ============================================================================ #
class TestAcX5AGroupWithNoIncomingCounterpartIsKeptNotSuperseded:
    def test_an_unnamed_product_group_stays_closed_and_keeps_its_link_and_is_not_counted(
        self, env
    ):
        """AC-X5. xlsx rows exist for products P and Q on SPO N; the first
        push names only P. After the push: Q's row still exists, is closed,
        keeps its link; the verdict counts `lines.superseded 1` (P only).

        RED today: since BOTH rows are ref-less (P and Q), both are CLOSED,
        both fall into `already_closed`, neither is superseded and P's own
        row is left behind too (a third, untouched row) alongside a brand
        new row created for the pushed P line - `str(legacy_p.id) not in
        rows` fails, and there is no `superseded` key at all.
        """
        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        legacy_p = _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            product_ref=env.product_ref,
            location_code=wh_code,
            allocated_quantity=10,
            quantity_received=10,
            line_status="closed",
        )
        legacy_q = _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=2,
            product_ref=env.product2_ref,
            location_code=wh_code,
            allocated_quantity=5,
            quantity_received=5,
            line_status="closed",
        )
        claim = _order_link_claim_pointing_at(env, legacy_q.id, number)

        line = _spo_line(
            env, warehouse_ref=env.warehouse_ref, product_ref=env.product_ref,
            qty_ordered=10, qty_received=0,
        )
        record = _spo_record(env, number=number, lines=[line], supplier_ref=env.supplier_ref)

        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        lines_summary = entry.get("lines") or {}
        assert lines_summary.get("superseded") == 1, lines_summary

        rows = {str(r["id"]): r for r in _spo_rows(env, number)}
        assert str(legacy_q.id) in rows, "Q's group had no incoming line and must be kept"
        assert rows[str(legacy_q.id)]["line_status"] == "closed"
        assert str(legacy_p.id) not in rows, "P's group had an incoming line and must be gone"

        claim_alloc = env.db.execute(
            text("SELECT spo_allocation_id FROM order_link_claim WHERE id = :id"),
            {"id": claim.id},
        ).scalar()
        assert str(claim_alloc) == str(legacy_q.id)


# ============================================================================ #
# AC-X6 (D26)
# ============================================================================ #
class TestAcX6ShipmentLinkCarriesToEveryLineOfTheGroup:
    def test_every_pushed_line_carries_the_xlsx_rows_shipment_when_the_container_does_not_resolve(
        self, env
    ):
        """AC-X6. The xlsx row carries `inbound_shipment_id`; the push's own
        `container_number` resolves to no shipment. After the push, every
        line of that group must carry the xlsx row's `inbound_shipment_id`.

        RED today: since nothing supersedes the row, no new row inherits
        anything from it - both created rows land with `inbound_shipment_id
        IS NULL` (the container this push named resolves to nothing, and
        there is no carry-forward from the xlsx row at all).
        """
        shipment = InboundShipment(
            id=str(uuid.uuid4()),
            shipment_number=f"{MARKER}-SH-{uuid.uuid4().hex[:6]}",
            shipping_container_number=f"{MARKER}-CONT-{uuid.uuid4().hex[:6]}",
            shipment_date=date(2026, 1, 1),
            shipment_status="pending",
        )
        env.db.add(shipment)
        env.db.flush()

        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=47,
            quantity_received=47,
            line_status="closed",
            inbound_shipment_id=shipment.id,
        )

        line1 = _spo_line(env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0)
        line2 = _spo_line(env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0)
        record = _spo_record(
            env,
            number=number,
            lines=[line1, line2],
            supplier_ref=env.supplier_ref,
            container_number=f"{MARKER}-UNRESOLVED-{uuid.uuid4().hex[:6]}",
        )

        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text

        rows = _spo_rows(env, number)
        assert len(rows) == 2, "AC-X1 must land first for this to hold"
        for row in rows:
            assert row["inbound_shipment_id"] is not None
            assert str(row["inbound_shipment_id"]) == str(shipment.id), row


# ============================================================================ #
# AC-X7 (D25)
# ============================================================================ #
class TestAcX7AnOpenXlsxRowIsAlsoSuperseded:
    def test_an_open_xlsx_row_is_superseded_too_not_only_a_closed_one(self, env):
        """AC-X7. An OPEN xlsx row (received 0) for the same product +
        location; the first push supersedes it too (deleted), the pushed
        lines carry received 0, open.

        RED today: an OPEN ref-less row lands in `pool` (not
        `already_closed`), so the EXISTING (pre-D25) adoption ladder
        (AC-V3-4) claims it for ONE of the two incoming lines in place -
        the row keeps its OWN id (never deleted) and the other line is
        created fresh. `str(legacy.id) not in all_ids` therefore fails: the
        xlsx row's id is still present, just repurposed as one ESB line
        instead of the clean two-new-rows-and-delete D25/D27 mandate.
        """
        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        legacy = _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=47,
            quantity_received=0,
            line_status="open",
        )

        line1 = _spo_line(env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0)
        line2 = _spo_line(env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0)
        record = _spo_record(
            env, number=number, lines=[line1, line2], supplier_ref=env.supplier_ref
        )

        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text

        rows = _spo_rows(env, number)
        assert len(rows) == 2, rows
        for row in rows:
            assert row["quantity_received"] == 0, row
            assert row["line_status"] == "open", row
        all_ids = {str(r["id"]) for r in rows}
        assert str(legacy.id) not in all_ids, (
            "the xlsx row must be a deleted, superseded row - not adopted in place"
        )


# ============================================================================ #
# AC-X9 (D25)
# ============================================================================ #
class TestAcX9ARepushOfTheSameDockeyIsAPlainUpdate:
    def test_a_second_push_of_the_same_dockey_after_ac_x1_updates_without_re_superseding(
        self, env
    ):
        """AC-X9. A second push of the same DocKey after AC-X1 (same lines,
        qty_received still 0) answers `updated`, leaves both rows received
        29 / 18 closed (received never shrinks); `lines.superseded` absent.

        RED today: the first push doesn't even reach AC-X1's two-row shape
        (see that test) - it produces THREE rows, the two new ones at
        received 0. The second push then updates those two rows by their own
        `source_ref`, but since no carry-forward ever wrote 29/18 onto them
        in the first place, they still read 0 - `quantity_received == 29`
        fails.
        """
        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=47,
            quantity_received=47,
            line_status="closed",
        )

        line1 = _spo_line(env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0)
        line2 = _spo_line(env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0)
        record = _spo_record(
            env, number=number, lines=[line1, line2], supplier_ref=env.supplier_ref
        )
        res1 = env.post(INGEST_SPO, [record])
        assert res1.status_code == 200, res1.text

        res2 = env.post(INGEST_SPO, [record])

        entry2 = res2.json()["records"][0]
        assert entry2["outcome"] == "updated", res2.text
        assert "superseded" not in (entry2.get("lines") or {}), entry2

        rows = {r["source_ref"]: r for r in _spo_rows(env, number)}
        assert rows[line1["source_ref"]]["quantity_received"] == 29, rows[line1["source_ref"]]
        assert rows[line1["source_ref"]]["line_status"] == "closed"
        assert rows[line2["source_ref"]]["quantity_received"] == 18, rows[line2["source_ref"]]
        assert rows[line2["source_ref"]]["line_status"] == "closed"


# ============================================================================ #
# AC-X8 (D28)
# ============================================================================ #
class TestAcX8RecomputeRespectsOwnership:
    def test_an_allocation_with_no_picking_line_keeps_its_stored_value_on_recompute(
        self, env
    ):
        """AC-X8. An allocation with `quantity_received=29` and NO picking
        line, and a sibling allocation of the same SPO with one approved
        picking line of 5; after `sync_received_for_spo_number(N)`, the
        sibling reads 5 and the first still reads 29.

        RED today: `sync_received_for_spo_number` calls
        `compute_received_for_allocation` for EVERY allocation matching the
        SPO regardless of whether it has a picking line - an allocation with
        none sums to 0 and gets overwritten from 29 to 0.
        """
        from app.services.procurement_service import PickingHeaderService

        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        product2_id = env.refs.resolve(entity_type="products", source_ref=env.product2_ref)

        no_picking = SPOAllocation(
            id=str(uuid.uuid4()),
            company_id=env.company_a,
            spo_number=number,
            spo_line_number=1,
            product_id=product_id,
            allocated_quantity=29,
            quantity_received=29,
            line_status="closed",
            receipt_status="fully_received",
            source_system="scm_upload",
        )
        env.db.add(no_picking)
        env.db.flush()

        with_picking = SPOAllocation(
            id=str(uuid.uuid4()),
            company_id=env.company_a,
            spo_number=number,
            spo_line_number=2,
            product_id=product2_id,
            allocated_quantity=5,
            quantity_received=0,
            line_status="open",
            receipt_status="pending",
            source_system="scm_upload",
        )
        env.db.add(with_picking)
        env.db.flush()
        _picking_line_pointing_at(env, with_picking.id, qty_picked=5)
        env.db.commit()

        PickingHeaderService(env.db).sync_received_for_spo_number(number)

        env.db.expire_all()
        received_no_picking = env.db.execute(
            text("SELECT quantity_received FROM spo_allocations WHERE id = :id"),
            {"id": no_picking.id},
        ).scalar()
        received_with_picking = env.db.execute(
            text("SELECT quantity_received FROM spo_allocations WHERE id = :id"),
            {"id": with_picking.id},
        ).scalar()

        assert received_with_picking == 5, received_with_picking
        assert received_no_picking == 29, (
            "an allocation with no picking line must keep its stored value, "
            f"not be recomputed to 0 - got {received_no_picking}"
        )


# ============================================================================ #
# AC-X11
# ============================================================================ #
class TestAcX11XlsxThenGrnThenPushMatchesPushThenGrn:
    """AC-X11. The AC-X1 outcome (two rows, 29 / 18 allocated, closed, same
    `quantity_received`) must be identical whether reached by (xlsx upload ->
    GRN -> first push, D26's carry) or by (first push -> GRN, the ordinary
    two-line receiving flow), for the same fixture in two separate companies
    so this compares row SHAPE, not the same rows twice. The GRN half is
    seeded directly (`quantity_received` / `line_status` set by hand) rather
    than driven through `PickingHeaderService`, the same simplification
    `tests/test_ingest_parity_s3_shipping_orders.py` uses for its own
    "already received via a GRN, out of scope here" fixture.

    RED today: company A's push (step 3 below) does not carry the closed
    xlsx row's receipt forward at all - it leaves the xlsx row untouched
    (a THIRD row) and creates two new rows at `quantity_received=0`, so the
    two companies' final row sets disagree on `quantity_received` and
    `line_status`, and company A holds 3 rows against company B's 2.
    """

    def _seed_company(self, db) -> str:
        from app.models.company import Company

        other = Company(
            id=str(uuid.uuid4()),
            name=f"{MARKER} PAR {uuid.uuid4().hex[:6]}",
            code=unique_code(MARKER)[:10],
        )
        db.add(other)
        db.flush()
        return str(other.id)

    def _seed_product_and_warehouse(self, db, company_id=None):
        from app.models.product import Product, ProductCategory, UnitOfMeasure

        category = ProductCategory(category_code=unique_code(MARKER), category_name="Cat")
        uom = UnitOfMeasure(uom_code=unique_code(MARKER), uom_name="Each")
        db.add_all([category, uom])
        db.flush()
        product = Product(
            product_code=unique_code(MARKER),
            product_name="Item",
            category_id=category.id,
            base_uom_id=uom.id,
            list_price=0,
        )
        db.add(product)
        db.flush()
        ref = f"{MARKER}:ITEM:{uuid.uuid4().hex[:8]}"
        from app.services.company_scope import DEFAULT_COMPANY_ID
        from app.services.integration_reference_service import IntegrationReferenceService

        # Anchored to the company this call is seeding into (defaults to the
        # incumbent): plan D14 is strict, an unanchored link on a scoped type
        # raises ValueError, and this helper is called for BOTH company A and
        # company B in the cross-company test below.
        IntegrationReferenceService(
            db, company_id=company_id or DEFAULT_COMPANY_ID
        ).link(entity_type="products", entity_id=product.id, source_ref=ref)
        warehouse = Warehouse(
            warehouse_code=f"{MARKER}WH{uuid.uuid4().hex[:6]}", warehouse_name="Main"
        )
        db.add(warehouse)
        db.flush()
        return product, ref, warehouse

    def test_the_supersede_outcome_matches_the_ordinary_receiving_order(self):
        from app.models.base import set_company_scope
        from app.services.company_scope import DEFAULT_COMPANY_ID
        from app.services.master_ingest_service import IngestOutcome
        from app.services.shipping_order_ingest_service import ShippingOrderIngestService

        from tests._pg_fixture import blank_session

        with blank_session() as db:
            spo_number = f"{MARKER}-SPOPAR-{uuid.uuid4().hex[:8]}"

            # ---- company A: xlsx -> GRN -> first push ----
            set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
            product_a, ref_a, wh_a = self._seed_product_and_warehouse(db)
            xlsx_row = SPOAllocation(
                id=str(uuid.uuid4()),
                company_id=DEFAULT_COMPANY_ID,
                spo_number=spo_number,
                spo_line_number=1,
                product_id=product_a.id,
                location_code=wh_a.warehouse_code,
                allocated_quantity=47,
                quantity_received=0,
                line_status="open",
                source_system="scm_upload",
            )
            db.add(xlsx_row)
            db.flush()
            # The GRN receives everything and closes it - seeded directly.
            xlsx_row.quantity_received = 47
            xlsx_row.line_status = "closed"
            xlsx_row.receipt_status = "fully_received"
            db.flush()

            svc_a = ShippingOrderIngestService(
                db, integration_id=None, company_id=DEFAULT_COMPANY_ID, may_delete=True
            )
            result_a = svc_a.ingest(
                "shipping_orders",
                [
                    {
                        "source_ref": f"{MARKER}:SPO:{uuid.uuid4().hex[:8]}",
                        "spo_number": spo_number,
                        "status": "open",
                        "lines": [
                            {
                                "source_ref": f"{MARKER}:SPOL:{uuid.uuid4().hex[:8]}",
                                "product_ref": ref_a,
                                "product_code": product_a.product_code,
                                "warehouse_code": wh_a.warehouse_code,
                                "qty_ordered": "29",
                                "qty_received": "0",
                            },
                            {
                                "source_ref": f"{MARKER}:SPOL:{uuid.uuid4().hex[:8]}",
                                "product_ref": ref_a,
                                "product_code": product_a.product_code,
                                "warehouse_code": wh_a.warehouse_code,
                                "qty_ordered": "18",
                                "qty_received": "0",
                            },
                        ],
                    }
                ],
            )
            # AC-X1: a first push whose spo_number holds only ref-less rows
            # supersedes them - the verdict reads `created`, not `updated`.
            assert result_a.records[0].outcome is IngestOutcome.CREATED, result_a.records[0].errors

            # ---- company B: first push -> GRN ----
            company_b = self._seed_company(db)
            set_company_scope(db, frozenset({company_b}))
            product_b, ref_b, wh_b = self._seed_product_and_warehouse(db, company_id=company_b)

            svc_b = ShippingOrderIngestService(db, integration_id=None, company_id=company_b)
            result_b = svc_b.ingest(
                "shipping_orders",
                [
                    {
                        "source_ref": f"{MARKER}:SPOB:{uuid.uuid4().hex[:8]}",
                        "spo_number": spo_number,
                        "status": "open",
                        "lines": [
                            {
                                "source_ref": f"{MARKER}:SPOLB1:{uuid.uuid4().hex[:8]}",
                                "product_ref": ref_b,
                                "product_code": product_b.product_code,
                                "warehouse_code": wh_b.warehouse_code,
                                "qty_ordered": "29",
                                "qty_received": "0",
                            },
                            {
                                "source_ref": f"{MARKER}:SPOLB2:{uuid.uuid4().hex[:8]}",
                                "product_ref": ref_b,
                                "product_code": product_b.product_code,
                                "warehouse_code": wh_b.warehouse_code,
                                "qty_ordered": "18",
                                "qty_received": "0",
                            },
                        ],
                    }
                ],
            )
            assert result_b.records[0].outcome is IngestOutcome.CREATED, result_b.records[0].errors

            rows_b_ids = db.execute(
                text(
                    "SELECT id, allocated_quantity FROM spo_allocations "
                    "WHERE company_id = :c AND spo_number = :n ORDER BY allocated_quantity"
                ),
                {"c": company_b, "n": spo_number},
            ).mappings().all()
            for row in rows_b_ids:
                target = 29 if row["allocated_quantity"] == 29 else 18
                db.execute(
                    text(
                        "UPDATE spo_allocations SET quantity_received = :q, "
                        "line_status = 'closed', receipt_status = 'fully_received' "
                        "WHERE id = :id"
                    ),
                    {"q": target, "id": row["id"]},
                )
            db.flush()

            columns = "allocated_quantity, quantity_received, line_status, inbound_shipment_id"
            rows_a = (
                db.execute(
                    text(
                        f"SELECT {columns} FROM spo_allocations "
                        "WHERE company_id = :c AND spo_number = :n ORDER BY allocated_quantity"
                    ),
                    {"c": DEFAULT_COMPANY_ID, "n": spo_number},
                )
                .mappings()
                .all()
            )
            rows_b_final = (
                db.execute(
                    text(
                        f"SELECT {columns} FROM spo_allocations "
                        "WHERE company_id = :c AND spo_number = :n ORDER BY allocated_quantity"
                    ),
                    {"c": company_b, "n": spo_number},
                )
                .mappings()
                .all()
            )

            assert len(rows_a) == 2, (
                f"company A must hold exactly two rows after the supersede - got {len(rows_a)}"
            )
            assert len(rows_b_final) == 2

            for row_a, row_b in zip(rows_a, rows_b_final):
                diff = {k: (row_a[k], row_b[k]) for k in row_a.keys() if row_a[k] != row_b[k]}
                assert diff == {}, diff


# ============================================================================ #
# AC-X13 (REVISED, D25c) - production dedupe finding, 2026-09-08
# ============================================================================ #
class TestAcX13OnlyScmUploadRowsAreSupersedeCandidates:
    def test_a_null_source_warehouse_only_row_is_also_a_supersede_candidate(self, env):
        """AC-X13 (REVISED, D25c). A ref-less row for product P with
        `source_system` NULL, NO `location_code`, `warehouse_id` set (the
        Procurement Upload SPO / n8n packing-list shape), closed, received
        47; a first push names P at the warehouse whose code resolves to
        that SAME warehouse with two lines 29 / 18. The row is superseded
        exactly as AC-X1: deleted, receipt carried 29 / 18 (both closed),
        `lines.superseded 1`.

        This is the OPPOSITE of the old AC-X13 (D25a), which asserted a
        NULL-source row is NEVER a candidate. Production evidence
        (SPO-2026/09-0028) showed the Procurement Upload SPO
        (`import_tasks.process_spo_import`) and the n8n packing-list route
        both write NULL `source_system` with a `warehouse_id`, and BOTH
        load an Excel aggregate - D25a's "one line for one real line"
        premise for a NULL-source row was wrong.

        RED today: `is_xlsx_era_row` still excludes `source_system IS
        NULL` - this row is never gathered as a supersede candidate at
        all. It falls to the leftover sweep (closed already, so
        `already_closed`), the push creates two BRAND-NEW rows alongside
        it, and the SPO ends up holding THREE rows instead of two, with no
        `superseded` key on the verdict at all.
        """
        wh_id = env.refs.resolve(entity_type="warehouses", source_ref=env.warehouse_ref)
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"

        legacy = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=None,
            warehouse_id=wh_id, allocated_quantity=47, quantity_received=47,
            line_status="closed", receipt_status="fully_received",
            source_system=None,
        )
        env.db.add(legacy)
        env.db.flush()
        env.db.commit()

        line1 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0, line_number=1
        )
        line2 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0, line_number=2
        )
        record = _spo_record(
            env, number=number, lines=[line1, line2], supplier_ref=env.supplier_ref
        )

        res = env.post(INGEST_SPO, [record])

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        lines_summary = entry.get("lines") or {}
        assert lines_summary.get("created") == 2, lines_summary
        assert lines_summary.get("superseded") == 1, lines_summary

        rows = _spo_rows(env, number)
        assert len(rows) == 2, (
            "the NULL-source row must be gone, leaving exactly the two pushed lines", rows
        )
        by_ref = {r["source_ref"]: r for r in rows}
        first = by_ref[line1["source_ref"]]
        second = by_ref[line2["source_ref"]]
        assert first["quantity_received"] == 29, first
        assert first["line_status"] == "closed", first
        assert second["quantity_received"] == 18, second
        assert second["line_status"] == "closed", second
        assert str(legacy.id) not in {str(r["id"]) for r in rows}


# ============================================================================ #
# AC-X14 (D25a, per-group)
# ============================================================================ #
class TestAcX14PerGroupEligibilitySurvivesAPriorPushsRefRow:
    def test_a_still_xlsx_group_is_superseded_on_the_push_that_first_names_it(self, env):
        """AC-X14 (D25a, per-group). xlsx rows for P and Q on SPO N; push 1
        (a DocKey) names P only - Q is kept, closed, untouched. Push 2 (the
        SAME DocKey) now ALSO names Q, at qty 20 / received 0, while Q's
        xlsx row carried received 20. After push 2, Q's group must hold
        EXACTLY ONE row - ref set, received 20, closed - the xlsx Q row
        gone (`lines.superseded 1` on push 2); no open Q row may exist.

        RED today: eligibility is decided per DOCUMENT
        (`_has_ref_row(payload)`, D25), not per group - after push 1 gave
        the spo_number a ref row (P's), push 2 sees `first_push=False` for
        the WHOLE document, so Q's still-xlsx-era, still-ref-less, CLOSED
        row falls into `already_closed` (S4) rather than the supersede
        pool: it is never superseded, and push 2's Q line is created as a
        brand new, OPEN row alongside it - TWO rows, not one, and an open
        row exists where none should.
        """
        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            product_ref=env.product_ref,
            location_code=wh_code,
            allocated_quantity=10,
            quantity_received=10,
            line_status="closed",
        )
        legacy_q = _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=2,
            product_ref=env.product2_ref,
            location_code=wh_code,
            allocated_quantity=20,
            quantity_received=20,
            line_status="closed",
        )

        p_line = _spo_line(
            env, warehouse_ref=env.warehouse_ref, product_ref=env.product_ref,
            qty_ordered=10, qty_received=0,
        )
        record1 = _spo_record(env, number=number, lines=[p_line], supplier_ref=env.supplier_ref)
        res1 = env.post(INGEST_SPO, [record1])
        assert res1.status_code == 200, res1.text

        q_line = _spo_line(
            env, warehouse_ref=env.warehouse_ref, product_ref=env.product2_ref,
            qty_ordered=20, qty_received=0,
        )
        record2 = dict(record1, lines=[p_line, q_line])
        res2 = env.post(INGEST_SPO, [record2])

        assert res2.status_code == 200, res2.text
        entry2 = res2.json()["records"][0]
        assert entry2.get("lines", {}).get("superseded") == 1, entry2

        product2_id = env.refs.resolve(entity_type="products", source_ref=env.product2_ref)
        q_rows = [
            r for r in _spo_rows(env, number) if str(r["product_id"]) == str(product2_id)
        ]
        assert len(q_rows) == 1, ("Q's group must hold exactly one row after push 2", q_rows)
        q_row = q_rows[0]
        assert q_row["source_ref"] == q_line["source_ref"]
        assert q_row["quantity_received"] == 20
        assert q_row["line_status"] == "closed"
        assert str(legacy_q.id) not in {str(r["id"]) for r in q_rows}
        assert not any(r["line_status"] == "open" for r in q_rows)


# ============================================================================ #
# AC-X15 (D26a guard)
# ============================================================================ #
class TestAcX15GuardOnGroupTotalBelowCarriedReceipt:
    def test_a_first_push_undercutting_the_carried_receipt_is_not_superseded(self, env):
        """AC-X15 (D26a guard). The AC-X1 xlsx row (allocated 47, received
        47, closed) and a first push with ONE line for P at L, `qty_ordered
        1`, `qty_received 0` - the incoming group's own allocated total (1)
        is below the group's carried receipt (47). The group must NOT be
        superseded: the xlsx row stays (still present, closed), a new OPEN
        row of 1 is created normally (no carry), the record warns
        `received_locked`, and the xlsx row's own receipt never drops below
        what it held before the push.

        RED today: D26a's guard does not exist - `plan_xlsx_supersede`
        dumps the WHOLE carried receipt (47) onto the single incoming line
        regardless of its own allocated_quantity, so the group IS
        superseded: the xlsx row is deleted, and the new row lands
        `allocated_quantity=1`, `quantity_received=47`, CLOSED - not the
        open, uncarried row of 1 this AC wants - and no `received_locked`
        warning is emitted at all.
        """
        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        legacy = _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=47,
            quantity_received=47,
            line_status="closed",
        )

        line = _spo_line(env, warehouse_ref=env.warehouse_ref, qty_ordered=1, qty_received=0)
        record = _spo_record(env, number=number, lines=[line], supplier_ref=env.supplier_ref)

        res = env.post(INGEST_SPO, [record])

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert "received_locked" in (entry.get("warnings") or []), entry

        rows = _spo_rows(env, number)
        by_id = {str(r["id"]): r for r in rows}
        assert str(legacy.id) in by_id, (
            "the xlsx row must not be deleted - the group total undercuts its receipt"
        )
        assert by_id[str(legacy.id)]["line_status"] == "closed"
        assert by_id[str(legacy.id)]["quantity_received"] == 47

        by_ref = {r["source_ref"]: r for r in rows if r["source_ref"]}
        new_row = by_ref[line["source_ref"]]
        assert new_row["allocated_quantity"] == 1
        assert new_row["quantity_received"] == 0
        assert new_row["line_status"] == "open"


# ============================================================================ #
# AC-X16 (D26a shipments)
# ============================================================================ #
class TestAcX16MergedShipmentsWarnAndLogTheDropped:
    def test_two_shipments_on_one_group_use_the_first_and_warn_and_log_the_dropped(
        self, env, caplog
    ):
        """AC-X16 (D26a shipments). Two xlsx rows for P at L on SPO N,
        linked to two DIFFERENT inbound shipments A (line 1) and B (line
        2); a first push naming P at L with two lines. Every new line must
        carry shipment A (D26's own "first non-null" rule - already
        correct), the record must ALSO warn `shipment_merged`, and the
        supersede must log at INFO the dropped shipment (B)'s identity.

        RED today: D26a's warning/log for a merged shipment does not exist
        - only the "use the first" behaviour (D26) is there. The
        `shipment_merged` warning is absent, and no INFO log line names
        shipment B at all.
        """
        shipment_a = InboundShipment(
            id=str(uuid.uuid4()),
            shipment_number=f"{MARKER}-SHA-{uuid.uuid4().hex[:6]}",
            shipping_container_number=f"{MARKER}-CONTA-{uuid.uuid4().hex[:6]}",
            shipment_date=date(2026, 1, 1),
            shipment_status="pending",
        )
        shipment_b = InboundShipment(
            id=str(uuid.uuid4()),
            shipment_number=f"{MARKER}-SHB-{uuid.uuid4().hex[:6]}",
            shipping_container_number=f"{MARKER}-CONTB-{uuid.uuid4().hex[:6]}",
            shipment_date=date(2026, 1, 1),
            shipment_status="pending",
        )
        env.db.add_all([shipment_a, shipment_b])
        env.db.flush()

        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=29,
            quantity_received=0,
            line_status="open",
            inbound_shipment_id=shipment_a.id,
        )
        _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=2,
            location_code=wh_code,
            allocated_quantity=18,
            quantity_received=0,
            line_status="open",
            inbound_shipment_id=shipment_b.id,
        )

        line1 = _spo_line(env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0, line_number=1)
        line2 = _spo_line(env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0, line_number=2)
        record = _spo_record(
            env, number=number, lines=[line1, line2], supplier_ref=env.supplier_ref
        )

        with caplog.at_level(logging.INFO, logger=_SUPERSEDE_LOGGER):
            res = env.post(INGEST_SPO, [record])

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert "shipment_merged" in (entry.get("warnings") or []), entry

        rows = _spo_rows(env, number)
        for row in rows:
            assert str(row["inbound_shipment_id"]) == str(shipment_a.id), row

        assert (
            str(shipment_b.id) in caplog.text or shipment_b.shipment_number in caplog.text
        ), caplog.text


# ============================================================================ #
# AC-X17 (D30)
# ============================================================================ #
class TestAcX17SupersedeNeedsDeletePermission:
    """AC-X17 (D30). Only a principal holding `scm.shipping_orders.delete`
    may have the supersede actually DELETE the xlsx row; a principal with
    only `.edit` still gets the full carry (receipt, links) but the row is
    CLOSED, annotated and warned instead. Both cases log at INFO.
    """

    def _seed_ac_x1_fixture(self, env):
        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        legacy = _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=47,
            quantity_received=47,
            line_status="closed",
        )
        picking_line = _picking_line_pointing_at(env, legacy.id)
        claim = _order_link_claim_pointing_at(env, legacy.id, number)
        link = _order_inquiry_link_pointing_at(env, legacy.id)
        line1 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0, line_number=1
        )
        line2 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0, line_number=2
        )
        record = _spo_record(
            env, number=number, lines=[line1, line2], supplier_ref=env.supplier_ref
        )
        return number, legacy, picking_line, claim, link, record, line1, line2

    def test_without_delete_permission_the_xlsx_row_is_closed_not_deleted(self, env, caplog):
        """AC-X17, without `.delete`. Receipts carry and links move exactly
        as AC-X1/AC-X2, but the xlsx row is CLOSED, its `allocation_notes`
        reads `superseded by <DocKey>`, and the record warns
        `superseded_closed_only`.

        RED today: D30 does not exist - the service has no notion of the
        calling principal's grants at all, so the supersede deletes the row
        unconditionally regardless of the caller's permissions. The xlsx
        row's id is gone entirely, not closed, `allocation_notes` is never
        set, and no `superseded_closed_only` warning nor INFO log line
        exists.
        """
        number, legacy, picking_line, claim, link, record, line1, line2 = (
            self._seed_ac_x1_fixture(env)
        )
        held = int(legacy.quantity_received or 0)  # read before the push zeroes it (D33)
        user_id = _seed_principal_with_permissions(env, ["scm.shipping_orders.edit"])

        with caplog.at_level(logging.INFO, logger=_SUPERSEDE_LOGGER):
            with _as_principal(user_id):
                res = env.post(INGEST_SPO, [record])

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert "superseded_closed_only" in (entry.get("warnings") or []), entry

        rows = _spo_rows(env, number)
        by_id = {str(r["id"]): r for r in rows}
        assert str(legacy.id) in by_id, "without .delete the xlsx row must be closed, not removed"
        closed_row = by_id[str(legacy.id)]
        assert closed_row["line_status"] == "closed"
        # D33 (SPO-XLSX-SUPERSEDE round 2): retired, its receipt zeroed (carried onto the
        # replacement lines) and the old figure kept as its own note fragment.
        expected_note = f"superseded by {record['source_ref']}"
        if held:
            expected_note += f"; received {held} carried"
        assert closed_row["allocation_notes"] == expected_note, closed_row
        assert closed_row["retired_at"] is not None
        assert int(closed_row["quantity_received"] or 0) == 0

        by_ref = {r["source_ref"]: r for r in rows if r["source_ref"]}
        target_id = str(by_ref[line1["source_ref"]]["id"])

        picking_line_alloc = env.db.execute(
            text("SELECT spo_allocation_id FROM picking_lines WHERE id = :id"),
            {"id": picking_line.id},
        ).scalar()
        claim_alloc = env.db.execute(
            text("SELECT spo_allocation_id FROM order_link_claim WHERE id = :id"),
            {"id": claim.id},
        ).scalar()
        link_alloc = env.db.execute(
            text("SELECT spo_allocation_id FROM order_inquiry_links WHERE id = :id"),
            {"id": link.id},
        ).scalar()
        assert str(picking_line_alloc) == target_id
        assert str(claim_alloc) == target_id
        assert str(link_alloc) == target_id

        assert str(legacy.id) in caplog.text, caplog.text

    def test_with_delete_permission_the_xlsx_row_is_deleted_as_ac_x1(self, env, caplog):
        """AC-X17, with `.delete` held too: AC-X1's own behaviour (deleted),
        no `superseded_closed_only` warning, and the supersede still logs
        at INFO.

        RED today: the row IS already deleted regardless of permissions
        (D30 doesn't check anything yet), so this half is a live control -
        only the INFO-log assertion is red (no logging exists at all yet).
        """
        number, legacy, picking_line, claim, link, record, line1, line2 = (
            self._seed_ac_x1_fixture(env)
        )
        user_id = _seed_principal_with_permissions(
            env, ["scm.shipping_orders.edit", "scm.shipping_orders.delete"]
        )

        with caplog.at_level(logging.INFO, logger=_SUPERSEDE_LOGGER):
            with _as_principal(user_id):
                res = env.post(INGEST_SPO, [record])

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert "superseded_closed_only" not in (entry.get("warnings") or []), entry

        rows = _spo_rows(env, number)
        assert len(rows) == 2, "with .delete held the xlsx row must be gone, AC-X1's own shape"
        assert str(legacy.id) not in {str(r["id"]) for r in rows}

        assert str(legacy.id) in caplog.text, caplog.text


# ============================================================================ #
# AC-X18 (D28a)
# ============================================================================ #
class TestAcX18GroupAwareRecomputeForAutocountOwnedLines:
    def test_a_repointed_picking_lines_total_is_redistributed_not_dumped_on_one_line(self, env):
        """AC-X18 (D28a). After the AC-X1 supersede (line 1 = 29, line 2 =
        18), one picking line of 47 now points at line 1 (the group's own
        repointed GRN draw). Running `sync_received_for_spo_number(N)` and
        `sync_grn_received_to_spo(<that header>)` must leave line 1 at 29
        and line 2 at 18 - the group total (47) redistributed in Seq order,
        never 47 dumped onto line 1 alone. A sibling allocation whose
        `source_system` is `scm_upload`, with its own picking line of 5,
        still recomputes to 5 (D28's per-allocation rule, unaffected).

        RED today: D28a's group-aware recompute for `source_system=
        'autocount'` rows does not exist - both recompute methods still
        recompute PER ALLOCATION (`compute_received_for_allocation`, summing
        only the picking lines that point at THAT row): line 1 is
        overwritten to 47 (the whole picking line's quantity, since it is
        the only one pointing at line 1) - never 29.
        """
        from app.services.procurement_service import PickingHeaderService

        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=47,
            quantity_received=47,
            line_status="closed",
        )
        line1 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0, line_number=1
        )
        line2 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0, line_number=2
        )
        record = _spo_record(
            env, number=number, lines=[line1, line2], supplier_ref=env.supplier_ref
        )
        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text

        rows = {r["source_ref"]: r for r in _spo_rows(env, number)}
        line1_id = rows[line1["source_ref"]]["id"]
        line2_id = rows[line2["source_ref"]]["id"]

        header = PickingHeader(
            id=str(uuid.uuid4()),
            company_id=env.company_a,
            picking_number=unique_code(MARKER),
            picking_type="goods_received",
            picking_status="approved",
            spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()),
                company_id=env.company_a,
                picking_header_id=header.id,
                spo_allocation_id=line1_id,
                product_id=product_id,
                quantity_expected=47,
                quantity_picked=47,
            )
        )
        env.db.flush()

        # A sibling allocation (scm_upload), unrelated to the group above,
        # with its own picking line - the D28 per-allocation rule must be
        # left untouched by the D28a change.
        product2_id = env.refs.resolve(entity_type="products", source_ref=env.product2_ref)
        sibling = SPOAllocation(
            id=str(uuid.uuid4()),
            company_id=env.company_a,
            spo_number=number,
            spo_line_number=99,
            product_id=product2_id,
            allocated_quantity=5,
            quantity_received=0,
            line_status="open",
            receipt_status="pending",
            source_system="scm_upload",
        )
        env.db.add(sibling)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()),
                company_id=env.company_a,
                picking_header_id=header.id,
                spo_allocation_id=sibling.id,
                product_id=product2_id,
                quantity_expected=5,
                quantity_picked=5,
            )
        )
        env.db.flush()
        env.db.commit()

        svc = PickingHeaderService(env.db)
        svc.sync_grn_received_to_spo(header.id)
        svc.sync_received_for_spo_number(number)

        env.db.expire_all()
        final = (
            env.db.execute(
                text("SELECT id, quantity_received FROM spo_allocations WHERE spo_number = :n"),
                {"n": number},
            )
            .mappings()
            .all()
        )
        by_id = {str(r["id"]): r["quantity_received"] for r in final}

        assert by_id[str(line1_id)] == 29, (
            f"line 1 must recompute to 29 (its own share of the group), not 47 - "
            f"got {by_id[str(line1_id)]}"
        )
        assert by_id[str(line2_id)] == 18, by_id
        assert by_id[str(sibling.id)] == 5, by_id


# ============================================================================ #
# AC-X19 (S4)
# ============================================================================ #
class TestAcX19DependantsWithNullCompanyIdStillRepoint:
    def test_a_null_company_claim_and_link_still_repoint_to_line_1(self, env):
        """AC-X19 (S4). The xlsx row's `order_link_claim` and
        `order_inquiry_links` dependants carry `company_id` NULL (both
        columns are nullable); after the supersede both must still point at
        line 1, not NULL.

        RED today: `repoint_allocation_dependants`'s query is
        `if company_id: query.filter(model.company_id == company_id)` - SQL
        equality against NULL is never true, so a NULL-company dependant is
        excluded from the move entirely. Confirmed live: the failure mode is
        even sharper than a stale FK - `order_inquiry_links` carries
        `ck_order_inquiry_links_one_target` (exactly one of `po_line_id` /
        `spo_allocation_id` set), so when the superseded row is then DELETED
        and its `ON DELETE SET NULL` fires on the un-repointed link, the
        UPDATE that nulls `spo_allocation_id` violates the CHECK constraint
        outright and the whole push 500s with an uncaught `IntegrityError`
        instead of the record answering a clean per-record verdict.

        Forced NULL via a raw UPDATE after insert, not via the constructor:
        `company_scope`'s `before_insert` auto-stamp silently overwrites an
        explicit `company_id=None` with the ambient scope's own company on
        flush (confirmed live) - a fixture that let it do that would pass
        for the wrong reason, the dependant never actually being NULL.
        """
        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        legacy = _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=47,
            quantity_received=47,
            line_status="closed",
        )
        claim = OrderLinkClaim(
            company_id=env.company_a,
            so_number=f"{MARKER}-SO-{uuid.uuid4().hex[:8]}",
            po_number=number,
            source="autocount",
            spo_allocation_id=legacy.id,
        )
        env.db.add(claim)
        env.db.flush()
        env.db.execute(
            text("UPDATE order_link_claim SET company_id = NULL WHERE id = :id"),
            {"id": claim.id},
        )
        env.db.flush()
        link = _order_inquiry_link_pointing_at(env, legacy.id, null_company=True)

        line1 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0, line_number=1
        )
        line2 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0, line_number=2
        )
        record = _spo_record(
            env, number=number, lines=[line1, line2], supplier_ref=env.supplier_ref
        )

        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text

        rows = {r["source_ref"]: r for r in _spo_rows(env, number)}
        target_id = str(rows[line1["source_ref"]]["id"])

        claim_alloc = env.db.execute(
            text("SELECT spo_allocation_id FROM order_link_claim WHERE id = :id"),
            {"id": claim.id},
        ).scalar()
        link_alloc = env.db.execute(
            text("SELECT spo_allocation_id FROM order_inquiry_links WHERE id = :id"),
            {"id": link.id},
        ).scalar()

        assert claim_alloc is not None and str(claim_alloc) == target_id, claim_alloc
        assert link_alloc is not None and str(link_alloc) == target_id, link_alloc


# ============================================================================ #
# AC-X12
# ============================================================================ #
class TestAcX12ContractListsLinesSuperseded:
    def test_the_external_contract_names_lines_superseded_in_the_verdict_vocabulary(self, env):
        """AC-X12. `GET /api/v1/external/contract`'s verdict/warning note lists
        `lines.superseded` among the shipping-order line-outcome vocabulary.

        RED today: the note only ever documents `adopted` / `created` /
        `updated` / `deleted` / `cancelled` - `superseded` is nowhere in it
        yet (`app/api/v1/external/contract.py` unchanged).
        """
        res = env.client.get("/api/v1/external/contract")
        assert res.status_code == 200, res.text
        assert "superseded" in res.text, "contract note must list lines.superseded"


# ============================================================================ #
# AC-X22 (S9)
# ============================================================================ #
class TestAcX22RecomputeNeverCrossesCompanyScope:
    def test_sync_received_for_spo_number_under_company_a_never_writes_company_b(self, env):
        """AC-X22 (S9). Company B holds an allocation sharing the SAME
        `spo_number` as company A's, with its own picking line; calling
        `sync_received_for_spo_number(N)` UNDER COMPANY A's SCOPE must
        never write company B's row.

        RED today: `sync_received_for_spo_number` queries
        `self.db.query(SPOAllocation).filter(SPOAllocation.spo_number.
        isnot(None)).all()` with no explicit `company_id` predicate of its
        own - it relies ENTIRELY on whatever ambient ORM scope the caller's
        session happens to carry. Called here under company A's scope, the
        ambient auto-filter is exactly what should stop company B's row
        being read at all; if it does not, company B's row is recomputed
        too - see the assertion for the actual observed value.
        """
        from app.models.base import set_company_scope
        from app.models.company import Company
        from app.services.procurement_service import PickingHeaderService

        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)

        row_a = SPOAllocation(
            id=str(uuid.uuid4()),
            company_id=env.company_a,
            spo_number=number,
            spo_line_number=1,
            product_id=product_id,
            allocated_quantity=10,
            quantity_received=0,
            line_status="open",
            receipt_status="pending",
            source_system="scm_upload",
        )
        env.db.add(row_a)
        env.db.flush()

        other = Company(
            id=str(uuid.uuid4()),
            name=f"{MARKER} B {uuid.uuid4().hex[:6]}",
            code=unique_code(MARKER)[:10],
        )
        env.db.add(other)
        env.db.flush()
        company_b = str(other.id)

        row_b = SPOAllocation(
            id=str(uuid.uuid4()),
            company_id=company_b,
            spo_number=number,
            spo_line_number=1,
            product_id=product_id,
            allocated_quantity=10,
            quantity_received=0,
            line_status="open",
            receipt_status="pending",
            source_system="scm_upload",
        )
        env.db.add(row_b)
        env.db.flush()

        header = PickingHeader(
            id=str(uuid.uuid4()),
            company_id=company_b,
            picking_number=unique_code(MARKER),
            picking_type="goods_received",
            picking_status="approved",
            spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()),
                company_id=company_b,
                picking_header_id=header.id,
                spo_allocation_id=row_b.id,
                product_id=product_id,
                quantity_expected=6,
                quantity_picked=6,
            )
        )
        env.db.flush()
        env.db.commit()

        set_company_scope(env.db, frozenset({env.company_a}))
        PickingHeaderService(env.db).sync_received_for_spo_number(number)

        env.db.expire_all()
        after_b = env.db.execute(
            text("SELECT quantity_received FROM spo_allocations WHERE id = :id"),
            {"id": row_b.id},
        ).scalar()
        assert after_b == 0, (
            "sync_received_for_spo_number under company A's scope must never "
            f"write company B's allocation - got {after_b}"
        )


# ============================================================================ #
# AC-X23 (D26 Seq order)
# ============================================================================ #
class TestAcX23DistributionFollowsLineNumberNotPayloadPosition:
    def test_reverse_payload_order_still_distributes_by_line_number(self, env):
        """AC-X23 (D26 Seq order, reviewer kill test; seed fixed 2026-09-07
        - functional delta review). The AC-X1 push with its two lines sent
        in REVERSE payload order (line_number 2 first, then line_number 1)
        must still yield line 1 = 29 and line 2 = 1 - distribution follows
        `line_number`, not payload position.

        Seeded at received 30 (not 47, the original seed), on purpose: at
        47 (exactly `29 + 18`), Seq order (line 1 first, remainder 18 on
        line 2 last) and PAYLOAD order (line 2 first, remainder 29 on line
        1 last - the bug this AC guards against) both land on the SAME
        29 / 18 split, so the seed could not actually tell the two apart -
        a payload-order bug would have passed this test silently. At 30,
        Seq order gives 29 / 1 (line 1 takes its own 29, line 2 the 1 left
        over) while payload order would give 12 / 18 (line 2 takes its own
        18 first, line 1 the 12 left over) - the two orders now disagree,
        so a regression to payload order fails this test's own assertions,
        not just its docstring's claim. (Manually confirmed against the
        current, correct implementation: distribution is keyed on
        `line_number`, giving 29 / 1 as asserted below.)
        """
        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=47,
            quantity_received=30,
            line_status="open",
        )

        line1 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0, line_number=1
        )
        line2 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0, line_number=2
        )
        # REVERSE payload order: line_number 2 first, then 1.
        record = _spo_record(
            env, number=number, lines=[line2, line1], supplier_ref=env.supplier_ref
        )

        res = env.post(INGEST_SPO, [record])

        assert res.status_code == 200, res.text
        rows = {r["source_ref"]: r for r in _spo_rows(env, number)}
        first = rows[line1["source_ref"]]
        second = rows[line2["source_ref"]]
        assert first["quantity_received"] == 29, (
            "line_number 1 must carry its own 29 regardless of payload position", first
        )
        assert first["line_status"] == "closed"
        assert second["quantity_received"] == 1, (
            "line_number 2 must carry only the 1 left over (30 - 29), not 18 - "
            "a payload-order bug would instead read 12 / 18 here", second
        )
        assert second["line_status"] == "open"


# ============================================================================ #
# AC-X24 (D26 max rule, AutoCount side)
# ============================================================================ #
class TestAcX24AutocountAboveTheCarryWins:
    def test_an_incoming_qty_received_above_the_carry_wins_on_that_line(self, env):
        """AC-X24 (D26 max rule, reviewer kill test). The xlsx row received
        10 of 47; a first push whose line 1 states `qty_received 25` and
        line 2 states `qty_received 0`. Line 1 must read 25 (AutoCount's
        own stated figure, above the 10 carried onto it, wins), line 2
        reads 0, and the group's total received never drops below the 10
        the xlsx row carried before the push.
        """
        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=47,
            quantity_received=10,
            line_status="open",
        )

        line1 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=25, line_number=1
        )
        line2 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0, line_number=2
        )
        record = _spo_record(
            env, number=number, lines=[line1, line2], supplier_ref=env.supplier_ref
        )

        res = env.post(INGEST_SPO, [record])

        assert res.status_code == 200, res.text
        rows = {r["source_ref"]: r for r in _spo_rows(env, number)}
        first = rows[line1["source_ref"]]
        second = rows[line2["source_ref"]]
        assert first["quantity_received"] == 25, first
        assert second["quantity_received"] == 0, second
        assert first["quantity_received"] + second["quantity_received"] >= 10, (
            "the group's total received must never drop below the 10 the xlsx "
            "row carried before the push",
            first,
            second,
        )


# ============================================================================ #
# AC-X26 (D27a)
# ============================================================================ #
class TestAcX26ShipmentLineStatusRefreshedAfterSupersede:
    def test_the_ingest_supersede_refreshes_the_linked_shipments_line_status(
        self, env, monkeypatch
    ):
        """AC-X26 (D27a). After the AC-X1 supersede,
        `InboundShipmentService.refresh_shipment_line_statuses` must run for
        the linked shipment - same as every other writer of allocations
        already does.

        RED today: `_supersede_xlsx_rows` never calls it at all.
        """
        import app.services.procurement_service as procurement_service

        calls: list[str] = []
        real = procurement_service.InboundShipmentService.refresh_shipment_line_statuses

        def _spy(self, shipment_id):
            calls.append(str(shipment_id))
            return real(self, shipment_id)

        monkeypatch.setattr(
            procurement_service.InboundShipmentService, "refresh_shipment_line_statuses", _spy
        )

        shipment = InboundShipment(
            id=str(uuid.uuid4()),
            shipment_number=f"{MARKER}-SH-{uuid.uuid4().hex[:6]}",
            shipping_container_number=f"{MARKER}-CONT-{uuid.uuid4().hex[:6]}",
            shipment_date=date(2026, 1, 1),
            shipment_status="pending",
        )
        env.db.add(shipment)
        env.db.flush()

        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=47,
            quantity_received=47,
            line_status="closed",
            inbound_shipment_id=shipment.id,
        )
        line1 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0, line_number=1
        )
        line2 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0, line_number=2
        )
        record = _spo_record(
            env, number=number, lines=[line1, line2], supplier_ref=env.supplier_ref
        )

        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text

        assert str(shipment.id) in calls, (
            "the supersede must refresh the linked shipment's line statuses, "
            f"same as every other allocation writer - calls: {calls}"
        )


# ============================================================================ #
# AC-X28 (D28a floor)
# ============================================================================ #
class TestAcX28GroupFloorNeverDropsBelowTheStoredNonReleasedSum:
    def test_a_partial_pick_against_one_line_never_lowers_the_groups_stated_receipts(self, env):
        """AC-X28 (D28a floor), AMENDED round 4 (D28c, MB1, 2026-09-07). Two
        `autocount` lines for P at L on SPO N with ESB-STATED receipts
        (line 1 allocated 29 / received 25 / `stated_received` 25, line 2
        allocated 18 / received 18 / `stated_received` 18) and NO picking
        line at all; a Sorento GRN approves one picking line of 5 against
        line 1. Running `sync_grn_received_to_spo` (and separately
        `sync_received_for_spo_number`) must leave line 1 at 25 and line 2
        at 18 - the group total is the MAX of the picking sum (5) and the
        stated sum of non-released members (43), and since the picking
        sum does not exceed it, neither member is lowered. THE RELEASE
        CASE (MB1): deleting the only GRN against line 1 releases it - it
        must return to its STATED 25 (`stated_received`, the ESB's own
        TransferedQty), NOT drop to 0. The D28b stored-sum floor could not
        tell "GRN-derived receipt" from "ESB-stated receipt" and zeroed a
        real AutoCount receipt whenever its only GRN was ever deleted;
        `stated_received` is the floor that survives the release. Line 2
        (never touched by any GRN) keeps its stated 18.

        RED today: `spo_allocations.stated_received` does not exist yet
        (migration 488 / model column, D28c) - the raw SQL read below
        raises `UndefinedColumn`. Once the column exists but before the
        release path is rewritten to read it, `_sync_group_received`'s
        release branch writes a released member from
        `computed.get(str(member.id), 0)` (its own remaining picking
        lines, 0 here) instead of `max(stated, its own remaining picking
        lines)`, landing line 1 at 0, not 25.
        """
        from app.services.procurement_service import PickingHeaderService

        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)
        doc_ref = f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}"

        line1 = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            allocated_quantity=29, quantity_received=25, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC1:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
            stated_received=25,
        )
        line2 = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=2, product_id=product_id, location_code=wh_code,
            allocated_quantity=18, quantity_received=18, line_status="closed",
            receipt_status="fully_received", source_system="autocount",
            source_ref=f"{MARKER}:AC2:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
            stated_received=18,
        )
        env.db.add_all([line1, line2])
        env.db.flush()

        header = PickingHeader(
            id=str(uuid.uuid4()), company_id=env.company_a,
            picking_number=unique_code(MARKER), picking_type="goods_received",
            picking_status="approved", spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()), company_id=env.company_a,
                picking_header_id=header.id, spo_allocation_id=line1.id,
                product_id=product_id, quantity_expected=5, quantity_picked=5,
            )
        )
        env.db.flush()
        env.db.commit()

        svc = PickingHeaderService(env.db)
        svc.sync_grn_received_to_spo(header.id)

        def _received(ids):
            env.db.expire_all()
            rows = env.db.execute(
                text(
                    "SELECT id, quantity_received, stated_received FROM spo_allocations "
                    "WHERE id IN (:id1, :id2)"
                ),
                {"id1": str(ids[0]), "id2": str(ids[1])},
            ).mappings().all()
            return {str(r["id"]): r["quantity_received"] for r in rows}

        by_id = _received([line1.id, line2.id])
        assert by_id[str(line1.id)] == 25, (
            f"line 1 must not drop below its stated 25 - got {by_id[str(line1.id)]}"
        )
        assert by_id[str(line2.id)] == 18, by_id

        svc.sync_received_for_spo_number(number)
        by_id2 = _received([line1.id, line2.id])
        assert by_id2[str(line1.id)] == 25, by_id2
        assert by_id2[str(line2.id)] == 18, by_id2

        # The release case (MB1, amended): deleting the GRN releases line 1
        # - it must return to its STATED 25 (`stated_received`), never 0.
        # Line 2 (never touched by any GRN) keeps its stated 18.
        svc.delete_grn(header.id)
        by_id3 = _received([line1.id, line2.id])
        assert by_id3[str(line1.id)] == 25, (
            f"a released member must return to its ESB-stated receipt (25), "
            f"not zero - got {by_id3}"
        )
        assert by_id3[str(line2.id)] == 18, by_id3


# ============================================================================ #
# AC-X29 (pass 3 gated)
# ============================================================================ #
class TestAcX29PositionalAdoptionNeverRunsAfterASupersede:
    def test_an_unrelated_null_source_row_is_never_overwritten_by_positional_adoption(
        self, env
    ):
        """AC-X29 (pass 3 gated). SPO N holds an xlsx row for P at L (open,
        10, `source_system='scm_upload'`) and a NULL-source (n8n / CRM) row
        for Q at M (open, 5, with a `storage_zone_id` and an
        `order_inquiry_links` placement). A push names (P, L) qty 10 - the
        supersede target - and an UNRELATED (R, S) qty 5. After the push
        the Q row must still describe product Q at location M, keep its
        zone and its placement, the (R, S) line must be a NEW row, and
        `lines.adopted` must be absent or 0.

        RED today: once P's group is superseded, Q's NULL-source row (not
        a supersede candidate under D25a, so it sits in the ordinary
        adoption `pool`) is the only ref-less row left, and (R, S) is the
        only unmatched line left - pass 3 (position only, "remaining
        counts agree") pairs them BLINDLY, overwriting Q's row with R's
        product/location entirely: a positional adoption running in a push
        that already superseded a different group.
        """
        wh_code = _warehouse_code(env, env.warehouse_ref)
        wh_id = env.refs.resolve(entity_type="warehouses", source_ref=env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"

        _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=10,
            quantity_received=0,
            line_status="open",
            source_system="scm_upload",
        )

        product2_id = env.refs.resolve(entity_type="products", source_ref=env.product2_ref)
        zone = StorageZone(
            id=str(uuid.uuid4()), warehouse_id=wh_id,
            zone_code=f"{MARKER[:8]}Z{uuid.uuid4().hex[:6]}", zone_type="storage",
        )
        env.db.add(zone)
        env.db.flush()
        q_location = f"ZZT-QLOC-{uuid.uuid4().hex[:6]}"
        q_row = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=2, product_id=product2_id, location_code=q_location,
            storage_zone_id=zone.id, allocated_quantity=5, quantity_received=0,
            line_status="open", receipt_status="pending", source_system=None,
        )
        env.db.add(q_row)
        env.db.flush()
        link = _order_inquiry_link_pointing_at(env, q_row.id, qty=5)

        r_product_ref = env.link_product(env.company_a)
        r_warehouse_ref = env.link_warehouse(env.company_a)

        p_line = _spo_line(
            env, warehouse_ref=env.warehouse_ref, product_ref=env.product_ref,
            qty_ordered=10, qty_received=0,
        )
        r_line = _spo_line(
            env, warehouse_ref=r_warehouse_ref, product_ref=r_product_ref,
            qty_ordered=5, qty_received=0,
        )
        record = _spo_record(
            env, number=number, lines=[p_line, r_line], supplier_ref=env.supplier_ref
        )

        res = env.post(INGEST_SPO, [record])

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert (entry.get("lines") or {}).get("adopted", 0) == 0, entry

        q_after = env.db.execute(
            text(
                "SELECT product_id, location_code, storage_zone_id, source_ref "
                "FROM spo_allocations WHERE id = :id"
            ),
            {"id": q_row.id},
        ).mappings().first()
        assert q_after is not None, "Q's row must still exist"
        assert str(q_after["product_id"]) == str(product2_id), q_after
        assert q_after["location_code"] == q_location, q_after
        assert str(q_after["storage_zone_id"]) == str(zone.id), q_after
        assert q_after["source_ref"] is None, (
            "Q's row must not have been claimed/overwritten by the (R, S) line",
            q_after,
        )

        link_alloc = env.db.execute(
            text("SELECT spo_allocation_id FROM order_inquiry_links WHERE id = :id"),
            {"id": link.id},
        ).scalar()
        assert str(link_alloc) == str(q_row.id), link_alloc

        r_rows = env.db.execute(
            text(
                "SELECT id FROM spo_allocations WHERE company_id = :c AND spo_number = :n "
                "AND product_id = :p"
            ),
            {"c": env.company_a, "n": number, "p": env.refs.resolve(
                entity_type="products", source_ref=r_product_ref
            )},
        ).mappings().all()
        assert len(r_rows) == 1, "(R, S) must be a NEW row, not Q's overwritten one"
        assert str(r_rows[0]["id"]) != str(q_row.id)


# ============================================================================ #
# AC-X30
# ============================================================================ #
class TestAcX30MayDeleteDefaultsToClosedNotDeleted:
    def test_constructing_without_may_delete_closes_the_xlsx_row_not_deletes_it(self, env):
        """AC-X30. `ShippingOrderIngestService(may_delete=...)` must
        default to False: constructing the service WITHOUT the flag and
        pushing AC-X1's shape closes the xlsx row (`superseded_closed_
        only`) rather than deleting it. The route passes the resolved
        permission explicitly; the dedupe passes `True` explicitly - only
        a caller with no opinion at all (a bare construction) gets the
        SAFE default.

        RED today: `may_delete: bool = True` - a bare construction deletes
        the row, same as AC-X1's own shape, because nothing computed a
        permission at all.
        """
        from app.services.shipping_order_ingest_service import ShippingOrderIngestService

        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        legacy = _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=47,
            quantity_received=47,
            line_status="closed",
        )
        line1 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0, line_number=1
        )
        line2 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0, line_number=2
        )
        record = _spo_record(
            env, number=number, lines=[line1, line2], supplier_ref=env.supplier_ref
        )

        svc = ShippingOrderIngestService(env.db, integration_id=None, company_id=env.company_a)
        result = svc.ingest("shipping_orders", [record])
        env.db.commit()

        entry = result.records[0]
        assert "superseded_closed_only" in (entry.warnings or []), entry.warnings

        rows = _spo_rows(env, number)
        by_id = {str(r["id"]): r for r in rows}
        assert str(legacy.id) in by_id, (
            "without may_delete resolved, the xlsx row must be CLOSED, not deleted"
        )
        assert by_id[str(legacy.id)]["line_status"] == "closed"


# ============================================================================ #
# AC-X31
# ============================================================================ #
class TestAcX31RepointRequiresCompanyId:
    def test_repoint_allocation_dependants_requires_company_id(self, env):
        """AC-X31. `repoint_allocation_dependants` must REQUIRE
        `company_id` - a call without it is a `TypeError`, raised before
        the function body ever runs (Python enforces a required
        keyword-only argument at the call site).

        RED today: `company_id: Optional[str] = None` still has a
        default, so the SAME call below completes normally (moving
        nothing, since the id does not exist) instead of raising -
        `pytest.raises(TypeError)` reports "DID NOT RAISE".
        """
        from app.services.rules import shipping_order_rules

        with pytest.raises(TypeError):
            shipping_order_rules.repoint_allocation_dependants(
                env.db, [str(uuid.uuid4())], str(uuid.uuid4())
            )


class TestAcX31BackfillScriptRegistersCompanyScopeListeners:
    def test_the_backfill_script_registers_company_scope_listeners(self):
        """AC-X31 (script hygiene). `scripts/backfill_grn_spo_allocation_
        links.py` must call `register_company_scope_listeners()` and pin
        one company, or its closing recompute
        (`sync_received_for_spo_number`) re-reads every company's rows
        sharing an SPO number (S9) - the same class of gap AC-X22 pinned
        for the ingest path.

        RED today: a source-level check confirms no call to
        `register_company_scope_listeners` anywhere in the module.
        """
        import inspect

        from scripts import backfill_grn_spo_allocation_links as backfill_module

        source = inspect.getsource(backfill_module)
        assert "register_company_scope_listeners" in source, (
            "the backfill script must call register_company_scope_listeners() "
            "before its closing recompute, same as the dedupe script does"
        )


# ============================================================================ #
# AC-X32 (D26 remainder, both writers)
# ============================================================================ #
class TestAcX32RemainderOnTheLastLineInBothWriters:
    def test_supersede_puts_the_remainder_on_the_last_line(self, env):
        """AC-X32 (D26 remainder), supersede half. An xlsx row received 50
        against AutoCount lines allocated 29 and 18 (group total 50 is
        ABOVE the allocated sum 47); after the supersede line 1 reads 29
        (its own allocated) and line 2 reads 21 (the remainder, 50 - 29),
        both closed.
        """
        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=47,
            quantity_received=50,
            line_status="closed",
        )

        line1 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0, line_number=1
        )
        line2 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0, line_number=2
        )
        record = _spo_record(
            env, number=number, lines=[line1, line2], supplier_ref=env.supplier_ref
        )

        res = env.post(INGEST_SPO, [record])

        assert res.status_code == 200, res.text
        rows = {r["source_ref"]: r for r in _spo_rows(env, number)}
        first = rows[line1["source_ref"]]
        second = rows[line2["source_ref"]]
        assert first["quantity_received"] == 29, first
        assert first["line_status"] == "closed"
        assert second["quantity_received"] == 21, (
            "the remainder (50 - 29) belongs on the LAST line, not capped at "
            "its own allocated 18", second
        )
        assert second["line_status"] == "closed"

    def test_group_recompute_puts_the_remainder_on_the_last_line(self, env):
        """AC-X32 (D28a group recompute), same shape. Two `autocount`
        lines allocated 29 / 18 with a picking total of 50 (one approved
        picking line against line 1) - the group recompute must land line
        1 at 29 and line 2 at 21, same remainder rule as the supersede
        half.
        """
        from app.services.procurement_service import PickingHeaderService

        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)
        doc_ref = f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}"

        line1 = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            allocated_quantity=29, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC1:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
        )
        line2 = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=2, product_id=product_id, location_code=wh_code,
            allocated_quantity=18, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC2:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
        )
        env.db.add_all([line1, line2])
        env.db.flush()

        header = PickingHeader(
            id=str(uuid.uuid4()), company_id=env.company_a,
            picking_number=unique_code(MARKER), picking_type="goods_received",
            picking_status="approved", spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()), company_id=env.company_a,
                picking_header_id=header.id, spo_allocation_id=line1.id,
                product_id=product_id, quantity_expected=50, quantity_picked=50,
            )
        )
        env.db.flush()
        env.db.commit()

        PickingHeaderService(env.db).sync_grn_received_to_spo(header.id)

        env.db.expire_all()
        rows = env.db.execute(
            text(
                "SELECT id, quantity_received, line_status FROM spo_allocations "
                "WHERE id IN (:id1, :id2)"
            ),
            {"id1": str(line1.id), "id2": str(line2.id)},
        ).mappings().all()
        by_id = {str(r["id"]): r for r in rows}
        assert by_id[str(line1.id)]["quantity_received"] == 29, by_id
        assert by_id[str(line1.id)]["line_status"] == "closed", by_id
        assert by_id[str(line2.id)]["quantity_received"] == 21, (
            "the remainder (50 - 29) belongs on the LAST line", by_id
        )
        assert by_id[str(line2.id)]["line_status"] == "closed", by_id


# ============================================================================ #
# AC-X33 (D28a status consistency)
# ============================================================================ #
class TestAcX33GroupRecomputeKeepsLineStatusConsistent:
    def test_a_line_reaching_its_allocation_is_marked_closed(self, env):
        """AC-X33 (D28a status). A single-member AutoCount group, open,
        allocated 10, received 0; one approved picking line of 10 fully
        covers it. The group recompute must leave it `quantity_received
        10`, `receipt_status fully_received` AND `line_status closed` -
        never `closed` numerically (`receipt_status`) but still `open`
        (`line_status`).

        RED today: `_write_received` sets `quantity_received` and
        `receipt_status` only - it never touches `line_status` at all, so
        a line that reaches its allocation through the group recompute
        stays `open` forever, even though `receipt_status` already reads
        `fully_received`.
        """
        from app.services.procurement_service import PickingHeaderService

        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)

        line = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            allocated_quantity=10, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC1:{uuid.uuid4().hex[:8]}",
            source_doc_ref=f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}",
        )
        env.db.add(line)
        env.db.flush()

        header = PickingHeader(
            id=str(uuid.uuid4()), company_id=env.company_a,
            picking_number=unique_code(MARKER), picking_type="goods_received",
            picking_status="approved", spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()), company_id=env.company_a,
                picking_header_id=header.id, spo_allocation_id=line.id,
                product_id=product_id, quantity_expected=10, quantity_picked=10,
            )
        )
        env.db.flush()
        env.db.commit()

        PickingHeaderService(env.db).sync_grn_received_to_spo(header.id)

        env.db.expire_all()
        row = env.db.execute(
            text(
                "SELECT quantity_received, receipt_status, line_status "
                "FROM spo_allocations WHERE id = :id"
            ),
            {"id": line.id},
        ).mappings().first()
        assert row["quantity_received"] == 10, row
        assert row["receipt_status"] == "fully_received", row
        assert row["line_status"] == "closed", (
            "a line whose receipt reaches its allocation must read closed, "
            f"not stay open - got {row}"
        )
        # The invariant this AC names directly: never closed + pending.
        assert not (row["line_status"] == "closed" and row["receipt_status"] == "pending"), row

    def test_a_line_below_its_allocation_and_open_stays_open(self, env):
        """AC-X33 (D28a status), regression guard. A single-member group,
        open, allocated 10, received 0; a picking line of 4 leaves it
        under-received - it must stay `line_status open`,
        `receipt_status pending`.
        """
        from app.services.procurement_service import PickingHeaderService

        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)

        line = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            allocated_quantity=10, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC1:{uuid.uuid4().hex[:8]}",
            source_doc_ref=f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}",
        )
        env.db.add(line)
        env.db.flush()

        header = PickingHeader(
            id=str(uuid.uuid4()), company_id=env.company_a,
            picking_number=unique_code(MARKER), picking_type="goods_received",
            picking_status="approved", spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()), company_id=env.company_a,
                picking_header_id=header.id, spo_allocation_id=line.id,
                product_id=product_id, quantity_expected=4, quantity_picked=4,
            )
        )
        env.db.flush()
        env.db.commit()

        PickingHeaderService(env.db).sync_grn_received_to_spo(header.id)

        env.db.expire_all()
        row = env.db.execute(
            text(
                "SELECT quantity_received, receipt_status, line_status "
                "FROM spo_allocations WHERE id = :id"
            ),
            {"id": line.id},
        ).mappings().first()
        assert row["quantity_received"] == 4, row
        assert row["receipt_status"] == "pending", row
        assert row["line_status"] == "open", row

    def test_a_line_closed_by_the_leftover_sweep_is_never_reopened(self, env):
        """AC-X33 (D28a status), regression guard, AMENDED round 4 (D28c,
        AC-X36/AC-X41): a member closed BY A RECEIPT (`receipt_status
        fully_received`) is now DELIBERATELY reopenable when what closed it
        goes away - that is AC-X35/AC-X36's own fix for MB2, not a
        regression. What this guard actually pins is the OTHER kind of
        closed row: one the leftover sweep retired by absence
        (`receipt_status` stays `pending` - it was never received, only
        retired), which must stay closed and untouched regardless of what a
        sibling's picking line redistributes. See AC-X41 for the same shape
        pinned as its own AC.
        """
        from app.services.procurement_service import PickingHeaderService

        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)
        doc_ref = f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}"

        closed_line = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            allocated_quantity=29, quantity_received=0, line_status="closed",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC1:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
            stated_received=0,
        )
        sibling = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=2, product_id=product_id, location_code=wh_code,
            allocated_quantity=18, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC2:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
        )
        env.db.add_all([closed_line, sibling])
        env.db.flush()

        header = PickingHeader(
            id=str(uuid.uuid4()), company_id=env.company_a,
            picking_number=unique_code(MARKER), picking_type="goods_received",
            picking_status="approved", spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()), company_id=env.company_a,
                picking_header_id=header.id, spo_allocation_id=sibling.id,
                product_id=product_id, quantity_expected=5, quantity_picked=5,
            )
        )
        env.db.flush()
        env.db.commit()

        PickingHeaderService(env.db).sync_grn_received_to_spo(header.id)

        env.db.expire_all()
        row = env.db.execute(
            text("SELECT line_status FROM spo_allocations WHERE id = :id"),
            {"id": closed_line.id},
        ).mappings().first()
        assert row["line_status"] == "closed", (
            "a member the leftover sweep already closed must never be "
            f"reopened by the group recompute - got {row}"
        )


# ============================================================================ #
# Round 4 (reviewer MB1 / MB2, 2026-09-07, PLAN D28c): `stated_received`
# ============================================================================ #
# Fixture vocabulary (UAC): "stated" = `spo_allocations.stated_received`, the
# receipt an ESB push (TransferedQty) or a supersede / dedupe carry DECLARED
# for an `autocount` line; NULL reads as 0.


# ============================================================================ #
# AC-X35 (D28c, MB2)
# ============================================================================ #
class TestAcX35ARedistributedShareLeavesWithTheGrnThatProducedIt:
    def _seed(self, env):
        """Two `autocount` lines allocated 29 / 18, stated 0 / 0, stored 0,
        one approved GRN of 47 against line 1 - the exact shape a share of
        18 lands on line 2 purely by redistribution, never by its own proof.
        """
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)
        doc_ref = f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}"

        line1 = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            allocated_quantity=29, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC1:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
            stated_received=0,
        )
        line2 = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=2, product_id=product_id, location_code=wh_code,
            allocated_quantity=18, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC2:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
            stated_received=0,
        )
        env.db.add_all([line1, line2])
        env.db.flush()

        header = PickingHeader(
            id=str(uuid.uuid4()), company_id=env.company_a,
            picking_number=unique_code(MARKER), picking_type="goods_received",
            picking_status="approved", spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()), company_id=env.company_a,
                picking_header_id=header.id, spo_allocation_id=line1.id,
                product_id=product_id, quantity_expected=47, quantity_picked=47,
            )
        )
        env.db.flush()
        env.db.commit()
        return line1, line2, header

    def _received(self, env, ids):
        env.db.expire_all()
        rows = env.db.execute(
            text(
                "SELECT id, quantity_received, line_status, receipt_status, "
                "stated_received FROM spo_allocations WHERE id IN (:id1, :id2)"
            ),
            {"id1": str(ids[0]), "id2": str(ids[1])},
        ).mappings().all()
        return {str(r["id"]): r for r in rows}

    def test_delete_grn_zeroes_and_reopens_both_lines(self, env):
        """AC-X35 (D28c, MB2). After `sync_grn_received_to_spo` the lines
        read 29 / 18 closed. After `delete_grn` BOTH must read 0,
        `line_status open`, `receipt_status pending`: the share that
        landed on line 2 leaves with the GRN that produced it, and a line
        closed by a receipt that is gone reopens.

        RED today (before migration 488 / the model column land):
        `stated_received` does not exist, so the raw SQL read raises
        `UndefinedColumn`.
        """
        from app.services.procurement_service import PickingHeaderService

        line1, line2, header = self._seed(env)
        svc = PickingHeaderService(env.db)
        svc.sync_grn_received_to_spo(header.id)

        by_id = self._received(env, [line1.id, line2.id])
        assert by_id[str(line1.id)]["quantity_received"] == 29, by_id
        assert by_id[str(line1.id)]["line_status"] == "closed", by_id
        assert by_id[str(line2.id)]["quantity_received"] == 18, by_id
        assert by_id[str(line2.id)]["line_status"] == "closed", by_id

        svc.delete_grn(header.id)
        by_id2 = self._received(env, [line1.id, line2.id])
        assert by_id2[str(line1.id)]["quantity_received"] == 0, (
            "the only GRN behind the group's whole 47 is gone - line 1 "
            f"must drop to its (zero) stated receipt - got {by_id2}"
        )
        assert by_id2[str(line1.id)]["line_status"] == "open", by_id2
        assert by_id2[str(line1.id)]["receipt_status"] == "pending", by_id2
        assert by_id2[str(line2.id)]["quantity_received"] == 0, (
            "line 2's 18 was only ever a redistributed SHARE of line 1's "
            f"GRN, never its own proof - it must leave with the GRN that "
            f"produced it - got {by_id2}"
        )
        assert by_id2[str(line2.id)]["line_status"] == "open", by_id2
        assert by_id2[str(line2.id)]["receipt_status"] == "pending", by_id2

    def test_the_same_shape_through_bulk_delete_grns(self, env):
        """AC-X35, same shape through `bulk_delete_grns`."""
        from app.services.procurement_service import PickingHeaderService

        line1, line2, header = self._seed(env)
        svc = PickingHeaderService(env.db)
        svc.sync_grn_received_to_spo(header.id)

        by_id = self._received(env, [line1.id, line2.id])
        assert by_id[str(line1.id)]["quantity_received"] == 29, by_id
        assert by_id[str(line2.id)]["quantity_received"] == 18, by_id

        svc.bulk_delete_grns([header.id])
        by_id2 = self._received(env, [line1.id, line2.id])
        assert by_id2[str(line1.id)]["quantity_received"] == 0, by_id2
        assert by_id2[str(line1.id)]["line_status"] == "open", by_id2
        assert by_id2[str(line2.id)]["quantity_received"] == 0, by_id2
        assert by_id2[str(line2.id)]["line_status"] == "open", by_id2


# ============================================================================ #
# AC-X36 (D28c)
# ============================================================================ #
class TestAcX36AStatedFloorNeverDropsAndCancelledIsNeverTouched:
    def test_a_stated_29_line_never_drops_or_reopens_when_a_siblings_grn_is_deleted(
        self, env
    ):
        """AC-X36 (D28c). A line whose stated receipt is 29 (closed) never
        drops below 29 and never reopens when a SIBLING's GRN is deleted -
        `_write_received` reopens ONLY a line closed by a receipt
        (`receipt_status fully_received`) whose OWN new receipt is below
        its allocation, never a line another member's release merely
        redistributes around.
        """
        from app.services.procurement_service import PickingHeaderService

        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)
        doc_ref = f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}"

        stated_line = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            allocated_quantity=29, quantity_received=29, line_status="closed",
            receipt_status="fully_received", source_system="autocount",
            source_ref=f"{MARKER}:AC1:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
            stated_received=29,
        )
        sibling = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=2, product_id=product_id, location_code=wh_code,
            allocated_quantity=18, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC2:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
            stated_received=0,
        )
        env.db.add_all([stated_line, sibling])
        env.db.flush()

        header = PickingHeader(
            id=str(uuid.uuid4()), company_id=env.company_a,
            picking_number=unique_code(MARKER), picking_type="goods_received",
            picking_status="approved", spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()), company_id=env.company_a,
                picking_header_id=header.id, spo_allocation_id=sibling.id,
                product_id=product_id, quantity_expected=5, quantity_picked=5,
            )
        )
        env.db.flush()
        env.db.commit()

        PickingHeaderService(env.db).delete_grn(header.id)

        env.db.expire_all()
        row = env.db.execute(
            text(
                "SELECT quantity_received, line_status, receipt_status "
                "FROM spo_allocations WHERE id = :id"
            ),
            {"id": stated_line.id},
        ).mappings().first()
        assert row["quantity_received"] == 29, (
            f"a stated 29 must never drop when a SIBLING's GRN is deleted - got {row}"
        )
        assert row["line_status"] == "closed", row
        assert row["receipt_status"] == "fully_received", row

    def test_a_cancelled_member_is_never_touched_when_a_siblings_grn_is_deleted(self, env):
        """AC-X36 (D28c), the `cancelled` guard. A member with
        `line_status='cancelled'` must be skipped completely by the group
        recompute - no share, no write - even when a sibling's GRN release
        forces the group to recompute.
        """
        from app.services.procurement_service import PickingHeaderService

        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)
        doc_ref = f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}"

        cancelled = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            allocated_quantity=29, quantity_received=7, line_status="cancelled",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC1:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
            stated_received=7,
        )
        sibling = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=2, product_id=product_id, location_code=wh_code,
            allocated_quantity=18, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC2:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
            stated_received=0,
        )
        env.db.add_all([cancelled, sibling])
        env.db.flush()

        header = PickingHeader(
            id=str(uuid.uuid4()), company_id=env.company_a,
            picking_number=unique_code(MARKER), picking_type="goods_received",
            picking_status="approved", spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()), company_id=env.company_a,
                picking_header_id=header.id, spo_allocation_id=sibling.id,
                product_id=product_id, quantity_expected=5, quantity_picked=5,
            )
        )
        env.db.flush()
        env.db.commit()

        PickingHeaderService(env.db).delete_grn(header.id)

        env.db.expire_all()
        row = env.db.execute(
            text(
                "SELECT quantity_received, line_status, receipt_status "
                "FROM spo_allocations WHERE id = :id"
            ),
            {"id": cancelled.id},
        ).mappings().first()
        assert row["quantity_received"] == 7, (
            f"a cancelled member must never be written by the group recompute - got {row}"
        )
        assert row["line_status"] == "cancelled", row
        assert row["receipt_status"] == "pending", row


# ============================================================================ #
# AC-X37 (D28b draft gate, reviewer KB)
# ============================================================================ #
class TestAcX37DraftHeaderNeverRunsTheRecompute:
    def test_a_draft_headers_picking_line_writes_nothing_and_updated_at_is_unchanged(
        self, env
    ):
        """AC-X37. With the AC-X28 seed and the picking line on a DRAFT
        `goods_received` header, neither `sync_grn_received_to_spo` nor
        `sync_received_for_spo_number` writes either line (25 / 18
        unchanged, `updated_at` unchanged). Approving the header is what
        makes the recompute run.
        """
        from app.services.procurement_service import PickingHeaderService

        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)
        doc_ref = f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}"

        line1 = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            allocated_quantity=29, quantity_received=25, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC1:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
            stated_received=25,
        )
        line2 = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=2, product_id=product_id, location_code=wh_code,
            allocated_quantity=18, quantity_received=18, line_status="closed",
            receipt_status="fully_received", source_system="autocount",
            source_ref=f"{MARKER}:AC2:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
            stated_received=18,
        )
        env.db.add_all([line1, line2])
        env.db.flush()

        header = PickingHeader(
            id=str(uuid.uuid4()), company_id=env.company_a,
            picking_number=unique_code(MARKER), picking_type="goods_received",
            picking_status="draft", spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()), company_id=env.company_a,
                picking_header_id=header.id, spo_allocation_id=line1.id,
                product_id=product_id, quantity_expected=5, quantity_picked=5,
            )
        )
        env.db.flush()
        env.db.commit()

        def _row(alloc_id):
            env.db.expire_all()
            return env.db.execute(
                text(
                    "SELECT quantity_received, updated_at FROM spo_allocations "
                    "WHERE id = :id"
                ),
                {"id": alloc_id},
            ).mappings().first()

        before1, before2 = _row(line1.id), _row(line2.id)

        svc = PickingHeaderService(env.db)
        svc.sync_grn_received_to_spo(header.id)
        svc.sync_received_for_spo_number(number)

        after1, after2 = _row(line1.id), _row(line2.id)
        assert after1["quantity_received"] == 25, after1
        assert after2["quantity_received"] == 18, after2
        assert after1["updated_at"] == before1["updated_at"], (
            "a DRAFT header's picking line must never trigger the recompute at all",
            after1, before1,
        )
        assert after2["updated_at"] == before2["updated_at"], (after2, before2)


# ============================================================================ #
# AC-X38 (D28c writers)
# ============================================================================ #
class TestAcX38StatedReceivedWrittenByTheIngestWriterNeverByTheGrnRecompute:
    def test_after_ac_x1_both_lines_carry_the_carried_share_as_stated_and_it_survives_a_repush(
        self, env
    ):
        """AC-X38. `stated_received` is written by every declarer of an
        AutoCount line's receipt - here, the first-push supersede carry
        (each line's carried share, D26). After AC-X1's push both lines
        must carry `stated_received` 29 / 18; after a second push of the
        same DocKey with `qty_received 0` (AC-X9's shape) they must still
        carry 29 / 18 - the GRN recompute is not involved in this test at
        all, and the max rule never lowers a stated figure.
        """
        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=47,
            quantity_received=47,
            line_status="closed",
        )

        line1 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0, line_number=1
        )
        line2 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0, line_number=2
        )
        record = _spo_record(
            env, number=number, lines=[line1, line2], supplier_ref=env.supplier_ref
        )

        res1 = env.post(INGEST_SPO, [record])
        assert res1.status_code == 200, res1.text

        rows = {r["source_ref"]: r for r in _spo_rows(env, number)}
        first_id = rows[line1["source_ref"]]["id"]
        second_id = rows[line2["source_ref"]]["id"]

        def _stated():
            env.db.expire_all()
            found = env.db.execute(
                text(
                    "SELECT id, stated_received FROM spo_allocations "
                    "WHERE id IN (:a, :b)"
                ),
                {"a": str(first_id), "b": str(second_id)},
            ).mappings().all()
            return {str(r["id"]): r["stated_received"] for r in found}

        by_id = _stated()
        assert by_id[str(first_id)] == 29, by_id
        assert by_id[str(second_id)] == 18, by_id

        res2 = env.post(INGEST_SPO, [record])
        assert res2.status_code == 200, res2.text

        by_id2 = _stated()
        assert by_id2[str(first_id)] == 29, (
            "a re-push with qty_received 0 must never lower a stated figure "
            f"- got {by_id2}"
        )
        assert by_id2[str(second_id)] == 18, by_id2


# ============================================================================ #
# AC-X40 (D28c retirement freeze, reviewer F2)
# ============================================================================ #
class TestAcX40RetirementFreezesTheReceiptAGrnAloneProved:
    def test_a_line_the_push_no_longer_names_freezes_its_receipt_and_never_reopens(
        self, env
    ):
        """AC-X40. An `autocount` line closed by a GRN (allocated 29,
        received 29 via one approved picking line, stated 0) that a later
        push of the same DocKey no longer names (leftover sweep closes it)
        must carry `stated_received 29` after that push; deleting the GRN
        afterwards must leave it closed at 29, never reopened - a line
        AutoCount retired is not demand again because the CRM receipt that
        closed it went away. The SIBLING, still named by the push, behaves
        per AC-X35 (its share was only ever a redistribution of the same
        GRN, so it drops and reopens when that GRN is deleted).
        """
        from app.services.procurement_service import PickingHeaderService

        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        doc_ref = f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}"
        dtl1 = f"{MARKER}:AC1:{uuid.uuid4().hex[:8]}"
        dtl2 = f"{MARKER}:AC2:{uuid.uuid4().hex[:8]}"

        line1 = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            allocated_quantity=29, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=dtl1, source_doc_ref=doc_ref, stated_received=0,
        )
        line2 = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=2, product_id=product_id, location_code=wh_code,
            allocated_quantity=18, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=dtl2, source_doc_ref=doc_ref, stated_received=0,
        )
        env.db.add_all([line1, line2])
        env.db.flush()

        header = PickingHeader(
            id=str(uuid.uuid4()), company_id=env.company_a,
            picking_number=unique_code(MARKER), picking_type="goods_received",
            picking_status="approved", spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()), company_id=env.company_a,
                picking_header_id=header.id, spo_allocation_id=line1.id,
                product_id=product_id, quantity_expected=47, quantity_picked=47,
            )
        )
        env.db.flush()
        env.db.commit()

        svc = PickingHeaderService(env.db)
        svc.sync_grn_received_to_spo(header.id)

        def _row(alloc_id):
            env.db.expire_all()
            return env.db.execute(
                text(
                    "SELECT quantity_received, line_status, receipt_status, "
                    "stated_received FROM spo_allocations WHERE id = :id"
                ),
                {"id": alloc_id},
            ).mappings().first()

        pre = _row(line1.id)
        assert pre["quantity_received"] == 29, pre
        assert pre["line_status"] == "closed", pre

        line2_push = _spo_line(
            env, ref=dtl2, warehouse_ref=env.warehouse_ref, product_ref=env.product_ref,
            qty_ordered=18, qty_received=0,
        )
        record = _spo_record(
            env, ref=doc_ref, number=number, lines=[line2_push], supplier_ref=env.supplier_ref
        )
        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text

        after_push = _row(line1.id)
        assert after_push["stated_received"] == 29, (
            "retiring an AutoCount line must freeze the receipt it was "
            f"retired with - got {after_push}"
        )
        assert after_push["line_status"] == "closed", after_push

        svc.delete_grn(header.id)
        after_delete = _row(line1.id)
        assert after_delete["quantity_received"] == 29, after_delete
        assert after_delete["line_status"] == "closed", (
            "a line AutoCount retired must never reopen because the CRM "
            f"receipt that closed it went away - got {after_delete}"
        )
        assert after_delete["receipt_status"] == "fully_received", after_delete

        sibling_after = _row(line2.id)
        assert sibling_after["quantity_received"] == 0, sibling_after
        assert sibling_after["line_status"] == "open", sibling_after
        assert sibling_after["receipt_status"] == "pending", sibling_after


# ============================================================================ #
# AC-X41 (D28c distribution members, reviewer F4)
# ============================================================================ #
class TestAcX41ARetiredByAbsenceMemberTakesNoShare:
    def test_a_line_retired_by_absence_never_receives_a_share_remainder_goes_to_the_last_live_line(
        self, env
    ):
        """AC-X41. Two `autocount` lines, L1 (Seq 1, allocated 29) retired
        by absence at received 0 (closed, `receipt_status pending`, stated
        0) and L2 (Seq 2, allocated 18, open) with one approved GRN of 40
        against L2: after the recompute L1 still reads 0 closed and L2
        reads 40 (the whole picking total, remainder on the last LIVE
        line). A member closed with `receipt_status != fully_received`
        (retired by absence, or `cancelled`) takes no share; a member
        closed BY receipt still does (and may reopen, AC-X35).
        """
        from app.services.procurement_service import PickingHeaderService

        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)
        doc_ref = f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}"

        retired = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            allocated_quantity=29, quantity_received=0, line_status="closed",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC1:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
            stated_received=0,
        )
        live_line = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=2, product_id=product_id, location_code=wh_code,
            allocated_quantity=18, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC2:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
            stated_received=0,
        )
        env.db.add_all([retired, live_line])
        env.db.flush()

        header = PickingHeader(
            id=str(uuid.uuid4()), company_id=env.company_a,
            picking_number=unique_code(MARKER), picking_type="goods_received",
            picking_status="approved", spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()), company_id=env.company_a,
                picking_header_id=header.id, spo_allocation_id=live_line.id,
                product_id=product_id, quantity_expected=40, quantity_picked=40,
            )
        )
        env.db.flush()
        env.db.commit()

        PickingHeaderService(env.db).sync_grn_received_to_spo(header.id)

        env.db.expire_all()
        rows = env.db.execute(
            text(
                "SELECT id, quantity_received, line_status, receipt_status "
                "FROM spo_allocations WHERE id IN (:a, :b)"
            ),
            {"a": str(retired.id), "b": str(live_line.id)},
        ).mappings().all()
        by_id = {str(r["id"]): r for r in rows}

        assert by_id[str(retired.id)]["quantity_received"] == 0, (
            f"a line retired by absence must take NO share - got {by_id}"
        )
        assert by_id[str(retired.id)]["line_status"] == "closed", by_id
        assert by_id[str(retired.id)]["receipt_status"] == "pending", by_id

        assert by_id[str(live_line.id)]["quantity_received"] == 40, (
            "the whole picking total must land on the last LIVE line, "
            f"since the retired member takes no share - got {by_id}"
        )
        assert by_id[str(live_line.id)]["line_status"] == "closed", by_id
        assert by_id[str(live_line.id)]["receipt_status"] == "fully_received", by_id


# ============================================================================ #
# AC-X42 (D28c, security round 4)
# ============================================================================ #
class TestAcX42ARetiredMembersOwnGrnIsNotRedistributed:
    def test_a_retired_lines_own_picking_line_stays_on_it_never_redistributed_to_a_sibling(
        self, env
    ):
        """AC-X42. A retired member's own GRN is not redistributed. L1
        (Seq 1, allocated 29, retired by absence: closed, `receipt_status
        pending`, received 10, stated 10) HOLDS one approved picking line
        of 10; L2 (Seq 2, allocated 18, open, 0) has no picking line at
        all. After `sync_grn_received_to_spo(<L1's header>)` and after
        `sync_received_for_spo_number(N)`: L1 still reads 10 closed
        pending, L2 still reads 0 open. The live lines share only the
        picking total drawn against LIVE members; a receipt a retired
        line reports is never counted twice.

        RED today: `_sync_group_received`'s `kept_total` sums `computed`
        over every NON-RELEASED member regardless of live status - L1's
        own 10 (proven by its own picking line) is folded into the pool
        redistributed across `live_targets`, which here is L2 alone. L2
        therefore receives L1's already-accounted-for 10 as if it were
        its own share, reading `quantity_received == 10` instead of 0.
        """
        from app.services.procurement_service import PickingHeaderService

        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)
        doc_ref = f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}"

        retired = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            allocated_quantity=29, quantity_received=10, line_status="closed",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC1:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
            stated_received=10,
        )
        live_line = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=2, product_id=product_id, location_code=wh_code,
            allocated_quantity=18, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC2:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
            stated_received=0,
        )
        env.db.add_all([retired, live_line])
        env.db.flush()

        header = PickingHeader(
            id=str(uuid.uuid4()), company_id=env.company_a,
            picking_number=unique_code(MARKER), picking_type="goods_received",
            picking_status="approved", spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()), company_id=env.company_a,
                picking_header_id=header.id, spo_allocation_id=retired.id,
                product_id=product_id, quantity_expected=10, quantity_picked=10,
            )
        )
        env.db.flush()
        env.db.commit()

        def _rows():
            env.db.expire_all()
            rows = env.db.execute(
                text(
                    "SELECT id, quantity_received, line_status, receipt_status "
                    "FROM spo_allocations WHERE id IN (:a, :b)"
                ),
                {"a": str(retired.id), "b": str(live_line.id)},
            ).mappings().all()
            return {str(r["id"]): r for r in rows}

        PickingHeaderService(env.db).sync_grn_received_to_spo(header.id)

        by_id = _rows()
        assert by_id[str(retired.id)]["quantity_received"] == 10, by_id
        assert by_id[str(retired.id)]["line_status"] == "closed", by_id
        assert by_id[str(retired.id)]["receipt_status"] == "pending", by_id
        assert by_id[str(live_line.id)]["quantity_received"] == 0, (
            "a retired member's own proven receipt must never be "
            f"redistributed to a live sibling - got {by_id}"
        )
        assert by_id[str(live_line.id)]["line_status"] == "open", by_id
        assert by_id[str(live_line.id)]["receipt_status"] == "pending", by_id

        PickingHeaderService(env.db).sync_received_for_spo_number(number)

        by_id2 = _rows()
        assert by_id2[str(retired.id)]["quantity_received"] == 10, by_id2
        assert by_id2[str(retired.id)]["line_status"] == "closed", by_id2
        assert by_id2[str(retired.id)]["receipt_status"] == "pending", by_id2
        assert by_id2[str(live_line.id)]["quantity_received"] == 0, by_id2
        assert by_id2[str(live_line.id)]["line_status"] == "open", by_id2
        assert by_id2[str(live_line.id)]["receipt_status"] == "pending", by_id2


# ============================================================================ #
# Round 5 (reviewer kill-test round, 2026-09-07, PLAN D28d retirement marker)
# ============================================================================ #
# Vocabulary (UAC): "retired" = `spo_allocations.retired_at IS NOT NULL`, set
# when the ESB stops naming a line - by absence in a re-push of the same
# DocKey (the leftover sweep) or by the document being re-created under a
# NEW DocKey (the old DocKey's rows) - and cleared when a push names the row
# again.


# ============================================================================ #
# AC-X43 (D28d DocKey change)
# ============================================================================ #
class TestAcX43ANewDockeyRetiresTheOldDockeysClosedRow:
    def test_a_push_under_a_fresh_dockey_retires_the_old_dockeys_row_and_never_double_counts_open(
        self, env
    ):
        """AC-X43. DocKey A's line L1 (P at L, allocated 29, received 29 by
        a Sorento GRN only, stated NULL, closed `fully_received`); a push
        under a FRESH DocKey B names P at L qty 29 received 0 with a NEW
        DtlKey. After push B: L1 carries `retired_at` set and
        `stated_received 29`; M1 (B's row) is open 0 / 29. After
        `delete_grn` of L1's GRN: L1 still closed at 29, M1 unchanged; open
        outstanding on the SPO is 29, never 58.

        RED today: `retired_at` does not exist - the raw SQL read below
        raises `UndefinedColumn`. Once the column lands but before the
        DocKey-change retirement is written, L1 is simply never touched by
        push B at all (a push under a NEW DocKey has no code path that
        looks at another DocKey's rows), so `retired_at` stays NULL.
        """
        from app.services.procurement_service import PickingHeaderService

        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)
        doc_a = f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}"

        l1 = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            allocated_quantity=29, quantity_received=29, line_status="closed",
            receipt_status="fully_received", source_system="autocount",
            source_ref=f"{MARKER}:AC1:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_a,
            stated_received=None,
        )
        env.db.add(l1)
        env.db.flush()

        header = PickingHeader(
            id=str(uuid.uuid4()), company_id=env.company_a,
            picking_number=unique_code(MARKER), picking_type="goods_received",
            picking_status="approved", spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()), company_id=env.company_a,
                picking_header_id=header.id, spo_allocation_id=l1.id,
                product_id=product_id, quantity_expected=29, quantity_picked=29,
            )
        )
        env.db.flush()
        env.db.commit()

        line_b = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0
        )
        record_b = _spo_record(
            env, number=number, lines=[line_b], supplier_ref=env.supplier_ref
        )
        res = env.post(INGEST_SPO, [record_b])
        assert res.status_code == 200, res.text

        def _row(alloc_id):
            env.db.expire_all()
            return env.db.execute(
                text(
                    "SELECT quantity_received, line_status, receipt_status, "
                    "stated_received, retired_at FROM spo_allocations WHERE id = :id"
                ),
                {"id": alloc_id},
            ).mappings().first()

        after_push = _row(l1.id)
        assert after_push["retired_at"] is not None, (
            "L1 (DocKey A's row) must be retired once a push arrives under a "
            f"fresh DocKey naming the same product/location - got {after_push}"
        )
        assert after_push["stated_received"] == 29, (
            "the DocKey-change retirement must freeze stated = max(stated, "
            f"received) - got {after_push}"
        )
        assert after_push["quantity_received"] == 29, after_push
        assert after_push["line_status"] == "closed", after_push

        rows_by_ref = {r["source_ref"]: r for r in _spo_rows(env, number)}
        m1 = rows_by_ref[line_b["source_ref"]]
        assert m1["quantity_received"] == 0, m1
        assert m1["line_status"] == "open", m1
        assert m1["allocated_quantity"] == 29, m1

        PickingHeaderService(env.db).delete_grn(header.id)

        after_delete = _row(l1.id)
        assert after_delete["quantity_received"] == 29, (
            "a retired row must stay at 29 - a GRN release must never touch "
            f"it - got {after_delete}"
        )
        assert after_delete["line_status"] == "closed", after_delete

        m1_after = {r["source_ref"]: r for r in _spo_rows(env, number)}[
            line_b["source_ref"]
        ]
        assert m1_after["quantity_received"] == 0, (
            "M1 must be unaffected by L1's GRN release - it never shared a "
            f"group with a retired row - got {m1_after}"
        )

        open_gap = env.db.execute(
            text(
                "SELECT COALESCE(SUM(allocated_quantity - quantity_received), 0) AS gap "
                "FROM spo_allocations WHERE company_id = :c AND spo_number = :n "
                "AND line_status != 'closed'"
            ),
            {"c": env.company_a, "n": number},
        ).scalar()
        assert int(open_gap) == 29, (
            "open outstanding on the SPO must be 29 (M1's own gap), never 58 "
            f"(double-counting L1's retired 29) - got {open_gap}"
        )


# ============================================================================ #
# AC-X44 (D28d membership)
# ============================================================================ #
class TestAcX44ARetiredRowIsNotAGroupMember:
    def test_a_retired_dockey_change_row_takes_no_share_from_a_fresh_grn_on_the_new_row(
        self, env
    ):
        """AC-X44, first half. A retired row (L1 from the AC-X43 shape:
        closed, `fully_received`, stated 29, `retired_at` set, no active
        picking line of its own) beside M1 (open, allocated 29, same
        group) with an approved GRN of 29 against M1 ONLY: the recompute
        must write M1 29 closed and leave L1 untouched - a retired, fully
        received line takes no share.

        RED today: `_autocount_group_members` does not exclude a retired
        row (the column/filter does not exist) - L1 is closed +
        `fully_received`, which `_is_live_group_member` already treats as
        LIVE (D28c/AC-X35), so L1 (Seq 1) absorbs the WHOLE redistribution
        ahead of M1 (Seq 2) in `distribute_received`'s ordered algorithm
        (L1's own allocated 29 exactly consumes the total), leaving M1 at
        0 instead of 29.
        """
        from app.services.procurement_service import PickingHeaderService

        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)
        doc_a = f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}"
        doc_b = f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}"

        l1 = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            allocated_quantity=29, quantity_received=29, line_status="closed",
            receipt_status="fully_received", source_system="autocount",
            source_ref=f"{MARKER}:AC1:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_a,
            stated_received=29, retired_at=_now(),
        )
        m1 = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=2, product_id=product_id, location_code=wh_code,
            allocated_quantity=29, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:BC1:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_b,
            stated_received=0,
        )
        env.db.add_all([l1, m1])
        env.db.flush()

        header = PickingHeader(
            id=str(uuid.uuid4()), company_id=env.company_a,
            picking_number=unique_code(MARKER), picking_type="goods_received",
            picking_status="approved", spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()), company_id=env.company_a,
                picking_header_id=header.id, spo_allocation_id=m1.id,
                product_id=product_id, quantity_expected=29, quantity_picked=29,
            )
        )
        env.db.flush()
        env.db.commit()

        PickingHeaderService(env.db).sync_grn_received_to_spo(header.id)

        env.db.expire_all()
        rows = env.db.execute(
            text(
                "SELECT id, quantity_received, line_status, receipt_status "
                "FROM spo_allocations WHERE id IN (:a, :b)"
            ),
            {"a": str(l1.id), "b": str(m1.id)},
        ).mappings().all()
        by_id = {str(r["id"]): r for r in rows}

        assert by_id[str(m1.id)]["quantity_received"] == 29, (
            "M1 must receive the full 29 its own GRN proved - a retired "
            f"sibling must never absorb it first - got {by_id}"
        )
        assert by_id[str(m1.id)]["line_status"] == "closed", by_id
        assert by_id[str(m1.id)]["receipt_status"] == "fully_received", by_id

        assert by_id[str(l1.id)]["quantity_received"] == 29, (
            f"a retired row must never be written by the group recompute - got {by_id}"
        )
        assert by_id[str(l1.id)]["line_status"] == "closed", by_id
        assert by_id[str(l1.id)]["receipt_status"] == "fully_received", by_id

    def test_a_retired_by_absence_at_full_receipt_row_takes_no_share_from_a_fresh_grn_on_a_sibling(
        self, env
    ):
        """AC-X44, second half (the AC-X40 shape). A same-DocKey row
        retired BY ABSENCE at FULL receipt (closed, `fully_received`,
        stated 29, `retired_at` set - AC-X40's outcome) beside a LIVE
        sibling (open, allocated 18) with a FRESH approved GRN of 40
        against the sibling ONLY: the sibling must read 40 (the whole
        total, remainder rule - it is the only LIVE member), the retired
        row must stay untouched.

        RED today for the same structural reason as the first half: a
        closed + `fully_received` row is LIVE under D28c alone (AC-X35),
        so without the `retired_at` exclusion the retired row (Seq 1,
        allocated 29) absorbs 29 of the 40 ahead of the sibling (Seq 2,
        last), leaving the sibling at 11 instead of 40.
        """
        from app.services.procurement_service import PickingHeaderService

        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)
        doc_ref = f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}"

        retired = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            allocated_quantity=29, quantity_received=29, line_status="closed",
            receipt_status="fully_received", source_system="autocount",
            source_ref=f"{MARKER}:AC1:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
            stated_received=29, retired_at=_now(),
        )
        sibling = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=2, product_id=product_id, location_code=wh_code,
            allocated_quantity=18, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC2:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
            stated_received=0,
        )
        env.db.add_all([retired, sibling])
        env.db.flush()

        header = PickingHeader(
            id=str(uuid.uuid4()), company_id=env.company_a,
            picking_number=unique_code(MARKER), picking_type="goods_received",
            picking_status="approved", spo_number=number,
        )
        env.db.add(header)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()), company_id=env.company_a,
                picking_header_id=header.id, spo_allocation_id=sibling.id,
                product_id=product_id, quantity_expected=40, quantity_picked=40,
            )
        )
        env.db.flush()
        env.db.commit()

        PickingHeaderService(env.db).sync_grn_received_to_spo(header.id)

        env.db.expire_all()
        rows = env.db.execute(
            text(
                "SELECT id, quantity_received, line_status, receipt_status "
                "FROM spo_allocations WHERE id IN (:a, :b)"
            ),
            {"a": str(retired.id), "b": str(sibling.id)},
        ).mappings().all()
        by_id = {str(r["id"]): r for r in rows}

        assert by_id[str(sibling.id)]["quantity_received"] == 40, (
            "the sibling must take the WHOLE 40 (remainder rule, only LIVE "
            f"member) - a retired row must never absorb any of it - got {by_id}"
        )
        assert by_id[str(sibling.id)]["line_status"] == "closed", by_id
        assert by_id[str(sibling.id)]["receipt_status"] == "fully_received", by_id

        assert by_id[str(retired.id)]["quantity_received"] == 29, (
            f"a retired row must never be written by the group recompute - got {by_id}"
        )
        assert by_id[str(retired.id)]["line_status"] == "closed", by_id


# ============================================================================ #
# AC-X45 (D28d unretire)
# ============================================================================ #
class TestAcX45ARowRetiredByAbsenceUnretiresWhenNamedAgain:
    def test_a_third_push_naming_the_row_again_clears_retired_at(self, env):
        """AC-X45. A row retired by absence (a re-push of the same DocKey
        that omitted it) has `retired_at` NULL again once a LATER push of
        the SAME DocKey names it again - it rejoins the group.

        RED today: `retired_at` does not exist - the raw SQL read raises
        `UndefinedColumn`. Once it exists but before the leftover sweep
        sets it, the first assertion below (retired_at IS NOT NULL after
        the omitting push) already fails.
        """
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)
        doc_ref = f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}"
        dtl1 = f"{MARKER}:AC1:{uuid.uuid4().hex[:8]}"
        dtl2 = f"{MARKER}:AC2:{uuid.uuid4().hex[:8]}"

        line1 = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            allocated_quantity=29, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=dtl1, source_doc_ref=doc_ref, stated_received=0,
        )
        line2 = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=2, product_id=product_id, location_code=wh_code,
            allocated_quantity=18, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="autocount",
            source_ref=dtl2, source_doc_ref=doc_ref, stated_received=0,
        )
        env.db.add_all([line1, line2])
        env.db.flush()
        env.db.commit()

        def _row(alloc_id):
            env.db.expire_all()
            return env.db.execute(
                text(
                    "SELECT line_status, retired_at FROM spo_allocations WHERE id = :id"
                ),
                {"id": alloc_id},
            ).mappings().first()

        # Push 2: same DocKey, omits line 1's DtlKey - the leftover sweep
        # retires it.
        line2_push = _spo_line(
            env, ref=dtl2, warehouse_ref=env.warehouse_ref, product_ref=env.product_ref,
            qty_ordered=18, qty_received=0,
        )
        record2 = _spo_record(
            env, ref=doc_ref, number=number, lines=[line2_push], supplier_ref=env.supplier_ref
        )
        res2 = env.post(INGEST_SPO, [record2])
        assert res2.status_code == 200, res2.text

        after_retire = _row(line1.id)
        assert after_retire["retired_at"] is not None, (
            f"a leftover-swept AutoCount line must be retired - got {after_retire}"
        )
        assert after_retire["line_status"] == "closed", after_retire

        # Push 3: same DocKey, names line 1's DtlKey again - it must unretire.
        line1_push = _spo_line(
            env, ref=dtl1, warehouse_ref=env.warehouse_ref, product_ref=env.product_ref,
            qty_ordered=29, qty_received=0,
        )
        line2_push_again = _spo_line(
            env, ref=dtl2, warehouse_ref=env.warehouse_ref, product_ref=env.product_ref,
            qty_ordered=18, qty_received=0,
        )
        record3 = _spo_record(
            env, ref=doc_ref, number=number, lines=[line1_push, line2_push_again],
            supplier_ref=env.supplier_ref,
        )
        res3 = env.post(INGEST_SPO, [record3])
        assert res3.status_code == 200, res3.text

        after_unretire = _row(line1.id)
        assert after_unretire["retired_at"] is None, (
            "a push naming the row again must clear retired_at - it rejoins "
            f"the group - got {after_unretire}"
        )


# ============================================================================ #
# Round 6 (production dedupe finding, 2026-09-08, PLAN D25c)
# ============================================================================ #
# SPO-2026/09-0028 (the incident): 16 xlsx rows carry `source_system` NULL,
# `location_code` NULL, `warehouse_id` set. Both the Procurement page's
# Upload SPO and the n8n packing-list route write this shape; only the SCM
# outstanding upload writes `scm_upload`. D25a's premise that a NULL-source
# row "states one line for one real line" was wrong.


# ============================================================================ #
# AC-X49 (D25c grouping)
# ============================================================================ #
class TestAcX49GroupingIsByWarehouseWhenBothSidesCarryOne:
    def test_a_scm_upload_row_with_a_free_text_location_and_a_warehouse_groups_by_the_warehouse(
        self, env
    ):
        """AC-X49, first half. A `scm_upload` row with `location_code
        'brw'` (free text) AND `warehouse_id` W, closed, received 47; a
        push naming P at the warehouse whose code resolves to W with ONE
        line qty 47. Grouping must be by `(product_id, warehouse_id)`
        (both sides carry a warehouse) - `'brw'` never has to match
        anything - so the two must group together and carry 47.

        RED today: `supersede_group_key` still takes only
        `(product_id, location_code)` - it never sees `warehouse_id` at
        all, so grouping stays keyed on the free-text `'brw'`, which the
        push's own resolved `location_code` (the warehouse's REAL code)
        never equals. The groups never match: the row is left as a THIRD,
        untouched row and the push's line is created fresh at 0 received.
        """
        wh_id = env.refs.resolve(entity_type="warehouses", source_ref=env.warehouse_ref)
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"

        legacy = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code="brw",
            warehouse_id=wh_id, allocated_quantity=47, quantity_received=47,
            line_status="closed", receipt_status="fully_received",
            source_system="scm_upload",
        )
        env.db.add(legacy)
        env.db.flush()
        env.db.commit()

        line = _spo_line(env, warehouse_ref=env.warehouse_ref, qty_ordered=47, qty_received=0)
        record = _spo_record(env, number=number, lines=[line], supplier_ref=env.supplier_ref)

        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        lines_summary = entry.get("lines") or {}
        assert lines_summary.get("superseded") == 1, lines_summary

        rows = _spo_rows(env, number)
        assert len(rows) == 1, (
            "the free-text 'brw' row and the warehouse-resolved push line "
            f"must group together, not stay as two rows - got {rows}"
        )
        assert rows[0]["quantity_received"] == 47, rows[0]
        assert rows[0]["line_status"] == "closed", rows[0]
        assert str(legacy.id) not in {str(r["id"]) for r in rows}

    def test_two_rows_for_different_warehouses_form_two_groups_each_carried_onto_its_own_line(
        self, env
    ):
        """AC-X49, second half. Two `scm_upload` rows, same product, one
        for warehouse W (allocated 29, received 29, closed) and one for a
        SECOND warehouse W2 (allocated 18, received 18, closed); a push
        names the SAME product at BOTH warehouses. Grouping by
        `(product_id, warehouse_id)` must keep these as TWO SEPARATE
        groups - W's carry (29) must never land on W2's line or vice
        versa.

        RED today: the pre-D25c key is `(product_id, upper(location_code))`
        only, with no notion of warehouse at all, and both rows/lines here
        share the same `location_code` fallback path incorrectly (or, if
        `location_code` differs, the key already happens to separate them
        by accident) - the deterministic failure is that BOTH rows are
        `scm_upload` and BOTH groups have no ref row of their own yet, so
        without the warehouse-aware key the push's own resolved
        `location_code` for each warehouse is what actually has to
        separate them, which is exactly the pre-existing (working) case -
        this test instead pins the NEW dimension by asserting the carried
        totals land on the CORRECT warehouse's line, not merely that two
        rows exist.
        """
        wh1_id = env.refs.resolve(entity_type="warehouses", source_ref=env.warehouse_ref)
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh2_ref = env.link_warehouse(env.company_a)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"

        legacy_w1 = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=None,
            warehouse_id=wh1_id, allocated_quantity=29, quantity_received=29,
            line_status="closed", receipt_status="fully_received",
            source_system="scm_upload",
        )
        wh2_id = env.refs.resolve(entity_type="warehouses", source_ref=wh2_ref)
        legacy_w2 = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=2, product_id=product_id, location_code=None,
            warehouse_id=wh2_id, allocated_quantity=18, quantity_received=18,
            line_status="closed", receipt_status="fully_received",
            source_system="scm_upload",
        )
        env.db.add_all([legacy_w1, legacy_w2])
        env.db.flush()
        env.db.commit()

        line_w1 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0
        )
        line_w2 = _spo_line(
            env, warehouse_ref=wh2_ref, qty_ordered=18, qty_received=0
        )
        record = _spo_record(
            env, number=number, lines=[line_w1, line_w2], supplier_ref=env.supplier_ref
        )

        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        lines_summary = entry.get("lines") or {}
        assert lines_summary.get("superseded") == 2, lines_summary

        rows = _spo_rows(env, number)
        assert len(rows) == 2, rows
        by_ref = {r["source_ref"]: r for r in rows}
        row_w1 = by_ref[line_w1["source_ref"]]
        row_w2 = by_ref[line_w2["source_ref"]]
        assert row_w1["quantity_received"] == 29, (
            f"W's own 29 must land on W's own line, never W2's - got {row_w1}"
        )
        assert row_w1["line_status"] == "closed", row_w1
        assert row_w2["quantity_received"] == 18, (
            f"W2's own 18 must land on W2's own line, never W's - got {row_w2}"
        )
        assert row_w2["line_status"] == "closed", row_w2
        assert str(legacy_w1.id) not in {str(r["id"]) for r in rows}
        assert str(legacy_w2.id) not in {str(r["id"]) for r in rows}


# ============================================================================ #
# AC-X50 (D26 carry, storage_zone_id)
# ============================================================================ #
class TestAcX50StorageZoneCarriesLikeInboundShipment:
    def test_the_superseded_rows_storage_zone_carries_onto_every_new_line_of_the_group(
        self, env
    ):
        """AC-X50. The superseded row's `storage_zone_id` (like its
        `inbound_shipment_id`, AC-X6) is carried onto every new line of
        the group that has none of its own.

        RED today: `SupersedeLinePlan` / the writer that turns a plan into
        a row never reads or writes `storage_zone_id` at all - both new
        lines land with `storage_zone_id IS NULL`.
        """
        wh_id = env.refs.resolve(entity_type="warehouses", source_ref=env.warehouse_ref)
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"

        zone = StorageZone(
            id=str(uuid.uuid4()), warehouse_id=wh_id,
            zone_code=f"{MARKER[:8]}Z{uuid.uuid4().hex[:6]}", zone_type="storage",
        )
        env.db.add(zone)
        env.db.flush()

        legacy = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=None,
            warehouse_id=wh_id, storage_zone_id=zone.id,
            allocated_quantity=47, quantity_received=47, line_status="closed",
            receipt_status="fully_received", source_system="scm_upload",
        )
        env.db.add(legacy)
        env.db.flush()
        env.db.commit()

        line1 = _spo_line(env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0)
        line2 = _spo_line(env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0)
        record = _spo_record(
            env, number=number, lines=[line1, line2], supplier_ref=env.supplier_ref
        )

        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text

        rows = _spo_rows(env, number)
        assert len(rows) == 2, "AC-X1/AC-X49 must land first for this to hold"
        for row in rows:
            assert row["storage_zone_id"] is not None, row
            assert str(row["storage_zone_id"]) == str(zone.id), (
                f"every new line of the group must carry the superseded row's "
                f"storage_zone_id - got {row}"
            )


# ============================================================================ #
# Round 7 (security round 6, 2026-09-08, PLAN D25c amended)
# ============================================================================ #
# Five writers produce ref-less rows. Two load an Excel aggregate with
# `source_system` NULL (Procurement Upload SPO, n8n packing list); one writes
# `scm_upload`; two SCM writers raise ONE row per PO line with `po_line_id`
# set and `source_system` NULL (`spo_conversion_service._write_allocations`,
# `allocation_suggestion_service`). The last two are not aggregates and must
# never be superseded.


def _seed_po_line(env, *, product_id: str, qty_ordered: int = 29, qty_received: int = 0) -> str:
    """A minimal `purchase_order_lines` row under MARKER - the FK a
    `po_line_id`-carrying `spo_allocations` row needs (AC-X52)."""
    po = PurchaseOrder(
        id=str(uuid.uuid4()), company_id=env.company_a,
        po_number=unique_code(MARKER), status="active",
    )
    env.db.add(po)
    env.db.flush()
    po_line = PurchaseOrderLine(
        id=str(uuid.uuid4()), company_id=env.company_a,
        purchase_order_id=po.id, product_id=product_id,
        qty_ordered=qty_ordered, qty_received=qty_received, line_status="open",
    )
    env.db.add(po_line)
    env.db.flush()
    return po_line.id


# ============================================================================ #
# AC-X52 (D25c guard)
# ============================================================================ #
class TestAcX52PoLineIdRowIsNeverASupersedeCandidate:
    def test_a_null_source_row_with_a_po_line_id_is_adopted_not_superseded(self, env):
        """AC-X52 (D25c guard, security round 6). A ref-less NULL-source
        row carrying `po_line_id` (the SCM writers' shape - ONE row per PO
        line, never an Excel aggregate) is never a supersede candidate: on
        a first push naming its product and warehouse it keeps its id AND
        its `po_line_id` (adopted in place per the pre-existing rules),
        `lines.superseded` absent.

        RED today: `is_xlsx_era_row` has no `po_line_id` guard - this row
        (NULL `source_system`, no `source_ref`) is gathered into
        `supersede_pool` exactly like a genuine Excel aggregate, deleted,
        and its `po_line_id` (the PO -> SPO -> GRN chain) is lost with it:
        `len(rows) == 1` fails (two rows land instead) and the surviving
        row's id is the NEW one, not the seeded one.
        """
        wh_id = env.refs.resolve(entity_type="warehouses", source_ref=env.warehouse_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"

        po_line_id = _seed_po_line(env, product_id=product_id, qty_ordered=29, qty_received=0)

        row = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            warehouse_id=wh_id, allocated_quantity=29, quantity_received=0,
            line_status="open", receipt_status="pending", source_system=None,
            po_line_id=po_line_id,
        )
        env.db.add(row)
        env.db.flush()
        env.db.commit()

        line = _spo_line(env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0)
        record = _spo_record(env, number=number, lines=[line], supplier_ref=env.supplier_ref)

        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        lines_summary = entry.get("lines") or {}
        assert "superseded" not in lines_summary, lines_summary

        rows = _spo_rows(env, number)
        assert len(rows) == 1, (
            "a po_line_id row must be ADOPTED in place, never superseded "
            f"into two rows - got {rows}"
        )
        assert str(rows[0]["id"]) == str(row.id), (
            "adoption in place must keep the SAME row id", rows
        )
        assert rows[0]["po_line_id"] is not None and str(rows[0]["po_line_id"]) == str(
            po_line_id
        ), ("po_line_id must survive - the PO -> SPO -> GRN chain must never be severed", rows)
        assert rows[0]["source_ref"] == line["source_ref"], rows

    def test_the_dedupe_leaves_a_po_line_id_row_alone(self, env):
        """AC-X52, dedupe half. The dedupe must skip a document whose only
        ref-less row carries `po_line_id`, even alongside `autocount` ref
        rows for the SAME product/warehouse - deleting it would sever the
        PO linkage.

        RED today: the dedupe's own document-selection predicate has no
        `po_line_id IS NULL` guard, so this document is gathered as a
        candidate and the row is removed exactly like a genuine xlsx row.
        """
        wh_id = env.refs.resolve(entity_type="warehouses", source_ref=env.warehouse_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"

        po_line_id = _seed_po_line(env, product_id=product_id, qty_ordered=29, qty_received=29)

        row = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            warehouse_id=wh_id, allocated_quantity=29, quantity_received=29,
            line_status="closed", receipt_status="fully_received", source_system=None,
            po_line_id=po_line_id,
        )
        env.db.add(row)
        env.db.flush()

        doc_ref = f"{MARKER}:ACDOC:{uuid.uuid4().hex[:8]}"
        ref_row = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=2, product_id=product_id, location_code=wh_code,
            warehouse_id=wh_id, allocated_quantity=18, quantity_received=0,
            line_status="open", receipt_status="pending", source_system="autocount",
            source_ref=f"{MARKER}:AC1:{uuid.uuid4().hex[:8]}", source_doc_ref=doc_ref,
        )
        env.db.add(ref_row)
        env.db.flush()
        env.db.commit()

        from scripts.dedupe_spo_xlsx_superseded import run

        run(env.db, env.company_a, dry_run=False)

        env.db.expire_all()
        still_there = env.db.execute(
            text("SELECT id, po_line_id FROM spo_allocations WHERE id = :id"),
            {"id": row.id},
        ).mappings().first()
        assert still_there is not None, (
            "a po_line_id row must never be removed by the dedupe", still_there
        )
        assert str(still_there["po_line_id"]) == str(po_line_id), still_there


# ============================================================================ #
# AC-X53a (read path)
# ============================================================================ #
class TestAcX53aCrmSpoReadsComputedLikeNullSource:
    def test_a_crm_spo_row_reads_computed_from_its_approved_picking_line(self, env):
        """AC-X53a (read path). `_receipt_is_computed` must treat
        `crm_spo` exactly like NULL: an SCM-raised allocation stamped
        `crm_spo` with one approved GRN of 5 must list `quantity_received
        5` on the SPO allocations read path (`SPOAllocationService.
        list_allocations`), not its stored 0.

        RED today: `_receipt_is_computed` returns `getattr(allocation,
        "source_system", None) is None` - a `crm_spo` row is not None, so
        the read path skips the compute and reports the stored value (0).
        """
        from app.services.procurement_service import SPOAllocationService

        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        wh_id = env.refs.resolve(entity_type="warehouses", source_ref=env.warehouse_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"

        allocation = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, warehouse_id=wh_id,
            allocated_quantity=29, quantity_received=0, line_status="open",
            receipt_status="pending", source_system="crm_spo",
        )
        env.db.add(allocation)
        env.db.flush()

        header = PickingHeader(
            id=str(uuid.uuid4()), company_id=env.company_a,
            picking_number=unique_code(MARKER), picking_type="goods_received",
            picking_status="approved",
        )
        env.db.add(header)
        env.db.flush()
        env.db.add(
            PickingLine(
                id=str(uuid.uuid4()), company_id=env.company_a,
                picking_header_id=header.id, spo_allocation_id=allocation.id,
                product_id=product_id, quantity_expected=5, quantity_picked=5,
            )
        )
        env.db.flush()
        env.db.commit()

        result = SPOAllocationService(env.db).list_allocations(limit=100)
        rows = {str(r.id): r for r in result["data"]}
        row = rows.get(str(allocation.id))
        assert row is not None, "the seeded allocation must appear on the read path"
        assert row.quantity_received == 5, (
            "a crm_spo row must read its COMPUTED receipt from the approved "
            f"picking line, exactly like a NULL-source row - got {row.quantity_received}"
        )


# ============================================================================ #
# AC-X54 (D26 carry, packing-list columns)
# ============================================================================ #
class TestAcX54PackingListColumnsCarryOntoTheGroup:
    def test_uom_rejected_and_notes_carry_the_ac_x1_shape(self, env):
        """AC-X54. The AC-X1 shape, with the xlsx row ALSO carrying
        `uom_id`, `quantity_rejected 2` and `allocation_notes 'damaged
        carton'`. After the push: BOTH new lines carry the `uom_id`
        (first non-null, like `storage_zone_id` / `inbound_shipment_id`,
        AC-X50/AC-X6); line 1 (the group's first line / repoint target)
        carries `quantity_rejected 2` and notes containing `'damaged
        carton'`; line 2 carries `quantity_rejected 0` - the group total
        lands on the FIRST line only, never split.

        RED today: the writer never reads `line_plan.uom_id`, `group.
        rejected_total` or `group.notes` at all - both new lines land
        with `uom_id IS NULL`, `quantity_rejected 0` and no notes.
        """
        wh_code = _warehouse_code(env, env.warehouse_ref)
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        uom_id = env.db.execute(
            text("SELECT base_uom_id FROM products WHERE id = :id"), {"id": product_id}
        ).scalar()
        assert uom_id is not None, "the seeded product must carry a base uom"

        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        legacy = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=wh_code,
            allocated_quantity=47, quantity_received=47, line_status="closed",
            receipt_status="fully_received", source_system="scm_upload",
            uom_id=uom_id, quantity_rejected=2, allocation_notes="damaged carton",
        )
        env.db.add(legacy)
        env.db.flush()
        env.db.commit()

        line1 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=29, qty_received=0, line_number=1
        )
        line2 = _spo_line(
            env, warehouse_ref=env.warehouse_ref, qty_ordered=18, qty_received=0, line_number=2
        )
        record = _spo_record(
            env, number=number, lines=[line1, line2], supplier_ref=env.supplier_ref
        )

        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text

        rows = {r["source_ref"]: r for r in _spo_rows(env, number)}
        first = rows[line1["source_ref"]]
        second = rows[line2["source_ref"]]

        assert first["uom_id"] is not None and str(first["uom_id"]) == str(uom_id), first
        assert second["uom_id"] is not None and str(second["uom_id"]) == str(uom_id), second

        assert first["quantity_rejected"] == 2, (
            f"the group's rejected total must land on the FIRST line - got {first}"
        )
        assert "damaged carton" in (first["allocation_notes"] or ""), first
        assert second["quantity_rejected"] == 0, (
            f"the rejected total must never be split onto the second line - got {second}"
        )


# ============================================================================ #
# AC-X55 (D25c, warehouse-keyed and location-keyed groups never merge)
# ============================================================================ #
class TestAcX55AWarehouseKeyedGroupNeverMergesWithADifferentlyKeyedOne:
    def test_a_warehouse_id_row_and_a_location_code_row_stay_two_separate_groups(self, env):
        """AC-X55. Row A: NULL-source, `location_code` NULL, `warehouse_id`
        W, 10/10 closed. Row B: `source_system='scm_upload'`,
        `location_code`= W's OWN warehouse_code (any case), `warehouse_id`
        NULL, 5/5 closed. Same product, same SPO, no ref rows. A first
        push names ONE line for W's warehouse_code, qty 15, received 0.

        The incoming line resolves BOTH `warehouse_id=W` and
        `location_code=<W's code>`, so it answers to BOTH row A's key
        (`wh:W`) and row B's key (`loc:<CODE>`) - but it is still only ONE
        line, and warehouse-keyed groups are considered first and claim it
        first (the id is the exact statement, the code the fallback): row
        A is superseded (carries its own 10), row B's group then finds no
        counterpart left and is KEPT, not double-claimed and not merged
        with row A's.

        Expected: 200, outcome `created`, `lines.superseded 1`, exactly
        ONE row carrying the pushed line's `source_ref` with
        `quantity_received 10` (row A's own carry, not row A's 10 + row
        B's 5 combined), row B still present and closed, no error/conflict
        in the verdict.
        """
        wh_id = env.refs.resolve(entity_type="warehouses", source_ref=env.warehouse_ref)
        wh_code = _warehouse_code(env, env.warehouse_ref)
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"

        row_a = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=1, product_id=product_id, location_code=None,
            warehouse_id=wh_id, allocated_quantity=10, quantity_received=10,
            line_status="closed", receipt_status="fully_received", source_system=None,
        )
        row_b = SPOAllocation(
            id=str(uuid.uuid4()), company_id=env.company_a, spo_number=number,
            spo_line_number=2, product_id=product_id, location_code=wh_code.lower(),
            warehouse_id=None, allocated_quantity=5, quantity_received=5,
            line_status="closed", receipt_status="fully_received", source_system="scm_upload",
        )
        env.db.add_all([row_a, row_b])
        env.db.flush()
        env.db.commit()

        line = _spo_line(env, warehouse_ref=env.warehouse_ref, qty_ordered=15, qty_received=0)
        record = _spo_record(env, number=number, lines=[line], supplier_ref=env.supplier_ref)

        res = env.post(INGEST_SPO, [record])

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert not entry.get("errors"), entry
        assert entry["outcome"] == "created", entry
        lines_summary = entry.get("lines") or {}
        assert lines_summary.get("superseded") == 1, lines_summary

        rows = _spo_rows(env, number)
        by_ref = {r["source_ref"]: r for r in rows if r["source_ref"]}
        assert len(by_ref) == 1, (
            "exactly one row must carry the pushed line's source_ref", rows
        )
        new_row = by_ref[line["source_ref"]]
        assert new_row["quantity_received"] == 10, (
            "row A's own 10 must carry - never row A + row B combined (15) "
            f"and never row B's alone - got {new_row}"
        )

        row_b_after = {str(r["id"]): r for r in rows}.get(str(row_b.id))
        assert row_b_after is not None, (
            "row B must still be present - its own group found no incoming "
            "counterpart once row A's group claimed the only line", rows
        )
        assert row_b_after["line_status"] == "closed", row_b_after
