"""RED tests for PRODUCT-REF-COLLISION (code only, owner rulings 1 + 2, 3 Oct), AC-1..AC-11.

UAC: documentation/plans/autocount/product-ref-collision-acceptance-criteria.md

Product identity is company + normalised product code, for the feed and for every
document line. Product rows in integration_references are never read for matching and
never written by ingest. Billing documents are covered in
tests/test_product_ref_collision_billing.py (that suite has its own env).

Substrate: the `env` fixture of tests.test_ingest_documents (blank Postgres schema,
real routes). Every scenario seeds its own products.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.product import Product
from tests.test_ingest_documents import (
    INGEST_PO,
    INGEST_SO,
    MARKER,
    _po_line,
    _po_record,
    _so_line,
    _so_record,
    env,  # noqa: F401 - pytest fixture, imported for reuse
)
from tests.test_ingest_shipping_orders import INGEST_SPO, _spo_line, _spo_record, _spo_rows

__all__ = ["env"]

BOOK = "AED_V2_MOCHA"
FOREIGN_CODE = "MKT4524SS-DIY"
INGEST_PRODUCTS = "/api/v1/external/ingest/products"


# ------------------------------------------------------------------ helpers
def _product(env, code: str, *, list_price=Decimal("10.00")) -> str:
    row = Product(
        product_code=code,
        product_name=code,
        category_id=env._category.id,
        base_uom_id=env._uom.id,
        list_price=list_price,
        company_id=env.company_a,
    )
    env.db.add(row)
    env.db.flush()
    env.db.commit()
    return str(row.id)


def _link(env, product_id: str, ref: str) -> None:
    env.refs.link(entity_type="products", entity_id=product_id, source_ref=ref)
    env.db.commit()


def _row(env, product_id: str):
    env.db.expire_all()
    return env.db.execute(
        text("SELECT * FROM products WHERE id = :i"), {"i": product_id}
    ).mappings().first()


def _snapshot(env, product_id: str) -> dict:
    return dict(_row(env, product_id))


def _refs_of(env, product_id: str) -> list[str]:
    return list(
        env.db.execute(
            text(
                "SELECT source_ref FROM integration_references "
                "WHERE entity_type = 'products' AND entity_id = :i"
            ),
            {"i": product_id},
        ).scalars()
    )


def _owner_of(env, ref: str):
    v = env.db.execute(
        text(
            "SELECT entity_id FROM integration_references "
            "WHERE source_ref = :r AND entity_type = 'products'"
        ),
        {"r": ref},
    ).scalar()
    return str(v) if v else None


def _count_code(env, code: str) -> int:
    return env.db.execute(
        text("SELECT count(*) FROM products WHERE product_code = :c AND company_id = :co"),
        {"c": code, "co": env.company_a},
    ).scalar()


def _push(env, records, *, dry_run=False):
    res = env.post(INGEST_PRODUCTS, records, dry_run=dry_run)
    assert res.status_code == 200, res.text
    return res.json()["records"]


def _foreign_setup(env):
    """MKT4524SS-DIY holds ref BOOK:2001; product '2001' exists with no ref."""
    ref = f"{BOOK}:2001"
    foreign_id = _product(env, FOREIGN_CODE)
    _link(env, foreign_id, ref)
    target_id = _product(env, "2001")
    return ref, foreign_id, target_id


def _ref_rows(env) -> list[tuple]:
    """Every product row in integration_references, comparable before/after."""
    env.db.expire_all()
    return sorted(
        tuple(str(c) for c in r)
        for r in env.db.execute(
            text(
                "SELECT source_ref, entity_id, source_system FROM integration_references "
                "WHERE entity_type = 'products'"
            )
        ).all()
    )


def _delete_all_product_refs(env) -> None:
    env.db.execute(text("DELETE FROM integration_references WHERE entity_type = 'products'"))
    env.db.commit()


CAT = {"category_code": "ZZPRC-CAT"}


# ------------------------------------------------------------- product feed
class TestFeedPush:
    def test_ac1_foreign_ref_updates_the_code_owner_not_the_ref_holder(self, env):
        ref, foreign_id, target_id = _foreign_setup(env)
        before = _snapshot(env, foreign_id)

        (entry,) = _push(env, [{"source_ref": ref, "code": "2001", "name": "Two Thousand One", "list_price": "42.00"}])

        assert entry["outcome"] == "updated", entry
        assert entry["entity_id"] == target_id, entry
        assert _row(env, target_id)["list_price"] == Decimal("42.00")
        assert _snapshot(env, foreign_id) == before

    def test_ac1_dry_run_reports_no_failure(self, env):
        ref, foreign_id, target_id = _foreign_setup(env)
        before = _snapshot(env, foreign_id)

        (entry,) = _push(
            env,
            [{"source_ref": ref, "code": "2001", "name": "Two Thousand One", "list_price": "42.00"}],
            dry_run=True,
        )

        assert entry["outcome"] == "updated", entry
        assert entry["entity_id"] == target_id, entry
        assert _snapshot(env, foreign_id) == before
        assert _row(env, target_id)["list_price"] == Decimal("10.00")

    def test_ac2_foreign_ref_with_no_code_owner_creates_a_new_product(self, env):
        ref = f"{BOOK}:2004"
        foreign_id = _product(env, "MKT4528ASS-DIY")
        _link(env, foreign_id, ref)
        before = _snapshot(env, foreign_id)
        assert _count_code(env, "2004") == 0

        (entry,) = _push(env, [{"source_ref": ref, "code": "2004", "name": "Two Thousand Four", **CAT}])

        assert entry["outcome"] == "created", entry
        assert entry["entity_id"] and entry["entity_id"] != foreign_id, entry
        assert _count_code(env, "2004") == 1
        assert _snapshot(env, foreign_id) == before

    def test_ac3_feed_writes_no_product_ref_on_create_update_or_adopt(self, env):
        linked_id = _product(env, "2001")
        _link(env, linked_id, f"{BOOK}:99001")
        unlinked_id = _product(env, "2002")
        baseline = _ref_rows(env)

        created, updated_linked, adopted = _push(
            env,
            [
                {"source_ref": f"{BOOK}:2004", "code": "2004", "name": "New", **CAT},
                {"source_ref": f"{BOOK}:2001", "code": "2001", "name": "Linked", "list_price": "11.00"},
                {"source_ref": f"{BOOK}:2002", "code": "2002", "name": "Unlinked", "list_price": "12.00"},
            ],
        )

        assert created["outcome"] == "created", created
        assert updated_linked["entity_id"] == linked_id
        assert adopted["entity_id"] == unlinked_id
        assert _ref_rows(env) == baseline

    def test_ac4_code_owner_is_updated_whatever_ref_it_holds_and_keeps_its_stored_ref(self, env):
        stored = f"{BOOK}:99001"
        product_id = _product(env, "2001")
        _link(env, product_id, stored)
        before_rows = _ref_rows(env)

        (entry,) = _push(
            env,
            [{"source_ref": f"{BOOK}:2001", "code": "2001", "name": "Kept", "list_price": "13.00"}],
        )

        assert entry["outcome"] == "updated", entry
        assert entry["entity_id"] == product_id
        assert _row(env, product_id)["list_price"] == Decimal("13.00")
        assert _ref_rows(env) == before_rows
        assert _refs_of(env, product_id) == [stored]

    def test_ac9_feed_code_comparison_ignores_case_and_edge_whitespace(self, env):
        product_id = _product(env, FOREIGN_CODE)

        (entry,) = _push(
            env,
            [{"source_ref": f"{BOOK}:2001", "code": " mkt4524ss-diy ", "name": "Same", "list_price": "14.00"}],
        )

        assert entry["outcome"] == "updated", entry
        assert entry["entity_id"] == product_id
        assert _row(env, product_id)["list_price"] == Decimal("14.00")

    def test_ac10_two_records_in_one_batch_hit_foreign_refs(self, env):
        ref, foreign_id, target_id = _foreign_setup(env)
        ref2 = f"{BOOK}:2004"
        foreign2_id = _product(env, "MKT4528ASS-DIY")
        _link(env, foreign2_id, ref2)
        before1, before2 = _snapshot(env, foreign_id), _snapshot(env, foreign2_id)

        entries = _push(
            env,
            [
                {"source_ref": ref, "code": "2001", "name": "Batch One", "list_price": "15.00"},
                {"source_ref": ref2, "code": "2004", "name": "Batch Four", **CAT},
            ],
        )

        assert [e["outcome"] for e in entries] == ["updated", "created"], entries
        assert entries[0]["entity_id"] == target_id, entries
        assert entries[1]["entity_id"] not in (foreign_id, foreign2_id), entries
        assert _snapshot(env, foreign_id) == before1
        assert _snapshot(env, foreign2_id) == before2

    def test_ac11_feed_behaves_the_same_with_every_product_ref_deleted(self, env):
        _, foreign_id, target_id = _foreign_setup(env)
        _delete_all_product_refs(env)
        before = _snapshot(env, foreign_id)

        updated, created = _push(
            env,
            [
                {"source_ref": f"{BOOK}:2001", "code": "2001", "name": "Two", "list_price": "16.00"},
                {"source_ref": f"{BOOK}:2004", "code": "2004", "name": "Four", **CAT},
            ],
        )

        assert updated["outcome"] == "updated" and updated["entity_id"] == target_id, updated
        assert created["outcome"] == "created", created
        assert _snapshot(env, foreign_id) == before
        assert _ref_rows(env) == []


# -------------------------------------------------------------- doc lines
def _good_line_product(env, code="ZZPRC-GOOD") -> str:
    return _product(env, code)


def _so_post(env, bad_line: dict):
    good = {"source_ref": f"{BOOK}:L0", "product_code": "ZZPRC-GOOD", "qty_ordered": 1}
    bad = {"source_ref": f"{BOOK}:L1", "qty_ordered": 3, **bad_line}
    record = _so_record(env, lines=[good, bad])
    res = env.post(INGEST_SO, [record])
    assert res.status_code == 200, res.text
    return res.json()["records"][0], record


def _so_bound_ids(env, record) -> list[str]:
    header = env.header("sales_orders", record["source_ref"])
    return [str(l["product_id"]) for l in env.so_lines(header["id"])]


class TestSalesOrderLine:
    def test_ac5_code_owned_by_b_binds_b_whatever_the_ref_names(self, env):
        _good_line_product(env)
        a_id = _product(env, FOREIGN_CODE)
        _link(env, a_id, f"{BOOK}:2001")
        b_id = _product(env, "2001")
        before_a = _snapshot(env, a_id)

        entry, record = _so_post(env, {"product_ref": f"{BOOK}:2001", "product_code": "2001"})

        assert entry["outcome"] == "created", entry
        assert entry["lines"].get("dropped", 0) == 0, entry
        assert b_id in _so_bound_ids(env, record)
        assert a_id not in _so_bound_ids(env, record)
        assert _snapshot(env, a_id) == before_a

    def test_ac5_agreeing_ref_and_code_binds_the_product(self, env):
        _good_line_product(env)
        pid = _product(env, FOREIGN_CODE)
        _link(env, pid, f"{BOOK}:2001")

        entry, record = _so_post(env, {"product_ref": f"{BOOK}:2001", "product_code": FOREIGN_CODE})

        assert entry["outcome"] == "created", entry
        assert pid in _so_bound_ids(env, record)

    def test_ac6_code_owned_by_nobody_drops_the_line_not_binding_the_ref_holder(self, env):
        good_id = _good_line_product(env)
        a_id = _product(env, "MKT4528ASS-DIY")
        _link(env, a_id, f"{BOOK}:2004")
        assert _count_code(env, "2004") == 0

        entry, record = _so_post(env, {"product_ref": f"{BOOK}:2004", "product_code": "2004"})

        assert entry["outcome"] == "created", entry
        assert entry["lines"].get("dropped") == 1, entry
        assert _so_bound_ids(env, record) == [good_id]

    def test_ac7_ref_only_line_takes_the_unknown_product_path(self, env):
        good_id = _good_line_product(env)
        a_id = _product(env, FOREIGN_CODE)
        _link(env, a_id, f"{BOOK}:2001")

        entry, record = _so_post(env, {"product_ref": f"{BOOK}:2001"})

        assert entry["outcome"] == "created", entry
        assert entry["lines"].get("dropped") == 1, entry
        assert _so_bound_ids(env, record) == [good_id]

    def test_ac8_so_line_push_writes_no_product_ref_even_binding_an_unlinked_product(self, env):
        _good_line_product(env)
        b_id = _product(env, "2001")
        assert _refs_of(env, b_id) == []
        baseline = _ref_rows(env)

        entry, record = _so_post(env, {"product_ref": f"{BOOK}:2001", "product_code": "2001"})

        assert entry["outcome"] == "created", entry
        assert b_id in _so_bound_ids(env, record)
        assert _ref_rows(env) == baseline

    def test_ac9_line_code_comparison_ignores_case_and_whitespace(self, env):
        _good_line_product(env)
        pid = _product(env, FOREIGN_CODE)

        entry, record = _so_post(env, {"product_code": " mkt4524ss-diy "})

        assert entry["outcome"] == "created", entry
        assert pid in _so_bound_ids(env, record)

    def test_ac11_so_line_behaves_the_same_with_every_product_ref_deleted(self, env):
        _good_line_product(env)
        a_id = _product(env, FOREIGN_CODE)
        _link(env, a_id, f"{BOOK}:2001")
        b_id = _product(env, "2001")
        _delete_all_product_refs(env)

        entry, record = _so_post(env, {"product_ref": f"{BOOK}:2001", "product_code": "2001"})

        assert entry["outcome"] == "created", entry
        assert b_id in _so_bound_ids(env, record)
        assert a_id not in _so_bound_ids(env, record)
        assert _ref_rows(env) == []


# -------------------------------------------------------------- PO line
def _po_post(env, bad_line: dict):
    good = {"source_ref": f"{BOOK}:PL0", "product_code": "ZZPRC-GOOD", "qty_ordered": 1}
    bad = {"source_ref": f"{BOOK}:PL1", "qty_ordered": 4, **bad_line}
    record = _po_record(env, lines=[good, bad], supplier_ref=env.supplier_ref)
    res = env.post(INGEST_PO, [record])
    assert res.status_code == 200, res.text
    return res.json()["records"][0], record


def _po_bound_ids(env, record) -> list[str]:
    header = env.header("purchase_orders", record["source_ref"])
    return [str(l["product_id"]) for l in env.po_lines(header["id"])]


class TestPurchaseOrderLine:
    def test_ac5_code_owned_by_b_binds_b_whatever_the_ref_names(self, env):
        _good_line_product(env)
        a_id = _product(env, FOREIGN_CODE)
        _link(env, a_id, f"{BOOK}:2001")
        b_id = _product(env, "2001")
        baseline = _ref_rows(env)

        entry, record = _po_post(env, {"product_ref": f"{BOOK}:2001", "product_code": "2001"})

        assert entry["outcome"] == "created", entry
        assert entry["lines"].get("dropped", 0) == 0, entry
        assert b_id in _po_bound_ids(env, record)
        assert a_id not in _po_bound_ids(env, record)
        assert _ref_rows(env) == baseline

    def test_ac6_code_owned_by_nobody_drops_the_line(self, env):
        good_id = _good_line_product(env)
        a_id = _product(env, "MKT4528ASS-DIY")
        _link(env, a_id, f"{BOOK}:2004")

        entry, record = _po_post(env, {"product_ref": f"{BOOK}:2004", "product_code": "2004"})

        assert entry["outcome"] == "created", entry
        assert entry["lines"].get("dropped") == 1, entry
        assert _po_bound_ids(env, record) == [good_id]

    def test_ac7_ref_only_line_takes_the_unknown_product_path(self, env):
        good_id = _good_line_product(env)
        a_id = _product(env, FOREIGN_CODE)
        _link(env, a_id, f"{BOOK}:2001")

        entry, record = _po_post(env, {"product_ref": f"{BOOK}:2001"})

        assert entry["outcome"] == "created", entry
        assert entry["lines"].get("dropped") == 1, entry
        assert _po_bound_ids(env, record) == [good_id]


# -------------------------------------------------------------- SPO line
def _spo_post(env, line_fields: dict):
    line = {"source_ref": f"{BOOK}:SL1", "qty_ordered": 10, **line_fields}
    record = _spo_record(env, lines=[line], supplier_ref=env.supplier_ref)
    res = env.post(INGEST_SPO, [record])
    assert res.status_code == 200, res.text
    return res.json()["records"][0], record


class TestShippingOrderLine:
    def test_ac5_code_owned_by_b_binds_b_whatever_the_ref_names(self, env):
        a_id = _product(env, FOREIGN_CODE)
        _link(env, a_id, f"{BOOK}:2001")
        b_id = _product(env, "2001")
        baseline = _ref_rows(env)

        entry, record = _spo_post(env, {"product_ref": f"{BOOK}:2001", "product_code": "2001"})

        assert entry["outcome"] == "created", entry
        rows = _spo_rows(env, record["spo_number"])
        assert [str(r["product_id"]) for r in rows] == [b_id]
        assert _ref_rows(env) == baseline

    def test_ac6_code_owned_by_nobody_is_retryable_on_the_product_code(self, env):
        a_id = _product(env, "MKT4528ASS-DIY")
        _link(env, a_id, f"{BOOK}:2004")

        entry, record = _spo_post(env, {"product_ref": f"{BOOK}:2004", "product_code": "2004"})

        assert entry["outcome"] == "retryable", entry
        assert "lines.0.product_code" in entry.get("errors", {}), entry
        assert _spo_rows(env, record["spo_number"]) == []

    def test_ac7_ref_only_line_is_retryable_never_the_ref_holder(self, env):
        a_id = _product(env, FOREIGN_CODE)
        _link(env, a_id, f"{BOOK}:2001")

        entry, record = _spo_post(env, {"product_ref": f"{BOOK}:2001"})

        assert entry["outcome"] == "retryable", entry
        assert _spo_rows(env, record["spo_number"]) == []


# ------------------------------------------------------- AC-12 deletions
def _delete_products(env, source_refs, codes=None):
    body = {"companyCode": env.company_a_code, "source_refs": source_refs}
    if codes is not None:
        body["codes"] = codes
    res = env.client.post(f"{INGEST_PRODUCTS}/deletions", json=body)
    assert res.status_code == 200, res.text
    return res.json()["records"][0]


class TestProductDeletion:
    def test_ac12_delete_resolves_by_code_never_by_the_ref_holder(self, env):
        ref, foreign_id, target_id = _foreign_setup(env)
        before = _snapshot(env, foreign_id)

        entry = _delete_products(env, [ref], codes={ref: "2001"})

        assert entry["outcome"] in ("deleted", "deactivated"), entry
        assert entry["entity_id"] == target_id, entry
        assert _count_code(env, "2001") == 0 or _row(env, target_id)["is_discontinued"] is True
        assert _snapshot(env, foreign_id) == before
        assert _owner_of(env, ref) == foreign_id

    def test_ac12_delete_without_a_code_is_not_found_and_touches_nothing(self, env):
        ref, foreign_id, target_id = _foreign_setup(env)
        before_foreign, before_target = _snapshot(env, foreign_id), _snapshot(env, target_id)

        entry = _delete_products(env, [ref])

        assert entry["outcome"] == "not_found", entry
        assert _snapshot(env, foreign_id) == before_foreign
        assert _snapshot(env, target_id) == before_target


# ------------------------------------------------- plan-exception snapshot
class TestPlanExceptionBeforeSnapshot:
    def test_snapshot_covers_the_code_owner_not_the_ref_holder(self, env, monkeypatch):
        import app.services.scm.plan_exception_service as pes

        a_id = _product(env, FOREIGN_CODE)
        _link(env, a_id, f"{BOOK}:2001")
        b_id = _product(env, "2001")
        seen: list[set[str]] = []
        real = pes.snapshot

        def _spy(db, product_ids, *args, **kwargs):
            seen.append({str(p) for p in product_ids})
            return real(db, product_ids, *args, **kwargs)

        monkeypatch.setattr(pes, "snapshot", _spy)

        entry, _record = _so_post_single(env, product_ref=f"{BOOK}:2001", product_code="2001")

        assert entry["outcome"] == "created", entry
        before = set().union(*seen) if seen else set()
        assert b_id in before, seen
        assert a_id not in before, seen


def _so_post_single(env, **line_fields):
    line = {"source_ref": f"{BOOK}:L9", "qty_ordered": 3, **line_fields}
    record = _so_record(env, lines=[line])
    res = env.post(INGEST_SO, [record])
    assert res.status_code == 200, res.text
    return res.json()["records"][0], record
