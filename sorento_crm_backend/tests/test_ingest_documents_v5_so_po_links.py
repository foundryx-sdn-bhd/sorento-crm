"""Group V8 - AutoCount linkage widen: `from_so_line_ref` / `from_so_external`
/ `from_po_line_ref` / `from_po_number`, on both `purchase_orders.lines` and
`shipping_orders.lines` (brief: ingest-contract-2-2-so-links; no separate
PLAN file for this slice - the coder brief is the contract, frozen with the
foundryx ESB session).

UAC: `documentation/plans/autocount/autocount-document-ingest-v2-acceptance-criteria.md`
Group V8, AC-V8-1..7. Labelled V8, not V5, because V5/V6/V7 were already
taken by earlier slices in that same file when this one landed - see that
group's own header note.

  AC-V8-1  a PO/SPO line carrying all four new fields is ACCEPTED; a
           v2.1-shaped payload (none of them) still ingests unchanged
  AC-V8-2  `from_so_external` with no `db` fails validation, naming the field
  AC-V8-3  `from_so_line_ref`/`from_po_line_ref`/`from_po_number` persist
           uniformly on BOTH `purchase_order_lines` and `spo_allocations`;
           an omitted field never clears a stored value, an explicit `null`
           DOES (absent_vs_null)
  AC-V8-4  (B1 review fix) a resolvable `from_so_line_ref` wins over the
           ambiguous `(so_number, item_code)` match `from_so_numbers` alone
           would make, on both a PO line and an SPO line
  AC-V8-5  an unresolvable ref falls back to today's `from_so_numbers`
           behaviour, and a LATER `resolve()` sweep recovers the exact line
           from the ref persisted on the row (B2 review fix)
  AC-V8-6  `from_so_external` is recorded raw, verbatim (`exclude_unset`),
           never resolved into an id or a claim
  AC-V8-7  dry run writes no claim, ref-based or number-based

Substrate reused byte-for-byte from `test_ingest_documents.py` /
`test_ingest_shipping_orders.py`, same as every other V-group file in this
suite.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text

from app.api.v1.external.contract import FIELDS_ADDED
from app.models.inventory import Warehouse
from app.models.order import SalesOrder, SalesOrderLine
from app.models.procurement import PurchaseOrderLine, SPOAllocation
from app.models.product import Product
from app.models.project_so import (
    ACK_ACKNOWLEDGED,
    INQUIRY_RAISED,
    IV_ORDER,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRow,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
)
from app.services.scm import order_link_service

from tests.test_ingest_documents import (
    INGEST_PO,
    MARKER,
    _po_line,
    _po_record,
    _ref,
    env,  # noqa: F401 - pytest fixture, imported for reuse
)
from tests.test_ingest_shipping_orders import (
    INGEST_SPO,
    _seed_legacy_row,
    _spo_line,
    _spo_record,
)

__all__ = ["env"]

CONTRACT_URL = "/api/v1/external/contract"


def _seed_so_line(env, *, so_number: str, product_id: str, source_ref: str, qty=10):
    """A sales order + one line carrying a real `source_ref`, bypassing
    ingest entirely - `from_so_line_ref` resolution is a plain join against
    `sales_order_lines.source_ref`, independent of how that row got there.

    Committed, not just flushed, for the same reason
    `test_ingest_documents_v2_links._seed_plain_so` commits: a dry-run
    ingest call rolls its own transaction back, and an uncommitted seed
    sitting inside that same transaction would vanish with it.
    """
    so = SalesOrder(so_number=so_number, status="open", company_id=env.company_a)
    env.db.add(so)
    env.db.flush()
    line = SalesOrderLine(
        sales_order_id=so.id,
        product_id=product_id,
        qty_ordered=qty,
        source_ref=source_ref,
        company_id=env.company_a,
    )
    env.db.add(line)
    env.db.flush()
    env.db.commit()
    return so, line


def _claims_for(env, *, po_number: str) -> list[dict]:
    """Byte-for-byte copy of `test_ingest_documents_v2_links._claims_for` -
    see that file's own comment for why the table name is bare, not
    `scm.order_link_claim`."""
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


def _spo_rows(env, spo_number: str):
    """Byte-for-byte copy of `test_ingest_shipping_orders._spo_rows`."""
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


def _assert_ambiguous_match_picks_the_decoy(env, *, so_number: str, product_id: str, decoy_line):
    """The premise `TestRefTakesPrecedenceOverAmbiguousNumberMatch` and
    `TestExactRefRecoveryOnLaterSweep` both rest on (reviewer follow-up):
    `_sales_side`'s `by_key` dict comprehension, over an ORDER-BY-less
    SELECT, keeps the DECOY for the shared `(so_number, item_code)` key -
    verified empirically in this environment, never guaranteed by Postgres.

    Asserted HERE, before the behaviour under test runs, so a broken
    premise fails LOUDLY, naming itself, instead of the surrounding test
    quietly going on passing with the exact-ref logic it exists to guard
    doing nothing at all - the ordering assumption can only fail OPEN
    otherwise (a flipped order makes the ambiguous match agree with the
    exact-ref result by coincidence, and the real assertion below cannot
    tell the two apart).
    """
    item_code = env.db.query(Product.product_code).filter(Product.id == product_id).scalar()
    by_key, _by_number = order_link_service._sales_side(env.db, {so_number})
    ambiguous = by_key[(so_number, item_code)]
    assert str(ambiguous.id) == str(decoy_line.id), (
        "premise broken: _sales_side's ambiguous (so_number, item_code) match no "
        "longer picks the decoy line - this test can no longer prove the exact-ref "
        "precedence it exists to guard"
    )


# ============================================================ AC-V8-1/2
class TestSchemaAcceptance:
    def test_a_po_line_with_all_four_new_fields_is_accepted(self, env):
        line = _po_line(
            env,
            from_so_line_ref=f"{MARKER}:45700100:45700148",
            from_so_external={"db": "AED_OTHER", "doc_key": 1, "doc_no": "SO-1", "dtl_key": 2},
            from_po_line_ref=f"{MARKER}:44909094:45021331",
            from_po_number="202606-S0018",
        )
        record = _po_record(env, lines=[line])

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text

    def test_a_spo_line_with_all_four_new_fields_is_accepted(self, env):
        line = _spo_line(
            env,
            from_so_line_ref=f"{MARKER}:45700100:45700148",
            from_so_external={"db": "AED_OTHER", "doc_key": 1, "doc_no": None, "dtl_key": None},
            from_po_line_ref=f"{MARKER}:44909094:45021331",
            from_po_number="202606-S0018",
        )
        record = _spo_record(env, lines=[line], supplier_ref=env.supplier_ref)

        res = env.post(INGEST_SPO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text

    def test_a_v21_shaped_po_payload_still_ingests_unchanged(self, env):
        """No trace of any of the four fields - the plain v1/v2.1 shape
        every pre-existing test in this suite already sends."""
        record = _po_record(env)

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text

    def test_from_so_external_with_no_db_fails_and_names_the_field(self, env):
        line = _po_line(env, from_so_external={"doc_key": 1})
        record = _po_record(env, lines=[line])

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "failed", res.text
        assert "lines.0.from_so_external.db" in entry["errors"], entry


# ==================================================================== V8 contract
class TestContractVersion22:
    def test_contract_reports_2_2_and_lists_the_four_fields(self, env):
        res = env.client.get(CONTRACT_URL)

        assert res.status_code == 200, res.text
        body = res.json()
        # Bumped again (autocount-brands-ingest, AC-13): "2.3" adds `brands`.
        # Bumped again (ingest-products-code-wins, SR0): "2.4" adds products
        # code-wins deletion `codes`. Bumped again (ingest-stock-balances-2-5,
        # Foundryx SR5): "2.5" adds `stock_balances` - unrelated to the PO/SPO
        # link fields this test pins, only the version literal needed to move.
        # Bumped again (finance S0, #1309): "2.6" adds `billing_documents`.
        # Bumped again (#1354 S2): "2.7" adds `delivery_orders`, `goods_receive_notes`, `branches`.
        # Bumped again (AC-CONSIGN-CRM): "2.9" adds consignment fields; "2.8" added `cancel_sales_orders`, `cancel_purchase_orders`.
        assert body["version"] == "2.10"
        wanted = {
            "from_so_line_ref", "from_so_external", "from_po_line_ref", "from_po_number",
        }
        for entity in ("purchase_orders", "shipping_orders"):
            got = set(body["fields_added"].get(entity, []))
            assert wanted.issubset(got), (entity, wanted - got)

    def test_the_registry_constant_itself_carries_the_same_fields(self):
        """Pin at the source, not just at the HTTP surface - a future
        refactor of `get_contract()`'s response shape should not be able to
        silently drop these from `FIELDS_ADDED` unnoticed."""
        wanted = {
            "from_so_line_ref", "from_so_external", "from_po_line_ref", "from_po_number",
        }
        for entity in ("purchase_orders", "shipping_orders"):
            got = set(FIELDS_ADDED.get(entity, []))
            assert wanted.issubset(got), (entity, wanted - got)


# ================================================================== AC-V8-3
class TestSpoAllocationPurchaseLink:
    def test_from_po_fields_land_on_the_spo_allocation_row(self, env):
        line = _spo_line(
            env,
            from_po_line_ref=f"{MARKER}:44909094:45021331",
            from_po_number="202606-S0018",
        )
        record = _spo_record(env, lines=[line], supplier_ref=env.supplier_ref)

        res = env.post(INGEST_SPO, [record])

        assert res.json()["records"][0]["outcome"] == "created", res.text
        row = _spo_rows(env, record["spo_number"])[0]
        assert row["from_po_line_ref"] == f"{MARKER}:44909094:45021331"
        assert row["from_po_number"] == "202606-S0018"

    def test_an_omitted_field_on_repush_never_clears_a_stored_value(self, env):
        line = _spo_line(
            env,
            from_po_line_ref=f"{MARKER}:44909094:45021331",
            from_po_number="202606-S0018",
        )
        record = _spo_record(env, lines=[line], supplier_ref=env.supplier_ref)
        first = env.post(INGEST_SPO, [record])
        assert first.json()["records"][0]["outcome"] == "created", first.text

        # Re-push the SAME line by source_ref, this time with neither key
        # sent at all - the ordinary shape of a routine re-push.
        repush_line = _spo_line(env, ref=line["source_ref"])
        repush = dict(record, lines=[repush_line])

        res = env.post(INGEST_SPO, [repush])

        assert res.json()["records"][0]["outcome"] == "updated", res.text
        row = _spo_rows(env, record["spo_number"])[0]
        assert row["from_po_line_ref"] == f"{MARKER}:44909094:45021331"
        assert row["from_po_number"] == "202606-S0018"


# ================================================================ AC-V8-4/5
class TestFromSoLineRefClaims:
    def test_a_resolvable_ref_writes_the_claim_already_paired_on_a_po_line(self, env):
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        so_ref = _ref("SOL")
        so, so_line = _seed_so_line(
            env,
            so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}",
            product_id=product_id,
            source_ref=so_ref,
        )

        line = _po_line(env, from_so_line_ref=so_ref)
        record = _po_record(env, lines=[line])

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        claims = _claims_for(env, po_number=record["po_number"])
        assert len(claims) == 1, claims
        claim = claims[0]
        assert claim["so_number"] == so.so_number
        assert str(claim["so_line_id"]) == str(so_line.id)
        assert claim["source"] == "autocount"
        assert claim["resolved_at"] is not None

    def test_an_unresolvable_ref_falls_back_to_the_from_so_numbers_open_claim(self, env):
        """The SO has not been pushed yet - the normal case. There is no
        number inside a ref alone, so this proves the plain `from_so_numbers`
        companion field still opens a claim exactly as it did before this
        slice, unaffected by the ref failing to resolve."""
        so_a = f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}"
        line = _po_line(
            env,
            from_so_numbers=[so_a],
            from_so_line_ref=f"{MARKER}:99999999:99999999",  # resolves to nothing
        )
        record = _po_record(env, lines=[line])

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        claims = _claims_for(env, po_number=record["po_number"])
        assert len(claims) == 1, claims
        assert claims[0]["so_number"] == so_a
        assert claims[0]["so_line_id"] is None
        assert claims[0]["resolved_at"] is None

    def test_a_resolvable_ref_writes_the_claim_already_paired_on_an_spo_line(self, env):
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        so_ref = _ref("SOL")
        so, so_line = _seed_so_line(
            env,
            so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}",
            product_id=product_id,
            source_ref=so_ref,
        )

        line = _spo_line(env, from_so_line_ref=so_ref)
        record = _spo_record(env, lines=[line], supplier_ref=env.supplier_ref)

        res = env.post(INGEST_SPO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        claims = _claims_for(env, po_number=record["spo_number"])
        assert len(claims) == 1, claims
        claim = claims[0]
        assert claim["so_number"] == so.so_number
        assert str(claim["so_line_id"]) == str(so_line.id)
        assert claim["spo_allocation_id"] is not None
        assert claim["po_line_id"] is None
        assert claim["resolved_at"] is not None

    def test_repush_does_not_duplicate_the_ref_claim(self, env):
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        so_ref = _ref("SOL")
        _seed_so_line(
            env,
            so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}",
            product_id=product_id,
            source_ref=so_ref,
        )
        line = _po_line(env, from_so_line_ref=so_ref)
        record = _po_record(env, lines=[line])

        first = env.post(INGEST_PO, [record])
        assert first.json()["records"][0]["outcome"] == "created", first.text
        second = env.post(INGEST_PO, [record])

        assert second.json()["records"][0]["outcome"] == "updated", second.text
        assert len(_claims_for(env, po_number=record["po_number"])) == 1


# ================================================================== AC-V8-6
class TestFromSoExternalRawStorage:
    def test_from_so_external_lands_raw_on_the_purchase_order_line(self, env):
        line = _po_line(
            env,
            from_so_external={
                "db": "AED_OTHER", "doc_key": 45700100, "doc_no": "SO-9", "dtl_key": 45700148,
            },
        )
        record = _po_record(env, lines=[line])

        res = env.post(INGEST_PO, [record])

        assert res.json()["records"][0]["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]
        assert po_line["from_so_external"] == {
            "db": "AED_OTHER", "doc_key": 45700100, "doc_no": "SO-9", "dtl_key": 45700148,
        }
        # Never resolved into a claim - there is no so_number a cross-book
        # key can name, so no `order_link_claim` row exists for this
        # document at all.
        assert _claims_for(env, po_number=record["po_number"]) == []

    def test_from_so_external_lands_raw_on_the_spo_allocation_row(self, env):
        line = _spo_line(
            env,
            from_so_external={"db": "AED_OTHER", "doc_key": 1, "doc_no": None, "dtl_key": None},
        )
        record = _spo_record(env, lines=[line], supplier_ref=env.supplier_ref)

        res = env.post(INGEST_SPO, [record])

        assert res.json()["records"][0]["outcome"] == "created", res.text
        row = _spo_rows(env, record["spo_number"])[0]
        assert row["from_so_external"] == {
            "db": "AED_OTHER", "doc_key": 1, "doc_no": None, "dtl_key": None,
        }


# ================================================================ AC-V8-4 (B1)
class TestRefTakesPrecedenceOverAmbiguousNumberMatch:
    """Reviewer-confirmed blocker: with `from_so_numbers` AND a resolvable
    `from_so_line_ref` both present on one line, the claim must land on the
    EXACT line the ref names - never on whichever of two same-item lines the
    ambiguous `(so_number, item_code)` match in `resolve()` happens to pick.

    `_sales_side`'s `by_key` is a dict comprehension over an ORDER-BY-less
    SELECT: for two rows sharing one key it keeps whichever one the query
    returns LAST, which for a freshly seeded, never-updated two-row table is
    the one inserted SECOND (Postgres returns a plain heap scan in physical/
    insertion order absent any ORDER BY). Seeding the WANTED (ref-named)
    line FIRST and a DECOY line SECOND reproduces the exact case B1
    describes: the old call order (numbers before ref) resolves the open
    claim to the decoy before the ref ever gets a chance to correct it, and
    the ref then finds `so_line_id` already set and gives up
    (`claim_placed_on_po`'s fill-never-repoint guard).
    """

    def test_a_resolvable_ref_wins_on_a_po_line(self, env):
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        so_number = f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}"
        so = SalesOrder(so_number=so_number, status="open", company_id=env.company_a)
        env.db.add(so)
        env.db.flush()
        wanted_ref = _ref("SOL")
        wanted_line = SalesOrderLine(
            sales_order_id=so.id, product_id=product_id, qty_ordered=5,
            source_ref=wanted_ref, company_id=env.company_a,
        )
        env.db.add(wanted_line)
        env.db.flush()
        decoy_line = SalesOrderLine(
            sales_order_id=so.id, product_id=product_id, qty_ordered=10,
            source_ref=_ref("SOL"), company_id=env.company_a,
        )
        env.db.add(decoy_line)
        env.db.flush()
        env.db.commit()

        # Premise, asserted before exercising the behaviour under test
        # (reviewer follow-up): if `_sales_side`'s insertion-order
        # assumption ever stops holding, this fails HERE, loudly, instead
        # of the assertion below quietly continuing to pass with the fix
        # doing nothing.
        _assert_ambiguous_match_picks_the_decoy(
            env, so_number=so_number, product_id=product_id, decoy_line=decoy_line,
        )

        line = _po_line(env, from_so_numbers=[so_number], from_so_line_ref=wanted_ref)
        record = _po_record(env, lines=[line])

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        claims = _claims_for(env, po_number=record["po_number"])
        assert len(claims) == 1, claims
        assert str(claims[0]["so_line_id"]) == str(wanted_line.id), claims

    def test_a_resolvable_ref_wins_on_an_spo_line(self, env):
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        so_number = f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}"
        so = SalesOrder(so_number=so_number, status="open", company_id=env.company_a)
        env.db.add(so)
        env.db.flush()
        wanted_ref = _ref("SOL")
        wanted_line = SalesOrderLine(
            sales_order_id=so.id, product_id=product_id, qty_ordered=5,
            source_ref=wanted_ref, company_id=env.company_a,
        )
        env.db.add(wanted_line)
        env.db.flush()
        decoy_line = SalesOrderLine(
            sales_order_id=so.id, product_id=product_id, qty_ordered=10,
            source_ref=_ref("SOL"), company_id=env.company_a,
        )
        env.db.add(decoy_line)
        env.db.flush()
        env.db.commit()

        _assert_ambiguous_match_picks_the_decoy(
            env, so_number=so_number, product_id=product_id, decoy_line=decoy_line,
        )

        line = _spo_line(env, from_so_numbers=[so_number], from_so_line_ref=wanted_ref)
        record = _spo_record(env, lines=[line], supplier_ref=env.supplier_ref)

        res = env.post(INGEST_SPO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        claims = _claims_for(env, po_number=record["spo_number"])
        assert len(claims) == 1, claims
        assert str(claims[0]["so_line_id"]) == str(wanted_line.id), claims


# ================================================================ AC-V8-3 (S1)
class TestExplicitNullClears:
    """`test_an_omitted_field_on_repush_never_clears_a_stored_value` passes
    even under a truthiness check, since an OMITTED field yields `None` the
    same way an explicit `null` does. These tests are what actually pins
    `model_fields_set` (presence, not truthiness) and therefore the
    `absent_vs_null: true` the contract advertises: an explicit `null` DOES
    clear a stored value, on both `purchase_order_lines` and
    `spo_allocations`.
    """

    def test_an_explicit_null_clears_from_po_line_ref_on_a_purchase_order_line(self, env):
        line = _po_line(env, from_po_line_ref=f"{MARKER}:44909094:45021331")
        record = _po_record(env, lines=[line])
        first = env.post(INGEST_PO, [record])
        assert first.json()["records"][0]["outcome"] == "created", first.text
        # D3 review fix: prove the field was actually WRITTEN on the first
        # push, before checking the null clears it - otherwise "is None
        # after the null re-push" is identically true whether the write
        # path works or was never wired at all.
        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]
        assert po_line["from_po_line_ref"] == f"{MARKER}:44909094:45021331"

        repush_line = _po_line(env, ref=line["source_ref"], from_po_line_ref=None)
        repush = dict(record, lines=[repush_line])
        res = env.post(INGEST_PO, [repush])

        assert res.json()["records"][0]["outcome"] == "updated", res.text
        po_line = env.po_lines(header["id"])[0]
        assert po_line["from_po_line_ref"] is None

    def test_an_explicit_null_clears_from_po_line_ref_on_an_spo_line(self, env):
        line = _spo_line(env, from_po_line_ref=f"{MARKER}:44909094:45021331")
        record = _spo_record(env, lines=[line], supplier_ref=env.supplier_ref)
        first = env.post(INGEST_SPO, [record])
        assert first.json()["records"][0]["outcome"] == "created", first.text
        row = _spo_rows(env, record["spo_number"])[0]
        assert row["from_po_line_ref"] == f"{MARKER}:44909094:45021331"

        repush_line = _spo_line(env, ref=line["source_ref"], from_po_line_ref=None)
        repush = dict(record, lines=[repush_line])
        res = env.post(INGEST_SPO, [repush])

        assert res.json()["records"][0]["outcome"] == "updated", res.text
        row = _spo_rows(env, record["spo_number"])[0]
        assert row["from_po_line_ref"] is None

    def test_an_explicit_null_clears_from_so_line_ref_on_a_purchase_order_line(self, env):
        line = _po_line(env, from_so_line_ref=f"{MARKER}:45700100:45700148")
        record = _po_record(env, lines=[line])
        first = env.post(INGEST_PO, [record])
        assert first.json()["records"][0]["outcome"] == "created", first.text
        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]
        assert po_line["from_so_line_ref"] == f"{MARKER}:45700100:45700148"

        repush_line = _po_line(env, ref=line["source_ref"], from_so_line_ref=None)
        repush = dict(record, lines=[repush_line])
        res = env.post(INGEST_PO, [repush])

        assert res.json()["records"][0]["outcome"] == "updated", res.text
        po_line = env.po_lines(header["id"])[0]
        assert po_line["from_so_line_ref"] is None

    def test_an_explicit_null_clears_from_so_line_ref_on_an_spo_line(self, env):
        line = _spo_line(env, from_so_line_ref=f"{MARKER}:45700100:45700148")
        record = _spo_record(env, lines=[line], supplier_ref=env.supplier_ref)
        first = env.post(INGEST_SPO, [record])
        assert first.json()["records"][0]["outcome"] == "created", first.text
        row = _spo_rows(env, record["spo_number"])[0]
        assert row["from_so_line_ref"] == f"{MARKER}:45700100:45700148"

        repush_line = _spo_line(env, ref=line["source_ref"], from_so_line_ref=None)
        repush = dict(record, lines=[repush_line])
        res = env.post(INGEST_SPO, [repush])

        assert res.json()["records"][0]["outcome"] == "updated", res.text
        row = _spo_rows(env, record["spo_number"])[0]
        assert row["from_so_line_ref"] is None

    def test_an_explicit_null_clears_from_so_external_on_a_purchase_order_line(self, env):
        line = _po_line(env, from_so_external={"db": "AED_OTHER", "doc_key": 1})
        record = _po_record(env, lines=[line])
        first = env.post(INGEST_PO, [record])
        assert first.json()["records"][0]["outcome"] == "created", first.text
        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]
        assert po_line["from_so_external"] == {"db": "AED_OTHER", "doc_key": 1}

        repush_line = _po_line(env, ref=line["source_ref"], from_so_external=None)
        repush = dict(record, lines=[repush_line])
        res = env.post(INGEST_PO, [repush])

        assert res.json()["records"][0]["outcome"] == "updated", res.text
        po_line = env.po_lines(header["id"])[0]
        assert po_line["from_so_external"] is None

    def test_an_explicit_null_clears_from_so_external_on_an_spo_line(self, env):
        line = _spo_line(env, from_so_external={"db": "AED_OTHER", "doc_key": 1})
        record = _spo_record(env, lines=[line], supplier_ref=env.supplier_ref)
        first = env.post(INGEST_SPO, [record])
        assert first.json()["records"][0]["outcome"] == "created", first.text
        row = _spo_rows(env, record["spo_number"])[0]
        assert row["from_so_external"] == {"db": "AED_OTHER", "doc_key": 1}

        repush_line = _spo_line(env, ref=line["source_ref"], from_so_external=None)
        repush = dict(record, lines=[repush_line])
        res = env.post(INGEST_SPO, [repush])

        assert res.json()["records"][0]["outcome"] == "updated", res.text
        row = _spo_rows(env, record["spo_number"])[0]
        assert row["from_so_external"] is None


# ==================================================================== D4
class TestExcludeUnsetPinning:
    """`exclude_unset=True` was unpinned - both existing from_so_external
    tests send all four keys, so they pass identically under plain
    `model_dump()`. These pin the actual difference: a NARROWER object
    stores ONLY the keys sent (no filled-in nulls for the ones omitted),
    and an explicit inner `null` IS stored as null (present, not dropped).
    """

    def test_a_narrower_object_stores_only_the_keys_sent(self, env):
        line = _po_line(env, from_so_external={"db": "AED_OTHER", "doc_key": 45700100})
        record = _po_record(env, lines=[line])

        res = env.post(INGEST_PO, [record])

        assert res.json()["records"][0]["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]
        # Exactly the two keys sent - no doc_no/dtl_key filled in as None,
        # which is what a plain model_dump() (no exclude_unset) would do.
        assert po_line["from_so_external"] == {"db": "AED_OTHER", "doc_key": 45700100}

    def test_an_explicit_inner_null_is_stored_as_null(self, env):
        line = _spo_line(
            env,
            from_so_external={
                "db": "AED_OTHER", "doc_key": 45700100, "doc_no": None, "dtl_key": 2,
            },
        )
        record = _spo_record(env, lines=[line], supplier_ref=env.supplier_ref)

        res = env.post(INGEST_SPO, [record])

        assert res.json()["records"][0]["outcome"] == "created", res.text
        row = _spo_rows(env, record["spo_number"])[0]
        # doc_no was SENT as null, not omitted - it must be PRESENT as null,
        # distinct from a key that was never mentioned at all.
        assert row["from_so_external"] == {
            "db": "AED_OTHER", "doc_key": 45700100, "doc_no": None, "dtl_key": 2,
        }


# ================================================================ AC-V8-7 (S3)
class TestDryRunWritesNoRefClaim:
    """V5 equivalent of AC-V4-4. `claim_placed_on_po` (the function a
    resolvable `from_so_line_ref` writes through) does an UNCONDITIONAL
    `db.flush()` of its own - unlike `claim_book_pairing`, which only
    flushes when the caller tells it to - so it is the route-level dry-run
    SAVEPOINT rollback that makes this safe, not anything inside
    `write_line_ref_claims` itself. Pinned the same way
    `test_dry_run_creates_no_claim_rows` (V4) pins the number path.
    """

    def test_a_dry_run_resolvable_ref_writes_no_claim(self, env):
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        so_ref = _ref("SOL")
        so, so_line = _seed_so_line(
            env,
            so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}",
            product_id=product_id,
            source_ref=so_ref,
        )
        line = _po_line(env, from_so_line_ref=so_ref)
        record = _po_record(env, lines=[line])

        res = env.post(INGEST_PO, [record], dry_run=True)

        body = res.json()
        assert body["dry_run"] is True
        entry = body["records"][0]
        assert entry["outcome"] == "created", res.text
        assert _claims_for(env, po_number=record["po_number"]) == []
        assert env.header("purchase_orders", record["source_ref"]) is None


# ==================================================================== D1
class TestExactRefNeverCrossesSalesOrders:
    """D1 (blocker, reviewer-confirmed regression - reproduces the review's
    own walkthrough exactly). `_exact_so_line_for` used to query
    `SalesOrderLine` alone, with no join to `SalesOrder` and no check
    against the claim's OWN `so_number`, so it could point a claim at a
    line belonging to a DIFFERENT sales order than the one it names, and
    `resolve()` assigned the result unconditionally - stamping
    `resolved_at` and putting the wrong pairing out of reach for good.

    `from_so_numbers=["SO-A", "SO-B"]` plus a `from_so_line_ref` naming an
    SO-B line: the ref path (`write_line_ref_claims`) resolves and writes
    the SO-B claim fully paired, at push time, deriving `so_number` FROM
    the resolved line - safe by construction. The number path
    (`write_claims_for_lines`) opens a SEPARATE SO-A claim (SO-A is never
    even seeded, so the ambiguous match cannot resolve it either) and calls
    `resolve()`, which - pre-fix - read the ref off the SAME purchase
    line's `from_so_line_ref` (one column, shared by every claim that
    points at that line) with no check against which so_number the claim
    in hand actually names, and assigned SO-B's line to the SO-A claim.

    Confirmed by running this test against c2c1e5148 (the commit the D1
    regression shipped in, before this fix): it FAILED there - the SO-A
    claim's `so_line_id` was SO-B's line id and `resolved_at` was set.
    """

    def test_the_so_a_claim_never_lands_on_the_so_b_line(self, env):
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        so_a = f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}"
        so_b = f"{MARKER}-SOB-{uuid.uuid4().hex[:8]}"
        ref_b = _ref("SOL")
        so_b_row, so_b_line = _seed_so_line(
            env, so_number=so_b, product_id=product_id, source_ref=ref_b,
        )

        line = _po_line(env, from_so_numbers=[so_a, so_b], from_so_line_ref=ref_b)
        record = _po_record(env, lines=[line])

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        claims = _claims_for(env, po_number=record["po_number"])
        by_so = {c["so_number"]: c for c in claims}
        assert set(by_so) == {so_a, so_b}, claims

        # SO-B resolved correctly and immediately, through the exact ref.
        assert str(by_so[so_b]["so_line_id"]) == str(so_b_line.id)
        assert by_so[so_b]["resolved_at"] is not None

        # SO-A must NEVER be assigned SO-B's line - the whole point of D1.
        assert by_so[so_a]["so_line_id"] is None, by_so[so_a]
        assert by_so[so_a]["resolved_at"] is None, by_so[so_a]


# ==================================================================== D2
class TestExactRefRecoveryOnLaterSweep:
    """D2 (should-fix treated as blocking per PRINCIPLES: a green kill test
    is a defect on its own). The `_exact_so_line_for` half of `resolve()`'s
    sweep was entirely unexercised - deleting
    `exact_so_line.get(str(claim.id)) or` from the assignment in
    `resolve()` left the whole suite green. This is the test AC-V8-5
    actually describes: a `from_so_line_ref` that could not resolve at
    push time (the sales order had not been pushed yet) is recovered on a
    LATER `resolve()` sweep, once it arrives - the reason the ref is
    persisted on the purchase-side row at all (B2) rather than merely
    consumed and discarded.
    """

    def test_resolve_sweep_recovers_the_exact_line_over_the_ambiguous_match(self, env):
        so_number = f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}"
        wanted_ref = _ref("SOL")
        line = _po_line(env, from_so_numbers=[so_number], from_so_line_ref=wanted_ref)
        record = _po_record(env, lines=[line])

        res = env.post(INGEST_PO, [record])
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text

        # The sales order did not exist at push time, so the ref could not
        # resolve - today's from_so_numbers path opened the claim, unresolved.
        claims = _claims_for(env, po_number=record["po_number"])
        assert len(claims) == 1, claims
        assert claims[0]["so_line_id"] is None
        assert claims[0]["resolved_at"] is None

        # NOW the sales order arrives - the WANTED (ref-named) line seeded
        # FIRST and a same-item DECOY line SECOND, so the ambiguous
        # (so_number, item_code) match (last-wins over an ORDER-BY-less
        # SELECT - see TestRefTakesPrecedenceOverAmbiguousNumberMatch's own
        # comment) would pick the DECOY if the exact-ref recovery below
        # were not running.
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        so = SalesOrder(so_number=so_number, status="open", company_id=env.company_a)
        env.db.add(so)
        env.db.flush()
        wanted_line = SalesOrderLine(
            sales_order_id=so.id, product_id=product_id, qty_ordered=5,
            source_ref=wanted_ref, company_id=env.company_a,
        )
        env.db.add(wanted_line)
        env.db.flush()
        decoy_line = SalesOrderLine(
            sales_order_id=so.id, product_id=product_id, qty_ordered=10,
            source_ref=_ref("SOL"), company_id=env.company_a,
        )
        env.db.add(decoy_line)
        env.db.flush()
        env.db.commit()

        # Premise, asserted before calling resolve() (reviewer follow-up):
        # if the ambiguous match ever stopped picking the decoy, the
        # assertion below would go on passing with the exact-ref recovery
        # dead - it can only fail OPEN otherwise.
        _assert_ambiguous_match_picks_the_decoy(
            env, so_number=so_number, product_id=product_id, decoy_line=decoy_line,
        )

        result = order_link_service.resolve(env.db)

        assert result["resolved"] == 1, result
        claims = _claims_for(env, po_number=record["po_number"])
        assert len(claims) == 1, claims
        assert str(claims[0]["so_line_id"]) == str(wanted_line.id), claims
        assert claims[0]["resolved_at"] is not None


# ============================================================ retraction pin
class TestFromSoLineRefAndExternalCanCoexist:
    """The ESB retracted an earlier guarantee that `from_so_external` is
    never sent alongside a same-book `from_so_line_ref` on one line - the
    two come from different AutoCount columns (`FromSODtlKey` versus the
    ICB plugin's UDFs) and describe different books, so a line raised from
    a same-book sales order AND tagged by the ICB plugin legitimately
    carries both. No mutual-exclusion validator was ever written to enforce
    the old guarantee (both fields are plain `Optional`), so there was
    nothing to relax in code - this pins the CURRENT rule so nobody adds
    that validator later: both may be present, the ref resolves normally
    into a claim, and the external object is stored untouched alongside it.
    """

    def test_a_line_with_both_fields_is_accepted_ref_resolves_and_external_stored(self, env):
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        so_ref = _ref("SOL")
        so, so_line = _seed_so_line(
            env,
            so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}",
            product_id=product_id,
            source_ref=so_ref,
        )
        external = {
            "db": "AED_OTHER", "doc_key": 45700100, "doc_no": "SO-9", "dtl_key": 45700148,
        }
        line = _po_line(env, from_so_line_ref=so_ref, from_so_external=external)
        record = _po_record(env, lines=[line])

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text

        claims = _claims_for(env, po_number=record["po_number"])
        assert len(claims) == 1, claims
        assert str(claims[0]["so_line_id"]) == str(so_line.id)
        assert claims[0]["resolved_at"] is not None

        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]
        assert po_line["from_so_line_ref"] == so_ref
        assert po_line["from_so_external"] == external


# ================================================================ AC-RL-40 to AC-RL-46
# S5 (`PLAN-oi-replan-received-links.md`): "our link follows the book pairing" -
# `ProjectOrderInquiryService.follow_book_repairing` does not exist yet, so every red
# state below is that missing hook (an unmoved link, or an unchanged `row_a.note`),
# never an import error.


def _mirror_row(env, *, core_line, product_id, qty: str, line_no: int = 1):
    """A project mirror of `core_line`, with its own RAISED order inquiry row - the
    row a `from_so_line_ref` move has to find (or fail to find) on the NEW line.
    `project_id=None` / `status="adopted"` mirrors `_seed()`'s own adopted shape in
    `test_order_inquiry_worklist.py` - an AutoCount order this suite never registers.

    `autocount_doc_no` is stamped to the CORE order's own `so_number` - `claim_
    identity`/`_row_so_number` reads `autocount_doc_no or provisional_ref` as a
    row's "own SO" identity, the same one G7 dedication (`_dedication_for_target`)
    exempts a candidate from being refused for. Left unset, that identity falls
    back to the random `provisional_ref` below, which never matches the REAL
    `so_number` the ingest's own ref-resolution claim was written under - so a
    genuinely-its-own-SO placement onto the mirror row reads as "dedicated to a
    different SO" and `place_on_po_allocations` refuses it (`follow_book_
    repairing`'s own `except AppException` swallows the refusal silently).
    """
    so_number = env.db.execute(
        text("SELECT so_number FROM sales_orders WHERE id = :id"),
        {"id": core_line.sales_order_id},
    ).scalar()
    pso = ProjectSalesOrder(
        id=str(uuid.uuid4()), company_id=env.company_a, project_id=None,
        so_id=core_line.sales_order_id, provisional_ref=f"{MARKER}-PSO-{uuid.uuid4().hex[:8]}",
        autocount_doc_no=so_number, status="adopted",
    )
    env.db.add(pso)
    env.db.flush()
    mirror_line = ProjectSalesOrderLine(
        id=str(uuid.uuid4()), company_id=env.company_a, project_sales_order_id=pso.id,
        line_no=line_no, core_sales_order_line_id=core_line.id, product_id=product_id,
        description=f"{MARKER} mirror", qty=Decimal(qty), uom="UNIT",
        unit_price=Decimal("10.00"), amount=Decimal("0"),
    )
    env.db.add(mirror_line)
    env.db.flush()
    inquiry = OrderInquiry(
        id=str(uuid.uuid4()), company_id=env.company_a, project_sales_order_id=pso.id,
    )
    env.db.add(inquiry)
    env.db.flush()
    row = OrderInquiryRow(
        id=str(uuid.uuid4()), company_id=env.company_a, order_inquiry_id=inquiry.id,
        so_line_id=mirror_line.id, qty=Decimal(qty), verb=IV_ORDER, state=INQUIRY_RAISED,
        ack_state=ACK_ACKNOWLEDGED,
    )
    env.db.add(row)
    env.db.flush()
    env.db.commit()
    return pso, mirror_line, inquiry, row


def _seed_ref_only_so_line(env, *, so_number: str, product_id: str, source_ref: str):
    """A `_seed_so_line` row whose only purpose is giving `from_so_line_ref` something
    real to resolve against - never competing demand of its own.

    `write_line_ref_claims` (the ingest's own ref-resolution) opens a REAL claim
    against whatever this line's `source_ref` resolves to, through `claim_placed_on_
    po` - a genuine, correct side effect of AutoCount pairing, not a bug. But
    `order_link_service`'s own claim-outstanding read (`_claims_of`: `line_status ==
    'open' and qty_ordered - qty_delivered`) then reserves that claim's SHARE of the
    PO line's capacity via G7 dedication (`_dedication_for_target`) - and `_seed_so_
    line`'s own default (`qty_ordered=10, qty_delivered` unset) leaves that share
    genuinely outstanding, competing with `TestLinkFollowsBookPairing`'s own 90 / 120
    mirror-row need for capacity the fixture never meant to contest. Fully delivered
    here so that claim's outstanding reads zero and drops out of dedication entirely.
    """
    so, line = _seed_so_line(
        env, so_number=so_number, product_id=product_id, source_ref=source_ref,
    )
    line.qty_delivered = line.qty_ordered
    env.db.commit()
    return so, line


def _pool_warehouse_ref(env) -> str:
    """A genuine POOL location - a warehouse SOME OTHER warehouse's `pool_warehouse_
    id` points at, the FK-based test `_pool_codes()` reads (R11, `PLAN-scm-oi-draft-
    links.md`): the AUTOMATIC (`manual=False`) SPO candidate walk in `_candidates_
    for_row` offers only a pool line, and `place_on_po_allocations` runs `follow_book_
    repairing`'s own placement in automatic mode (`auto_trigger=trigger`, never
    `None`). `env.warehouse_ref`'s plain depot is not a pool - nothing points its own
    `pool_warehouse_id` at it - so an SPO allocation seeded there is SHOWN (visible in
    the lightbox) but never OFFERED to the automatic walk, the same fixture shape
    `test_order_inquiry_draft_links.py::_pooled` exists for.
    """
    pool = Warehouse(
        id=str(uuid.uuid4()), company_id=env.company_a,
        warehouse_code=f"{MARKER}POOL{uuid.uuid4().hex[:6]}",
        warehouse_name=f"{MARKER} pool",
    )
    env.db.add(pool)
    env.db.flush()
    child = Warehouse(
        id=str(uuid.uuid4()), company_id=env.company_a,
        warehouse_code=f"{MARKER}SIB{uuid.uuid4().hex[:6]}",
        warehouse_name=f"{MARKER} sub-inventory",
        pool_warehouse_id=pool.id,
    )
    env.db.add(child)
    env.db.flush()
    env.db.commit()
    return env._link("warehouses", pool.id, "POOL")


class TestLinkFollowsBookPairing:
    """AC-RL-40 to AC-RL-46. `follow_book_repairing` is the hook `ingest.py` calls
    after the existing relink hook for POs and beside the forward-match hook for
    SPOs (S5's own words), so every scenario here is a real re-push through the
    real ingest routes - never a direct call into a service method that does not
    exist yet."""

    def test_po_line_ref_moved_follows_to_new_line_row(self, env):
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        ref_a, ref_b = _ref("SOLA"), _ref("SOLB")
        so_a, core_line_a = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_a,
        )
        so_b, core_line_b = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOB-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_b,
        )
        _pso_a, _line_a, _inquiry_a, row_a = _mirror_row(
            env, core_line=core_line_a, product_id=product_id, qty="90",
        )
        _pso_b, _line_b, _inquiry_b, row_b = _mirror_row(
            env, core_line=core_line_b, product_id=product_id, qty="120",
        )

        line = _po_line(env, from_so_line_ref=ref_a, qty_ordered=90)
        record = _po_record(env, lines=[line])
        res = env.post(INGEST_PO, [record])
        assert res.json()["records"][0]["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]

        # Row A's own link - never written by the ingest (claims never create
        # links, the plan's own "Facts" section) - stands in for what purchasing
        # already arranged before the book moved.
        env.db.add(OrderInquiryLink(
            id=str(uuid.uuid4()), company_id=env.company_a, row_id=row_a.id,
            po_line_id=po_line["id"], document=record["po_number"], qty=Decimal("90"),
            auto=True,
        ))
        env.db.commit()

        repush_line = _po_line(env, ref=line["source_ref"], from_so_line_ref=ref_b, qty_ordered=90)
        repush = dict(record, lines=[repush_line])
        res2 = env.post(INGEST_PO, [repush])
        assert res2.json()["records"][0]["outcome"] == "updated", res2.text

        env.db.expire_all()
        links_a = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).all()
        links_b = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_b.id).all()
        env.db.refresh(row_a)

        assert links_a == []
        assert "AutoCount moved" in (row_a.note or ""), row_a.note
        assert record["po_number"] in (row_a.note or ""), row_a.note
        assert so_b.so_number in (row_a.note or ""), row_a.note
        assert row_a.state == INQUIRY_RAISED
        assert len(links_b) == 1, links_b
        assert str(links_b[0].po_line_id) == str(po_line["id"])
        assert Decimal(str(links_b[0].qty)) == Decimal("90")
        assert links_b[0].auto is True

    def test_po_line_ref_moved_no_row_unlinks_only(self, env):
        """AC-RL-41: SO line B has no linkable row at all - the link comes off row
        A and nothing is placed anywhere; the PO line's capacity is genuinely
        free (`_linked_by_target` reads no link naming it)."""
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        ref_a, ref_b = _ref("SOLA"), _ref("SOLB")
        so_a, core_line_a = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_a,
        )
        # SO line B exists in the core book but has NO project mirror / OI row.
        _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOB-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_b,
        )
        _pso_a, _line_a, _inquiry_a, row_a = _mirror_row(
            env, core_line=core_line_a, product_id=product_id, qty="60",
        )

        line = _po_line(env, from_so_line_ref=ref_a, qty_ordered=60)
        record = _po_record(env, lines=[line])
        res = env.post(INGEST_PO, [record])
        assert res.json()["records"][0]["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]
        env.db.add(OrderInquiryLink(
            id=str(uuid.uuid4()), company_id=env.company_a, row_id=row_a.id,
            po_line_id=po_line["id"], document=record["po_number"], qty=Decimal("60"),
            auto=True,
        ))
        env.db.commit()

        repush_line = _po_line(env, ref=line["source_ref"], from_so_line_ref=ref_b, qty_ordered=60)
        repush = dict(record, lines=[repush_line])
        res2 = env.post(INGEST_PO, [repush])
        assert res2.json()["records"][0]["outcome"] == "updated", res2.text

        env.db.expire_all()
        links_a = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).all()
        assert links_a == []
        remaining = (
            env.db.query(OrderInquiryLink)
            .filter(OrderInquiryLink.po_line_id == po_line["id"])
            .all()
        )
        assert remaining == [], "the PO line's capacity must be genuinely free"

    def test_spo_ref_moved_follows(self, env):
        """AC-RL-42 (in-place repush): the SPO twin of AC-RL-40 - an ordinary
        re-push of an ALREADY-REF'D allocation, not the supersede path."""
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        ref_a, ref_b = _ref("SOLA"), _ref("SOLB")
        so_a, core_line_a = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_a,
        )
        so_b, core_line_b = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOB-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_b,
        )
        _pso_a, _line_a, _inquiry_a, row_a = _mirror_row(
            env, core_line=core_line_a, product_id=product_id, qty="12",
        )
        _pso_b, _line_b, _inquiry_b, row_b = _mirror_row(
            env, core_line=core_line_b, product_id=product_id, qty="20",
        )

        pool_ref = _pool_warehouse_ref(env)
        line = _spo_line(env, from_so_line_ref=ref_a, qty_ordered=12, warehouse_ref=pool_ref)
        record = _spo_record(env, lines=[line], supplier_ref=env.supplier_ref)
        res = env.post(INGEST_SPO, [record])
        assert res.json()["records"][0]["outcome"] == "created", res.text
        allocation = _spo_rows(env, record["spo_number"])[0]

        env.db.add(OrderInquiryLink(
            id=str(uuid.uuid4()), company_id=env.company_a, row_id=row_a.id,
            spo_allocation_id=allocation["id"], document=record["spo_number"],
            qty=Decimal("12"), auto=True,
        ))
        env.db.commit()

        repush_line = _spo_line(
            env, ref=line["source_ref"], from_so_line_ref=ref_b, qty_ordered=12,
            warehouse_ref=pool_ref,
        )
        repush = dict(record, lines=[repush_line])
        res2 = env.post(INGEST_SPO, [repush])
        assert res2.json()["records"][0]["outcome"] == "updated", res2.text

        env.db.expire_all()
        links_a = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).all()
        links_b = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_b.id).all()
        env.db.refresh(row_a)

        assert links_a == []
        assert "AutoCount moved" in (row_a.note or ""), row_a.note
        assert len(links_b) == 1, links_b
        assert str(links_b[0].spo_allocation_id) == str(allocation["id"])

    def test_spo_ref_moved_through_supersede_follows(self, env):
        """AC-RL-42 (through `_supersede_xlsx_rows`): the link sits on an XLSX-ERA
        allocation with no ref at all. The ESB's first push for this SPO/product/
        location supersedes it (D25) - a NEW allocation row, under a NEW id,
        carrying the payload's `from_so_line_ref` naming SO line B. `follow_book_
        repairing` has to read the supersede's own move record, not merely a ref
        that changed on the SAME row - `_supersede_xlsx_rows` "records the
        superseded row's ref against the new row" (the plan's own S5 facts)."""
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        # A genuine POOL location, not `env.warehouse_ref`'s plain depot - the automatic
        # SPO walk (R11) offers only a pool line, and the placement onto row B runs in
        # automatic mode (`follow_book_repairing`'s own `auto_trigger=trigger`).
        pool_ref = _pool_warehouse_ref(env)
        wh_id = env.refs.resolve(entity_type="warehouses", source_ref=pool_ref)
        wh_code = env.db.execute(
            text("SELECT warehouse_code FROM warehouses WHERE id = :id"), {"id": wh_id}
        ).scalar()
        ref_a, ref_b = _ref("SOLA"), _ref("SOLB")
        so_a, core_line_a = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_a,
        )
        so_b, core_line_b = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOB-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_b,
        )
        _pso_a, _line_a, _inquiry_a, row_a = _mirror_row(
            env, core_line=core_line_a, product_id=product_id, qty="10",
        )
        _pso_b, _line_b, _inquiry_b, row_b = _mirror_row(
            env, core_line=core_line_b, product_id=product_id, qty="15",
        )

        number = f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}"
        legacy = _seed_legacy_row(
            env, spo_number=number, spo_line_number=1, location_code=wh_code,
            allocated_quantity=10,
        )
        env.db.add(OrderInquiryLink(
            id=str(uuid.uuid4()), company_id=env.company_a, row_id=row_a.id,
            spo_allocation_id=legacy.id, document=number, qty=Decimal("10"), auto=True,
        ))
        env.db.commit()

        line = _spo_line(env, warehouse_ref=pool_ref, qty_ordered=10, from_so_line_ref=ref_b)
        record = _spo_record(env, number=number, lines=[line], supplier_ref=env.supplier_ref)
        res = env.post(INGEST_SPO, [record])
        assert res.json()["records"][0].get("lines", {}).get("superseded") == 1, res.text

        env.db.expire_all()
        links_a = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).all()
        links_b = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_b.id).all()

        assert links_a == []
        assert len(links_b) == 1, links_b
        new_rows = {str(r["id"]) for r in _spo_rows(env, number)}
        assert str(links_b[0].spo_allocation_id) in new_rows

    def test_received_document_ref_move_follows(self, env):
        """AC-FB-34 (D4, `PLAN-oi-follow-book-chain.md`): AC-RL-43 is RETIRED,
        owner ruling 18 Sep ("lift fully") - a fully received PO line's ref
        move follows the book anyway, exactly like an open one's."""
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        ref_a, ref_b = _ref("SOLA"), _ref("SOLB")
        so_a, core_line_a = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_a,
        )
        so_b, core_line_b = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOB-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_b,
        )
        _pso_a, _line_a, _inquiry_a, row_a = _mirror_row(
            env, core_line=core_line_a, product_id=product_id, qty="4",
        )
        _pso_b, _line_b, _inquiry_b, row_b = _mirror_row(
            env, core_line=core_line_b, product_id=product_id, qty="4",
        )

        line = _po_line(env, from_so_line_ref=ref_a, qty_ordered=4, qty_received=4)
        record = _po_record(env, lines=[line])
        res = env.post(INGEST_PO, [record])
        assert res.json()["records"][0]["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]
        assert po_line["line_status"] == "closed", "the fixture has to be genuinely received"
        env.db.add(OrderInquiryLink(
            id=str(uuid.uuid4()), company_id=env.company_a, row_id=row_a.id,
            po_line_id=po_line["id"], document=record["po_number"], qty=Decimal("4"),
            auto=True,
        ))
        env.db.commit()

        repush_line = _po_line(
            env, ref=line["source_ref"], from_so_line_ref=ref_b, qty_ordered=4, qty_received=4,
        )
        repush = dict(record, lines=[repush_line])
        res2 = env.post(INGEST_PO, [repush])
        assert res2.json()["records"][0]["outcome"] == "updated", res2.text

        env.db.expire_all()
        links_a = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).all()
        links_b = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_b.id).all()
        assert links_a == [], links_a
        row_a_db = env.db.query(OrderInquiryRow).filter(OrderInquiryRow.id == row_a.id).one()
        assert "AutoCount moved" in (row_a_db.note or ""), row_a_db.note
        assert len(links_b) == 1, links_b
        assert str(links_b[0].po_line_id) == str(po_line["id"])

    def test_manual_link_follows_book(self, env):
        """AC-RL-44: a link written BY HAND (`auto=False`) follows the book the
        same way an automatic one does - the same shape as AC-RL-40."""
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        ref_a, ref_b = _ref("SOLA"), _ref("SOLB")
        so_a, core_line_a = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_a,
        )
        so_b, core_line_b = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOB-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_b,
        )
        _pso_a, _line_a, _inquiry_a, row_a = _mirror_row(
            env, core_line=core_line_a, product_id=product_id, qty="25",
        )
        _pso_b, _line_b, _inquiry_b, row_b = _mirror_row(
            env, core_line=core_line_b, product_id=product_id, qty="40",
        )

        line = _po_line(env, from_so_line_ref=ref_a, qty_ordered=25)
        record = _po_record(env, lines=[line])
        res = env.post(INGEST_PO, [record])
        assert res.json()["records"][0]["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]
        env.db.add(OrderInquiryLink(
            id=str(uuid.uuid4()), company_id=env.company_a, row_id=row_a.id,
            po_line_id=po_line["id"], document=record["po_number"], qty=Decimal("25"),
            auto=False,
        ))
        env.db.commit()

        repush_line = _po_line(env, ref=line["source_ref"], from_so_line_ref=ref_b, qty_ordered=25)
        repush = dict(record, lines=[repush_line])
        res2 = env.post(INGEST_PO, [repush])
        assert res2.json()["records"][0]["outcome"] == "updated", res2.text

        env.db.expire_all()
        links_a = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).all()
        links_b = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_b.id).all()
        assert links_a == []
        assert len(links_b) == 1, links_b
        assert str(links_b[0].po_line_id) == str(po_line["id"])

    def test_ref_cleared_unlinks(self, env):
        """AC-RL-45 (first half): a re-push with `from_so_line_ref: null` removes
        the link with the `AutoCount removed` note, and places nothing."""
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        ref_a = _ref("SOLA")
        so_a, core_line_a = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_a,
        )
        _pso_a, _line_a, _inquiry_a, row_a = _mirror_row(
            env, core_line=core_line_a, product_id=product_id, qty="18",
        )

        line = _po_line(env, from_so_line_ref=ref_a, qty_ordered=18)
        record = _po_record(env, lines=[line])
        res = env.post(INGEST_PO, [record])
        assert res.json()["records"][0]["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]
        env.db.add(OrderInquiryLink(
            id=str(uuid.uuid4()), company_id=env.company_a, row_id=row_a.id,
            po_line_id=po_line["id"], document=record["po_number"], qty=Decimal("18"),
            auto=True,
        ))
        env.db.commit()

        repush_line = _po_line(env, ref=line["source_ref"], from_so_line_ref=None, qty_ordered=18)
        repush = dict(record, lines=[repush_line])
        res2 = env.post(INGEST_PO, [repush])
        assert res2.json()["records"][0]["outcome"] == "updated", res2.text

        env.db.expire_all()
        links_a = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).all()
        env.db.refresh(row_a)

        assert links_a == []
        assert "AutoCount removed" in (row_a.note or ""), row_a.note
        assert record["po_number"] in (row_a.note or ""), row_a.note

    def test_same_ref_repush_changes_nothing(self, env):
        """AC-RL-45 (second half): a re-push with the SAME ref moves nothing at
        all - no FURTHER note, the same link untouched.

        Seeding repair (fix round, 19 Sep): S2's own ingest hook now links row A
        during the FIRST push already (its line's `from_so_line_ref` names row
        A's exact core line), so the manual `OrderInquiryLink` add this test used
        to seed here would be a SECOND link on the same row and raise
        `MultipleResultsFound` - asserted on the hook's own link instead."""
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        ref_a = _ref("SOLA")
        so_a, core_line_a = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_a,
        )
        _pso_a, _line_a, _inquiry_a, row_a = _mirror_row(
            env, core_line=core_line_a, product_id=product_id, qty="9",
        )

        line = _po_line(env, from_so_line_ref=ref_a, qty_ordered=9)
        record = _po_record(env, lines=[line])
        res = env.post(INGEST_PO, [record])
        assert res.json()["records"][0]["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]
        link_before = (
            env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).one()
        )
        assert str(link_before.po_line_id) == str(po_line["id"]), link_before
        link_id_before = str(link_before.id)
        note_before = row_a.note

        repush_line = _po_line(env, ref=line["source_ref"], from_so_line_ref=ref_a, qty_ordered=9)
        repush = dict(record, lines=[repush_line])
        res2 = env.post(INGEST_PO, [repush])
        assert res2.json()["records"][0]["outcome"] == "updated", res2.text

        env.db.expire_all()
        links_a = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).all()
        env.db.refresh(row_a)

        assert len(links_a) == 1, links_a
        assert str(links_a[0].id) == link_id_before
        assert row_a.note == note_before


    # -------------------------------------------------- security review findings (S5)
    # AC-RL-47 to AC-RL-51: `follow_book_repairing` (app/services/project_order_
    # inquiry_service.py ~:1182-1430) and its capture sites (document_ingest_
    # service.py ~:1356, shipping_order_ingest_service.py ~:449/:1115) had five
    # defects a security review found - `_resolve_ref_line` conflating "unresolved"
    # with "explicitly cleared" (AC-RL-47), no company scoping on the ref lookup
    # (AC-RL-48), a captured move surviving its own record's savepoint rollback
    # (AC-RL-49), no cap on moves applied per request (AC-RL-50), and `.first()`
    # picking an arbitrary line when a ref is ambiguous (AC-RL-51). Every test
    # below is RED against the current build for exactly that reason - never a
    # fixture bug.

    def test_ref_moved_to_a_line_we_do_not_hold_yet_changes_nothing(self, env):
        """AC-RL-47: a ref naming a well-formed but UNRESOLVABLE sales-order line
        (the SO has not been pushed yet - the ordinary case) must not be read the
        same way an explicit `null` is (AC-RL-45). `_resolve_ref_line` returns
        `(None, None)` for BOTH today, so an unresolved ref currently unlinks row A
        and stamps an "AutoCount removed" note exactly as a genuine clear does.

        Seeding repair (fix round, 19 Sep): S2's own ingest hook now links row A
        during the FIRST push already (its line's `from_so_line_ref` names row
        A's exact core line) - asserted on the hook's own link, never a second,
        manual one (`MultipleResultsFound`)."""
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        ref_a = _ref("SOLA")
        so_a, core_line_a = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_a,
        )
        _pso_a, _line_a, _inquiry_a, row_a = _mirror_row(
            env, core_line=core_line_a, product_id=product_id, qty="22",
        )

        line = _po_line(env, from_so_line_ref=ref_a, qty_ordered=22)
        record = _po_record(env, lines=[line])
        res = env.post(INGEST_PO, [record])
        assert res.json()["records"][0]["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]
        link_before = (
            env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).one()
        )
        assert str(link_before.po_line_id) == str(po_line["id"]), link_before
        link_id_before = str(link_before.id)
        note_before = row_a.note

        # A ref shaped exactly like a real one - three segments in the family
        # `_resolve_ref_line` matches on exactly - naming a document key nothing
        # in this company has ever pushed.
        unresolvable_ref = f"{MARKER}:99999999:99999998"
        repush_line = _po_line(
            env, ref=line["source_ref"], from_so_line_ref=unresolvable_ref, qty_ordered=22,
        )
        repush = dict(record, lines=[repush_line])
        res2 = env.post(INGEST_PO, [repush])
        assert res2.json()["records"][0]["outcome"] == "updated", res2.text

        env.db.expire_all()
        links_a = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).all()
        env.db.refresh(row_a)

        assert len(links_a) == 1, "an unresolved ref must not touch row A's link at all"
        assert str(links_a[0].id) == link_id_before
        assert row_a.note == note_before, "no note - nothing was actually observed to move"

    def test_ref_moved_to_another_companys_line_changes_nothing(self, env):
        """AC-RL-48: `_resolve_ref_line` carries no company filter at all - a ref
        that happens to name ANOTHER company's sales-order line resolves as
        confidently as one of ours, and row A's real link is unlinked for a "move"
        this company never authored or even has visibility into.

        Seeding repair (fix round, 19 Sep): S2's own ingest hook now links row A
        during the FIRST push already (its line's `from_so_line_ref` names row
        A's exact core line) - asserted on the hook's own link, never a second,
        manual one (`MultipleResultsFound`)."""
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        ref_a = _ref("SOLA")
        so_a, core_line_a = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_a,
        )
        _pso_a, _line_a, _inquiry_a, row_a = _mirror_row(
            env, core_line=core_line_a, product_id=product_id, qty="14",
        )

        # A real sales-order line, resolvable in every way `_resolve_ref_line`
        # checks - EXCEPT it belongs to company B, not this push's own anchor.
        foreign_ref = _ref("SOLFOREIGN")
        foreign_so = SalesOrder(
            id=str(uuid.uuid4()), so_number=f"{MARKER}-FOREIGN-{uuid.uuid4().hex[:8]}",
            status="open", company_id=env.company_b,
        )
        env.db.add(foreign_so)
        env.db.flush()
        foreign_line = SalesOrderLine(
            id=str(uuid.uuid4()), sales_order_id=foreign_so.id, product_id=product_id,
            qty_ordered=Decimal("14"), source_ref=foreign_ref, company_id=env.company_b,
        )
        env.db.add(foreign_line)
        env.db.flush()
        env.db.commit()

        line = _po_line(env, from_so_line_ref=ref_a, qty_ordered=14)
        record = _po_record(env, lines=[line])
        res = env.post(INGEST_PO, [record])
        assert res.json()["records"][0]["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]
        link_before = (
            env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).one()
        )
        assert str(link_before.po_line_id) == str(po_line["id"]), link_before
        link_id_before = str(link_before.id)
        note_before = row_a.note

        # Pushed under company A's own anchor (`env.post` defaults to
        # `env.company_a_code`) - the foreign ref is never named by this push's
        # own principal, only coincidentally resolvable by an unscoped lookup.
        repush_line = _po_line(
            env, ref=line["source_ref"], from_so_line_ref=foreign_ref, qty_ordered=14,
        )
        repush = dict(record, lines=[repush_line])
        res2 = env.post(INGEST_PO, [repush])
        assert res2.json()["records"][0]["outcome"] == "updated", res2.text

        env.db.expire_all()
        links_a = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).all()
        env.db.refresh(row_a)

        assert len(links_a) == 1, "a foreign company's line must not move row A's link"
        assert str(links_a[0].id) == link_id_before
        assert row_a.note == note_before
        # Nothing placed for the OTHER company either - no link this push wrote
        # names the foreign line's own company.
        other_company_links = (
            env.db.query(OrderInquiryLink)
            .filter(OrderInquiryLink.company_id == env.company_b)
            .all()
        )
        assert other_company_links == []

    def test_move_captured_in_a_record_that_later_fails_is_not_applied(self, env):
        """AC-RL-49: `self.ref_moves` is a plain Python list on the ingest service
        instance, appended to inside `_sync_lines` - it is NOT part of the
        record's own SAVEPOINT, so a move captured for line 1 survives even when
        line 2 of the SAME record fails later in the SAME `_sync_lines` call and
        `_ingest_one`'s `except Exception` (~:521) rolls the whole record back.
        The route's post-commit hook (`_run_document_hooks`) then reads `service.
        ref_moves` unconditionally and applies a move whose own ref change was
        never actually persisted.

        The reliable per-record failure: `qty_ordered` has no upper bound in the
        canonical payload schema (`Decimal = Field(..., ge=0)`) but the column is
        `Numeric(15, 4)` (11 integer digits) - a 15-digit value passes validation
        and overflows at `_sync_lines`' own flush, caught by the generic `except
        Exception` (empirically verified: the record reports `failed`).

        Seeding repair (fix round, 19 Sep): S2's own ingest hook now links row A
        during the FIRST push already (its line's `from_so_line_ref` names row
        A's exact core line) - asserted on the hook's own link, never a second,
        manual one (`MultipleResultsFound`)."""
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        ref_a, ref_b = _ref("SOLA"), _ref("SOLB")
        so_a, core_line_a = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_a,
        )
        so_b, core_line_b = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOB-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_b,
        )
        _pso_a, _line_a, _inquiry_a, row_a = _mirror_row(
            env, core_line=core_line_a, product_id=product_id, qty="30",
        )
        _pso_b, _line_b, _inquiry_b, row_b = _mirror_row(
            env, core_line=core_line_b, product_id=product_id, qty="45",
        )

        line1 = _po_line(env, from_so_line_ref=ref_a, qty_ordered=30)
        record = _po_record(env, lines=[line1])
        res = env.post(INGEST_PO, [record])
        assert res.json()["records"][0]["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]
        link_before = (
            env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).one()
        )
        assert str(link_before.po_line_id) == str(po_line["id"]), link_before
        link_id_before = str(
            env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).one().id
        )

        # ONE record, TWO lines: line 1 moves ref_a -> ref_b (captured into
        # `service.ref_moves` mid-`_sync_lines`), line 2 overflows `Numeric(15,4)`
        # at the SAME call's flush, failing the whole record.
        repush_line1 = _po_line(
            env, ref=line1["source_ref"], from_so_line_ref=ref_b, qty_ordered=30,
        )
        bad_line2 = _po_line(env, qty_ordered=999999999999999)
        repush = dict(record, lines=[repush_line1, bad_line2])
        res2 = env.post(INGEST_PO, [repush])
        entry = res2.json()["records"][0]
        assert entry["outcome"] == "failed", res2.text

        env.db.expire_all()
        links_a = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).all()
        links_b = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_b.id).all()

        assert len(links_a) == 1, "the failed record's own captured move must not be applied"
        assert str(links_a[0].id) == link_id_before
        assert str(links_a[0].po_line_id) == str(po_line["id"])
        assert links_b == [], "nothing should have been placed for a move that never really happened"

    def test_follow_book_repairing_caps_moves_per_request(self, env, monkeypatch, caplog):
        """AC-RL-50: nothing bounds how many moves ONE `follow_book_repairing` call
        processes - a single malicious or malformed ESB push naming thousands of
        `from_so_line_ref` changes runs the full per-move query fan-out
        (`_resolve_ref_line`, `_linkable_row_for_core_line`, `place_on_po_
        allocations`) unbounded. `FOLLOW_BOOK_REPAIRING_MAX_MOVES` (named on
        `ProjectOrderInquiryService`) does not exist yet, so this monkeypatches
        it in (`raising=False` - the attribute genuinely is not there today) to
        cap 3 real moves at 2 without seeding 200+.

        Seeding repair (fix round, 19 Sep): S2's own ingest hook now links row
        A during the FIRST push already (its line's `from_so_line_ref` names
        row A's exact core line) - the manual `OrderInquiryLink` add this test
        used to seed here became a SECOND link on the SAME row and target.
        `follow_book_repairing`'s own move capture reads every link on a
        target with no de-duplication by row, so one row holding two links on
        one target counted as TWO moves instead of one, throwing off the cap
        arithmetic across all three seeded rows (every row ended up unlinked
        instead of exactly one being left alone, confirmed by running this
        test alone first). Asserted on the hook's own link instead.

        Captain's ruling (fix round, 19 Sep 2026): AC-RL-50's own property -
        one push cannot fan out into unbounded work - still holds: this push's
        follow work is bounded by TWO caps now, `FOLLOW_BOOK_REPAIRING_MAX_
        MOVES` on the move sweep here and `FOLLOW_BOOK_FOR_ROWS_MAX_ROWS` on
        S2's own ingest hook (`test_follow_book_hook_cap_bounds_the_same_push`
        below guards that second one). What does NOT hold any more is this
        test's own incidental assumption that the THIRD line (the one this
        cap drops) is left exactly where it was: under owner rulings D3 and D4
        (19 Sep 2026, "we must follow autocount link always") S2's hook
        legitimately follows the book for that third line anyway, within ITS
        OWN cap (200 here, never reached by 3 rows) - intended behaviour, not
        a leak past the cap under test. The third line's outcome is
        deterministic here (`FOLLOW_BOOK_FOR_ROWS_MAX_ROWS` is untouched, so
        every one of the 3 candidate rows the hook resolves is processed, and
        only the third's own PO line is still wrongly held by a different
        sales-order line by the time the hook runs)."""
        from app.services.project_order_inquiry_service import ProjectOrderInquiryService

        monkeypatch.setattr(
            ProjectOrderInquiryService, "FOLLOW_BOOK_REPAIRING_MAX_MOVES", 2, raising=False,
        )
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)

        rows_a = []
        rows_b = []
        repush_records = []
        for i in range(3):
            ref_a, ref_b = _ref(f"SOLA{i}"), _ref(f"SOLB{i}")
            so_a, core_line_a = _seed_ref_only_so_line(
                env, so_number=f"{MARKER}-SOA{i}-{uuid.uuid4().hex[:8]}", product_id=product_id,
                source_ref=ref_a,
            )
            so_b, core_line_b = _seed_ref_only_so_line(
                env, so_number=f"{MARKER}-SOB{i}-{uuid.uuid4().hex[:8]}", product_id=product_id,
                source_ref=ref_b,
            )
            _pso_a, _line_a, _inquiry_a, row_a = _mirror_row(
                env, core_line=core_line_a, product_id=product_id, qty="9",
            )
            _pso_b, _line_b, _inquiry_b, row_b = _mirror_row(
                env, core_line=core_line_b, product_id=product_id, qty="11",
            )

            line = _po_line(env, from_so_line_ref=ref_a, qty_ordered=9)
            record = _po_record(env, lines=[line])
            res = env.post(INGEST_PO, [record])
            assert res.json()["records"][0]["outcome"] == "created", res.text
            header = env.header("purchase_orders", record["source_ref"])
            po_line = env.po_lines(header["id"])[0]
            link_before = (
                env.db.query(OrderInquiryLink)
                .filter(OrderInquiryLink.row_id == row_a.id)
                .one()
            )
            assert str(link_before.po_line_id) == str(po_line["id"]), link_before
            rows_a.append(row_a)
            rows_b.append(row_b)
            repush_records.append(dict(
                record,
                lines=[_po_line(env, ref=line["source_ref"], from_so_line_ref=ref_b, qty_ordered=9)],
            ))

        with caplog.at_level(logging.WARNING, logger="app.services.project_order_inquiry_service"):
            res2 = env.post(INGEST_PO, repush_records)
        assert all(r["outcome"] == "updated" for r in res2.json()["records"]), res2.text
        assert res2.json()["summary"].get("book_repair_moves_dropped") == 1, res2.text

        assert any(
            "follow_book_repairing" in record.getMessage() for record in caplog.records
        ), "the overflow must be logged, naming what was skipped"

        # The first two moves apply normally (`ref_moves` is built in submission
        # order, one entry per record, so the cap of 2 keeps exactly these two).
        env.db.expire_all()
        for row_a, row_b in zip(rows_a[:2], rows_b[:2]):
            a_links = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).all()
            b_links = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_b.id).all()
            assert a_links == [], a_links
            assert len(b_links) == 1, b_links

        # The THIRD line is the one `follow_book_repairing`'s own cap dropped -
        # but D3/D4 (owner ruling 19 Sep: "we must follow autocount link
        # always") means S2's OWN ingest hook still follows it: its document
        # now sits on row B (the line the repush actually names), and the
        # previous holder (row A) carries the "AutoCount states" note, not
        # "AutoCount moved" (that fragment belongs to `_follow_one_move`,
        # which never touched this link - the cap kept it out of `moving`
        # entirely; the log line above is what proves that).
        third_row_a, third_row_b = rows_a[2], rows_b[2]
        a_links = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == third_row_a.id).all()
        b_links = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == third_row_b.id).all()
        assert a_links == [], (
            "the third line's document now sits on row B, the line the repush names - D3/D4"
        )
        assert len(b_links) == 1, b_links
        third_row_a_db = (
            env.db.query(OrderInquiryRow).filter(OrderInquiryRow.id == third_row_a.id).one()
        )
        assert "AutoCount states" in (third_row_a_db.note or ""), third_row_a_db.note

    def test_follow_book_hook_cap_bounds_the_same_push(self, env, monkeypatch, caplog):
        """AC-RL-50, the OTHER half of the bound: S2's own ingest hook
        (`_run_follow_book_po_hook` -> `follow_book_for_rows`) fans out over
        every row a written PO line's `from_so_line_ref` resolves to, and D3/D4
        (owner ruling 19 Sep: "we must follow autocount link always") make it
        follow the book even for a line `follow_book_repairing`'s own cap
        dropped - the test above. Left unguarded, THIS fan-out would be the
        actual unbounded surface AC-RL-50 exists for; `FOLLOW_BOOK_FOR_ROWS_
        MAX_ROWS` is what bounds it.

        Five lines (more than either cap alone), `FOLLOW_BOOK_REPAIRING_MAX_
        MOVES=2` and `FOLLOW_BOOK_FOR_ROWS_MAX_ROWS=1` monkeypatched together.
        Tightened (review round item 6, captain's ruling): AC-FB-24's cap
        applies AFTER narrowing to linkable rows the book names, over a
        DETERMINISTIC order (`created_at`, `id`) - so once that lands, the
        rows cap's one admitted slot is the EARLIEST-created of the five
        candidates, which is `rows_a[0]` (created first in the loop below),
        the very same row `follow_book_repairing`'s own move cap (submission
        order, first 2 of 5) already completed. The hook's one allowance is
        then spent on a row with nothing left to do - a no-op - so the total
        number of rows this push actually completes stays at exactly 2,
        never 3. Measured today: `_rows_for_core_line_refs` carries no
        `ORDER BY` at all, so the rows cap's one slot lands on a DIFFERENT,
        not-yet-covered row instead, and completed reads 3."""
        from app.services.project_order_inquiry_service import ProjectOrderInquiryService

        monkeypatch.setattr(
            ProjectOrderInquiryService, "FOLLOW_BOOK_REPAIRING_MAX_MOVES", 2, raising=False,
        )
        monkeypatch.setattr(
            ProjectOrderInquiryService, "FOLLOW_BOOK_FOR_ROWS_MAX_ROWS", 1, raising=False,
        )
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)

        rows_a = []
        repush_records = []
        for i in range(5):
            ref_a, ref_b = _ref(f"SOLA{i}"), _ref(f"SOLB{i}")
            so_a, core_line_a = _seed_ref_only_so_line(
                env, so_number=f"{MARKER}-SOA{i}-{uuid.uuid4().hex[:8]}", product_id=product_id,
                source_ref=ref_a,
            )
            so_b, core_line_b = _seed_ref_only_so_line(
                env, so_number=f"{MARKER}-SOB{i}-{uuid.uuid4().hex[:8]}", product_id=product_id,
                source_ref=ref_b,
            )
            _pso_a, _line_a, _inquiry_a, row_a = _mirror_row(
                env, core_line=core_line_a, product_id=product_id, qty="9",
            )
            _pso_b, _line_b, _inquiry_b, row_b = _mirror_row(
                env, core_line=core_line_b, product_id=product_id, qty="11",
            )
            # Pin the CANDIDATE row's (row_b) creation order explicitly: within
            # one open transaction Postgres's `now()` is transaction-start time,
            # stable for every row this test seeds, so the `created_at` server
            # default ties across all 5 and the (created_at, id) cap order falls
            # back to a random UUID tiebreak - flaky between runs, not between
            # queries within one. row_b is what `_rows_for_core_line_refs`
            # actually orders (the rows-cap's candidate set, named by ref_b on
            # this push); row_a's own move is ordered by submission order
            # instead, so it needs no pin.
            row_b.created_at = datetime.utcnow() + timedelta(seconds=i)
            env.db.add(row_b)
            env.db.commit()

            line = _po_line(env, from_so_line_ref=ref_a, qty_ordered=9)
            record = _po_record(env, lines=[line])
            res = env.post(INGEST_PO, [record])
            assert res.json()["records"][0]["outcome"] == "created", res.text
            header = env.header("purchase_orders", record["source_ref"])
            po_line = env.po_lines(header["id"])[0]
            link_before = (
                env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).one()
            )
            assert str(link_before.po_line_id) == str(po_line["id"]), link_before
            rows_a.append(row_a)
            repush_records.append(dict(
                record,
                lines=[_po_line(env, ref=line["source_ref"], from_so_line_ref=ref_b, qty_ordered=9)],
            ))

        with caplog.at_level(logging.WARNING, logger="app.services.project_order_inquiry_service"):
            res2 = env.post(INGEST_PO, repush_records)
        assert all(r["outcome"] == "updated" for r in res2.json()["records"]), res2.text

        summary = res2.json()["summary"]
        # Deterministic set SIZES, never row order: 5 moves capped at 2 (drop
        # 3), 5 candidate rows resolved from the 5 written refs capped at 1
        # (drop 4) - each figure is its own cap's arithmetic, provable without
        # knowing which specific rows survive either cap.
        assert summary.get("book_repair_moves_dropped") == 3, summary
        assert summary.get("book_follow_rows_dropped") == 4, summary

        # Pinned identity (review round item 6): `follow_book_repairing`'s
        # move cap guarantees `rows_a[0]` and `rows_a[1]` move (submission
        # order, first 2 of 5). Once the rows cap narrows to linkable rows
        # FIRST and orders by (`created_at`, `id`), its one slot is
        # `rows_a[0]` too - already moved, so a no-op - and the total number
        # of rows this push actually completes is exactly 2, never 3.
        env.db.expire_all()
        completed = sum(
            1 for row_a in rows_a
            if env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).count() == 0
        )
        assert completed == 2, (
            f"completed={completed}: the rows cap's one slot must land on the SAME "
            "earliest row the moves cap already covered, a no-op, once both walk "
            "the same deterministic (created_at, id) order"
        )

    def test_ambiguous_ref_two_lines_same_source_ref_is_refused(self, env, caplog):
        """AC-RL-51: `_resolve_ref_line` reads `.first()` off a query that has no
        uniqueness guarantee on `source_ref` - two `sales_order_lines` rows
        sharing one ref (a data anomaly the ESB should never produce, but the
        column carries no unique constraint to refuse it) resolve to whichever
        one Postgres happens to return first, and the move is applied against a
        guess rather than refused.

        Seeding repair (fix round, 19 Sep): S2's own ingest hook now links row A
        during the FIRST push already (its line's `from_so_line_ref` names row
        A's exact core line) - asserted on the hook's own link, never a second,
        manual one (`MultipleResultsFound`). The push #2 ambiguous ref itself
        (`shared_ref`) never resolves to any order-inquiry row either (the two
        dupe lines carry no mirror), so S2's own hook has nothing to follow
        there and this stays a pure test of the AC-RL-51 refusal."""
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        ref_a = _ref("SOLA")
        so_a, core_line_a = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_a,
        )
        _pso_a, _line_a, _inquiry_a, row_a = _mirror_row(
            env, core_line=core_line_a, product_id=product_id, qty="18",
        )

        shared_ref = _ref("SHARED")
        dupe_so_1 = SalesOrder(
            id=str(uuid.uuid4()), so_number=f"{MARKER}-DUPE1-{uuid.uuid4().hex[:8]}",
            status="open", company_id=env.company_a,
        )
        dupe_so_2 = SalesOrder(
            id=str(uuid.uuid4()), so_number=f"{MARKER}-DUPE2-{uuid.uuid4().hex[:8]}",
            status="open", company_id=env.company_a,
        )
        env.db.add_all([dupe_so_1, dupe_so_2])
        env.db.flush()
        dupe_line_1 = SalesOrderLine(
            id=str(uuid.uuid4()), sales_order_id=dupe_so_1.id, product_id=product_id,
            qty_ordered=Decimal("5"), source_ref=shared_ref, company_id=env.company_a,
        )
        dupe_line_2 = SalesOrderLine(
            id=str(uuid.uuid4()), sales_order_id=dupe_so_2.id, product_id=product_id,
            qty_ordered=Decimal("5"), source_ref=shared_ref, company_id=env.company_a,
        )
        env.db.add_all([dupe_line_1, dupe_line_2])
        env.db.flush()
        env.db.commit()

        line = _po_line(env, from_so_line_ref=ref_a, qty_ordered=18)
        record = _po_record(env, lines=[line])
        res = env.post(INGEST_PO, [record])
        assert res.json()["records"][0]["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]
        link_before = (
            env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).one()
        )
        assert str(link_before.po_line_id) == str(po_line["id"]), link_before
        link_id_before = str(link_before.id)

        with caplog.at_level(logging.WARNING, logger="app.services.project_order_inquiry_service"):
            repush_line = _po_line(
                env, ref=line["source_ref"], from_so_line_ref=shared_ref, qty_ordered=18,
            )
            repush = dict(record, lines=[repush_line])
            res2 = env.post(INGEST_PO, [repush])
        assert res2.json()["records"][0]["outcome"] == "updated", res2.text

        env.db.expire_all()
        links_a = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).all()

        assert len(links_a) == 1, "an ambiguous ref must not move anything"
        assert str(links_a[0].id) == link_id_before
        assert str(links_a[0].po_line_id) == str(po_line["id"])
        assert len(caplog.records) >= 1, "an ambiguous ref must log a warning"

    def test_ref_moved_from_an_unresolvable_old_ref_is_a_no_op(self, env, caplog):
        """AC-RL-52 (S3, code review 17 Sep): a NON-null `old_ref` that resolves to
        no line is not the same fact as NO old ref at all (the genuine xlsx-
        supersede case, where the superseded row truly never carried one) -
        `_follow_one_move` currently treats `old_line_id is None` the SAME way
        either way, so an unresolvable old ref falls into the "every link on the
        target is a candidate" branch and sweeps up row A's real link even though
        nothing here proves it was ever on the line the move claims to be FROM.

        Assertion rewrite (fix round, 19 Sep, owner ruling D3 19 Sep 2026 "the
        book wins, always"): the AC-RL-52 guard itself still holds - the warning
        log line below ("did not resolve ... no-op") is `_follow_one_move`'s OWN
        early return, proof the sweep this test originally guarded against did
        NOT fire. But the SAME repush's `from_so_line_ref` now genuinely names
        row B's own core line for the whole of `po_line`'s capacity, and row B is
        in need - so S2's own ingest hook (a SEPARATE mechanism from the
        AC-RL-52 sweep) runs `follow_book_for_rows`, and D3 correctly displaces
        row A's link (sitting on a target the book now states for a DIFFERENT
        core line) to free it for row B. Row A loses the document with the
        "AutoCount states" note, never "AutoCount moved" (that note is
        `_follow_one_move`'s own, and `_follow_one_move` never touched this
        link at all - the log line proves it). This is a real book-naming
        collision the original seeding did not intend to create; kept AS THE
        RED CASE for this AC per the fix-round brief, since it is a more
        faithful proof of the AC-RL-52 guard than an inert repush would be (the
        guard is exactly what stops `_follow_one_move` from ALSO sweeping row A,
        leaving D3 as the only mechanism that legitimately touches it)."""
        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        ref_a, ref_b = _ref("SOLA"), _ref("SOLB")
        so_a, core_line_a = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOA-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_a,
        )
        so_b, core_line_b = _seed_ref_only_so_line(
            env, so_number=f"{MARKER}-SOB-{uuid.uuid4().hex[:8]}", product_id=product_id,
            source_ref=ref_b,
        )
        _pso_a, _line_a, _inquiry_a, row_a = _mirror_row(
            env, core_line=core_line_a, product_id=product_id, qty="16",
        )
        _pso_b, _line_b, _inquiry_b, row_b = _mirror_row(
            env, core_line=core_line_b, product_id=product_id, qty="20",
        )

        # The PO line's own `from_so_line_ref` never actually named row A's line -
        # it is a well-formed ref nothing resolves, seeded straight onto the line.
        unresolvable_old_ref = f"{MARKER}:88888888:88888887"
        line = _po_line(env, from_so_line_ref=unresolvable_old_ref, qty_ordered=16)
        record = _po_record(env, lines=[line])
        res = env.post(INGEST_PO, [record])
        assert res.json()["records"][0]["outcome"] == "created", res.text
        header = env.header("purchase_orders", record["source_ref"])
        po_line = env.po_lines(header["id"])[0]
        # Row A's own link, established by hand - the ref on the PO line has never
        # pointed at row A's line at all, which is the whole point: only a GENUINE
        # match should ever decide whether row A's link moves.
        env.db.add(OrderInquiryLink(
            id=str(uuid.uuid4()), company_id=env.company_a, row_id=row_a.id,
            po_line_id=po_line["id"], document=record["po_number"], qty=Decimal("16"),
            auto=True,
        ))
        env.db.commit()

        repush_line = _po_line(
            env, ref=line["source_ref"], from_so_line_ref=ref_b, qty_ordered=16,
        )
        repush = dict(record, lines=[repush_line])
        with caplog.at_level(logging.WARNING, logger="app.services.project_order_inquiry_service"):
            res2 = env.post(INGEST_PO, [repush])
        assert res2.json()["records"][0]["outcome"] == "updated", res2.text

        # The caplog assertion this test's own docstring promises: AC-RL-52's
        # guard is `_follow_one_move`'s own early return on the unresolvable
        # OLD ref, proven by its warning line, not merely inferred from the
        # note's wording below.
        assert any(
            "did not resolve" in record.getMessage() and "no-op" in record.getMessage()
            for record in caplog.records
        ), [record.getMessage() for record in caplog.records]

        env.db.expire_all()
        links_a = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_a.id).all()
        links_b = env.db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_b.id).all()

        # AC-RL-52's own guard held: `_follow_one_move` logged its no-op (the
        # unresolvable OLD ref) and never touched row A's link itself. What
        # actually moves it is D3 - the repush's `from_so_line_ref=ref_b` names
        # row B's own line for the whole of `po_line`, and row B is in need, so
        # S2's ingest hook displaces row A's link to free it for row B (owner
        # ruling D3, 19 Sep 2026: "the book wins, always").
        assert links_a == [], "row A's link is displaced by the book naming po_line for row B"
        row_a_db = env.db.query(OrderInquiryRow).filter(OrderInquiryRow.id == row_a.id).one()
        note = row_a_db.note or ""
        assert "AutoCount states" in note, note
        # Never `_follow_one_move`'s own vocabulary - that mechanism never
        # touched this link at all, proven above by the caplog no-op.
        assert "AutoCount moved" not in note, note
        assert "AutoCount removed" not in note, note
        assert len(links_b) == 1, links_b
        assert str(links_b[0].po_line_id) == str(po_line["id"])
