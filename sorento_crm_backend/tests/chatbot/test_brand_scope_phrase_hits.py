"""Phase 2 kill-gap tests: the phrase (embedding) search reads raw SQL, so its product hits
must be re-checked against the session's brand scope (PLAN slice 2b).

Driven through `entity_resolver._rag_resolve_phrase` with only the embed call and the vector
SELECT stubbed (a thin session proxy answers the `embedding_chunks` statement with one row per
seeded product), so the test does not depend on the helper that does the filtering.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.models.base import set_company_scope
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests._brand_scope_seed import db, world  # noqa: F401  (fixtures by name)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _VectorStub:
    """The real session, except the pgvector SELECT returns the canned rows."""

    def __init__(self, real, rows):
        self._real, self._rows = real, rows

    def execute(self, statement, *args, **kwargs):
        if "embedding_chunks" in str(statement):
            return _Rows(self._rows)
        return self._real.execute(statement, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _phrase_hits(db, world, monkeypatch, *, scoped: bool) -> set[str]:
    import app.services.embedding_worker as worker
    from app.models.base import set_brand_scope
    from app.services.entity_resolver import _rag_resolve_phrase

    monkeypatch.setattr(worker, "_embed_text_chunks", lambda texts: [[0.0] * 8 for _ in texts])
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    set_brand_scope(db, frozenset({world.mocha.id}) if scoped else None)
    rows = [
        SimpleNamespace(source_type="product", source_id=str(p.id), source_key=p.product_code, similarity=0.9)
        for p in world.products
    ]
    hits = _rag_resolve_phrase(_VectorStub(db, rows), "any phrase", frozenset({"product"}))
    return {h.canonical_code for h in hits}


def test_phrase_search_unscoped_returns_all_three_products(db, world, monkeypatch) -> None:
    assert _phrase_hits(db, world, monkeypatch, scoped=False) == set(world.codes.values())


def test_phrase_search_scoped_keeps_only_in_scope_products(db, world, monkeypatch) -> None:
    """MOCHA survives; the SORENTO and unbranded embedding hits are dropped."""
    assert _phrase_hits(db, world, monkeypatch, scoped=True) == {world.codes["mocha"]}
