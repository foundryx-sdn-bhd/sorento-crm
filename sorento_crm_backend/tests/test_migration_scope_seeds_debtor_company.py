"""SCOPE-SEEDS migration, tested by running it on a scratch schema (CI's DB has no data).

1. Legacy company-less ``debtor:<code>`` seeds become ``debtor:<company>:<code>`` rows, one
   per company whose live orders carry the code under the SAME name the seed was embedded
   from (so no row carries another company's debtor text), vector reused; the legacy rows
   go. A company with a customer master row for the code gets no seed (the backfill rule).
2. Certificate and product_spec embedding rows embedded before migration 304 get their
   source row's ``company_id``; a shared (NULL-company) certificate stays NULL.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests._pg_fixture import blank_session, unique_code

MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "scope_seeds_debtor_company.py"
)
VEC = "[" + ",".join(["0.01"] * 1536) + "]"


def _run_upgrade(db):
    spec = importlib.util.spec_from_file_location("m_scope_seeds", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = MigrationContext.configure(db.connection())
    with Operations.context(ctx):
        module.upgrade()


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def _company(db) -> str:
    cid = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO companies (id, name, code, is_active) VALUES (:i, :n, :c, true)"),
        {"i": cid, "n": unique_code("SDCO"), "c": unique_code("SD")[:20]},
    )
    return cid


def _order(db, company_id: str, code: str, name: str | None, deleted: bool = False) -> None:
    db.execute(
        text(
            """
            INSERT INTO orders (id, order_number, debtor_code, debtor_name, company_id,
                is_cancelled, kpi_warning, subtotal_amount, discount_amount, tax_amount,
                total_amount, synced_to_excel, deleted_at)
            VALUES (:i, :n, :c, :d, :co, false, false, 0, 0, 0, 0, false,
                    CASE WHEN :del THEN now() ELSE NULL END)
            """
        ),
        {"i": str(uuid.uuid4()), "n": unique_code("SO"), "c": code, "d": name, "co": company_id, "del": deleted},
    )


def _customer(db, company_id: str, code: str) -> None:
    db.execute(
        text(
            "INSERT INTO customers (id, customer_code, customer_name, company_id, is_active, created_at, updated_at) "
            "VALUES (:i, :c, :c, :co, true, now(), now())"
        ),
        {"i": str(uuid.uuid4()), "c": code, "co": company_id},
    )


def _embedding(db, source_type: str, source_id: str, title: str, company_id=None, key=None) -> str:
    doc_id = str(uuid.uuid4())
    db.execute(
        text(
            """
            INSERT INTO embedding_documents (id, source_type, source_id, source_key, title, body_text,
                metadata, visibility_scope, source_hash, is_active, company_id)
            VALUES (:i, :t, :s, :k, :ti, :b, '{}', 'internal', 'h', true, :co)
            """
        ),
        {"i": doc_id, "t": source_type, "s": source_id, "k": key or source_id, "ti": title,
         "b": f"Customer Name: {title}", "co": company_id},
    )
    for idx, current in ((0, True), (1, False)):
        db.execute(
            text(
                """
                INSERT INTO embedding_chunks (id, document_id, source_type, source_id, chunk_index,
                    chunk_text, chunk_hash, embedding, model_name, model_version, embedding_provider,
                    source_hash, metadata, company_id, is_current)
                VALUES (:i, :d, :t, :s, :x, :ti, 'h', CAST(:v AS vector), 'm', '1', 'p', 'h', '{}', :co, :cur)
                """
            ),
            {"i": str(uuid.uuid4()), "d": doc_id, "t": source_type, "s": source_id, "x": idx,
             "ti": title, "v": VEC, "co": company_id, "cur": current},
        )
    return doc_id


def _docs(db, source_id: str):
    return db.execute(
        text("SELECT source_key, title, company_id::text AS company_id FROM embedding_documents WHERE source_id = :s"),
        {"s": source_id},
    ).all()


def _chunks(db, source_id: str):
    return db.execute(
        text(
            "SELECT company_id::text AS company_id, is_current, document_id::text AS document_id "
            "FROM embedding_chunks WHERE source_id = :s"
        ),
        {"s": source_id},
    ).all()


@pytest.fixture
def world(db):
    a, b, c = _company(db), _company(db), _company(db)
    code = unique_code("DEB").upper()
    _order(db, a, code, "ALPHA TRADING")
    _order(db, b, code, "BETA HARDWARE")  # same code, another name: not A's text
    _order(db, c, code, "ALPHA TRADING")
    _customer(db, c, code)  # C has the master row: no seed for C
    _order(db, b, f"  {code}  ", "ALPHA TRADING", deleted=True)  # deleted orders do not count
    legacy = f"debtor:{code}"
    _embedding(db, "customer", legacy, "ALPHA TRADING")
    db.flush()
    return {"a": a, "b": b, "c": c, "code": code, "legacy": legacy}


def test_legacy_seed_is_copied_to_the_company_whose_text_it_is(db, world):
    _run_upgrade(db)
    sid = f"debtor:{world['a']}:{world['code']}"
    docs = _docs(db, sid)
    assert [(d.source_key, d.title, d.company_id) for d in docs] == [
        (f"debtor:{world['code']}", "ALPHA TRADING", world["a"])
    ]
    chunks = _chunks(db, sid)
    assert len(chunks) == 1  # current chunk only
    assert chunks[0].company_id == world["a"] and chunks[0].is_current is True


def test_no_copy_for_another_name_or_a_mastered_company(db, world):
    _run_upgrade(db)
    assert _docs(db, f"debtor:{world['b']}:{world['code']}") == []
    assert _docs(db, f"debtor:{world['c']}:{world['code']}") == []


def test_legacy_company_less_rows_are_removed(db, world):
    _run_upgrade(db)
    assert _docs(db, world["legacy"]) == []
    assert _chunks(db, world["legacy"]) == []


def test_pending_legacy_queue_items_are_skipped(db, world):
    qid = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO embedding_queue (id, source_type, source_id, event_type, event_version, payload, status, retry_count) "
            "VALUES (:i, 'customer', :s, 'embedding.rebuild_requested', 1, '{}', 'pending', 0)"
        ),
        {"i": qid, "s": world["legacy"]},
    )
    _run_upgrade(db)
    status = db.execute(text("SELECT status FROM embedding_queue WHERE id = :i"), {"i": qid}).scalar()
    assert status == "skipped"


def test_certificate_and_product_spec_rows_get_their_source_company(db, world):
    owned_cert, shared_cert = str(uuid.uuid4()), str(uuid.uuid4())
    for cid, company in ((owned_cert, world["a"]), (shared_cert, None)):
        db.execute(
            text(
                "INSERT INTO certificates (id, scheme, certificate_number, status, company_id) "
                "VALUES (:i, 'SIRIM', :n, 'active', :co)"
            ),
            {"i": cid, "n": unique_code("CERT"), "co": company},
        )
    _embedding(db, "certificate", owned_cert, "owned cert")
    _embedding(db, "certificate", shared_cert, "shared cert")

    cat, uom, pid = (str(uuid.uuid4()) for _ in range(3))
    db.execute(
        text("INSERT INTO product_categories (id, category_code, category_name, is_active, company_id, created_at) "
             "VALUES (:i, :c, :c, true, :co, now())"),
        {"i": cat, "c": unique_code("CAT"), "co": world["b"]},
    )
    db.execute(
        text("INSERT INTO units_of_measure (id, uom_code, uom_name, is_active, company_id, created_at) "
             "VALUES (:i, :c, :c, true, :co, now())"),
        {"i": uom, "c": unique_code("U")[:20], "co": world["b"]},
    )
    db.execute(
        text(
            "INSERT INTO products (id, product_code, product_name, category_id, base_uom_id, list_price, currency, "
            " is_active, has_serial_tracking, has_batch_tracking, variant_link_manual, is_discontinued, company_id, created_at) "
            "VALUES (:i, :c, :c, :cat, :u, 1, 'MYR', true, false, false, false, false, :co, now())"
        ),
        {"i": pid, "c": unique_code("P"), "cat": cat, "u": uom, "co": world["b"]},
    )
    _embedding(db, "product_spec", pid, "spec")
    db.flush()

    _run_upgrade(db)

    assert {c.company_id for c in _chunks(db, owned_cert)} == {world["a"]}
    assert _docs(db, owned_cert)[0].company_id == world["a"]
    assert {c.company_id for c in _chunks(db, shared_cert)} == {None}
    assert {c.company_id for c in _chunks(db, pid)} == {world["b"]}
    assert _docs(db, pid)[0].company_id == world["b"]


def test_running_it_twice_changes_nothing(db, world):
    _run_upgrade(db)
    sid = f"debtor:{world['a']}:{world['code']}"
    before = (_docs(db, sid), _chunks(db, sid))
    _run_upgrade(db)
    assert (_docs(db, sid), _chunks(db, sid)) == before
