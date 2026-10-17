"""AC-BILLING S3-1 (crew Q4 (a)): an invoice line transferred from a DO links to the SO line
through the CRM's own DO line.

An invoice line pushed with `from_doc_type: "DO"` and `from_line_ref: "{book}:DO:{DocKey}:{DtlKey}"`
gets `sales_order_line_id` = the `sales_order_line_id` of the CRM DO line found EXACTLY by
`orders.company_id` = the anchor, `orders.source_book` = book, `orders.doc_key` = DocKey and
`order_lines.dtl_key` = DtlKey. Anything else stays NULL with the `from_*` values kept as sent.

Reuses the seeding and fixtures of `test_ingest_billing_documents.py` (company A holds the
masters and one SO line, `env.so_line_id`; company B is the other company).

Today the ingest looks `from_line_ref` up as an SO line `source_ref` only, so the first test
fails with `sales_order_line_id is None`; the NULL cases and the SO-ref case already hold and are
the guard that the hop does not over-link or break the old path.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.inventory import Warehouse
from app.models.order import Order, OrderLine

# The fixtures and helpers of the sibling file (pytest fixtures are found by import).
from tests.test_ingest_billing_documents import (  # noqa: F401 - env is a fixture
    MARKER,
    SO_LINE_REF,
    _minimal,
    _record,
    env,
)

BOOK = "SRT_DB"


def _warehouse(env, company_id: str) -> Warehouse:
    row = Warehouse(warehouse_code=f"ZZ{uuid.uuid4().hex[:6]}".upper(), company_id=company_id)
    env.db.add(row)
    env.db.flush()
    return row


def _do_line(
    env,
    *,
    company_id: str | None = None,
    book: str = BOOK,
    doc_key: int = 5001,
    dtl_key: int = 7001,
    so_line_id: str | None,
) -> OrderLine:
    """A CRM delivery order with one line; `so_line_id` is what the DO ingest resolved."""
    company_id = company_id or env.company_a
    order = Order(
        order_number=f"ZZDO-{uuid.uuid4().hex[:8]}",
        company_id=company_id,
        source_book=book,
        doc_key=doc_key,
    )
    env.db.add(order)
    env.db.flush()
    line = OrderLine(
        order_id=order.id,
        line_sequence=1,
        product_id=env.product1_id,
        warehouse_id=_warehouse(env, company_id).id,
        quantity=5,
        dtl_key=dtl_key,
        sales_order_line_id=so_line_id,
        company_id=company_id,
    )
    env.db.add(line)
    env.db.flush()
    env.db.commit()
    return line


def _push_do_invoice(env, tag: str, from_line_ref: str, *, from_doc_no: str = "DO-2610/0001"):
    ref = f"{MARKER}:IV:{tag}"
    record = _minimal(ref)
    record["lines"][0].update(
        from_doc_type="DO", from_doc_no=from_doc_no, from_line_ref=from_line_ref
    )
    res = env.push([record])
    assert _record(res, ref)["outcome"] == "created", res.json()
    return env.lines(env.doc(ref).id)[0]


def _assert_from_kept(line, from_line_ref: str, from_doc_no: str = "DO-2610/0001") -> None:
    assert (line.from_doc_type, line.from_doc_no, line.from_line_ref) == (
        "DO",
        from_doc_no,
        from_line_ref,
    )


class TestDoHop:
    def test_do_line_with_an_so_line_links_the_invoice_line_to_it(self, env):
        """S3-1: the hop."""
        _do_line(env, so_line_id=env.so_line_id)
        ref = f"{BOOK}:DO:5001:7001"
        line = _push_do_invoice(env, "HOP1", ref)
        assert str(line.sales_order_line_id) == env.so_line_id
        _assert_from_kept(line, ref)

    def test_the_hop_is_exact_on_dockey_and_dtlkey(self, env):
        """Two DO lines on one DO, only one carrying an SO line: each invoice line gets its own."""
        _do_line(env, doc_key=5001, dtl_key=7001, so_line_id=env.so_line_id)
        # Same DO, a second line (dtl_key 7002) with no SO line; a third DO with the same dtl_key.
        order = env.db.execute(select(Order).where(Order.doc_key == 5001)).scalars().one()
        env.db.add(
            OrderLine(
                order_id=order.id,
                line_sequence=2,
                product_id=env.product1_id,
                warehouse_id=_warehouse(env, env.company_a).id,
                quantity=1,
                dtl_key=7002,
                sales_order_line_id=None,
                company_id=env.company_a,
            )
        )
        _do_line(env, doc_key=5002, dtl_key=7001, so_line_id=None)
        env.db.commit()

        linked = _push_do_invoice(env, "EX1", f"{BOOK}:DO:5001:7001")
        sibling = _push_do_invoice(env, "EX2", f"{BOOK}:DO:5001:7002")
        other_doc = _push_do_invoice(env, "EX3", f"{BOOK}:DO:5002:7001")
        assert str(linked.sales_order_line_id) == env.so_line_id
        assert sibling.sales_order_line_id is None
        assert other_doc.sales_order_line_id is None

    def test_the_book_must_match(self, env):
        _do_line(env, book="OTHER_DB", so_line_id=env.so_line_id)
        ref = f"{BOOK}:DO:5001:7001"
        line = _push_do_invoice(env, "BOOK", ref)
        assert line.sales_order_line_id is None
        _assert_from_kept(line, ref)

    def test_a_missing_do_line_stays_null_with_the_refs_kept(self, env):
        ref = f"{BOOK}:DO:4040:1"
        line = _push_do_invoice(env, "MISS", ref)
        assert line.sales_order_line_id is None
        _assert_from_kept(line, ref)

    def test_a_do_line_without_an_so_line_stays_null_with_the_refs_kept(self, env):
        _do_line(env, so_line_id=None)
        ref = f"{BOOK}:DO:5001:7001"
        line = _push_do_invoice(env, "NOSO", ref)
        assert line.sales_order_line_id is None
        _assert_from_kept(line, ref)

    def test_another_companys_do_is_never_hopped_through(self, env):
        _do_line(env, company_id=env.company_b, so_line_id=env.so_line_id)
        ref = f"{BOOK}:DO:5001:7001"
        line = _push_do_invoice(env, "COMP", ref)
        assert line.sales_order_line_id is None
        _assert_from_kept(line, ref)

    def test_a_non_numeric_ref_tail_is_not_an_error(self, env):
        ref = f"{BOOK}:DO:abc:def"
        line = _push_do_invoice(env, "JUNK", ref)
        assert line.sales_order_line_id is None
        _assert_from_kept(line, ref)

    def test_a_replayed_push_keeps_the_hop_and_is_unchanged(self, env):
        _do_line(env, so_line_id=env.so_line_id)
        ref = f"{BOOK}:DO:5001:7001"
        record = _minimal(f"{MARKER}:IV:REPLAY")
        record["lines"][0].update(from_doc_type="DO", from_doc_no="DO-1/1", from_line_ref=ref)
        assert _record(env.push([record]), f"{MARKER}:IV:REPLAY")["outcome"] == "created"
        again = env.push([record])
        assert _record(again, f"{MARKER}:IV:REPLAY")["outcome"] == "unchanged", again.json()
        line = env.lines(env.doc(f"{MARKER}:IV:REPLAY").id)[0]
        assert str(line.sales_order_line_id) == env.so_line_id


class TestSoRefPathStillLinks:
    def test_from_line_ref_naming_an_so_line_source_ref_links(self, env):
        """S0-13 stays: the old path is untouched by the hop."""
        record = _minimal(f"{MARKER}:IV:SOREF")
        record["lines"][0].update(
            from_doc_type="SO", from_doc_no="SO-2608/0001", from_line_ref=SO_LINE_REF
        )
        assert _record(env.push([record]), f"{MARKER}:IV:SOREF")["outcome"] == "created"
        line = env.lines(env.doc(f"{MARKER}:IV:SOREF").id)[0]
        assert str(line.sales_order_line_id) == env.so_line_id
        assert line.from_line_ref == SO_LINE_REF


class TestDoHopBounds:
    def test_a_key_beyond_bigint_stays_unlinked_and_is_not_a_failure(self, env):
        ref = f"{BOOK}:DO:{2**63}:7001"
        line = _push_do_invoice(env, "BIG1", ref)  # asserts the record was created, not failed
        assert line.sales_order_line_id is None
        _assert_from_kept(line, ref)
        ref = f"{BOOK}:DO:5001:{'9' * 40}"
        line = _push_do_invoice(env, "BIG2", ref)
        assert line.sales_order_line_id is None
        _assert_from_kept(line, ref)
