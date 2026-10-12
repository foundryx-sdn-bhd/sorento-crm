"""SCOPE-LEAK-FIX: the tier-3 embedding probe honours the caller's company scope.

`entity_resolver._tier3_embedding_lookup` is a raw `text()` vector search, so the
`do_orm_execute` scope listener never sees it. Before the fix its SQL carried no
company predicate at all: under a company-A scope it ranked company B's chunks
alongside A's, and

 * returned company B's uuid out of the probe itself for any type the
   `_chat_visible_product_ids` re-check does not cover (customer, order, ...),
   leaving the `_attach_company_info` post-pass as the only thing between that row
   and the contact; and
 * let B's near-duplicate chunk take the top slot (or close the confidence gap), so
   the contact's OWN company's match was thrown away and the token went unresolved.

Two companies, the same product / customer name in each, one shared query vector:
B's chunk is the exact match (similarity 1.0), A's is a close one (0.9). A contact
scoped to A must get A's row and never B's.
"""
from __future__ import annotations

import hashlib
import math
import os
import random
import uuid

import pytest
from sqlalchemy import text

import app.services.embedding_worker as embedding_worker
import app.services.entity_resolver as er
from app.models.base import UNSET, set_company_scope
from app.models.company import Company
from app.models.embeddings import EmbeddingChunk, EmbeddingDocument
from app.services.company_scope import register_company_scope_listeners

register_company_scope_listeners()

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_LIVE_DB_TESTS") == "1",
    reason="SKIP_LIVE_DB_TESTS=1",
)

DIM = 1536


@pytest.fixture()
def db():
    from app.database import SessionLocal

    session = SessionLocal()
    session.begin_nested()  # SAVEPOINT rolled back at teardown
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _vectors(seed: int) -> tuple[list[float], list[float]]:
    """(query, near): unit vectors with cosine similarity exactly 0.9.

    Random direction, so any embedding already in the database sits near 0 and
    cannot crowd the top-5 window this test reads.
    """
    rng = random.Random(seed)
    q = _unit([rng.gauss(0, 1) for _ in range(DIM)])
    r = [rng.gauss(0, 1) for _ in range(DIM)]
    dot = sum(a * b for a, b in zip(q, r))
    ortho = _unit([b - dot * a for a, b in zip(q, r)])
    near = [0.9 * a + math.sqrt(1 - 0.81) * b for a, b in zip(q, ortho)]
    return q, near


def _seed_chunk(db, source_type: str, source_id: str, key: str, company_id, vec) -> None:
    h = hashlib.sha256(f"{source_type}:{source_id}".encode()).hexdigest()
    doc = EmbeddingDocument(
        source_type=source_type,
        source_id=source_id,
        source_key=key,
        title=key,
        body_text=key,
        metadata_json={},
        visibility_scope="internal",
        source_hash=h,
        company_id=company_id,
        is_active=True,
    )
    db.add(doc)
    db.flush()
    db.add(
        EmbeddingChunk(
            document_id=doc.id,
            source_type=source_type,
            source_id=source_id,
            chunk_index=0,
            chunk_text=key,
            chunk_hash=h,
            embedding=vec,
            model_name="zz-test",
            model_version="1",
            embedding_provider="zz-test",
            source_hash=h,
            metadata_json={},
            company_id=company_id,
            is_current=True,
        )
    )
    db.flush()


def _seed_product(db, company_id: str, code: str, suffix: str) -> str:
    cat_id, uom_id, pid = (str(uuid.uuid4()) for _ in range(3))
    db.execute(
        text(
            "INSERT INTO product_categories (id, category_code, category_name, is_active, company_id, created_at) "
            "VALUES (:id, :code, :code, true, :cid, now())"
        ),
        {"id": cat_id, "code": f"ZZT3-CAT-{suffix}-{company_id[:4]}", "cid": company_id},
    )
    db.execute(
        text(
            "INSERT INTO units_of_measure (id, uom_code, uom_name, is_active, company_id, created_at) "
            "VALUES (:id, :code, :code, true, :cid, now())"
        ),
        {"id": uom_id, "code": f"ZZT3U{suffix}{company_id[:4]}", "cid": company_id},
    )
    db.execute(
        text(
            "INSERT INTO products "
            "(id, product_code, product_name, category_id, base_uom_id, list_price, currency, "
            " is_active, has_serial_tracking, has_batch_tracking, variant_link_manual, is_discontinued, "
            " company_id, created_at) "
            "VALUES (:id, :code, :name, :cat, :uom, 100, 'MYR', true, false, false, false, false, :cid, now())"
        ),
        {"id": pid, "code": code, "name": f"ZZT3 basin mixer {suffix}", "cat": cat_id, "uom": uom_id,
         "cid": company_id},
    )
    return pid


def _seed_customer(db, company_id: str, code: str, name: str) -> str:
    cid = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO customers (id, customer_code, customer_name, company_id, is_active, created_at, updated_at) "
            "VALUES (:i, :c, :n, :co, true, now(), now())"
        ),
        {"i": cid, "c": code, "n": name, "co": company_id},
    )
    return cid


@pytest.fixture()
def world(db, monkeypatch):
    """Companies A and B, each owning a same-named product and customer."""
    suffix = uuid.uuid4().hex[:8]
    a = Company(id=str(uuid.uuid4()), name=f"ZZT3 A {suffix}", code=f"T3A{suffix}")
    b = Company(id=str(uuid.uuid4()), name=f"ZZT3 B {suffix}", code=f"T3B{suffix}")
    db.add_all([a, b])
    db.flush()

    q, near = _vectors(int(suffix, 16))
    prod_a = _seed_product(db, a.id, f"ZZT3A{suffix}", suffix)
    prod_b = _seed_product(db, b.id, f"ZZT3B{suffix}", suffix)
    name = f"ZZT3 HARDWARE TRADING {suffix}"
    cust_a = _seed_customer(db, a.id, f"ZZT3CA{suffix}", name)
    cust_b = _seed_customer(db, b.id, f"ZZT3CB{suffix}", name)
    # B holds the EXACT vector, A the close one: the unscoped ranking puts B first.
    _seed_chunk(db, "product", prod_a, f"ZZT3A{suffix}", a.id, near)
    _seed_chunk(db, "product", prod_b, f"ZZT3B{suffix}", b.id, q)
    _seed_chunk(db, "customer", cust_a, f"ZZT3CA{suffix}", a.id, near)
    _seed_chunk(db, "customer", cust_b, f"ZZT3CB{suffix}", b.id, q)

    monkeypatch.setattr(embedding_worker, "_embed_text_chunks", lambda texts, *a, **k: [q for _ in texts])
    return {
        "a": a.id, "b": b.id, "suffix": suffix,
        "prod_a": prod_a, "prod_b": prod_b, "cust_a": cust_a, "cust_b": cust_b,
    }


def _uuids(hits) -> set[str]:
    return {str(h.uuid) for h in hits}


# ------------------------------------------------------------- probe level


def test_probe_never_returns_another_companys_customer(db, world):
    """Red before the fix: the probe handed back company B's customer uuid."""
    set_company_scope(db, frozenset({world["a"]}))
    hits = er._tier3_embedding_lookup(db, f"zzt3 hardware {world['suffix']}", frozenset({"customer"}))
    assert world["cust_b"] not in _uuids(hits)
    assert _uuids(hits) == {world["cust_a"]}


def test_probe_finds_own_product_when_another_company_has_the_closer_twin(db, world):
    """Red before the fix: B's chunk took the top slot, failed the ORM visibility
    re-check, and the probe returned nothing instead of A's own product."""
    set_company_scope(db, frozenset({world["a"]}))
    hits = er._tier3_embedding_lookup(db, f"zzt3 mixer {world['suffix']}", frozenset({"product"}))
    assert _uuids(hits) == {world["prod_a"]}


def test_probe_fails_closed_under_unset_scope(db, world):
    """UNSET (scope resolver never ran) sees no company-owned chunk."""
    set_company_scope(db, UNSET)
    hits = er._tier3_embedding_lookup(db, f"zzt3 hardware {world['suffix']}", frozenset({"customer"}))
    assert not (_uuids(hits) & {world["cust_a"], world["cust_b"]})


def test_probe_under_no_scope_still_ranks_every_company(db, world):
    """Guard: a None scope (staff / back-compat) is unchanged - B is the top hit."""
    set_company_scope(db, None)
    hits = er._tier3_embedding_lookup(db, f"zzt3 hardware {world['suffix']}", frozenset({"customer"}))
    assert _uuids(hits) == {world["cust_b"]}


def test_probe_keeps_shared_company_less_chunks(db, world):
    """Guard: a NULL-company chunk (forms, shared knowledge) stays visible under a scope."""
    form_id = str(uuid.uuid4())
    q, _ = _vectors(int(world["suffix"], 16))
    _seed_chunk(db, "form", form_id, f"ZZT3FORM{world['suffix']}", None, q)
    set_company_scope(db, frozenset({world["a"]}))
    hits = er._tier3_embedding_lookup(db, f"zzt3 form {world['suffix']}", frozenset({"form"}))
    assert _uuids(hits) == {form_id}


# ------------------------------------------------------------- resolve level


def test_resolve_references_resolves_to_own_company_customer(db, world):
    """Red before the fix: the backstop dropped B's hit and the token came back
    unresolved, although A owns a matching customer."""
    set_company_scope(db, frozenset({world["a"]}))
    token = f"zzt3 hardware qq{world['suffix']}"
    res = er.resolve_references(db, [token], allowed_entity_types=["customer"])
    got = {str(m.uuid) for tr in res.resolutions for m in tr.matches}
    assert world["cust_b"] not in got
    assert world["cust_a"] in got
