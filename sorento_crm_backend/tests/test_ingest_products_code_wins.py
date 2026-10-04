"""Products resolve code-wins on ingest (contract 2.4), updated for the 3 Oct owner
rulings (PRODUCT-REF-COLLISION): products match by company + code ONLY. Ingest
never reads or writes product `integration_references` rows, so the
`ref_mismatch` warning, the reference-link-on-adopt and the other-source-system
conflict described below no longer exist for products; the tests assert the
code-only behaviour instead (see tests/test_product_ref_collision.py for the
collision cases). Original SR0 notes follow.

RED tests for SR0 - products resolve code-wins on ingest (contract 2.4).

UAC:  documentation/plans/autocount/ingest-products-code-wins-acceptance-criteria.md
PLAN: documentation/plans/autocount/PLAN-ingest-products-code-wins.md

Today `MasterIngestService._apply_scoped` raises `ReferenceConflict` the
moment a product's adopt-by-code match already carries a reference under
ANY source system - which is exactly the shape FoundryX's item-code push
hits for every one of the 9,067 SRT products already linked by SO/PO line
ingest (`AED_SORENTO:<numeric item key>`). This slice adds one branch: a
code match whose existing reference is under the SAME source system
(`autocount`) resolves instead of conflicting - the row updates, the
STORED reference is kept (never overwritten with the incoming one), and the
record carries the `ref_mismatch` warning (`WARN_REF_MISMATCH`, already
used by the document-line ladder for the identical situation - PLAN
copies that rule with its justification).

Deletions gain an optional `codes` map so a deletion-by-item-code can find
the same product when its own reference misses, following the identical
same-source-system rule.

Every scenario is seeded from a blank chain (no borrowed rows). Company A is
`DEFAULT_COMPANY_ID`; company B is a throwaway sibling for the isolation
tests. Substrate is `tests._pg_fixture.blank_session()`, matching every
other ingest test file - the real route via `TestClient`, so the guard, the
company anchor and the wire shape are all exercised, not only the service
method.

CW-3, CW-4, CW-7, CW-8, CW-9, DL-3, DL-5 are guard tests: the behaviour they
assert already exists today (unaffected by this slice) and they are
expected to PASS on this red run, kept as regression fences for the coder's
change.
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

# MUST be the first app import - resolves the circular import in
# app.modules.runtime.guards.
from app.main import app  # noqa: E402

from app.models.company import Company
from app.models.inventory import Warehouse
from app.models.order import SalesOrder, SalesOrderLine
from app.models.procurement import Supplier
from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.integration_reference_service import IntegrationReferenceService

from tests._pg_fixture import blank_session, unique_code

MARKER = "ZZTCW"

_USER_ID = "6c9cad20-9999-4222-8333-4444555566f1"
_ROLE_ID = "6c9cad20-9998-4222-8333-4444555566f2"

# Every other master that adopts by code (AC-CW-8). model, code column, name
# column - a code-only push builds a canonical payload with just those two.
_OTHER_MASTERS: dict[str, tuple[type, str, str]] = {
    "brands": (Brand, "brand_code", "brand_name"),
    "product_categories": (ProductCategory, "category_code", "category_name"),
    "units_of_measure": (UnitOfMeasure, "uom_code", "uom_name"),
    "warehouses": (Warehouse, "warehouse_code", "warehouse_name"),
    "suppliers": (Supplier, "supplier_code", "supplier_name"),
}


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
    """Two companies, plus a product/master seed helper for every shape a
    code-wins scenario needs. Every seed method commits: a dry-run call makes
    the SERVICE roll back to the savepoint it opened, which must not also
    discard the fixture's own seeds - the outer transaction still discards
    everything at teardown."""

    def __init__(self, client: TestClient, db):
        self.client = client
        self.db = db
        self.company_a = DEFAULT_COMPANY_ID
        self.refs = IntegrationReferenceService(db, company_id=self.company_a)

        suffix = uuid.uuid4().hex[:8]
        other = Company(id=str(uuid.uuid4()), name=f"{MARKER} B {suffix}", code=f"ZW{suffix}")
        db.add(other)
        db.flush()
        self.company_b = str(other.id)
        self.company_a_code = db.execute(
            text("SELECT code FROM companies WHERE id = :id"), {"id": self.company_a}
        ).scalar()
        self.company_b_code = db.execute(
            text("SELECT code FROM companies WHERE id = :id"), {"id": self.company_b}
        ).scalar()

        self._category = ProductCategory(
            category_code=unique_code(MARKER), category_name=f"{MARKER} category"
        )
        self._uom = UnitOfMeasure(uom_code=unique_code(MARKER), uom_name=f"{MARKER} unit")
        db.add_all([self._category, self._uom])
        db.flush()
        db.commit()

    # ------------------------------------------------------------- companies
    def code_for(self, company_id: str) -> str:
        if company_id == self.company_a:
            return self.company_a_code
        if company_id == self.company_b:
            return self.company_b_code
        return self.db.execute(
            text("SELECT code FROM companies WHERE id = :id"), {"id": company_id}
        ).scalar()

    # -------------------------------------------------------------- product
    def product(
        self,
        *,
        code: str = None,
        company_id: str = None,
        list_price=Decimal("10.00"),
        description: str = None,
    ) -> tuple[str, str]:
        anchor = company_id or self.company_a
        code = code or unique_code(MARKER)
        row = Product(
            product_code=code,
            product_name=code,
            description=description,
            category_id=self._category.id,
            base_uom_id=self._uom.id,
            list_price=list_price,
            company_id=anchor,
        )
        self.db.add(row)
        self.db.flush()
        self.db.commit()
        return str(row.id), code

    def link(
        self,
        entity_type: str,
        entity_id: str,
        *,
        ref: str = None,
        source_system: str = "autocount",
        company_id: str = None,
        stem: str = "REF",
    ) -> str:
        ref = ref or _ref(stem)
        anchor = company_id or self.company_a
        svc = (
            self.refs
            if anchor == self.company_a
            else IntegrationReferenceService(self.db, company_id=anchor)
        )
        svc.link(
            entity_type=entity_type,
            entity_id=str(entity_id),
            source_ref=ref,
            source_system=source_system,
        )
        self.db.commit()
        return ref

    def linked_product(
        self,
        *,
        code: str = None,
        company_id: str = None,
        list_price=Decimal("10.00"),
        description: str = None,
        source_system: str = "autocount",
        ref_stem: str = "NUM",
    ) -> tuple[str, str, str]:
        """(ref, product_id, code) - a product already claimed by a reference,
        the state every SRT product is in today (minted by SO/PO line ingest)."""
        product_id, code = self.product(
            code=code, company_id=company_id, list_price=list_price, description=description
        )
        ref = self.link(
            "products", product_id, source_system=source_system, company_id=company_id, stem=ref_stem
        )
        return ref, product_id, code

    def sales_order_line(self, product_id: str, *, company_id: str = None) -> tuple[str, str]:
        anchor = company_id or self.company_a
        header = SalesOrder(
            so_number=f"{MARKER}-SO-{uuid.uuid4().hex[:8]}", status="open", company_id=anchor
        )
        self.db.add(header)
        self.db.flush()
        line = SalesOrderLine(
            sales_order_id=header.id,
            product_id=product_id,
            qty_ordered=5,
            qty_delivered=0,
            line_status="open",
            company_id=anchor,
        )
        self.db.add(line)
        self.db.flush()
        self.db.commit()
        return str(header.id), str(line.id)

    # -------------------------------------------------------- other masters
    def other_master(
        self, entity_type: str, *, code: str = None, name: str = None, company_id: str = None
    ) -> tuple[str, str]:
        model, code_col, name_col = _OTHER_MASTERS[entity_type]
        anchor = company_id or self.company_a
        code = code or unique_code(MARKER)
        name = name or f"{MARKER} {entity_type}"
        kwargs = {code_col: code, name_col: name}
        if hasattr(model, "company_id"):
            kwargs["company_id"] = anchor
        row = model(**kwargs)
        self.db.add(row)
        self.db.flush()
        self.db.commit()
        return str(row.id), code

    # --------------------------------------------------------------- calls
    def ingest(
        self,
        entity: str,
        records: list[dict],
        *,
        company_id: str = None,
        dry_run: bool = False,
    ):
        payload = {"companyCode": self.code_for(company_id or self.company_a), "records": records}
        url = f"/api/v1/external/ingest/{entity}"
        if dry_run:
            url += "?dry_run=true"
        return self.client.post(url, json=payload)

    def delete(
        self,
        entity: str,
        source_refs: list[str],
        *,
        codes: dict = None,
        company_id: str = None,
        dry_run: bool = False,
        body: dict = None,
    ):
        if body is not None:
            payload = dict(body)
        else:
            payload = {"source_refs": source_refs}
            if codes is not None:
                payload["codes"] = codes
        payload.setdefault("companyCode", self.code_for(company_id or self.company_a))
        url = f"/api/v1/external/ingest/{entity}/deletions"
        if dry_run:
            url += "?dry_run=true"
        return self.client.post(url, json=payload)

    # -------------------------------------------------------------- reads
    def row(self, table: str, entity_id: str):
        return (
            self.db.execute(
                text(f"SELECT * FROM {table} WHERE id = :id"), {"id": str(entity_id)}
            )
            .mappings()
            .first()
        )

    def ref_rows(self, entity_type: str, entity_id: str):
        return (
            self.db.execute(
                text(
                    "SELECT source_ref, source_system FROM integration_references "
                    "WHERE entity_type = :t AND entity_id = :i"
                ),
                {"t": entity_type, "i": str(entity_id)},
            )
            .mappings()
            .all()
        )

    def ref_owner(self, ref: str):
        return (
            self.db.execute(
                text(
                    "SELECT entity_id FROM integration_references "
                    "WHERE source_ref = :r AND entity_type = 'products'"
                ),
                {"r": ref},
            )
            .scalar()
        )


@pytest.fixture()
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
            # The real integration principal: an X-API-Key call arrives scoped
            # to ALL companies, and the anchor is what narrows it.
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


# ============================================================== ingest (CW)
class TestCodeWinsIngest:
    def test_cw1_linked_product_updates_keeps_ref_and_warns(self, env):
        ref, product_id, code = env.linked_product(
            list_price=Decimal("10.00"), description="Old description"
        )
        code_ref = _ref("BRA")

        res = env.ingest(
            "products",
            [
                {
                    "source_ref": code_ref,
                    "code": code,
                    "name": "Whatever AutoCount Calls It",
                    "description": "New description",
                    "list_price": "25.50",
                }
            ],
        )

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        assert entry["entity_id"] == product_id, entry

        row = env.row("products", product_id)
        assert row["description"] == "New description"
        assert row["list_price"] == Decimal("25.50")

        rows = env.ref_rows("products", product_id)
        assert len(rows) == 1, rows
        assert rows[0]["source_ref"] == ref
        assert env.ref_owner(code_ref) is None

    def test_cw2_dry_run_reports_updated_diff_warning_and_persists_nothing(self, env):
        ref, product_id, code = env.linked_product(list_price=Decimal("10.00"))
        code_ref = _ref("BRA")

        res = env.ingest(
            "products",
            [
                {
                    "source_ref": code_ref,
                    "code": code,
                    "name": "Whatever",
                    "list_price": "25.50",
                }
            ],
            dry_run=True,
        )

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["dry_run"] is True
        entry = body["records"][0]
        assert entry["outcome"] == "updated", entry
        assert entry["diff"] is not None, entry
        assert entry["diff"]["list_price"]["current"] == Decimal("10.00")
        assert entry["diff"]["list_price"]["incoming"] == Decimal("25.50")

        row = env.row("products", product_id)
        assert row["list_price"] == Decimal("10.00")
        rows = env.ref_rows("products", product_id)
        assert len(rows) == 1
        assert rows[0]["source_ref"] == ref

    def test_cw3_unlinked_product_is_adopted_by_code_and_not_linked(self, env):
        product_id, code = env.product()
        code_ref = _ref("BRA2")

        res = env.ingest(
            "products", [{"source_ref": code_ref, "code": code, "name": "Item"}]
        )

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        assert entry["entity_id"] == product_id, entry
        assert "ref_mismatch" not in entry.get("warnings", []), entry
        # Ingest never writes a product reference (owner ruling 2, 3 Oct).
        assert env.ref_rows("products", product_id) == []

    def test_cw4_ref_hit_updates_without_warning(self, env):
        ref, product_id, code = env.linked_product()

        res = env.ingest(
            "products", [{"source_ref": ref, "code": code, "name": "Item", "list_price": "12.00"}]
        )

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        assert entry["entity_id"] == product_id
        assert "ref_mismatch" not in entry.get("warnings", []), entry
        row = env.row("products", product_id)
        assert row["list_price"] == Decimal("12.00")

    def test_cw5_second_push_is_idempotent(self, env):
        ref, product_id, code = env.linked_product()
        code_ref = _ref("BRA5")
        record = {"source_ref": code_ref, "code": code, "name": "Item", "list_price": "30.00"}

        first = env.ingest("products", [record])
        second = env.ingest("products", [record])

        assert first.status_code == 200 and second.status_code == 200
        e1, e2 = first.json()["records"][0], second.json()["records"][0]
        assert e1["outcome"] == "updated" == e2["outcome"], (e1, e2)
        rows = env.ref_rows("products", product_id)
        assert len(rows) == 1
        assert rows[0]["source_ref"] == ref

    def test_cw6_code_match_is_case_and_edge_whitespace_insensitive(self, env):
        ref, product_id, code = env.linked_product(code="BRA-1")
        code_ref = _ref("BRA6")

        res = env.ingest(
            "products", [{"source_ref": code_ref, "code": "bra-1 ", "name": "Item"}]
        )

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        assert entry["entity_id"] == product_id

    def test_cw7_other_source_system_ref_no_longer_conflicts_for_products(self, env):
        # Was: another source system's ref on the code match refused the push.
        # Products match by code only now, so the ref is irrelevant and stays
        # exactly as it was.
        ref, product_id, code = env.linked_product(source_system="othersys")
        code_ref = _ref("BRA7")

        res = env.ingest("products", [{"source_ref": code_ref, "code": code, "name": "Item"}])

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        assert entry["entity_id"] == product_id, entry
        rows = env.ref_rows("products", product_id)
        assert len(rows) == 1
        assert rows[0]["source_ref"] == ref
        assert rows[0]["source_system"] == "othersys"

    @pytest.mark.parametrize("entity_type", sorted(_OTHER_MASTERS))
    def test_cw8_other_masters_still_conflict(self, env, entity_type):
        entity_id, code = env.other_master(entity_type)
        old_ref = env.link(entity_type, entity_id, stem="OLD")
        new_ref = _ref("NEW")

        res = env.ingest(entity_type, [{"source_ref": new_ref, "code": code, "name": "Renamed"}])

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", (entity_type, entry)
        assert "source_ref" in entry.get("errors", {}), (entity_type, entry)
        rows = env.ref_rows(entity_type, entity_id)
        assert len(rows) == 1
        assert rows[0]["source_ref"] == old_ref

    def test_cw9_other_company_product_is_never_touched(self, env):
        shared_code = unique_code(MARKER)
        product_a, _ = env.product(code=shared_code, company_id=env.company_a)
        ref_b, product_b, _ = env.linked_product(code=shared_code, company_id=env.company_b)
        push_ref = _ref("ISO")

        res = env.ingest(
            "products",
            [{"source_ref": push_ref, "code": shared_code, "name": "Item"}],
            company_id=env.company_a,
        )

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        assert entry["entity_id"] == product_a, entry

        assert env.ref_rows("products", product_a) == []

        row_b = env.row("products", product_b)
        assert row_b["product_code"] == shared_code
        rows_b = env.ref_rows("products", product_b)
        assert len(rows_b) == 1
        assert rows_b[0]["source_ref"] == ref_b


# ============================================================ deletions (DL)
class TestCodeWinsDeletions:
    def test_dl1_code_fallback_deactivates_referenced_product(self, env):
        ref, product_id, code = env.linked_product()
        env.sales_order_line(product_id)
        code_ref = _ref("BRADL1")

        res = env.delete("products", [code_ref], codes={code_ref: code})

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "deactivated", entry
        row = env.row("products", product_id)
        assert row["is_discontinued"] is True
        assert row["is_active"] is True
        rows = env.ref_rows("products", product_id)
        assert len(rows) == 1
        assert rows[0]["source_ref"] == ref

    def test_dl2_code_fallback_hard_deletes_unreferenced_product(self, env):
        ref, product_id, code = env.linked_product()
        code_ref = _ref("BRADL2")

        res = env.delete("products", [code_ref], codes={code_ref: code})

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "deleted", entry
        assert env.row("products", product_id) is None
        assert env.ref_owner(ref) is None

    def test_dl3_ref_miss_without_codes_is_not_found(self, env):
        ref, product_id, code = env.linked_product()
        code_ref = _ref("BRADL3")

        no_codes_at_all = env.delete("products", [code_ref])
        assert no_codes_at_all.json()["records"][0]["outcome"] == "not_found"

        no_entry_for_ref = env.delete("products", [code_ref], codes={_ref("OTHER"): code})
        assert no_entry_for_ref.json()["records"][0]["outcome"] == "not_found"

        assert env.row("products", product_id) is not None

    def test_dl4_codes_decide_and_the_ref_holder_is_untouched(self, env):
        # Was: a ref hit ignored `codes`. Product deletes resolve by code only
        # now (owner ruling 1, 3 Oct): the ref's holder is never the target.
        ref, product_id, code = env.linked_product()
        other_id, other_code = env.product()

        res = env.delete("products", [ref], codes={ref: other_code})

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] in ("deleted", "deactivated"), entry
        assert entry["entity_id"] == other_id, entry
        holder = env.row("products", product_id)
        assert holder is not None
        assert holder["product_code"] == code

    def test_dl5_codes_ignored_for_non_product_entities(self, env):
        entity_id, code = env.other_master("suppliers")
        ref = env.link("suppliers", entity_id, stem="SUP")
        code_ref = _ref("SUPDL5")

        res = env.delete("suppliers", [code_ref], codes={code_ref: code})

        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "not_found"
        row = env.row("suppliers", entity_id)
        assert row is not None
        assert row["is_active"] is True

    def test_dl6_other_source_system_ref_does_not_block_a_code_match(self, env):
        # Product deletes resolve by code only (AC-12): the ref, whatever source
        # system it sits under, is irrelevant.
        ref, product_id, code = env.linked_product(source_system="othersys")
        code_ref = _ref("BRADL6")

        res = env.delete("products", [code_ref], codes={code_ref: code})

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] in ("deleted", "deactivated"), entry
        assert entry["entity_id"] == product_id, entry

    def test_dl7_other_company_product_is_not_found(self, env):
        ref_b, product_b, code_b = env.linked_product(company_id=env.company_b)
        code_ref = _ref("BRADL7")

        res = env.delete(
            "products", [code_ref], codes={code_ref: code_b}, company_id=env.company_a
        )

        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "not_found"
        row = env.row("products", product_b)
        assert row is not None
        assert row["product_code"] == code_b

    def test_dl8_dry_run_reports_and_persists_nothing(self, env):
        ref, product_id, code = env.linked_product()
        env.sales_order_line(product_id)
        code_ref = _ref("BRADL8")

        res = env.delete("products", [code_ref], codes={code_ref: code}, dry_run=True)

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["dry_run"] is True
        entry = body["records"][0]
        assert entry["outcome"] == "deactivated", entry

        row = env.row("products", product_id)
        assert row["is_discontinued"] is False
        rows = env.ref_rows("products", product_id)
        assert len(rows) == 1
        assert rows[0]["source_ref"] == ref

    def test_dl9_malformed_codes_is_422_and_deletes_nothing(self, env):
        ref, product_id, code = env.linked_product()
        code_ref = _ref("BRADL9")

        as_list = env.delete(
            "products",
            [],
            body={
                "companyCode": env.company_a_code,
                "source_refs": [code_ref],
                "codes": [code_ref, code],
            },
        )
        assert as_list.status_code == 422, as_list.text
        assert as_list.json()["code"] == "INVALID_BODY"

        non_string_value = env.delete(
            "products",
            [],
            body={
                "companyCode": env.company_a_code,
                "source_refs": [code_ref],
                "codes": {code_ref: 123},
            },
        )
        assert non_string_value.status_code == 422, non_string_value.text
        assert non_string_value.json()["code"] == "INVALID_BODY"

        assert env.row("products", product_id) is not None

        # A codes entry naming a ref that was never sent in source_refs is
        # simply ignored - the product it names is untouched.
        untouched_id, untouched_code = env.product()
        unrelated_ref = _ref("UNRELATED")
        res = env.delete(
            "products", [code_ref], codes={unrelated_ref: untouched_code}
        )
        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "not_found"
        assert env.row("products", untouched_id) is not None

    def test_dl10_unlinked_product_is_deleted_by_code(self, env):
        # Fix round 1, PIN (owner-approved contract A9 item 3): the code rung
        # proceeds when the matched product is unlinked, not only when its
        # own reference is under the same source system - already the
        # behaviour, pinned here as its own scenario.
        product_id, code = env.product()
        code_ref = _ref("BRADL10")

        res = env.delete("products", [code_ref], codes={code_ref: code})

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "deleted", entry
        assert env.row("products", product_id) is None

    def test_dl11_unlinked_product_with_dependents_is_deactivated(self, env):
        product_id, code = env.product()
        env.sales_order_line(product_id)
        code_ref = _ref("BRADL11")

        res = env.delete("products", [code_ref], codes={code_ref: code})

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "deactivated", entry
        row = env.row("products", product_id)
        assert row is not None
        assert row["is_discontinued"] is True
        assert row["is_active"] is True

    def test_dl12_oversized_codes_is_refused_and_deletes_nothing(self, env):
        # Fix round 1: `codes` gets the SAME batch cap `source_refs` already
        # has, refused with the same status/code/shape.
        ref, product_id, code = env.linked_product()
        code_ref = _ref("BRADL12")
        oversized_codes = {f"{MARKER}:X:{i}": "X" for i in range(1001)}

        res = env.delete("products", [code_ref], codes=oversized_codes)

        assert res.status_code == 413, res.text
        assert res.json()["code"] == "BATCH_TOO_LARGE"
        assert env.row("products", product_id) is not None

    def test_dl13_code_rung_delete_is_traced(self, env, caplog):
        # Fix round 1: one INFO line per record the code rung actually
        # resolved and deleted/deactivated - a security-review trace item,
        # not asserted anywhere else in this file.
        product_id, code = env.product()
        code_ref = _ref("BRADL13")

        with caplog.at_level(logging.INFO, logger="app.services.deletion_service"):
            res = env.delete("products", [code_ref], codes={code_ref: code})

        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "deleted"
        messages = [record.getMessage() for record in caplog.records]
        assert any("deletion.code_rung" in m and code_ref in m and code in m for m in messages), (
            messages
        )


# =================================================================== contract
class TestContractV24:
    def test_ct1_contract_reports_2_4_with_codes_and_notes(self, env):
        """Bumped again (ingest-stock-balances-2-5, Foundryx SR5) to "2.5" -
        unrelated to the products code-wins `codes` shape this test pins,
        only the version literal needed to move."""
        res = env.client.get("/api/v1/external/contract")

        assert res.status_code == 200, res.text
        body = res.json()
        # Bumped again (finance S0, #1309): "2.6" adds `billing_documents`.
        # Bumped again (#1354 S2): "2.7" adds `delivery_orders`, `goods_receive_notes`, `branches`.
        assert body["version"] == "2.7", body["version"]

        fields_added = body.get("fields_added", {})
        products_deletion_fields = fields_added.get("products_deletions") or fields_added.get(
            "products"
        )
        # `codes` is documented somewhere under fields_added for the products
        # deletion shape - the exact key is the coder's to choose, so this
        # asserts on the stable substring rather than one fixed dict path.
        assert any(
            "codes" in v if isinstance(v, list) else "codes" in str(v)
            for v in fields_added.values()
        ), fields_added

        field_notes = body.get("field_notes", {})
        notes_text = " ".join(str(v) for v in field_notes.values())
        assert "matched by company and code" in notes_text, field_notes
        assert "codes" in notes_text or "code" in notes_text.lower(), field_notes
