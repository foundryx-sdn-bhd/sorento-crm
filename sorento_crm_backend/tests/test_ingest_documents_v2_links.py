"""Group V4 - SO to PO dedication via `from_so_numbers` (PLAN section 2.5).

  AC-V4-1   a PO line's `from_so_numbers` -> one `order_link_claim` per number,
            source 'autocount', item_code = the resolved product's product_code,
            po_line_id = the written line; `resolve()` runs so a matching SO
            claim resolves in the same call, a missing one stays open
  AC-V4-2   re-push is idempotent (no duplicate claims); a claim another
            source already holds at the same identity is left alone
  AC-V4-4   dry_run writes no claim
  (AC-V4-3, the CHECK constraint admitting 'autocount', landed in S0 - the
  model already lists it in `app/models/scm.py`.)

Plus the SPO-line variant of AC-V4-1: `CanonicalShippingOrderLine` carries the
same `from_so_numbers` field (S4), and `ShippingOrderIngestService` writes the
claim the same way `DocumentIngestService` does for a purchase-order line -
its former `xfail(strict=False)` placeholder is gone now that both land in
this slice.

Substrate reused byte for byte from `test_ingest_documents` per the tester
brief; no new fixture.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.models.order import SalesOrder, SalesOrderLine
from app.models.scm import OrderLinkClaim

from tests.test_ingest_documents import (
    INGEST_PO,
    MARKER,
    _po_line,
    _po_record,
    _ref,
    env,  # noqa: F401 - pytest fixture, imported for reuse per the tester brief
)

__all__ = ["env"]

INGEST_SPO = "/api/v1/external/ingest/shipping_orders"


# ------------------------------------------------------------------ seed helpers
def _seed_plain_so(env, *, so_number: str, product_id: str, qty=10) -> SalesOrder:
    """A sales order + one line for `product_id`, bypassing ingest entirely.

    Plain ORM rows, not a push - AC-V4-1 is about the resolver finding a sales
    order that already exists locally, independent of how it got there.
    """
    so = SalesOrder(so_number=so_number, status="open", company_id=env.company_a)
    env.db.add(so)
    env.db.flush()
    line = SalesOrderLine(
        sales_order_id=so.id,
        product_id=product_id,
        qty_ordered=qty,
        company_id=env.company_a,
    )
    env.db.add(line)
    env.db.flush()
    # Committed, not just flushed, for the same reason `_Env.__init__` commits
    # its seeds: a dry-run ingest call rolls its transaction back, and an
    # uncommitted seed sitting in the same transaction would vanish with it,
    # making "the dry run wrote nothing" indistinguishable from "the fixture
    # lost its own data" (the outer test transaction still discards everything).
    env.db.commit()
    return so


def _seed_claim(env, *, so_number: str, po_number: str, item_code, source: str) -> OrderLinkClaim:
    row = OrderLinkClaim(
        so_number=so_number,
        po_number=po_number,
        item_code=item_code,
        source=source,
        company_id=env.company_a,
    )
    env.db.add(row)
    env.db.flush()
    env.db.commit()
    return row


def _product_code(env, product_ref: str) -> str:
    product_id = env.refs.resolve(entity_type="products", source_ref=product_ref)
    return env.db.execute(
        text("SELECT product_code FROM products WHERE id = :id"), {"id": product_id}
    ).scalar()


def _claims_for(env, *, po_number: str) -> list[dict]:
    """Every claim naming `po_number` in the anchor company, as plain dicts.

    Raw SQL against the BARE table name, not `scm.order_link_claim`: the
    scratch fixture's `search_path` is what routes an unqualified name into
    the translated `{name}_scm` schema (`tests/_pg_fixture.py`); a literal
    `scm.` prefix would instead hit the real `scm` schema outside the scratch
    sandbox. `test_order_inquiry_dedication.py` reads the same table the same
    way.
    """
    rows = (
        env.db.execute(
            text(
                "SELECT so_number, po_number, item_code, source, po_line_id, "
                "spo_allocation_id, so_line_id, resolved_at FROM order_link_claim "
                "WHERE po_number = :po AND company_id = :c"
            ),
            {"po": po_number, "c": env.company_a},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


# ================================================================== AC-V4-1
class TestFromSoNumbersClaims:
    def test_a_line_with_from_so_numbers_creates_one_claim_per_number(self, env):
        so_a = f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}"
        so_b = f"{MARKER}-SOB-{uuid.uuid4().hex[:8]}"  # never seeded - stays unresolved
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        _seed_plain_so(env, so_number=so_a, product_id=product_id)
        product_code = _product_code(env, env.product_ref)

        line = _po_line(env, from_so_numbers=[so_a, so_b])
        record = _po_record(env, lines=[line])

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]

        claims = _claims_for(env, po_number=record["po_number"])
        assert len(claims) == 2, claims
        by_so = {c["so_number"]: c for c in claims}
        assert set(by_so) == {so_a, so_b}
        for claim in claims:
            assert claim["source"] == "autocount"
            assert claim["item_code"] == product_code
            assert str(claim["po_line_id"]) == str(po_line["id"])
            assert claim["spo_allocation_id"] is None

        resolved = by_so[so_a]
        assert resolved["so_line_id"] is not None
        assert resolved["resolved_at"] is not None

        unresolved = by_so[so_b]
        assert unresolved["so_line_id"] is None
        assert unresolved["resolved_at"] is None


# ================================================================== AC-V4-2
class TestClaimsAreIdempotent:
    def test_repush_does_not_duplicate_claims(self, env):
        so_a = f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}"
        so_b = f"{MARKER}-SOB-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        _seed_plain_so(env, so_number=so_a, product_id=product_id)

        line = _po_line(env, from_so_numbers=[so_a, so_b])
        record = _po_record(env, lines=[line])

        first = env.post(INGEST_PO, [record])
        assert first.json()["records"][0]["outcome"] == "created", first.text

        second = env.post(INGEST_PO, [record])

        entry = second.json()["records"][0]
        assert entry["outcome"] == "updated", second.text
        claims = _claims_for(env, po_number=record["po_number"])
        assert len(claims) == 2, claims

    def test_a_claim_another_source_already_holds_is_left_as_is(self, env):
        """The first channel to state a pairing keeps its provenance
        (`order_link_service.claim_book_pairing`'s own rule) - ingest must
        find the existing row at this identity, not mint a second one or
        relabel it 'autocount'."""
        so_a = f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}"
        product_code = _product_code(env, env.product_ref)
        line = _po_line(env, from_so_numbers=[so_a])
        record = _po_record(env, lines=[line])

        _seed_claim(
            env,
            so_number=so_a,
            po_number=record["po_number"],
            item_code=product_code,
            source="po_upload",
        )

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        claims = _claims_for(env, po_number=record["po_number"])
        assert len(claims) == 1, claims
        assert claims[0]["source"] == "po_upload"


# ================================================================== AC-V4-4
class TestDryRunWritesNoClaim:
    def test_dry_run_creates_no_claim_rows(self, env):
        so_a = f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}"
        line = _po_line(env, from_so_numbers=[so_a])
        record = _po_record(env, lines=[line])

        res = env.post(INGEST_PO, [record], dry_run=True)

        body = res.json()
        assert body["dry_run"] is True
        entry = body["records"][0]
        assert entry["outcome"] == "created", res.text
        assert _claims_for(env, po_number=record["po_number"]) == []
        assert env.header("purchase_orders", record["source_ref"]) is None


# ============================================================== schema pin
class TestFromSoNumbersSchema:
    def test_an_empty_list_writes_no_claims(self, env):
        line = _po_line(env, from_so_numbers=[])
        record = _po_record(env, lines=[line])

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert _claims_for(env, po_number=record["po_number"]) == []

    def test_an_absent_key_writes_no_claims(self, env):
        """A plain v1 line, no `from_so_numbers` key at all. Nothing about this
        AC needs new behaviour to make this pass - it already does, because
        nothing writes a claim today. Kept here as the pin against a future
        implementation that starts claiming without being told to."""
        record = _po_record(env)

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert _claims_for(env, po_number=record["po_number"]) == []

    def test_a_non_list_value_fails_and_names_the_line(self, env):
        line = _po_line(env, from_so_numbers="SO-A")
        record = _po_record(env, lines=[line])

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", res.text
        assert "lines.0.from_so_numbers" in entry["errors"], entry


# ======================================================== AC-V4-1, SPO lines
class TestShippingOrderFromSoNumbersClaims:
    def test_a_shipping_order_line_with_from_so_numbers_creates_a_claim(self, env):
        so_a = f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}"
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        _seed_plain_so(env, so_number=so_a, product_id=product_id)

        spo_number = f"SPO-{uuid.uuid4().hex[:4]}/01-{uuid.uuid4().hex[:4]}"
        record = {
            "source_ref": _ref("SPO"),
            "spo_number": spo_number,
            "supplier_ref": env.supplier_ref,
            "status": "open",
            "lines": [
                {
                    "source_ref": _ref("SPOL"),
                    "product_ref": env.product_ref,
                    "product_code": _product_code(env, env.product_ref),
                    "qty_ordered": 5,
                    "from_so_numbers": [so_a],
                }
            ],
        }

        res = env.post(INGEST_SPO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        claims = _claims_for(env, po_number=spo_number)
        assert len(claims) == 1, claims
        assert claims[0]["source"] == "autocount"
        assert claims[0]["spo_allocation_id"] is not None
        assert claims[0]["po_line_id"] is None
        assert claims[0]["so_line_id"] is not None
        assert claims[0]["resolved_at"] is not None
