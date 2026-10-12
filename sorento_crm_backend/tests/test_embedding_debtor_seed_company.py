"""SCOPE-SEEDS: synthetic ``debtor:`` customer seeds belong to a company.

A seed is an ``orders.debtor_code`` with no customer master row. Until this lane it was
written company-less (``debtor:<code>``, ``company_id`` NULL, migration 304), so #1605 had
to drop every seed under any company scope, and a contact's own master-less debtor stopped
resolving. Now one seed per (company, debtor code): ``debtor:<company_id>:<code>`` with
``company_id`` stamped, so it resolves inside its own company and never in another.

Two companies, the SAME debtor code in each with a different debtor name.
"""
from __future__ import annotations

import hashlib
import math
import os
import random
import uuid

import pytest

import app.services.embedding_worker as embedding_worker
import app.services.entity_resolver as er
from app.models.base import UNSET, set_company_scope
from app.models.company import Company
from app.models.embeddings import EmbeddingChunk, EmbeddingDocument
from app.models.order import Customer, Order
from app.services.company_scope import register_company_scope_listeners
from app.services.embedding_backfill_service import EmbeddingBackfillService
from app.services.embedding_service import (
    EmbeddingReadService,
    debtor_seed_id,
    parse_debtor_seed_id,
)

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
    """(query, near): unit vectors with cosine similarity exactly 0.9."""
    rng = random.Random(seed)
    q = _unit([rng.gauss(0, 1) for _ in range(DIM)])
    r = [rng.gauss(0, 1) for _ in range(DIM)]
    dot = sum(a * b for a, b in zip(q, r))
    ortho = _unit([b - dot * a for a, b in zip(q, r)])
    near = [0.9 * a + math.sqrt(1 - 0.81) * b for a, b in zip(q, ortho)]
    return q, near


def _seed_chunk(db, source_id: str, key: str, company_id, vec) -> None:
    h = hashlib.sha256(f"customer:{source_id}".encode()).hexdigest()
    doc = EmbeddingDocument(
        source_type="customer",
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
            source_type="customer",
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


def _order(db, company_id: str, debtor_code: str, debtor_name: str) -> None:
    db.add(
        Order(
            id=str(uuid.uuid4()),
            order_number=f"ZZTSD-{uuid.uuid4().hex[:10]}",
            debtor_code=debtor_code,
            debtor_name=debtor_name,
            company_id=company_id,
        )
    )
    db.flush()


@pytest.fixture()
def world(db, monkeypatch):
    """A and B both order under debtor code ``code`` with different names; B also has
    a customer master row for ``master_code``, which A only knows as a debtor."""
    set_company_scope(db, None)
    suffix = uuid.uuid4().hex[:8]
    a = Company(id=str(uuid.uuid4()), name=f"ZZTSD A {suffix}", code=f"SDA{suffix}")
    b = Company(id=str(uuid.uuid4()), name=f"ZZTSD B {suffix}", code=f"SDB{suffix}")
    db.add_all([a, b])
    db.flush()
    code = f"ZZTSD{suffix}".upper()
    master_code = f"ZZTSDM{suffix}".upper()
    _order(db, a.id, code, f"ZZTSD ALPHA TRADING {suffix}")
    _order(db, b.id, code, f"ZZTSD BETA HARDWARE {suffix}")
    _order(db, a.id, master_code, f"ZZTSD ALPHA MASTERLESS {suffix}")
    _order(db, b.id, master_code, f"ZZTSD BETA MASTERED {suffix}")
    db.add(
        Customer(
            id=str(uuid.uuid4()),
            customer_code=master_code,
            customer_name=f"ZZTSD BETA MASTERED {suffix}",
            company_id=b.id,
        )
    )
    db.flush()
    q, near = _vectors(int(suffix, 16))
    monkeypatch.setattr(embedding_worker, "_embed_text_chunks", lambda texts, *a, **k: [q for _ in texts])
    return {"a": a.id, "b": b.id, "suffix": suffix, "code": code, "master_code": master_code, "q": q, "near": near}


# ------------------------------------------------------------- id helpers


def test_seed_id_round_trips_company_and_code():
    cid = str(uuid.uuid4())
    sid = debtor_seed_id(cid, "300-A001")
    assert sid == f"debtor:{cid}:300-A001"
    assert parse_debtor_seed_id(sid) == (cid, "300-A001")


def test_legacy_seed_id_parses_as_company_less():
    assert parse_debtor_seed_id("debtor:300-A001") == (None, "300-A001")


def test_non_seed_id_is_not_a_seed():
    assert parse_debtor_seed_id(str(uuid.uuid4())) is None


# ------------------------------------------------------------- backfill emits per-company seeds


def _seed_rows(db, world) -> dict[str, dict]:
    rows = EmbeddingBackfillService(db)._fetch_rows(source="customer", limit=100_000, offset=0)
    return {
        str(r["id"]): r
        for r in rows
        if isinstance(r["id"], str) and r["id"].startswith("debtor:") and world["suffix"].upper() in r["id"]
    }


def test_backfill_seeds_one_row_per_company(db, world):
    rows = _seed_rows(db, world)
    assert debtor_seed_id(world["a"], world["code"]) in rows
    assert debtor_seed_id(world["b"], world["code"]) in rows
    assert rows[debtor_seed_id(world["a"], world["code"])]["source_key"] == f"debtor:{world['code']}"


def test_backfill_skips_a_code_only_where_that_company_has_the_master(db, world):
    """B has a customer master for ``master_code``; A does not, so only A gets a seed."""
    rows = _seed_rows(db, world)
    assert debtor_seed_id(world["a"], world["master_code"]) in rows
    assert debtor_seed_id(world["b"], world["master_code"]) not in rows
    assert f"debtor:{world['master_code']}" not in rows
    assert f"debtor:{world['code']}" not in rows  # no legacy company-less id any more


# ------------------------------------------------------------- worker


def test_worker_stamps_the_seed_company(db, world):
    sid = debtor_seed_id(world["a"], world["code"])
    assert embedding_worker._company_id_for_source(db, "customer", sid) == world["a"]


def test_worker_reads_the_name_from_the_seed_company_only(db, world):
    sid = debtor_seed_id(world["b"], world["code"])
    source = embedding_worker._canonical_for_source(db, "customer", sid)
    assert source["title"] == f"ZZTSD BETA HARDWARE {world['suffix']}"
    assert "ALPHA" not in source["body_text"]
    assert source["source_key"] == f"debtor:{world['code']}"


def test_worker_refuses_a_legacy_company_less_seed(db, world):
    """A legacy id would re-create a company-less row, which reads as shared."""
    with pytest.raises(ValueError):
        embedding_worker._canonical_for_source(db, "customer", f"debtor:{world['code']}")


def test_worker_refuses_a_seed_whose_company_has_no_such_debtor(db, world):
    other = str(uuid.uuid4())
    with pytest.raises(ValueError):
        embedding_worker._canonical_for_source(db, "customer", debtor_seed_id(other, world["code"]))


# ------------------------------------------------------------- search under a company scope


def _seed_both(db, world) -> None:
    # B holds the EXACT vector, A the close one: an unscoped ranking puts B first.
    _seed_chunk(db, debtor_seed_id(world["a"], world["code"]), f"debtor:{world['code']}", world["a"], world["near"])
    _seed_chunk(db, debtor_seed_id(world["b"], world["code"]), f"debtor:{world['code']}", world["b"], world["q"])


def test_rag_search_returns_own_company_seed_only(db, world):
    """`/external/rag/search`: red before the fix (every seed dropped under a scope)."""
    _seed_both(db, world)
    set_company_scope(db, frozenset({world["a"]}))
    rows = EmbeddingReadService(db).search_current(world["q"], top_k=10, source_type="customer")
    ids = {c.source_id for c, _, _ in rows}
    assert debtor_seed_id(world["a"], world["code"]) in ids
    assert debtor_seed_id(world["b"], world["code"]) not in ids


def test_rag_search_unset_scope_returns_no_seed(db, world):
    _seed_both(db, world)
    set_company_scope(db, UNSET)
    rows = EmbeddingReadService(db).search_current(world["q"], top_k=10, source_type="customer")
    assert not [c for c, _, _ in rows if c.source_id.startswith("debtor:")]


def test_rag_search_never_returns_a_company_less_debtor_seed(db, world):
    """A legacy company-less seed left behind (say, written by an old worker mid-deploy)
    is not shared knowledge: no company scope sees it."""
    _seed_chunk(db, f"debtor:{world['code']}", f"debtor:{world['code']}", None, world["q"])
    set_company_scope(db, frozenset({world["a"]}))
    rows = EmbeddingReadService(db).search_current(world["q"], top_k=10, source_type="customer")
    assert f"debtor:{world['code']}" not in {c.source_id for c, _, _ in rows}


def test_rag_phrase_resolves_own_company_debtor(db, world):
    """MCP filter path: red before the fix (the seed was dropped, the code unresolved)."""
    _seed_both(db, world)
    set_company_scope(db, frozenset({world["a"]}))
    hits = er._rag_resolve_phrase(db, f"zztsd alpha {world['suffix']}", frozenset({"customer"}))
    debtor_hits = [h for h in hits if h.match_field == "debtor_code"]
    assert [h.canonical_code for h in debtor_hits] == [world["code"]]


def test_rag_phrase_never_returns_another_companys_debtor(db, world):
    _seed_chunk(db, debtor_seed_id(world["b"], world["code"]), f"debtor:{world['code']}", world["b"], world["q"])
    set_company_scope(db, frozenset({world["a"]}))
    hits = er._rag_resolve_phrase(db, f"zztsd beta {world['suffix']}", frozenset({"customer"}))
    assert world["code"] not in {h.canonical_code for h in hits}


def test_fuzzy_resolver_expands_a_seed_to_its_own_companys_debtor_name(db, world, monkeypatch):
    """customer_query expansion: the seed resolves to the name A's orders carry, never
    B's name for the same code."""
    import app.api.v1.external.rag as rag
    from app.services.fuzzy_resolver import resolve_via_embedding_then_ilike

    monkeypatch.setattr(rag, "_embed_query", lambda term: world["q"])
    _seed_both(db, world)
    set_company_scope(db, frozenset({world["a"]}))
    _, values = resolve_via_embedding_then_ilike(
        db,
        "zztsd alpha",
        source_type="customer",
        ilike_columns=[Order.debtor_name],
        canonical_model=Customer,
        canonical_fields=("customer_name", "customer_code"),
    )
    assert f"ZZTSD ALPHA TRADING {world['suffix']}" in values
    assert world["code"] in values
    assert f"ZZTSD BETA HARDWARE {world['suffix']}" not in values


def test_fuzzy_resolver_without_scope_pairs_code_with_the_seed_company(db, world, monkeypatch):
    """Scope None (staff): only A's seed matches, so only A's name expands, never B's
    name for the same code (the ORM listener filters nothing here)."""
    import app.api.v1.external.rag as rag
    from app.services.fuzzy_resolver import resolve_via_embedding_then_ilike

    monkeypatch.setattr(rag, "_embed_query", lambda term: world["q"])
    _seed_chunk(db, debtor_seed_id(world["a"], world["code"]), f"debtor:{world['code']}", world["a"], world["q"])
    set_company_scope(db, None)
    _, values = resolve_via_embedding_then_ilike(
        db,
        "zztsd alpha",
        source_type="customer",
        ilike_columns=[Order.debtor_name],
        canonical_model=Customer,
        canonical_fields=("customer_name", "customer_code"),
    )
    assert f"ZZTSD ALPHA TRADING {world['suffix']}" in values
    assert f"ZZTSD BETA HARDWARE {world['suffix']}" not in values


def test_tier3_one_code_seeded_in_two_companies_is_not_ambiguous_for_staff(db, world):
    """The migration reuses one vector per company, so identical seeds are expected.
    Staff (scope None) still get one confident debtor hit, as before the lane."""
    _seed_chunk(db, debtor_seed_id(world["a"], world["code"]), f"debtor:{world['code']}", world["a"], world["q"])
    _seed_chunk(db, debtor_seed_id(world["b"], world["code"]), f"debtor:{world['code']}", world["b"], world["q"])
    set_company_scope(db, None)
    hits = er._tier3_embedding_lookup(db, f"zztsd t3 {world['suffix']}", frozenset({"customer"}))
    assert [h.canonical_code for h in hits] == [f"debtor:{world['code']}"]


def test_backfill_pages_seeds_without_repeating_and_ends(db, world):
    """Pages past the customer master continue through the seeds instead of serving the
    first page again, so `_backfill_one` reaches an empty page."""
    svc = EmbeddingBackfillService(db)
    ids: list[str] = []
    offset = 0
    for _ in range(100_000):
        rows = svc._fetch_rows(source="customer", limit=500, offset=offset)
        if not rows:
            break
        ids.extend(str(r["id"]) for r in rows)
        offset += len(rows)
    else:
        pytest.fail("customer backfill never reached an empty page")
    assert len(ids) == len(set(ids))
    assert debtor_seed_id(world["a"], world["code"]) in ids
    assert debtor_seed_id(world["b"], world["code"]) in ids


def test_backfill_skips_a_code_too_long_for_source_id(db, world):
    long_code = ("ZZTSDL" + world["suffix"].upper()).ljust(90, "X")
    _order(db, world["a"], long_code, "LONG")
    rows = EmbeddingBackfillService(db)._fetch_rows(source="customer", limit=100_000, offset=0)
    assert not [r for r in rows if long_code in str(r["id"])]
