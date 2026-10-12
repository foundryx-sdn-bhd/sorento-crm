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


# ------------------------------------------------------------- RAG phrase probe
# `_rag_resolve_phrase` (the `resolve_entities_to_filters` fallback behind the MCP
# orders / stock / products tools) is the same raw vector search. Its hits carry no
# uuid, so `_attach_company_info` cannot gate them: before the fix another company's
# customer / product code came straight back and was echoed to the caller.


def test_rag_phrase_never_returns_another_companys_codes(db, world):
    """Red before the fix: company B's customer and product codes came back."""
    s = world["suffix"]
    set_company_scope(db, frozenset({world["a"]}))
    hits = er._rag_resolve_phrase(db, f"zzt3 phrase {s}", frozenset({"customer", "product"}))
    codes = {h.canonical_code for h in hits}
    assert not codes & {f"ZZT3CB{s}", f"ZZT3B{s}"}
    assert {f"ZZT3CA{s}", f"ZZT3A{s}"} <= codes


def test_rag_phrase_under_no_scope_still_returns_every_company(db, world):
    """Guard: a None scope (staff / back-compat) is unchanged."""
    s = world["suffix"]
    set_company_scope(db, None)
    hits = er._rag_resolve_phrase(db, f"zzt3 phrase {s}", frozenset({"customer"}))
    assert {f"ZZT3CA{s}", f"ZZT3CB{s}"} <= {h.canonical_code for h in hits}


# ------------------------------------------------------------- caches (CHAT-SPEED #1602)
# The per-request resolve memo (tier-1/2 answers, query vectors) and the cross-request
# vector cache are keyed by the caller's company (and brand) scope, so an answer or a
# vector looked up under one company is never served under another.


def test_memo_does_not_serve_one_scopes_miss_to_another(db, world):
    """Red before the fix: inside one memo, company B's empty tier-1 answer for A's
    product code was replayed under A's scope, so A's own product went missing."""
    code = f"ZZT3A{world['suffix']}"
    with er.resolve_memo():
        set_company_scope(db, frozenset({world["b"]}))
        miss = er.resolve_references(db, [code], allowed_entity_types=["product"],
                                     enable_embedding_fallback=False)
        set_company_scope(db, frozenset({world["a"]}))
        hit = er.resolve_references(db, [code], allowed_entity_types=["product"],
                                    enable_embedding_fallback=False)
    assert not {str(m.uuid) for tr in miss.resolutions for m in tr.matches}
    assert world["prod_a"] in {str(m.uuid) for tr in hit.resolutions for m in tr.matches}


def test_vector_cache_is_keyed_by_company_scope(db, world):
    """Red before the fix: a vector remembered for company A's caller was a hit for B's."""
    calls: list[list[str]] = []

    def embed(texts):
        calls.append(list(texts))
        return [[0.5] * DIM for _ in texts]

    token = f"zz-cache-{world['suffix']}"
    set_company_scope(db, frozenset({world["a"]}))
    er._memo_embedding(db, token, embed)
    set_company_scope(db, frozenset({world["b"]}))
    er._memo_embedding(db, token, embed)
    set_company_scope(db, frozenset({world["a"]}))
    er._memo_embedding(db, token, embed)
    assert calls == [[token], [token]]


# ------------------------------------------------------------- bare exists() (known gap)
# Measured 12 Oct 2026: `with_loader_criteria` does not reach a bare Core `exists()`, so
# the `do_orm_execute` listener leaves `db.query(exists().where(Product...))` and
# `select(exists().where(Product...))` unscoped. No app code on the chatbot resolve or
# MCP read paths issues that uncorrelated shape (every `exists()` there is correlated to
# a scoped outer row or carries its own company predicate, e.g.
# `product_predicate_service`), so nothing leaks today. This pins the listener gap so a
# listener change that closes it is noticed, and so new code knows to add
# `build_company_predicate` itself. The ORM `.exists()` shape IS scoped (guard below).


@pytest.mark.xfail(strict=True, reason="listener does not scope a bare Core exists(); see comment above")
def test_bare_exists_over_product_is_scoped_by_the_listener(db, world):
    from sqlalchemy import exists, select

    from app.models.product import Product

    set_company_scope(db, frozenset({world["a"]}))
    code_b = f"ZZT3B{world['suffix']}"
    assert db.query(exists().where(Product.product_code == code_b)).scalar() is False
    assert db.scalar(select(exists().where(Product.product_code == code_b))) is False


def test_orm_query_exists_over_product_is_scoped(db, world):
    from app.models.product import Product

    set_company_scope(db, frozenset({world["a"]}))
    code_b = f"ZZT3B{world['suffix']}"
    assert db.query(db.query(Product.id).filter(Product.product_code == code_b).exists()).scalar() is False


# ------------------------------------------------------------- debtor seeds (crew answer (b))
# A `debtor:<code>` customer seed (an `orders.debtor_code` with no customer master row) is
# written company-less (migration 304), so the shared-row rule alone lets company B's
# master-less debtor reach company A's contact. Under any company scope the RAG phrase
# probe drops these seeds; staff (scope None) still find them.


def _seed_debtor(db, world) -> str:
    code = f"ZZT3DEB{world['suffix']}"
    q, _ = _vectors(int(world["suffix"], 16))
    _seed_chunk(db, "customer", f"debtor:{code}", f"debtor:{code}", None, q)
    return code


def test_rag_phrase_never_returns_a_master_less_debtor_under_a_company_scope(db, world):
    """Red before the fix: B's master-less debtor code came back to A's contact."""
    code = _seed_debtor(db, world)
    set_company_scope(db, frozenset({world["a"]}))
    hits = er._rag_resolve_phrase(db, f"zzt3 debtor {world['suffix']}", frozenset({"customer"}))
    assert code not in {h.canonical_code for h in hits}
    assert f"ZZT3CA{world['suffix']}" in {h.canonical_code for h in hits}


def test_rag_phrase_never_returns_a_master_less_debtor_under_unset_scope(db, world):
    code = _seed_debtor(db, world)
    set_company_scope(db, UNSET)
    hits = er._rag_resolve_phrase(db, f"zzt3 debtor {world['suffix']}", frozenset({"customer"}))
    assert code not in {h.canonical_code for h in hits}


def test_rag_phrase_still_returns_a_master_less_debtor_to_staff(db, world):
    """Guard: no company scope (staff) keeps finding master-less debtors."""
    code = _seed_debtor(db, world)
    set_company_scope(db, None)
    hits = er._rag_resolve_phrase(db, f"zzt3 debtor {world['suffix']}", frozenset({"customer"}))
    assert code in {h.canonical_code for h in hits}
