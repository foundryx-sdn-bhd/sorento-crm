"""RED tests for the `stock_balances` push ingest entity (contract 2.5, SR5a).

PLAN: documentation/plans/autocount/PLAN-ingest-stock-balances-2-5.md D1-D12.
UAC:  documentation/plans/autocount/ingest-stock-balances-2-5-acceptance-criteria.md
      AC-SB-1 .. AC-SB-20. AC-SB-21 (SR5b) is pending a separate ruling and has
      no test here.

None of this exists yet (D1): `stock_balances` is not in `SUPPORTED_ENTITIES`,
`INGEST_PERMISSIONS`, `READ_PERMISSIONS` or `DELETE_PERMISSIONS`. The router
mount in `app/api/v1/external/__init__.py` wraps BOTH the ingest and read
routers in `Depends(require_external_permission_for_path(...))`, which 404s
("unknown_entity") an unmapped entity BEFORE the handler's own body parsing
or DB work ever runs - even for a superadmin principal, since the permission
map lookup happens before any permission CHECK. So every test below that
calls the real `/external/ingest|read/stock_balances*` routes is expected to
fail on that 404 today, not on an ImportError or a fixture bug. AC-SB-1 (the
contract endpoint) fails differently: it returns 200 today, just with the
WRONG version/entities/warnings - a value mismatch, not an exception.

Substrate: `tests._pg_fixture.blank_session()`, a real Postgres scratch
schema built from the current ORM models and rolled back per test - same
substrate as `tests/test_ingest_brands.py`. `stock_balances` resolution
(D4) never goes through `integration_references` (D3: `source_ref` is an
echo key only, never stored), so unlike the brands/masters fixtures this one
seeds no `IntegrationReferenceService` at all - identity is always the
(product, warehouse) pair, resolved fresh on every push.

Every code minted here carries a `ZZTSB` marker.
"""
from __future__ import annotations

import importlib.util
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

# MUST be the first app import - resolves the circular import in
# app.modules.runtime.guards.
from app.main import app  # noqa: E402

from app.api.v1.external.ingest import MAX_BATCH
from app.database import engine
from app.models.company import Company
from app.models.inventory import Stock, Warehouse
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.company_scope import DEFAULT_COMPANY_ID

from ._pg_fixture import blank_session, unique_code

MARKER = "ZZTSB"

INGEST_SB = "/api/v1/external/ingest/stock_balances"
READ_SB = "/api/v1/external/read/stock_balances"
DELETE_SB = "/api/v1/external/ingest/stock_balances/deletions"
CONTRACT_URL = "/api/v1/external/contract"

_USER_ID = "7d0dad30-5555-4222-8333-4444555577f5"
_ROLE_ID = "7d0dad30-6666-4222-8333-4444555577f6"


def _ref(stem: str) -> str:
    return f"{MARKER}:{stem}:{uuid.uuid4().hex[:8]}"


def _seed_principal(db) -> None:
    from app.models.user import User, UserRole, UserRoleAssignment

    db.add(
        UserRole(
            id=_ROLE_ID,
            slug="superadmin",
            name=f"{MARKER} Superadmin",
            description="",
            is_protected=True,
            is_default=False,
        )
    )
    db.flush()
    db.add(
        User(
            id=_USER_ID,
            name=f"{MARKER} admin",
            email=f"{MARKER.lower()}-admin@test.com",
            password="x",
            status="active",
        )
    )
    db.flush()
    db.add(UserRoleAssignment(user_id=_USER_ID, role_id=_ROLE_ID))
    db.flush()


class _Env:
    def __init__(self, client: TestClient, db):
        self.client = client
        self.db = db
        self.company_a = DEFAULT_COMPANY_ID

        suffix = uuid.uuid4().hex[:8]
        other = Company(id=str(uuid.uuid4()), name=f"{MARKER} B {suffix}", code=f"ZSB{suffix}")
        db.add(other)
        db.flush()
        self.company_b = str(other.id)
        self.company_a_code = db.execute(
            text("SELECT code FROM companies WHERE id = :id"), {"id": self.company_a}
        ).scalar()
        self.company_b_code = other.code

        self._category = ProductCategory(
            category_code=unique_code(MARKER), category_name=f"{MARKER} category"
        )
        self._uom = UnitOfMeasure(uom_code=unique_code(MARKER), uom_name=f"{MARKER} unit")
        db.add_all([self._category, self._uom])
        db.flush()

        # D4 step 2: location resolution is trimmed + case-insensitive,
        # company-scoped. One active, one inactive, one belonging to the
        # OTHER company - none share a code, so a resolution result can only
        # be explained by one of the three.
        self.wh_active = self._warehouse("MBS", is_active=True, company_id=self.company_a)
        self.wh_inactive = self._warehouse("CON", is_active=False, company_id=self.company_a)
        self.wh_other_company = self._warehouse(
            "OTH", is_active=True, company_id=self.company_b
        )

        # D4 step 3: item_code resolution, trimmed + case-insensitive,
        # company-scoped. One plain code, one with spaces AND quotes in it
        # (AC-SB-8), one belonging to the other company.
        self.product = self._product(unique_code(MARKER), company_id=self.company_a)
        self.special_code = f'1/2" ULTRA CIRCULAR {uuid.uuid4().hex[:6]}'
        self.special_product = self._product(self.special_code, company_id=self.company_a)
        self.product_other_company = self._product(
            unique_code(MARKER), company_id=self.company_b
        )
        db.commit()

    # --------------------------------------------------------------- seeds
    def _warehouse(self, code: str, *, is_active: bool, company_id: str) -> Warehouse:
        row = Warehouse(
            warehouse_code=f"{code}-{uuid.uuid4().hex[:6]}",
            warehouse_name=f"{MARKER} {code}",
            is_active=is_active,
            company_id=company_id,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def _product(self, code: str, *, company_id: str) -> Product:
        row = Product(
            product_code=code,
            product_name=f"{MARKER} product {code}",
            category_id=self._category.id,
            base_uom_id=self._uom.id,
            list_price=10,
            company_id=company_id,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def make_stock(self, *, product_id: str, warehouse_id: str, company_id: str = None, **kw) -> Stock:
        row = Stock(
            product_id=product_id,
            warehouse_id=warehouse_id,
            company_id=company_id or self.company_a,
            **kw,
        )
        self.db.add(row)
        # Fix round 1 (reviewer S1 / security N1): a real `commit()`, not a
        # bare `flush()` - the service's dry-run rollback is a plain
        # session-level `self.db.rollback()` again (the uniform pattern
        # every other ingest entity already uses), which discards anything
        # this session holds uncommitted, seed data included.
        self.db.commit()
        return row

    # --------------------------------------------------------------- calls
    def post(self, url: str, records: list, *, company_code=None, dry_run=False):
        return self.client.post(
            f"{url}?dry_run=true" if dry_run else url,
            json={"companyCode": company_code or self.company_a_code, "records": records},
        )

    def delete(self, source_refs: list, pairs: dict, *, company_code=None, dry_run=False, raw_pairs=False):
        body = {
            "companyCode": company_code or self.company_a_code,
            "source_refs": source_refs,
            "pairs": pairs,
        }
        return self.client.post(
            f"{DELETE_SB}?dry_run=true" if dry_run else DELETE_SB,
            json=body,
        )

    def read(self, source_refs: list, pairs: dict, *, company_code=None):
        return self.client.post(
            READ_SB,
            json={
                "companyCode": company_code or self.company_a_code,
                "source_refs": source_refs,
                "pairs": pairs,
            },
        )

    # --------------------------------------------------------------- reads
    def stock_row(self, product_id, warehouse_id):
        return (
            self.db.execute(
                text(
                    "SELECT * FROM stock WHERE product_id = :p AND warehouse_id = :w"
                ),
                {"p": str(product_id), "w": str(warehouse_id)},
            )
            .mappings()
            .first()
        )

    def stock_by_id(self, stock_id):
        return (
            self.db.execute(text("SELECT * FROM stock WHERE id = :id"), {"id": str(stock_id)})
            .mappings()
            .first()
        )


@pytest.fixture
def env():
    from app.dependencies import (  # safe: app.main is already loaded
        get_current_user,
        get_current_user_or_api_key,
        get_db,
        get_external_api_user,
    )
    from app.models.base import set_company_scope
    from app.services.company_scope_resolver import apply_company_scope

    with blank_session() as db:
        _seed_principal(db)

        def _override_get_db():
            yield db

        def _override_user():
            return {"id": _USER_ID, "email": f"{MARKER.lower()}-admin@test.com"}

        def _override_company_scope():
            set_company_scope(db, None)
            return None

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = _override_user
        app.dependency_overrides[get_current_user_or_api_key] = _override_user
        app.dependency_overrides[get_external_api_user] = _override_user
        app.dependency_overrides[apply_company_scope] = _override_company_scope
        try:
            with TestClient(app) as client:
                yield _Env(client, db)
        finally:
            app.dependency_overrides.clear()


def _sb_record(*, item_code=None, location_code=None, qty=0, ref=None, **extra) -> dict:
    record = {"source_ref": ref or _ref("SB")}
    if item_code is not None:
        record["item_code"] = item_code
    if location_code is not None:
        record["location_code"] = location_code
    record["qty"] = qty
    record.update(extra)
    return record


# ==================================================================== AC-SB-1
class TestContractAC1:
    def test_contract_lists_2_5_stock_balances_and_warehouse_inactive(self, env):
        res = env.client.get(CONTRACT_URL)
        assert res.status_code == 200, res.text
        body = res.json()
        # Bumped again (finance S0, #1309): "2.6" adds `billing_documents`.
        # Bumped again (#1354 S2): "2.7" adds `delivery_orders`, `goods_receive_notes`, `branches`.
        assert body["version"] == "2.7", body["version"]
        assert "stock_balances" in body["entities"]
        assert "warehouse_inactive" in body["warnings"]
        assert "stock_balances" in body["fields_added"]


# ==================================================================== AC-SB-2
class TestCreateAC2:
    def test_a_new_pair_is_created_with_reserved_and_damaged_zero(self, env):
        record = _sb_record(
            item_code=env.product.product_code,
            location_code=env.wh_active.warehouse_code,
            qty=25,
        )

        res = env.post(INGEST_SB, [record])

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", entry
        row = env.stock_row(env.product.id, env.wh_active.id)
        assert row is not None
        assert str(row["id"]) == entry["entity_id"]
        assert row["quantity_on_hand"] == 25
        assert row["quantity_reserved"] == 0
        assert row["quantity_damaged"] == 0


# ==================================================================== AC-SB-3
class TestUpdatePreservesOtherColumnsAC3:
    def test_existing_pair_updates_only_quantity_on_hand(self, env):
        zone = str(uuid.uuid4())
        existing = env.make_stock(
            product_id=env.product.id,
            warehouse_id=env.wh_active.id,
            quantity_on_hand=100,
            quantity_reserved=5,
            quantity_damaged=2,
            reorder_point=10,
            zone_id=zone,
        )

        record = _sb_record(
            item_code=env.product.product_code,
            location_code=env.wh_active.warehouse_code,
            qty=150,
        )
        res = env.post(INGEST_SB, [record])

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        assert entry["entity_id"] == str(existing.id)
        row = env.stock_by_id(existing.id)
        assert row["quantity_on_hand"] == 150
        assert row["quantity_reserved"] == 5
        assert row["quantity_damaged"] == 2
        assert row["reorder_point"] == 10
        assert str(row["zone_id"]) == zone


# ==================================================================== AC-SB-4
class TestRepushSameValueAC4:
    def test_same_record_twice_second_is_updated_with_empty_diff_on_dry_run(self, env):
        record = _sb_record(
            item_code=env.product.product_code,
            location_code=env.wh_active.warehouse_code,
            qty=10,
        )
        first = env.post(INGEST_SB, [record])
        assert first.status_code == 200, first.text
        assert first.json()["records"][0]["outcome"] == "created"

        again = env.post(INGEST_SB, [record], dry_run=True)
        assert again.status_code == 200, again.text
        entry = again.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        assert entry.get("diff") == {}, entry


# ==================================================================== AC-SB-5
class TestLocationCaseAndTrimAC5:
    def test_location_matches_trimmed_and_case_insensitive(self, env):
        record = _sb_record(
            item_code=env.product.product_code,
            location_code=f"  {env.wh_active.warehouse_code.lower()}  ",
            qty=5,
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", entry
        row = env.stock_row(env.product.id, env.wh_active.id)
        assert row is not None
        assert row["quantity_on_hand"] == 5


# ==================================================================== AC-SB-6
class TestUnknownLocationAC6:
    def test_unknown_location_is_updated_with_warehouse_unresolved_nothing_written(self, env):
        record = _sb_record(
            item_code=env.product.product_code,
            location_code=f"{MARKER}-NOWHERE-{uuid.uuid4().hex[:6]}",
            qty=5,
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        assert "warehouse_unresolved" in entry.get("warnings", []), entry
        assert entry["entity_id"] is None, entry
        # nothing written anywhere for this product
        row = env.stock_row(env.product.id, env.wh_active.id)
        assert row is None


# ==================================================================== AC-SB-7
class TestInactiveLocationAC7:
    def test_inactive_warehouse_is_updated_with_warehouse_inactive_nothing_changed(self, env):
        existing = env.make_stock(
            product_id=env.product.id,
            warehouse_id=env.wh_inactive.id,
            quantity_on_hand=42,
        )
        record = _sb_record(
            item_code=env.product.product_code,
            location_code=env.wh_inactive.warehouse_code,
            qty=999,
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        assert "warehouse_inactive" in entry.get("warnings", []), entry
        row = env.stock_by_id(existing.id)
        assert row["quantity_on_hand"] == 42, "must not have been touched"


# ==================================================================== AC-SB-8
class TestUnknownAndSpecialItemCodeAC8:
    def test_unknown_item_code_is_retryable_nothing_written(self, env):
        record = _sb_record(
            item_code=f"{MARKER}-GONE-{uuid.uuid4().hex[:6]}",
            location_code=env.wh_active.warehouse_code,
            qty=5,
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "retryable", entry

    def test_item_code_with_spaces_and_quotes_resolves(self, env):
        record = _sb_record(
            item_code=env.special_code,
            location_code=env.wh_active.warehouse_code,
            qty=7,
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", entry
        row = env.stock_row(env.special_product.id, env.wh_active.id)
        assert row is not None
        assert row["quantity_on_hand"] == 7


# ==================================================================== AC-SB-9
class TestFieldValidationAC9:
    def test_missing_item_code_fails(self, env):
        record = {"source_ref": _ref("MISS1"), "location_code": env.wh_active.warehouse_code, "qty": 1}
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", entry
        assert "item_code" in entry.get("errors", {}), entry

    def test_blank_item_code_fails(self, env):
        record = _sb_record(item_code="   ", location_code=env.wh_active.warehouse_code, qty=1)
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", entry

    def test_missing_location_code_fails(self, env):
        record = {"source_ref": _ref("MISS2"), "item_code": env.product.product_code, "qty": 1}
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", entry
        assert "location_code" in entry.get("errors", {}), entry

    def test_qty_string_fails(self, env):
        record = _sb_record(
            item_code=env.product.product_code, location_code=env.wh_active.warehouse_code, qty="10"
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", entry
        assert "qty" in entry.get("errors", {}), entry

    def test_qty_float_fails(self, env):
        record = _sb_record(
            item_code=env.product.product_code, location_code=env.wh_active.warehouse_code, qty=10.5
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", entry

    def test_qty_bool_fails(self, env):
        record = _sb_record(
            item_code=env.product.product_code, location_code=env.wh_active.warehouse_code, qty=True
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", entry

    def test_qty_negative_fails(self, env):
        record = _sb_record(
            item_code=env.product.product_code, location_code=env.wh_active.warehouse_code, qty=-1
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", entry

    def test_qty_zero_is_accepted_and_written(self, env):
        record = _sb_record(
            item_code=env.product.product_code, location_code=env.wh_active.warehouse_code, qty=0
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", entry
        row = env.stock_row(env.product.id, env.wh_active.id)
        assert row is not None
        assert row["quantity_on_hand"] == 0


# =================================================================== AC-SB-10
class TestExtraKeyAC10:
    def test_unknown_extra_key_is_dropped_not_refused(self, env):
        """Owner decision, 1 Oct 2026: an unknown key is dropped (its name logged), never
        refused - it used to fail the record (`extra="forbid"`)."""
        record = _sb_record(
            item_code=env.product.product_code,
            location_code=env.wh_active.warehouse_code,
            qty=1,
            not_a_real_field="whatever",
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", entry

    def test_item_description_and_uom_code_are_accepted_and_ignored(self, env):
        record = _sb_record(
            item_code=env.product.product_code,
            location_code=env.wh_active.warehouse_code,
            qty=1,
            item_description="Some description",
            uom_code=env._uom.uom_code if hasattr(env, "_uom") else "PCS",
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", entry

    def test_item_description_and_uom_code_absent_is_fine(self, env):
        record = _sb_record(
            item_code=env.product.product_code,
            location_code=env.wh_active.warehouse_code,
            qty=1,
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "created"


# =================================================================== AC-SB-11
class TestCompanyScopeAC11:
    def test_other_companys_warehouse_never_resolves(self, env):
        record = _sb_record(
            item_code=env.product.product_code,
            location_code=env.wh_other_company.warehouse_code,
            qty=5,
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        assert "warehouse_unresolved" in entry.get("warnings", []), entry

    def test_other_companys_product_never_resolves(self, env):
        record = _sb_record(
            item_code=env.product_other_company.product_code,
            location_code=env.wh_active.warehouse_code,
            qty=5,
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "retryable", entry

    def test_created_row_carries_the_anchored_company(self, env):
        record = _sb_record(
            item_code=env.product.product_code,
            location_code=env.wh_active.warehouse_code,
            qty=5,
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        row = env.stock_row(env.product.id, env.wh_active.id)
        assert row is not None
        assert str(row["company_id"]) == env.company_a


# =================================================================== AC-SB-12
class TestDryRunAC12:
    def test_dry_run_writes_nothing_created_has_no_diff(self, env):
        record = _sb_record(
            item_code=env.product.product_code,
            location_code=env.wh_active.warehouse_code,
            qty=5,
        )
        res = env.post(INGEST_SB, [record], dry_run=True)
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", entry
        assert "diff" not in entry, entry
        assert env.stock_row(env.product.id, env.wh_active.id) is None

    def test_dry_run_of_a_change_carries_current_vs_incoming_diff(self, env):
        existing = env.make_stock(
            product_id=env.product.id, warehouse_id=env.wh_active.id, quantity_on_hand=10
        )
        record = _sb_record(
            item_code=env.product.product_code,
            location_code=env.wh_active.warehouse_code,
            qty=99,
        )
        res = env.post(INGEST_SB, [record], dry_run=True)
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        assert entry.get("diff") == {"qty": {"current": 10, "incoming": 99}}, entry
        row = env.stock_by_id(existing.id)
        assert row["quantity_on_hand"] == 10, "dry run must not write"

    def test_real_run_of_a_change_carries_no_diff_key(self, env):
        env.make_stock(product_id=env.product.id, warehouse_id=env.wh_active.id, quantity_on_hand=10)
        record = _sb_record(
            item_code=env.product.product_code,
            location_code=env.wh_active.warehouse_code,
            qty=99,
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        assert "diff" not in entry, entry


# =================================================================== AC-SB-13
class TestDuplicatePairInBatchAC13:
    def test_duplicate_pair_last_value_wins(self, env):
        ref1 = _ref("DUP1")
        ref2 = _ref("DUP2")
        rec1 = _sb_record(
            item_code=env.product.product_code,
            location_code=env.wh_active.warehouse_code,
            qty=10,
            ref=ref1,
        )
        rec2 = _sb_record(
            item_code=env.product.product_code,
            location_code=env.wh_active.warehouse_code,
            qty=20,
            ref=ref2,
        )
        res = env.post(INGEST_SB, [rec1, rec2])
        assert res.status_code == 200, res.text
        by_ref = {r["source_ref"]: r for r in res.json()["records"]}
        assert by_ref[ref1]["outcome"] == "created", by_ref[ref1]
        assert by_ref[ref2]["outcome"] == "updated", by_ref[ref2]
        row = env.stock_row(env.product.id, env.wh_active.id)
        assert row is not None
        assert row["quantity_on_hand"] == 20


# =================================================================== AC-SB-14
class TestSummaryShapeAC14:
    def test_summary_shape_and_every_record_echoes_its_source_ref(self, env):
        created = _sb_record(
            item_code=env.product.product_code, location_code=env.wh_active.warehouse_code, qty=1
        )
        existing = env.make_stock(
            product_id=env.special_product.id, warehouse_id=env.wh_active.id, quantity_on_hand=1
        )
        updated = _sb_record(
            item_code=env.special_code, location_code=env.wh_active.warehouse_code, qty=2
        )
        failed = {"source_ref": _ref("FAIL"), "location_code": env.wh_active.warehouse_code, "qty": 1}
        retryable = _sb_record(
            item_code=f"{MARKER}-MISSING-{uuid.uuid4().hex[:6]}",
            location_code=env.wh_active.warehouse_code,
            qty=1,
        )

        records = [created, updated, failed, retryable]
        res = env.post(INGEST_SB, records)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["summary"] == {
            "total": 4,
            "created": 1,
            "updated": 1,
            "failed": 1,
            "retryable": 1,
        }, body["summary"]
        sent_refs = {r["source_ref"] for r in records}
        got_refs = {r["source_ref"] for r in body["records"]}
        assert sent_refs == got_refs


# =================================================================== AC-SB-15
class TestDeletionsHappyPathAC15:
    def test_resolvable_pair_zeroes_qty_keeps_row(self, env):
        existing = env.make_stock(
            product_id=env.product.id,
            warehouse_id=env.wh_active.id,
            quantity_on_hand=50,
            quantity_reserved=5,
            quantity_damaged=2,
        )
        ref = _ref("DEL1")
        pairs = {
            ref: {
                "item_code": env.product.product_code,
                "location_code": env.wh_active.warehouse_code,
            }
        }
        res = env.delete([ref], pairs)
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "deleted", entry
        row = env.stock_by_id(existing.id)
        assert row is not None, "row must be kept, never hard-deleted"
        assert row["quantity_on_hand"] == 0
        assert row["quantity_reserved"] == 5
        assert row["quantity_damaged"] == 2

    def test_already_zero_is_deleted_again(self, env):
        existing = env.make_stock(
            product_id=env.product.id, warehouse_id=env.wh_active.id, quantity_on_hand=0
        )
        ref = _ref("DEL2")
        pairs = {
            ref: {
                "item_code": env.product.product_code,
                "location_code": env.wh_active.warehouse_code,
            }
        }
        res = env.delete([ref], pairs)
        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "deleted"
        row = env.stock_by_id(existing.id)
        assert row["quantity_on_hand"] == 0


# =================================================================== AC-SB-16
class TestDeletionsNotFoundAC16:
    def test_missing_pairs_entry_is_not_found(self, env):
        ref = _ref("NOPAIR")
        res = env.delete([ref], {})
        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "not_found"

    def test_unknown_product_is_not_found(self, env):
        ref = _ref("NOPROD")
        pairs = {
            ref: {
                "item_code": f"{MARKER}-GONE-{uuid.uuid4().hex[:6]}",
                "location_code": env.wh_active.warehouse_code,
            }
        }
        res = env.delete([ref], pairs)
        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "not_found"

    def test_unknown_warehouse_is_not_found(self, env):
        ref = _ref("NOWH")
        pairs = {
            ref: {
                "item_code": env.product.product_code,
                "location_code": f"{MARKER}-NOWHERE-{uuid.uuid4().hex[:6]}",
            }
        }
        res = env.delete([ref], pairs)
        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "not_found"

    def test_resolvable_pair_with_no_stock_row_is_not_found(self, env):
        ref = _ref("NOROW")
        pairs = {
            ref: {
                "item_code": env.product.product_code,
                "location_code": env.wh_active.warehouse_code,
            }
        }
        res = env.delete([ref], pairs)
        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "not_found"

    def test_inactive_warehouse_is_not_found_with_warning_row_untouched(self, env):
        existing = env.make_stock(
            product_id=env.product.id, warehouse_id=env.wh_inactive.id, quantity_on_hand=42
        )
        ref = _ref("INACTDEL")
        pairs = {
            ref: {
                "item_code": env.product.product_code,
                "location_code": env.wh_inactive.warehouse_code,
            }
        }
        res = env.delete([ref], pairs)
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "not_found", entry
        assert "warehouse_inactive" in entry.get("warnings", []), entry
        row = env.stock_by_id(existing.id)
        assert row["quantity_on_hand"] == 42


# =================================================================== AC-SB-17
class TestDeletionsValidationAC17:
    def test_malformed_entry_is_failed(self, env):
        ref = _ref("MALFORMED")
        pairs = {ref: "not-a-dict"}
        res = env.delete([ref], pairs)
        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "failed"

    def test_pairs_not_an_object_is_422_invalid_body(self, env):
        res = env.client.post(
            DELETE_SB,
            json={
                "companyCode": env.company_a_code,
                "source_refs": [],
                "pairs": ["a", "b"],
            },
        )
        assert res.status_code == 422, res.text
        assert res.json()["code"] == "INVALID_BODY"

    def test_pairs_over_max_batch_is_413(self, env):
        pairs = {
            f"{MARKER}-{i}": {"item_code": "X", "location_code": "Y"}
            for i in range(MAX_BATCH + 1)
        }
        res = env.client.post(
            DELETE_SB,
            json={
                "companyCode": env.company_a_code,
                "source_refs": list(pairs.keys())[:5],
                "pairs": pairs,
            },
        )
        assert res.status_code == 413, res.text
        assert res.json()["code"] == "BATCH_TOO_LARGE"

    def test_dry_run_deletion_writes_nothing(self, env):
        existing = env.make_stock(
            product_id=env.product.id, warehouse_id=env.wh_active.id, quantity_on_hand=77
        )
        ref = _ref("DRYDEL")
        pairs = {
            ref: {
                "item_code": env.product.product_code,
                "location_code": env.wh_active.warehouse_code,
            }
        }
        res = env.delete([ref], pairs, dry_run=True)
        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "deleted"
        row = env.stock_by_id(existing.id)
        assert row["quantity_on_hand"] == 77, "dry run must not actually zero it"


# =================================================================== AC-SB-18
class TestPermissionGuardAC18:
    """The real RBAC guard, on an empty schema of its own - mirrors
    `test_ingest_brands.py::TestPermissionGuardAC9`. `stock_balances` is not
    yet in `INGEST_PERMISSIONS`/`DELETE_PERMISSIONS` (D1/D9), so every case
    here 404s ("unknown_entity") today, on BOTH keys, rather than 403/200 -
    that 404 is what this file pins as red-for-the-right-reason. It turns
    into a real 403/200 test the moment the coder adds
    `"stock_balances": "inventory.stock.edit"` etc to the live maps (imported
    by reference below, never copied)."""

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
        from app.api.v1.external import ingest as ingest_module
        from app.api.v1.external.permissions import require_external_permission_for_path

        api = FastAPI()

        @api.post("/ingest/{entity}")
        def _ingest_stub(
            entity: str,
            _: dict = Depends(
                require_external_permission_for_path(ingest_module.INGEST_PERMISSIONS)
            ),
        ):
            return {"ok": entity}

        @api.post("/ingest/{entity}/deletions")
        def _delete_stub(
            entity: str,
            _: dict = Depends(
                require_external_permission_for_path(ingest_module.DELETE_PERMISSIONS)
            ),
        ):
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

        edit_slug = "inventory.stock.edit"
        view_slug = "inventory.stock.view"
        delete_slug = "inventory.stock.delete"
        perms = {}
        for slug in (edit_slug, view_slug, delete_slug):
            perm = UserPermission(slug=slug, name=slug)
            guard_db.add(perm)
            guard_db.flush()
            perms[slug] = perm

        issued = {}
        for label, held in (
            ("editor", [edit_slug]),
            ("viewer", [view_slug]),
            ("deleter", [delete_slug]),
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
                guard_db.add(UserRolePermission(role_id=role.id, permission_id=perms[slug].id))
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

    def test_a_key_without_edit_is_403_on_ingest_naming_it(self, guard_client, keys):
        res = guard_client.post(
            "/ingest/stock_balances", headers={"X-API-Key": keys["viewer"]}
        )
        assert res.status_code == 403, res.text
        assert "inventory.stock.edit" in res.text

    def test_the_edit_slug_passes_ingest(self, guard_client, keys):
        res = guard_client.post(
            "/ingest/stock_balances", headers={"X-API-Key": keys["editor"]}
        )
        assert res.status_code == 200, res.text

    def test_a_key_without_delete_is_403_on_deletions_naming_it(self, guard_client, keys):
        res = guard_client.post(
            "/ingest/stock_balances/deletions", headers={"X-API-Key": keys["editor"]}
        )
        assert res.status_code == 403, res.text
        assert "inventory.stock.delete" in res.text

    def test_the_delete_slug_passes_deletions(self, guard_client, keys):
        res = guard_client.post(
            "/ingest/stock_balances/deletions", headers={"X-API-Key": keys["deleter"]}
        )
        assert res.status_code == 200, res.text


class TestMigrationGrantAC18:
    """D9: the migration grants `inventory.stock.{view,edit,delete}` to
    `integration_foundryx_esb`.

    Driven through `apply()`/`revert()` - the `tests/test_migration_brands_
    grant.py` convention - not `alembic upgrade head`. The original version
    of this test shelled out to the CLI: that only ever applies a NEW
    migration ONCE per database (it is stamped into `alembic_version`), so a
    test built around it could pass at most once and never again on a second
    run, CI included, whose own database is bootstrapped straight to head
    before the suite ever runs (`scripts/bootstrap_env.py`) - the very first
    invocation of this test would already see the migration as a no-op.
    `bind` mirrors `test_migration_brands_grant.py::bind` exactly, and for
    the same reason: the migration's own statements are plain, unqualified
    SQL against `user_permissions`/`user_roles`/`user_role_permissions`,
    resolved through the connection's ordinary `search_path` - a
    `pg_empty_schema` scratch schema would NOT catch that SQL, since its
    `schema_translate_map` only rewrites compiled ORM/Core constructs, never
    a raw `text()` string.
    """

    _MIG_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "alembic",
        "versions",
        "sb1_stock_balances_grant.py",
    )
    _ESB_ROLE_SLUG = "integration_foundryx_esb"
    _TARGET_SLUGS = (
        "inventory.stock.view",
        "inventory.stock.edit",
        "inventory.stock.delete",
    )

    def _load_migration(self):
        if not os.path.exists(self._MIG_PATH):
            pytest.fail(
                f"expected migration module at {self._MIG_PATH} (tester's "
                "assumed revision id - update _MIG_PATH here if the coder "
                "named it differently) with module-level apply(conn)/revert(conn)"
            )
        spec = importlib.util.spec_from_file_location(
            "mig_sb1_stock_balances_grant", self._MIG_PATH
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @pytest.fixture()
    def bind(self):
        """A connection whose every write is discarded - same reason
        `test_migration_brands_grant.py::bind` gives: the statement under
        test is exactly the one production will run, against the real
        tables, so narrowing it to a scratch schema would stop testing it."""
        connection = engine.connect()
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()
            connection.close()

    def _existing_role_id(self, bind) -> Optional[str]:
        row = bind.execute(
            text("SELECT id FROM user_roles WHERE slug = :s"),
            {"s": self._ESB_ROLE_SLUG},
        ).first()
        return str(row[0]) if row else None

    def _grant_count(self, bind, role_id: str, slug: str) -> int:
        return bind.execute(
            text(
                "SELECT count(*) FROM user_role_permissions rp "
                "JOIN user_permissions p ON p.id = rp.permission_id "
                "WHERE rp.role_id = :r AND p.slug = :s"
            ),
            {"r": role_id, "s": slug},
        ).scalar()

    @pytest.fixture()
    def esb_role(self, bind):
        """Round 3 fix (test defect found live): `integration_foundryx_esb`
        is REAL, persistent seed data on any database `seed_integrations`/
        migration 297 has run against - not scratch data this suite is free
        to assume absent. Reuses it when present rather than creating a
        second (suffixed) role alongside it; seeds one only when genuinely
        absent.

        Cleanup removes only what THIS fixture itself added: the role row,
        if it seeded one, and any of the three target grants that did not
        already exist on the reused role before `apply()` ran - never a
        pre-existing role or a grant it already held (that copy of Admin's
        permissions is real, and deleting it is not this suite's call to
        make). `bind`'s own transaction rollback is a second, structural
        guarantee of the same thing; this teardown states the contract
        explicitly rather than resting on that alone.
        """
        existing_id = self._existing_role_id(bind)
        seeded_role = existing_id is None
        if seeded_role:
            role_id = str(uuid.uuid4())
            bind.execute(
                text(
                    "INSERT INTO user_roles (id, slug, name, description, is_protected, "
                    "is_default, is_trashed) VALUES (:i, :s, :n, :d, false, false, false)"
                ),
                {
                    "i": role_id,
                    "s": self._ESB_ROLE_SLUG,
                    "n": f"{MARKER} ESB role",
                    "d": f"{MARKER} scratch",
                },
            )
        else:
            role_id = existing_id

        pre_existing_grants = {
            slug: self._grant_count(bind, role_id, slug) > 0 for slug in self._TARGET_SLUGS
        }

        try:
            yield role_id
        finally:
            for slug in self._TARGET_SLUGS:
                if not pre_existing_grants[slug]:
                    bind.execute(
                        text(
                            "DELETE FROM user_role_permissions rp USING user_permissions p "
                            "WHERE rp.role_id = :r AND rp.permission_id = p.id AND p.slug = :s"
                        ),
                        {"r": role_id, "s": slug},
                    )
            if seeded_role:
                bind.execute(text("DELETE FROM user_roles WHERE id = :r"), {"r": role_id})

    def test_apply_grants_view_edit_delete_to_the_esb_role(self, bind, esb_role):
        role_id = esb_role
        mig = self._load_migration()

        mig.apply(bind)

        for slug in self._TARGET_SLUGS:
            assert self._grant_count(bind, role_id, slug) == 1, slug

    def test_apply_is_idempotent(self, bind, esb_role):
        role_id = esb_role
        mig = self._load_migration()

        mig.apply(bind)
        mig.apply(bind)

        for slug in self._TARGET_SLUGS:
            assert self._grant_count(bind, role_id, slug) == 1, slug

    def test_downgrade_is_a_no_op(self, bind, esb_role):
        """Fix round 1 (both reviews): same no-op shape as
        `511_brands_esb_grant.py` - the ESB role's grants were seeded once as
        a copy of Admin's own, so these three likely pre-date this migration
        and a downgrade must not take away access the ESB already had."""
        role_id = esb_role
        mig = self._load_migration()

        mig.apply(bind)
        mig.revert(bind)

        for slug in self._TARGET_SLUGS:
            assert self._grant_count(bind, role_id, slug) == 1, (
                f"{slug} must survive the downgrade (fix round 1: the grant may "
                "pre-date this migration)"
            )

    def test_apply_on_a_database_without_the_esb_role_does_not_error(self, bind):
        """The no-role branch specifically - genuinely untestable when
        `integration_foundryx_esb` already exists here (round 3: that is now
        a real, expected state on any seeded database, not a fixture bug),
        so this skips rather than failing on a premise it cannot control."""
        if self._existing_role_id(bind) is not None:
            pytest.skip(
                "integration_foundryx_esb already exists on this database "
                "(seed_integrations has run here) - this test only proves the "
                "no-such-role branch, which needs genuine absence to exercise"
            )
        mig = self._load_migration()

        before = bind.execute(
            text("SELECT count(*) FROM user_role_permissions")
        ).scalar()

        mig.apply(bind)  # must not raise

        after = bind.execute(
            text("SELECT count(*) FROM user_role_permissions")
        ).scalar()
        assert after == before, "nothing to grant to, so nothing new was granted"


class TestMigrationSb2StockPairUniqueFixRound2:
    """Fix round 2: `sb2_stock_pair_unique.py` creates
    `uq_stock_product_id_warehouse_id` when absent, and refuses (never
    dedupes) when a duplicate `(product_id, warehouse_id)` pair already
    exists. Same `apply()`-driven, real-connection-with-rollback substrate as
    `TestMigrationGrantAC18` above and `test_migration_brands_grant.py`:
    `CREATE`/`DROP INDEX` are transactional DDL in Postgres, so the `bind`
    fixture's own rollback restores the index this test drops - no manual
    restore needed."""

    _MIG_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "alembic",
        "versions",
        "sb2_stock_pair_unique.py",
    )
    _INDEX_NAME = "uq_stock_product_id_warehouse_id"

    def _load_migration(self):
        if not os.path.exists(self._MIG_PATH):
            pytest.fail(
                f"expected migration module at {self._MIG_PATH} (tester's "
                "assumed revision id - update _MIG_PATH here if the coder "
                "named it differently) with module-level apply(conn)/revert(conn)"
            )
        spec = importlib.util.spec_from_file_location(
            "mig_sb2_stock_pair_unique", self._MIG_PATH
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @pytest.fixture()
    def bind(self):
        """A connection whose every write is discarded - same reason
        `TestMigrationGrantAC18::bind` gives, and doubly true here: dropping
        a real unique index on the database this whole suite runs against
        must never survive past this one test."""
        connection = engine.connect()
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()
            connection.close()

    def _index_exists(self, bind) -> bool:
        # `pg_indexes` lists every schema at once, unlike an ordinary table
        # reference - this suite's OWN `blank_session()` scratch schema
        # carries a same-named copy (built by `create_all` off the same ORM
        # model), so an unqualified check here would find that one even
        # after this test drops the REAL, `public` one. `bind` is a plain
        # `engine.connect()` with no `search_path` override (see this
        # class's own `bind` fixture docstring), so every DDL/DML statement
        # this test issues already targets `public` alone - only the CHECK
        # needs the explicit schema filter to match.
        return (
            bind.execute(
                text(
                    "SELECT 1 FROM pg_indexes WHERE indexname = :name AND schemaname = 'public'"
                ),
                {"name": self._INDEX_NAME},
            ).first()
            is not None
        )

    def _seed_duplicate_pair(self, bind) -> None:
        """Two `stock` rows sharing one `(product_id, warehouse_id)` pair -
        via the ORM, bound to THIS test's own connection/transaction, so the
        migration's own raw-SQL duplicate count (issued on the same `bind`)
        sees them without a commit."""
        from sqlalchemy.orm import Session

        from app.models.company import Company
        from app.models.inventory import Stock, Warehouse
        from app.models.product import Product, ProductCategory, UnitOfMeasure

        session = Session(bind=bind, join_transaction_mode="create_savepoint")
        suffix = uuid.uuid4().hex[:8]
        company = Company(id=str(uuid.uuid4()), name=f"{MARKER} dup co", code=f"ZSD{suffix}")
        category = ProductCategory(category_code=unique_code(MARKER), category_name="c")
        uom = UnitOfMeasure(uom_code=unique_code(MARKER), uom_name="u")
        session.add_all([company, category, uom])
        session.flush()
        warehouse = Warehouse(
            warehouse_code=unique_code(MARKER), warehouse_name="w", is_active=True,
            company_id=company.id,
        )
        product = Product(
            product_code=unique_code(MARKER), product_name="p", category_id=category.id,
            base_uom_id=uom.id, list_price=1, company_id=company.id,
        )
        session.add_all([warehouse, product])
        session.flush()
        session.add(Stock(product_id=product.id, warehouse_id=warehouse.id, company_id=company.id, quantity_on_hand=1))
        session.add(Stock(product_id=product.id, warehouse_id=warehouse.id, company_id=company.id, quantity_on_hand=2))
        session.flush()

    def test_apply_creates_the_index_when_absent(self, bind):
        assert self._index_exists(bind), (
            "expected the ORM-declared index to already exist on this database"
        )
        bind.execute(text(f"DROP INDEX {self._INDEX_NAME}"))
        assert not self._index_exists(bind)

        mig = self._load_migration()
        mig.apply(bind)

        assert self._index_exists(bind)

    def test_apply_is_idempotent(self, bind):
        mig = self._load_migration()
        mig.apply(bind)
        mig.apply(bind)
        assert self._index_exists(bind)

    def test_apply_raises_on_a_seeded_duplicate_pair(self, bind):
        bind.execute(text(f"DROP INDEX {self._INDEX_NAME}"))
        self._seed_duplicate_pair(bind)

        mig = self._load_migration()
        with pytest.raises(RuntimeError, match="product_id, warehouse_id"):
            mig.apply(bind)

        assert not self._index_exists(bind), "must not create the index when it refused"

    def test_downgrade_is_a_no_op(self, bind):
        assert self._index_exists(bind)
        mig = self._load_migration()
        mig.revert(bind)  # must not raise
        assert self._index_exists(bind), "downgrade must not drop the model-declared index"


# =================================================================== AC-SB-19
class TestReadBackAC19:
    def test_read_returns_records_and_not_found(self, env):
        env.make_stock(product_id=env.product.id, warehouse_id=env.wh_active.id, quantity_on_hand=33)
        ref = _ref("READ1")
        missing_ref = _ref("READMISS")
        pairs = {
            ref: {
                "item_code": env.product.product_code,
                "location_code": env.wh_active.warehouse_code,
            }
        }
        res = env.read([ref, missing_ref], pairs)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["not_found"] == [missing_ref], body
        got = {r["source_ref"]: r["qty"] for r in body["records"]}
        assert got == {ref: 33}, body


# ============================================================== Fix round 1
# Reviewer + security fix round 1 (2026-09-26): insert race (S2/N2),
# ambiguous code resolution (S3/N2), unchanged-value skip (N3), qty upper
# bound (security S1), and a real-mount double-gate probe (security S2).
class TestInsertRaceFixRound1:
    def test_insert_race_loser_updates_and_last_value_wins(self, env, monkeypatch):
        """A same-pair insert race: another writer's row for this EXACT
        (product, warehouse) pair lands after our own preload found nothing,
        but before our own INSERT - simulated by inserting it, via the same
        session, from inside `_build_preload` (called once, before the
        per-record loop even starts, so the injected row predates the
        record's own savepoint and survives that savepoint's rollback when
        our own insert hits the real unique constraint)."""
        from app.services import stock_balance_ingest_service as svc_module

        original_build_preload = svc_module.StockBalanceIngestService._build_preload
        injected = {"done": False}

        def _build_preload_then_race(self, item_codes, location_codes):
            original_build_preload(self, item_codes, location_codes)
            if not injected["done"]:
                injected["done"] = True
                env.db.execute(
                    text(
                        "INSERT INTO stock (id, product_id, warehouse_id, company_id, "
                        "quantity_on_hand, synced_to_excel) "
                        "VALUES (gen_random_uuid(), :p, :w, :c, :q, false)"
                    ),
                    {
                        "p": str(env.product.id),
                        "w": str(env.wh_active.id),
                        "c": env.company_a,
                        "q": 5,
                    },
                )

        monkeypatch.setattr(
            svc_module.StockBalanceIngestService, "_build_preload", _build_preload_then_race
        )

        record = _sb_record(
            item_code=env.product.product_code,
            location_code=env.wh_active.warehouse_code,
            qty=50,
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        row = env.stock_row(env.product.id, env.wh_active.id)
        assert row is not None
        assert row["quantity_on_hand"] == 50, "last value wins, same as a same-batch duplicate"

    def test_insert_race_still_not_found_after_retry_is_failed(self, env):
        """A "legacy data defect": a stock row already exists for this EXACT
        (product_id, warehouse_id) pair, but stamped to a DIFFERENT company -
        `uq_stock_product_id_warehouse_id` carries no company column of its
        own to prevent that. Our own company-scoped preload correctly does
        not see it (so the create branch is attempted), and the real unique
        constraint still refuses the insert; the retry's own company-scoped
        re-read still finds nothing, so the record is refused rather than
        silently adopted."""
        env.db.execute(
            text(
                "INSERT INTO stock (id, product_id, warehouse_id, company_id, quantity_on_hand, "
                "synced_to_excel) VALUES (gen_random_uuid(), :p, :w, :c, :q, false)"
            ),
            {
                "p": str(env.product.id),
                "w": str(env.wh_active.id),
                "c": env.company_b,
                "q": 5,
            },
        )
        env.db.commit()

        record = _sb_record(
            item_code=env.product.product_code,
            location_code=env.wh_active.warehouse_code,
            qty=50,
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", entry
        assert "exists under another company" in entry.get("errors", {}).get("_", ""), entry


class TestAmbiguousCodesFixRound1:
    """`warehouse_code`/`product_code` are unique per company only as the raw
    stored string - two rows spelled differently but case/whitespace-
    identical after normalising CAN coexist. Seeds exactly that pair for
    each (an exact-case code and a lower-cased, trailing-spaced twin,
    matching the plan's own `"MBS"`/`"mbs "` example) and sends a THIRD
    spelling that matches neither exactly."""

    def test_ambiguous_item_code_on_ingest_is_failed(self, env):
        base = f"AMBI{uuid.uuid4().hex[:8]}"
        env._product(base, company_id=env.company_a)
        env._product(f"{base.lower()} ", company_id=env.company_a)

        record = _sb_record(
            item_code=base.lower(), location_code=env.wh_active.warehouse_code, qty=5
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", entry
        assert entry.get("errors", {}).get("item_code", "").startswith("ambiguous:"), entry

    def test_ambiguous_location_code_on_ingest_is_failed(self, env):
        base = f"AMBW{uuid.uuid4().hex[:8]}"
        env.db.add(
            Warehouse(
                warehouse_code=base, warehouse_name=f"{MARKER} a", is_active=True,
                company_id=env.company_a,
            )
        )
        env.db.add(
            Warehouse(
                warehouse_code=f"{base.lower()} ", warehouse_name=f"{MARKER} b", is_active=True,
                company_id=env.company_a,
            )
        )
        env.db.commit()

        record = _sb_record(
            item_code=env.product.product_code, location_code=base.lower(), qty=5
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", entry
        assert entry.get("errors", {}).get("location_code", "").startswith("ambiguous:"), entry

    def test_exact_code_wins_over_ambiguous_decoys(self, env):
        base = f"AMBE{uuid.uuid4().hex[:8]}"
        env._product(base, company_id=env.company_a)
        env._product(f"{base.lower()} ", company_id=env.company_a)

        record = _sb_record(
            item_code=base, location_code=env.wh_active.warehouse_code, qty=5
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", entry

    def test_ambiguous_pair_on_deletion_is_failed(self, env):
        base = f"AMBD{uuid.uuid4().hex[:8]}"
        env._product(base, company_id=env.company_a)
        env._product(f"{base.lower()} ", company_id=env.company_a)
        ref = _ref("AMBDEL")
        pairs = {ref: {"item_code": base.lower(), "location_code": env.wh_active.warehouse_code}}

        res = env.delete([ref], pairs)
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", entry
        assert entry.get("errors", {}).get("item_code", "").startswith("ambiguous:"), entry

    def test_ambiguous_pair_on_read_back_is_not_found(self, env):
        base = f"AMBR{uuid.uuid4().hex[:8]}"
        env._product(base, company_id=env.company_a)
        env._product(f"{base.lower()} ", company_id=env.company_a)
        ref = _ref("AMBREAD")
        pairs = {ref: {"item_code": base.lower(), "location_code": env.wh_active.warehouse_code}}

        res = env.read([ref], pairs)
        assert res.status_code == 200, res.text
        assert res.json()["not_found"] == [ref], res.json()


class TestUnchangedValueSkipsWriteFixRound1:
    def test_unchanged_qty_skips_the_flush_updated_at_is_untouched(self, env):
        existing = env.make_stock(
            product_id=env.product.id, warehouse_id=env.wh_active.id, quantity_on_hand=10,
        )
        fixed_ts = datetime(2020, 1, 1, 0, 0, 0)
        env.db.execute(
            text("UPDATE stock SET updated_at = :ts WHERE id = :id"),
            {"ts": fixed_ts, "id": str(existing.id)},
        )
        env.db.commit()

        record = _sb_record(
            item_code=env.product.product_code, location_code=env.wh_active.warehouse_code, qty=10
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        row = env.stock_by_id(existing.id)
        assert row["quantity_on_hand"] == 10
        assert row["updated_at"] == fixed_ts, "no write at all, so updated_at must be untouched"


class TestQtyUpperBoundFixRound1:
    def test_qty_above_postgres_int4_max_is_failed(self, env):
        record = _sb_record(
            item_code=env.product.product_code,
            location_code=env.wh_active.warehouse_code,
            qty=2**31,
        )
        res = env.post(INGEST_SB, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", entry
        assert "qty" in entry.get("errors", {}), entry


class TestRealMountDoubleGateFixRound1:
    """Security S2: the SAME two-guard wiring `TestPermissionGuardAC18`
    proves against a throwaway stub app, now proved against the REAL mounted
    app with REAL issued integration keys and no `get_external_api_user`
    override - a stub app cannot catch a mounting mistake in
    `app/api/v1/external/__init__.py` itself, only a real router chain can.
    """

    @pytest.fixture()
    def gate_env(self):
        from app.dependencies import get_db
        from app.models.base import set_company_scope
        from app.models.integration import Integration
        from app.models.user import User, UserPermission, UserRole, UserRoleAssignment, UserRolePermission
        from app.services.company_scope_resolver import apply_company_scope
        from app.services.integration_key_service import IntegrationKeyService

        with blank_session() as db:
            set_company_scope(db, None)
            perms = {}
            for slug in ("inventory.stock.view", "inventory.stock.edit", "inventory.stock.delete"):
                perm = UserPermission(slug=slug, name=slug)
                db.add(perm)
                db.flush()
                perms[slug] = perm

            keys = {}
            for label, held in (
                ("edit", ["inventory.stock.edit"]),
                ("view", ["inventory.stock.view"]),
                ("delete", ["inventory.stock.delete"]),
                ("editdel", ["inventory.stock.edit", "inventory.stock.delete"]),
            ):
                user = User(
                    email=f"{MARKER.lower()}-realgate-{label}-{uuid.uuid4().hex[:6]}@integrations.local",
                    name=f"{MARKER} realgate {label}",
                    status="ACTIVE",
                    is_integration=True,
                )
                db.add(user)
                db.flush()
                role = UserRole(slug=f"{MARKER.lower()}_realgate_{label}_{uuid.uuid4().hex[:6]}", name=label)
                db.add(role)
                db.flush()
                db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
                for slug in held:
                    db.add(UserRolePermission(role_id=role.id, permission_id=perms[slug].id))
                db.flush()
                integration = Integration(
                    name=f"{MARKER}-realgate-{label}-{uuid.uuid4().hex[:6]}",
                    type="autocount_esb",
                    act_as_user_id=user.id,
                    is_active=True,
                )
                db.add(integration)
                db.flush()
                keys[label] = IntegrationKeyService(db).issue_key(integration)

            db.commit()
            company_code = db.execute(
                text("SELECT code FROM companies WHERE id = :id"), {"id": DEFAULT_COMPANY_ID}
            ).scalar()

            def _override_get_db():
                yield db

            def _override_scope():
                set_company_scope(db, None)
                return None

            app.dependency_overrides[get_db] = _override_get_db
            app.dependency_overrides[apply_company_scope] = _override_scope
            # Deliberately NO override of `get_external_api_user` (security
            # S2): the real dependency resolves the real issued key through
            # `integration_api_keys`, exactly as a live ESB call would.
            try:
                with TestClient(app) as client:
                    yield {"client": client, "keys": keys, "company_code": company_code}
            finally:
                app.dependency_overrides.clear()

    @staticmethod
    def _call(client, url, company_code, keys, label):
        body = {"companyCode": company_code, "source_refs": [], "pairs": {}}
        return client.post(url, json=body, headers={"X-API-Key": keys[label]})

    def test_delete_only_key_is_403_on_deletions(self, gate_env):
        res = self._call(
            gate_env["client"], DELETE_SB, gate_env["company_code"], gate_env["keys"], "delete"
        )
        assert res.status_code == 403, res.text

    def test_edit_only_key_is_403_on_deletions(self, gate_env):
        res = self._call(
            gate_env["client"], DELETE_SB, gate_env["company_code"], gate_env["keys"], "edit"
        )
        assert res.status_code == 403, res.text

    def test_edit_only_key_is_403_on_read(self, gate_env):
        res = self._call(
            gate_env["client"], READ_SB, gate_env["company_code"], gate_env["keys"], "edit"
        )
        assert res.status_code == 403, res.text

    def test_edit_and_delete_key_is_200_on_deletions(self, gate_env):
        res = self._call(
            gate_env["client"], DELETE_SB, gate_env["company_code"], gate_env["keys"], "editdel"
        )
        assert res.status_code == 200, res.text


# =================================================================== AC-SB-20
class TestFixturesAC20:
    """Replays the corrected Foundryx A7 fixtures
    (`tests/fixtures/stock_balances/`, copied verbatim from
    `foundryx-shared-service` commit 1b35224a - see that directory's own
    README for the request/response mapping and the corrections) against a
    chain seeded to match what each response implies: an `updated` row
    exists beforehand (its current quantity matching the dry-run fixture's
    own diff, or - where that diff is `{}` - the incoming qty itself, since
    nothing changed); a `created` row does not.

    `entity_id` is asserted PRESENT and null-vs-non-null only (README
    correction 1: the fixture's own ids are placeholders, never a value
    Sorento's real response would echo). The two disagreements this class
    used to carry (`contract-2.5.json`'s `fields_added` shape, and the
    original single 422-only deletions error fixture) were both corrected in
    the 2026-09-26 fixture update and now replay clean - see the class's own
    git history for the earlier notes.
    """

    _FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "stock_balances"

    # Warehouses the ingest + deletions fixtures resolve against - every code
    # the README's table names, minus BRW-VAR (deliberately never created:
    # the "unknown location" case).
    _ACTIVE_WAREHOUSE_CODES = ("BRW-BB", "BRW", "MWH", "WH3", "PJ-SR", "MAINTANC", "MBS")
    _INACTIVE_WAREHOUSE_CODE = "CON"

    # item_code -> product_code, every row EXCEPT SRT-NOTSYNCED-9001 (the
    # "item Sorento lacks" case) and GHOST-ITEM (deletions-only, same case).
    _PRODUCT_CODES = (
        '1/2" ULTRA CIRCULAR',
        "32MM TAIL PIECE COUPLING",
        "ACC-CB8001",
        "ACC-KS7001-YG",
        "ACC-SRT1024",
        "ACC-SRT6010",
        "ACC-SRT8003",
        "ACC-SRT9013",
        "B2154-NL",
    )

    @classmethod
    def _load(cls, name: str) -> dict:
        return json.loads((cls._FIXTURES_DIR / name).read_text())

    def _seed_chain(self, env) -> tuple[dict, dict]:
        warehouses: dict[str, Warehouse] = {}
        for code in self._ACTIVE_WAREHOUSE_CODES:
            row = Warehouse(
                warehouse_code=code,
                warehouse_name=f"{MARKER} {code}",
                is_active=True,
                company_id=env.company_a,
            )
            env.db.add(row)
            warehouses[code] = row
        inactive = Warehouse(
            warehouse_code=self._INACTIVE_WAREHOUSE_CODE,
            warehouse_name=f"{MARKER} {self._INACTIVE_WAREHOUSE_CODE}",
            is_active=False,
            company_id=env.company_a,
        )
        env.db.add(inactive)
        warehouses[self._INACTIVE_WAREHOUSE_CODE] = inactive
        env.db.flush()

        products = {
            code: env._product(code, company_id=env.company_a) for code in self._PRODUCT_CODES
        }

        # `updated` rows only - the pairs the ingest-response fixture verdicts
        # `created` (BRW-BB, MWH, PJ-SR) get no pre-existing row. Currents
        # match the dry-run fixture's own diff, or (where that diff is `{}`)
        # the same qty the request itself sends for that pair.
        env.make_stock(
            product_id=products["32MM TAIL PIECE COUPLING"].id,
            warehouse_id=warehouses["BRW"].id,
            quantity_on_hand=1216,
        )
        env.make_stock(
            product_id=products["ACC-KS7001-YG"].id,
            warehouse_id=warehouses["WH3"].id,
            quantity_on_hand=1000,
        )
        env.make_stock(
            product_id=products["ACC-SRT8003"].id,
            warehouse_id=warehouses["MBS"].id,
            quantity_on_hand=10,
        )
        env.make_stock(
            product_id=products["B2154-NL"].id,
            warehouse_id=warehouses["MAINTANC"].id,
            quantity_on_hand=34,
        )
        return warehouses, products

    @staticmethod
    def _assert_records_match(got: list, expected: list) -> None:
        """outcome, warnings, summary and diff match exactly (brief item 3);
        `entity_id` is asserted present-and-null-vs-non-null, but ONLY where
        the fixture itself carries an `entity_id` key - the deletions
        fixtures never do (Foundryx's own README says nothing about it
        there; the "assert presence only" instruction is about the ingest
        side, where `RecordResult.as_dict()` always includes the key).

        Paired by POSITION, not by `source_ref` keying into a dict - D5
        guarantees request order, and the duplicate-pair fixture sends the
        SAME `source_ref` twice, which a ref-keyed dict would collapse into
        one entry and silently compare the wrong record against the wrong
        expectation.
        """
        assert len(got) == len(expected), (got, expected)
        for row, exp in zip(got, expected):
            assert row["source_ref"] == exp["source_ref"], (row, exp)
            assert row["outcome"] == exp["outcome"], (row, exp)
            assert row.get("warnings", []) == exp.get("warnings", []), (row, exp)
            if "diff" in exp:
                assert row.get("diff") == exp["diff"], (row, exp)
            if "entity_id" in exp:
                assert "entity_id" in row, (row, exp)
                if exp["entity_id"] is None:
                    assert row["entity_id"] is None, (row, exp)
                else:
                    assert row["entity_id"] is not None, (row, exp)

    def test_ingest_and_deletions_fixtures_replay_against_seeded_chain(self, env):
        self._seed_chain(env)

        request = self._load("stock_balances-ingest-request.json")

        # Dry run FIRST - writes nothing, so the chain's currents are still
        # exactly what `_seed_chain` set, matching the dry-run fixture's own
        # diffs.
        dry_expected = self._load("stock_balances-ingest-dry-run-response.json")
        dry_res = env.client.post(f"{INGEST_SB}?dry_run=true", json=request)
        assert dry_res.status_code == 200, dry_res.text
        dry_body = dry_res.json()
        assert dry_body["dry_run"] is True
        assert dry_body["summary"] == dry_expected["summary"], dry_body["summary"]
        self._assert_records_match(dry_body["records"], dry_expected["records"])

        # Real run SECOND - the same request, now actually applied.
        real_expected = self._load("stock_balances-ingest-response.json")
        real_res = env.client.post(INGEST_SB, json=request)
        assert real_res.status_code == 200, real_res.text
        real_body = real_res.json()
        assert real_body["dry_run"] is False
        assert real_body["summary"] == real_expected["summary"], real_body["summary"]
        self._assert_records_match(real_body["records"], real_expected["records"])

        # Deletions - rows 1 and 10 now hold real stock from the real ingest
        # above (`created`/`updated`), so they resolve and zero.
        del_request = self._load("stock_balances-deletions-request.json")
        del_expected = self._load("stock_balances-deletions-response.json")
        del_res = env.client.post(DELETE_SB, json=del_request)
        assert del_res.status_code == 200, del_res.text
        del_body = del_res.json()
        assert del_body["summary"] == del_expected["summary"], del_body["summary"]
        self._assert_records_match(del_body["records"], del_expected["records"])

        # A single malformed `pairs` ENTRY fails only that ref; the clean ref
        # names the SAME pair the deletions fixture above already zeroed, and
        # an already-zero row still reports `deleted` (D7/AC-SB-15).
        malformed_request = self._load("stock_balances-deletions-malformed-entry-request.json")
        malformed_expected = self._load("stock_balances-deletions-malformed-entry-response.json")
        malformed_res = env.client.post(DELETE_SB, json=malformed_request)
        assert malformed_res.status_code == 200, malformed_res.text
        malformed_body = malformed_res.json()
        assert malformed_body["summary"] == malformed_expected["summary"], malformed_body["summary"]
        self._assert_records_match(malformed_body["records"], malformed_expected["records"])

    def test_duplicate_pair_fixture_last_value_wins(self, env):
        """Own, isolated chain: this pair must start with NO pre-existing
        stock row, so its first occurrence in the 2-record batch below is
        `created` - the main chained test above already claims this exact
        pair by the time its own ingest fixture runs."""
        warehouse = Warehouse(
            warehouse_code="BRW-BB",
            warehouse_name=f"{MARKER} BRW-BB",
            is_active=True,
            company_id=env.company_a,
        )
        env.db.add(warehouse)
        env.db.flush()
        env._product('1/2" ULTRA CIRCULAR', company_id=env.company_a)

        request = self._load("stock_balances-ingest-duplicate-pair-request.json")
        expected = self._load("stock_balances-ingest-duplicate-pair-response.json")

        res = env.client.post(INGEST_SB, json=request)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["summary"] == expected["summary"], body["summary"]
        self._assert_records_match(body["records"], expected["records"])
        # Both records name the SAME pair, so - unlike `_assert_records_match`,
        # which is keyed by `source_ref` - the SAME entity_id has to appear on
        # both entries here, in order, last value wins (D5/AC-SB-13).
        assert body["records"][0]["outcome"] == "created", body
        assert body["records"][1]["outcome"] == "updated", body
        assert body["records"][0]["entity_id"] == body["records"][1]["entity_id"]

    def test_malformed_pairs_body_is_422_invalid_body(self, env):
        """`pairs` not an object at all - the 2026-09-26 fixture split gives
        this its own file, distinct from the over-`MAX_BATCH` 413 case
        below."""
        expected = self._load("stock_balances-deletions-error-422-invalid-body.json")
        res = env.client.post(
            DELETE_SB,
            json={"companyCode": env.company_a_code, "source_refs": [], "pairs": ["a", "b"]},
        )
        assert res.status_code == 422, res.text
        assert res.json()["code"] == expected["code"]

    def test_pairs_over_max_batch_is_413_batch_too_large(self, env):
        """`pairs` IS an object but carries over `MAX_BATCH` entries - the
        same over-cap response `source_refs`/`codes` already get (README),
        never a second 422 shape for the identical failure mode."""
        expected = self._load("stock_balances-deletions-error-413-batch-too-large.json")
        pairs = {
            f"{MARKER}-{i}": {"item_code": "X", "location_code": "Y"}
            for i in range(MAX_BATCH + 1)
        }
        res = env.client.post(
            DELETE_SB,
            json={
                "companyCode": env.company_a_code,
                "source_refs": list(pairs.keys())[:5],
                "pairs": pairs,
            },
        )
        assert res.status_code == 413, res.text
        assert res.json()["code"] == expected["code"]

    def test_contract_2_5_fixture_replays_against_get_contract(self, env):
        expected = self._load("contract-2.5.json")
        res = env.client.get(CONTRACT_URL)
        assert res.status_code == 200, res.text
        body = res.json()
        # The fixture is a recording of 2.5 and stays one; a later point release is additive
        # (finance S0 made it "2.6"), so what the recording pins is that 2.5's surface below
        # still holds, on 2.5 or on anything after it.
        def _version(value: str) -> tuple[int, ...]:
            return tuple(int(part) for part in value.split("."))

        assert _version(body["version"]) >= _version(expected["version"])
        assert "stock_balances" in body["entities"]
        assert "warehouse_inactive" in body["warnings"]
        # Set comparison, not exact list equality (`fields_added` order
        # carries no meaning - the codebase's every other contract-version
        # test compares it the same way, e.g. `test_ingest_parity_s4_contract
        # .py::test_contract_lists_fields_added_per_entity`).
        assert set(body["fields_added"]["stock_balances"]) == set(
            expected["fields_added"]["stock_balances"]
        )
