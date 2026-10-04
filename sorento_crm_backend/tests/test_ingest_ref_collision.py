"""Fix round 2, BUG A - a code/name rung resolving to an ALREADY-linked row.

Reported from the live ESB run against :8042: a line sends `product_ref
"ac_sim:174"` + `product_code`; the ref misses (it was never registered), the
code rung resolves an existing product X, and the ladder then tried to link X
under the new ref - but X is ALREADY linked under a different source_ref from
the masters push, so the INSERT hit `uq_integration_ref_entity` and the whole
record failed with an internal error.

Two layers, one test each, plus the field-level tests (a)/(b) from the
coordinator's brief:

  (a) SO line, product_ref + product_code, product already linked elsewhere
  (b) PO header, supplier_ref + supplier_code, same shape
  (c) `IntegrationReferenceService.link()` unit test: a second ref for one
      entity raises `ReferenceConflict`, never lets the `IntegrityError`
      escape

No new substrate - reuses `env` from `tests.test_ingest_documents`, the same
convention every other ingest test file follows.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services.integration_reference_service import ReferenceConflict
from app.services.master_ref_resolver import WARN_REF_MISMATCH

from tests.test_ingest_documents import (
    INGEST_PO,
    INGEST_SO,
    _po_line,
    _po_record,
    _ref,
    _so_line,
    _so_record,
    env,  # noqa: F401 - pytest fixture, imported for reuse
)

__all__ = ["env"]


def _product_code(env, product_ref: str) -> str:
    product_id = env.refs.resolve(entity_type="products", source_ref=product_ref)
    return env.db.execute(
        text("SELECT product_code FROM products WHERE id = :id"), {"id": product_id}
    ).scalar()


def _supplier_code(env, supplier_ref: str) -> str:
    supplier_id = env.refs.resolve(entity_type="suppliers", source_ref=supplier_ref)
    return env.db.execute(
        text("SELECT supplier_code FROM suppliers WHERE id = :id"), {"id": supplier_id}
    ).scalar()


def _reference_rows(env, entity_type: str, entity_id: str):
    return (
        env.db.execute(
            text(
                "SELECT source_ref FROM integration_references "
                "WHERE entity_type = :t AND entity_id = :i"
            ),
            {"t": entity_type, "i": entity_id},
        )
        .scalars()
        .all()
    )


# ============================================================ (a) product/SO
class TestProductRefMismatchOnASalesOrderLine:
    def test_a_new_ref_alongside_the_linked_products_own_code_is_not_relinked(
        self, env
    ):
        product_id = env.refs.resolve(
            entity_type="products", source_ref=env.product_ref
        )
        code = _product_code(env, env.product_ref)

        line = _so_line(env, product_ref="ac_sim:174", product_code=code)
        record = _so_record(env, lines=[line])

        res = env.post(INGEST_SO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        # Products match by code only (owner rulings, 3 Oct): no ref_mismatch
        # for products, and the unknown ref is never linked.

        header = env.header("sales_orders", record["source_ref"])
        line_row = env.so_lines(header["id"])[0]
        assert str(line_row["product_id"]) == str(product_id)

        rows = _reference_rows(env, "products", product_id)
        assert rows == [env.product_ref]


# ============================================================= (b) supplier/PO
class TestSupplierRefMismatchOnAPurchaseOrder:
    def test_a_new_ref_alongside_the_linked_suppliers_own_code_is_not_relinked(
        self, env
    ):
        supplier_id = env.refs.resolve(
            entity_type="suppliers", source_ref=env.supplier_ref
        )
        code = _supplier_code(env, env.supplier_ref)

        record = _po_record(
            env, supplier_ref="ac_sim:174", supplier_code=code
        )

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert WARN_REF_MISMATCH in entry.get("warnings", []), entry

        header = env.header("purchase_orders", record["source_ref"])
        assert str(header["supplier_id"]) == str(supplier_id)

        rows = _reference_rows(env, "suppliers", supplier_id)
        assert rows == [env.supplier_ref]


# ==================================================== (c) link() unit test
class TestLinkRaisesReferenceConflictNotIntegrityError:
    def test_a_second_ref_for_one_entity_raises_reference_conflict(self, env):
        product_id = env.refs.resolve(
            entity_type="products", source_ref=env.product_ref
        )

        with pytest.raises(ReferenceConflict):
            env.refs.link(
                entity_type="products",
                entity_id=product_id,
                source_ref=_ref("OTHER"),
            )

        # The failed attempt left the ORIGINAL mapping exactly as it was -
        # `link()` raised before ever touching the session's pending writes.
        rows = _reference_rows(env, "products", product_id)
        assert rows == [env.product_ref]
