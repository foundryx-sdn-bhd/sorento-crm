"""Group V3 - shipping orders (PLAN section 2.4, D3/D5/D11; UAC AC-V3-2..8, AC-V7-6 tail).

A shipping order has no header table: it is a GROUP of `spo_allocations` rows
keyed `(company_id, spo_number, spo_line_number)`. The ingest surface for it is
new work end to end - schema, service, routes, permission wiring - none of
which exists yet as this file is written, so every test below is RED against
the current tree.

  AC-V3-2  N lines -> N `spo_allocations` rows, `spo_line_number` 1..N in
           payload order, `source_system='autocount'`, `source_ref`/
           `source_doc_ref` stamped from the DtlKey/DocKey, quantities/receipt
           status/line status derived the way `_write_spo_lines` derives them,
           CNY default currency.
  AC-V3-3  re-push updates the same row by DtlKey; an absent line is closed in
           place, never deleted; a new DtlKey gets the next line number.
  AC-V3-4  xlsx-era rows (`source_system='scm_upload'`, no ref) for the same
           `spo_number` are adopted by `(product_id, upper(location_code))` in
           `spo_line_number` order; the unmatched remainder is closed in place.
  AC-V3-5  a `purchase_orders` record whose `po_number` is an `SPO-` family
           number is refused, nothing written.
  AC-V3-6  read-back in canonical shape, refs where a master carries one.
  AC-V3-7  deletions: unreferenced rows hard-deleted; referenced rows closed in
           place with verdict `deactivated`; dry run writes nothing.
  AC-V3-8  `cancelled` closes every line; an unmapped status word is failed;
           line status is always derived from quantities, not the header word.
  AC-V3-1  the four `scm.shipping_orders.*` slugs gate ingest/read/delete.
  AC-V7-6  (tail) `line_number` is an optional int on a shipping-order line too.

Substrate is reused wholesale from `tests/test_ingest_documents.py` per the
tester brief - the `env` fixture, `_Env`, `_ref`, `MARKER`, and the linked
masters (`product_ref`, `product2_ref`, `warehouse_ref`, `supplier_ref`) it
seeds - exactly as `tests/test_ingest_documents_v2_resolution.py` does. No new
substrate is added here beyond shipping-order-specific record builders and a
couple of local read helpers.

Payloads use v1-style `*_ref` fields only (`supplier_ref`, `product_ref`,
`warehouse_ref`) - not `supplier_code`/`product_code` - so this file does not
depend on the S1 code/name ladder landing first.
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
from app.models.procurement import SPOAllocation
from app.models.scm import OrderLinkClaim
from app.services.scm.po_listing_reader import doc_family

from tests.test_ingest_documents import (
    INGEST_PO,
    MARKER,
    _po_record,
    _ref,
    _with_product_code,
    env,  # noqa: F401 - pytest fixture, imported for reuse per the tester brief
)

__all__ = ["env"]

INGEST_SPO = "/api/v1/external/ingest/shipping_orders"
READ_SPO = "/api/v1/external/read/shipping_orders"
DELETE_SPO = "/api/v1/external/ingest/shipping_orders/deletions"


# ------------------------------------------------------------------ builders
def _spo_record(env, *, ref=None, number=None, lines=None, **extra) -> dict:
    record = {
        "source_ref": ref or _ref("SPO"),
        "spo_number": number or f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}",
        "status": "open",
        "lines": lines if lines is not None else [_spo_line(env)],
    }
    record.update(extra)
    return record


def _spo_line(env, *, ref=None, product_ref=None, **extra) -> dict:
    line = {
        "source_ref": ref or _ref("SPOL"),
        "product_ref": product_ref or env.product_ref,
        "qty_ordered": 10,
    }
    line.update(extra)
    return _with_product_code(env, line)


def _spo_rows(env, spo_number: str):
    """Every `spo_allocations` row for this number, in the anchor company.

    Raw SQL is safe here (unlike the document tables): `spo_allocations` is not
    duplicated in the `projects` schema, so there is no bare-name ambiguity for
    `search_path` to resolve wrongly.
    """
    return (
        env.db.execute(
            text(
                "SELECT * FROM spo_allocations WHERE company_id = :c AND spo_number = :n "
                "ORDER BY spo_line_number"
            ),
            {"c": env.company_a, "n": spo_number},
        )
        .mappings()
        .all()
    )


def _seed_legacy_row(
    env,
    *,
    spo_number: str,
    spo_line_number: int,
    product_ref: str | None = None,
    location_code: str | None = None,
    allocated_quantity: int = 10,
    quantity_received: int = 0,
    line_status: str = "open",
    inbound_shipment_id: str | None = None,
    source_system: str | None = "scm_upload",
) -> SPOAllocation:
    """An xlsx-era row: `source_system='scm_upload'` BY DEFAULT, no ref columns.

    `source_ref` / `source_doc_ref` are left NULL on purpose - a ref-less row
    is exactly what the xlsx upload always wrote, and that shape is what the
    S4/D25 adoption-vs-supersede rules key off. `inbound_shipment_id`
    (spo-xlsx-supersede AC-X6) is the one column callers may want stamped on
    the seed itself, since it is what `spo-xlsx-supersede`'s D26 carries
    forward onto every line of a superseded group. `source_system` defaults
    to `'scm_upload'` (every existing caller's assumption) but AC-X13
    (D25a) needs a ref-less row the CRM UI / n8n packing-list route wrote
    instead - `source_system=None` - which is NEVER a supersede candidate.
    """
    product_id = env.refs.resolve(
        entity_type="products", source_ref=product_ref or env.product_ref
    )
    row = SPOAllocation(
        company_id=env.company_a,
        spo_number=spo_number,
        spo_line_number=spo_line_number,
        product_id=product_id,
        location_code=location_code,
        allocated_quantity=allocated_quantity,
        quantity_received=quantity_received,
        receipt_status="pending" if allocated_quantity > quantity_received else "fully_received",
        line_status=line_status,
        source_system=source_system,
        inbound_shipment_id=inbound_shipment_id,
    )
    env.db.add(row)
    env.db.flush()
    return row


# ============================================================== create (AC-V3-2)
class TestShippingOrderCreate:
    def test_n_lines_creates_n_spo_allocations_rows_under_the_anchor(self, env):
        line_a = _spo_line(env, warehouse_ref=env.warehouse_ref, unit_cost="9.99")
        line_b = _spo_line(
            env, product_ref=env.product2_ref, qty_ordered=6, qty_received=6
        )
        record = _spo_record(
            env,
            lines=[line_a, line_b],
            supplier_ref=env.supplier_ref,
            issue_date="2026-08-30",
            expected_date="2026-10-01",
        )

        res = env.post(INGEST_SPO, [record])

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["summary"]["created"] == 1, body
        entry = body["records"][0]
        assert entry["outcome"] == "created"
        # No header row exists for a shipping order (D3) - the verdict cannot
        # name one.
        assert entry["entity_id"] is None

        rows = _spo_rows(env, record["spo_number"])
        assert len(rows) == 2
        assert [r["spo_line_number"] for r in rows] == [1, 2]
        by_ref = {r["source_ref"]: r for r in rows}
        assert set(by_ref) == {line_a["source_ref"], line_b["source_ref"]}
        for row in rows:
            assert row["source_system"] == "autocount"
            assert row["source_doc_ref"] == record["source_ref"]
            assert row["currency"] == "CNY"
            assert row["issue_date"] is not None
            assert row["supplier_id"] is not None

        first = by_ref[line_a["source_ref"]]
        assert first["allocated_quantity"] == 10
        assert first["quantity_received"] == 0
        assert first["receipt_status"] == "pending"
        assert first["line_status"] == "open"
        assert first["warehouse_id"] is not None

        second = by_ref[line_b["source_ref"]]
        assert second["allocated_quantity"] == 6
        assert second["quantity_received"] == 6
        # Fully received on arrival, so this one is closed the same push.
        assert second["receipt_status"] == "fully_received"
        assert second["line_status"] == "closed"
        assert second["warehouse_id"] is None


# ========================================================== re-push (AC-V3-3)
class TestShippingOrderRePush:
    def test_a_changed_quantity_updates_the_same_row_and_line_number(self, env):
        line = _spo_line(env, qty_ordered=10)
        record = _spo_record(env, lines=[line], supplier_ref=env.supplier_ref)
        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text
        original = {
            r["source_ref"]: (str(r["id"]), r["spo_line_number"])
            for r in _spo_rows(env, record["spo_number"])
        }

        res2 = env.post(
            INGEST_SPO, [dict(record, lines=[dict(line, qty_ordered=15)])]
        )

        assert res2.json()["records"][0]["outcome"] == "updated", res2.text
        rows = {r["source_ref"]: r for r in _spo_rows(env, record["spo_number"])}
        assert str(rows[line["source_ref"]]["id"]) == original[line["source_ref"]][0]
        assert (
            rows[line["source_ref"]]["spo_line_number"]
            == original[line["source_ref"]][1]
        )
        assert rows[line["source_ref"]]["allocated_quantity"] == 15

    def test_a_line_absent_from_the_payload_is_closed_in_place_not_deleted(self, env):
        """GRN lines and order-link claims point at a `spo_allocations` id, the
        same reason a document line is cancelled rather than deleted (A3)."""
        keep = _spo_line(env, qty_ordered=10)
        drop = _spo_line(env, product_ref=env.product2_ref, qty_ordered=5)
        record = _spo_record(env, lines=[keep, drop], supplier_ref=env.supplier_ref)
        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text
        dropped_id = {
            r["source_ref"]: str(r["id"]) for r in _spo_rows(env, record["spo_number"])
        }[drop["source_ref"]]

        res2 = env.post(INGEST_SPO, [dict(record, lines=[keep])])

        assert res2.json()["records"][0]["outcome"] == "updated", res2.text
        rows = {r["source_ref"]: r for r in _spo_rows(env, record["spo_number"])}
        assert set(rows) == {keep["source_ref"], drop["source_ref"]}
        assert str(rows[drop["source_ref"]]["id"]) == dropped_id
        assert rows[drop["source_ref"]]["line_status"] == "closed"

    def test_a_new_dtlkey_gets_the_next_line_number(self, env):
        record = _spo_record(
            env,
            lines=[_spo_line(env), _spo_line(env, product_ref=env.product2_ref)],
            supplier_ref=env.supplier_ref,
        )
        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text

        added = _spo_line(env, qty_ordered=3)
        res2 = env.post(
            INGEST_SPO, [dict(record, lines=record["lines"] + [added])]
        )

        assert res2.json()["records"][0]["outcome"] == "updated", res2.text
        rows = {r["source_ref"]: r for r in _spo_rows(env, record["spo_number"])}
        assert rows[added["source_ref"]]["spo_line_number"] == 3


# ============================================================ adoption (AC-V3-4)
class TestShippingOrderAdoption:
    def test_xlsx_era_rows_are_matched_by_product_and_location_and_adopted(self, env):
        """AC-V3-4, revised 2026-09-07 under D25 (PLAN-spo-xlsx-supersede);
        the id-survival assertion moved to AC-X4's ref-row case.

        This SPO holds only ref-less rows, so D25's first-push supersede
        rule applies rather than the old "adopt in place" ladder: the
        MATCHED (product, location) group is superseded by the incoming
        line - the row's id does NOT survive (deleted, a new row is written
        for the pushed line, carrying the group's received quantity). The
        STRAY row (product2, an unmatched location) has no incoming
        counterpart, so it is kept and closed exactly as before.
        """
        wh_id = env.refs.resolve(entity_type="warehouses", source_ref=env.warehouse_ref)
        wh_code = env.db.execute(
            text("SELECT warehouse_code FROM warehouses WHERE id = :id"), {"id": wh_id}
        ).scalar()
        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        matched = _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=1,
            location_code=wh_code,
            allocated_quantity=10,
        )
        stray = _seed_legacy_row(
            env,
            spo_number=number,
            spo_line_number=2,
            product_ref=env.product2_ref,
            location_code="ZZT-UNMATCHED-LOC",
            allocated_quantity=4,
        )

        line = _spo_line(env, warehouse_ref=env.warehouse_ref, qty_ordered=10)
        record = _spo_record(
            env, number=number, lines=[line], supplier_ref=env.supplier_ref
        )

        res = env.post(INGEST_SPO, [record])

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry.get("lines", {}).get("superseded") == 1, entry

        rows = {str(r["id"]): r for r in _spo_rows(env, number)}
        assert str(matched.id) not in rows, (
            "the matched group's xlsx row must be superseded (deleted), not adopted in place"
        )
        by_ref = {r["source_ref"]: r for r in rows.values() if r["source_ref"]}
        new_row = by_ref[line["source_ref"]]
        assert new_row["source_system"] == "autocount"
        assert new_row["allocated_quantity"] == 10
        # Carried receipt from the superseded group (matched's own quantity_received, 0).
        assert new_row["quantity_received"] == 0

        stray_row = rows[str(stray.id)]
        assert stray_row["source_ref"] is None
        assert stray_row["line_status"] == "closed"


# ========================================================== SPO guard (AC-V3-5)
class TestSpoNumberGuard:
    def test_a_purchase_order_record_named_like_a_shipping_order_is_failed(self, env):
        number = f"SPO-{uuid.uuid4().hex[:6].upper()}/01-0001"
        assert doc_family(number) == "spo"
        before = env.db.execute(
            text("SELECT count(*) FROM purchase_orders WHERE po_number = :n"),
            {"n": number},
        ).scalar()

        res = env.post(INGEST_PO, [_po_record(env, number=number)])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", res.text
        assert "shipping order" in entry["errors"].get("po_number", "")
        after = env.db.execute(
            text("SELECT count(*) FROM purchase_orders WHERE po_number = :n"),
            {"n": number},
        ).scalar()
        assert after == before


# ============================================================= read-back (AC-V3-6)
class TestShippingOrderReadBack:
    def test_reads_back_in_canonical_shape_with_lines(self, env):
        line = _spo_line(
            env,
            warehouse_ref=env.warehouse_ref,
            qty_received=3,
            unit_cost="9.99",
            uom="CTN",
            expected_date="2026-10-01",
        )
        record = _spo_record(
            env,
            lines=[line],
            supplier_ref=env.supplier_ref,
            issue_date="2026-08-30",
            expected_date="2026-10-01",
            currency="MYR",
            status="partial",
        )
        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text

        got = env.read(READ_SPO, [record["source_ref"]]).json()["records"][0]

        assert got["spo_number"] == record["spo_number"]
        assert got["supplier_ref"] == env.supplier_ref
        assert got["issue_date"] == "2026-08-30"
        assert got["expected_date"] == "2026-10-01"
        assert got["currency"] == "MYR"
        assert got["status"] == "partial"
        assert len(got["lines"]) == 1
        got_line = got["lines"][0]
        assert got_line["entity_id"]
        assert got_line["source_ref"] == line["source_ref"]
        assert got_line["product_ref"] == env.product_ref
        assert got_line["warehouse_ref"] == env.warehouse_ref
        assert got_line["qty_ordered"] == 10
        assert got_line["qty_received"] == 3
        assert got_line["unit_cost"] == 9.99

    def test_an_unknown_ref_is_reported_not_found(self, env):
        res = env.read(READ_SPO, [_ref("SPO")])

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["records"] == []
        assert len(body["not_found"]) == 1


# ============================================================== deletions (AC-V3-7)
class TestShippingOrderDeletions:
    def test_an_unreferenced_row_is_hard_deleted(self, env):
        record = _spo_record(env, supplier_ref=env.supplier_ref)
        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text

        res2 = env.client.post(
            DELETE_SPO,
            json={
                "companyCode": env.company_a_code,
                "source_refs": [record["source_ref"]],
            },
        )

        entry = res2.json()["records"][0]
        assert entry["outcome"] == "deleted", res2.text
        assert _spo_rows(env, record["spo_number"]) == []

    def test_a_referenced_row_is_closed_in_place_not_deleted(self, env):
        """A claim pointing at the row (`scm.order_link_claim.spo_allocation_id`)
        is the dependent (`dependent_probe`'s own list): removing the row would
        SET NULL the claim and silently detach a pairing already made."""
        record = _spo_record(env, supplier_ref=env.supplier_ref)
        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text
        row_id = _spo_rows(env, record["spo_number"])[0]["id"]
        claim = OrderLinkClaim(
            company_id=env.company_a,
            so_number=f"{MARKER}-SO-{uuid.uuid4().hex[:8]}",
            po_number=record["spo_number"],
            source="autocount",
            spo_allocation_id=row_id,
        )
        env.db.add(claim)
        env.db.flush()

        res2 = env.client.post(
            DELETE_SPO,
            json={
                "companyCode": env.company_a_code,
                "source_refs": [record["source_ref"]],
            },
        )

        entry = res2.json()["records"][0]
        assert entry["outcome"] == "deactivated", res2.text
        rows = _spo_rows(env, record["spo_number"])
        assert len(rows) == 1
        assert rows[0]["line_status"] == "closed"

    def test_a_dry_run_writes_nothing(self, env):
        record = _spo_record(env, supplier_ref=env.supplier_ref)
        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text
        before = _spo_rows(env, record["spo_number"])

        res2 = env.client.post(
            f"{DELETE_SPO}?dry_run=true",
            json={
                "companyCode": env.company_a_code,
                "source_refs": [record["source_ref"]],
            },
        )

        assert res2.json()["dry_run"] is True
        after = _spo_rows(env, record["spo_number"])
        assert after == before


# =============================================================== status (AC-V3-8)
class TestStatusVocabulary:
    def test_cancelled_closes_every_line(self, env):
        record = _spo_record(
            env,
            lines=[_spo_line(env), _spo_line(env, product_ref=env.product2_ref)],
            supplier_ref=env.supplier_ref,
        )
        res = env.post(INGEST_SPO, [record])
        assert res.status_code == 200, res.text

        res2 = env.post(INGEST_SPO, [dict(record, status="cancelled")])

        assert res2.json()["records"][0]["outcome"] == "updated", res2.text
        rows = _spo_rows(env, record["spo_number"])
        assert {r["line_status"] for r in rows} == {"closed"}

    def test_an_unknown_status_word_is_failed_and_names_status(self, env):
        record = _spo_record(env, status="shipped", supplier_ref=env.supplier_ref)

        res = env.post(INGEST_SPO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", res.text
        assert "status" in entry["errors"]
        assert _spo_rows(env, record["spo_number"]) == []

    def test_line_status_derives_from_quantities_regardless_of_header_word(self, env):
        """`status` on the header is validated as vocabulary but each line's own
        status comes from `allocated_quantity - quantity_received`, not from
        the word - an `open` header can still carry a fully-received line."""
        line = _spo_line(env, qty_ordered=5, qty_received=5)
        record = _spo_record(
            env, lines=[line], status="open", supplier_ref=env.supplier_ref
        )

        res = env.post(INGEST_SPO, [record])

        assert res.status_code == 200, res.text
        rows = _spo_rows(env, record["spo_number"])
        assert rows[0]["line_status"] == "closed"
        assert rows[0]["receipt_status"] == "fully_received"


# ======================================================== line_number (AC-V7-6 tail)
class TestLineNumberWireField:
    def test_line_number_is_accepted_and_optional_on_shipping_order_lines(self, env):
        line = _spo_line(env, line_number=7)
        record = _spo_record(env, lines=[line], supplier_ref=env.supplier_ref)

        res = env.post(INGEST_SPO, [record])

        assert res.status_code == 200, res.text


# ================================================================ guard (AC-V3-1)
class TestPermissionGuard:
    """The real RBAC guard, on an empty schema of its own.

    S0 already registered `scm.shipping_orders.{edit,view,delete}` in the
    permission catalogue (`app/rbac/permission_registry.py`). What has NOT
    landed yet is `ingest.py`'s wiring: `INGEST_PERMISSIONS`,
    `READ_PERMISSIONS` and `DELETE_PERMISSIONS` carry no `shipping_orders` key,
    so `require_external_permission_for_path` 404s the entity itself before it
    ever reaches a slug check - REGARDLESS of which permissions the caller
    holds. Every assertion below that expects a 403 naming the slug (or a 200
    for a caller that holds it) is RED for that reason: the missing map entry,
    not a fixture defect. See the test-run report for which of these actually
    fail today and why.
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

        @api.post("/ingest/{entity}")
        def _ingest_stub(
            entity: str,
            _: dict = Depends(
                require_external_permission_for_path(ingest_module.INGEST_PERMISSIONS)
            ),
        ):
            return {"ok": entity}

        @api.post("/read/{entity}")
        def _read_stub(
            entity: str,
            _: dict = Depends(
                require_external_permission_for_path(ingest_module.READ_PERMISSIONS)
            ),
        ):
            return {"ok": entity}

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
            "po_edit": ingest_module.INGEST_PERMISSIONS["purchase_orders"],
            "po_view": ingest_module.READ_PERMISSIONS["purchase_orders"],
            "po_delete": ingest_module.DELETE_PERMISSIONS["purchase_orders"],
            # The SPO slugs S0 minted in the catalogue, not yet wired into any
            # *_PERMISSIONS map here - a key holding these has nothing to check
            # against until this slice lands.
            "spo_edit": "scm.shipping_orders.edit",
            "spo_view": "scm.shipping_orders.view",
            "spo_delete": "scm.shipping_orders.delete",
        }
        perms = {}
        for slug in slugs.values():
            perm = UserPermission(slug=slug, name=slug)
            guard_db.add(perm)
            guard_db.flush()
            perms[slug] = perm

        issued = {}
        for label, held in (
            ("po_only", [slugs["po_edit"], slugs["po_view"], slugs["po_delete"]]),
            ("spo_full", [slugs["spo_edit"], slugs["spo_view"], slugs["spo_delete"]]),
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
                guard_db.add(
                    UserRolePermission(role_id=role.id, permission_id=perms[slug].id)
                )
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

    def test_a_po_only_key_is_403_naming_the_shipping_order_edit_slug(
        self, guard_client, keys
    ):
        res = guard_client.post(
            "/ingest/shipping_orders", headers={"X-API-Key": keys["po_only"]}
        )
        assert res.status_code == 403
        assert "scm.shipping_orders.edit" in res.text

    def test_reading_takes_the_view_slug(self, guard_client, keys):
        res = guard_client.post(
            "/read/shipping_orders", headers={"X-API-Key": keys["spo_full"]}
        )
        assert res.status_code == 200, res.text

    def test_deleting_takes_both_the_edit_and_delete_slug(self, guard_client, keys):
        res = guard_client.post(
            "/ingest/shipping_orders/deletions", headers={"X-API-Key": keys["spo_full"]}
        )
        assert res.status_code == 200, res.text
