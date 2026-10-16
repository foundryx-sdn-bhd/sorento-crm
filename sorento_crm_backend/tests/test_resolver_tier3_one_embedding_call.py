"""Every token that reaches the embedding tier is embedded in ONE provider call (CHAT-SPEED).

Tier 3 embedded each still-unresolved token on its own: one OpenAI round trip each
(about 0.33 s measured, 12 Oct 2026), in sequence, so a message with three unknown words
paid about 1 s where one batched call of the same three texts costs about 0.39 s and
returns the same vectors (cosine 1.0, measured). The answer per token is unchanged; only
the number of round trips is.
"""
from __future__ import annotations

import collections
import uuid

import pytest
from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services import embedding_worker
from app.services import entity_resolver as er
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners

from ._pg_fixture import blank_session, unique_code

MISSING = ["ZZQ-NOPE-ONE", "ZZQ-NOPE-TWO", "ZZQ-NOPE-THREE"]


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope_listeners()


@pytest.fixture
def db():
    with blank_session() as session:
        current = session.execute(text("SHOW search_path")).scalar()
        session.execute(text(f"SET LOCAL search_path TO {current}, public"))
        set_company_scope(session, None)
        cat, uom = str(uuid.uuid4()), str(uuid.uuid4())
        session.add(ProductCategory(id=cat, category_code=unique_code("C")[:50], category_name="C"))
        session.add(UnitOfMeasure(id=uom, uom_code=unique_code("U")[:20], uom_name="Each"))
        session.flush()
        session.add(
            Product(
                id=str(uuid.uuid4()), product_code="SRTWC286", product_name="SRTWC286",
                category_id=cat, base_uom_id=uom, list_price=10, is_active=True,
                company_id=DEFAULT_COMPANY_ID,
            )
        )
        session.flush()
        yield session


@pytest.fixture(autouse=True)
def _fresh_vector_cache():
    """The cross-request vector cache is process-wide; each test starts empty."""
    cache = getattr(er, "_VECTOR_CACHE", {})
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def calls(monkeypatch):
    seen: list[list[str]] = []

    def fake_embed(chunks):
        seen.append(list(chunks))
        return [_vector_for(c) for c in chunks]

    monkeypatch.setattr(embedding_worker, "_embed_text_chunks", fake_embed)
    return seen


def _vector_for(text_value: str) -> list[float]:
    """A different vector per text, so a batch paired with the wrong tokens shows."""
    return [float(sum(map(ord, text_value)) % 997) + 1.0] + [0.0] * 1535


def _resolve(db, tokens):
    return er.resolve_references(db, tokens, allowed_entity_types=["product"])


def test_three_unknown_tokens_cost_one_embedding_call(db, calls):
    _resolve(db, ["SRTWC286", *MISSING])

    assert len(calls) == 1, calls
    assert sorted(calls[0]) == sorted(MISSING)


def test_a_token_found_by_an_earlier_tier_is_not_embedded(db, calls):
    _resolve(db, ["SRTWC286", MISSING[0]])

    assert [t for batch in calls for t in batch] == [MISSING[0]]


def test_the_answers_match_one_call_per_token(db, calls, monkeypatch):
    batched = {r.token: [m.canonical_code for m in r.matches] for r in _resolve(db, ["SRTWC286", *MISSING]).resolutions}
    # The per-token path, as it ran before: no prefetch, one call per token.
    monkeypatch.setattr(er, "_prefetch_embeddings", lambda db, tokens: None)
    getattr(er, "_VECTOR_CACHE", {}).clear()
    calls.clear()
    single = {r.token: [m.canonical_code for m in r.matches] for r in _resolve(db, ["SRTWC286", *MISSING]).resolutions}

    assert batched == single
    assert collections.Counter(len(b) for b in calls) == {1: len(MISSING)}


def test_a_failed_batch_falls_back_to_one_call_per_token(db, monkeypatch):
    seen: list[list[str]] = []

    def flaky(chunks):
        seen.append(list(chunks))
        if len(chunks) > 1:
            raise RuntimeError("provider refused the batch")
        return [[0.0] * 1536]

    monkeypatch.setattr(embedding_worker, "_embed_text_chunks", flaky)
    rows = _resolve(db, ["SRTWC286", *MISSING])

    assert [r.token for r in rows.resolutions] == ["SRTWC286", *MISSING]
    assert sorted(t for b in seen[1:] for t in b) == sorted(MISSING)


def test_a_phrase_embedded_by_one_request_is_not_embedded_again_by_the_next(db, calls):
    """A follow-up turn re-resolves the subject the conversation carries."""
    first = _resolve(db, ["SRTWC286", MISSING[0]])
    second = _resolve(db, [MISSING[0]])

    assert [t for batch in calls for t in batch] == [MISSING[0]]
    assert [m.canonical_code for m in first.resolutions[1].matches] == [
        m.canonical_code for m in second.resolutions[0].matches
    ]


def test_the_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(er, "_VECTOR_CACHE_MAX", 3)

    def embed(chunks):
        return [[1.0, 2.0] for _ in chunks]

    for token in ["a", "b", "c", "d"]:
        er._remember_vector(embed, (), token, [1.0, 2.0])

    assert len(er._VECTOR_CACHE) == 3
    assert er._known_vector(embed, (), "a") is None
    assert er._known_vector(embed, (), "d") == [1.0, 2.0]


def test_each_batched_vector_lands_on_its_own_token(db, calls):
    with er.resolve_memo():
        er._prefetch_embeddings(db, list(MISSING))
        remembered = {t: er._memo_embedding(db, t, lambda _c: pytest.fail("embedded again")) for t in MISSING}

    assert len(calls) == 1
    assert remembered == {t: _vector_for(t) for t in MISSING}


def test_another_embedding_model_is_a_cache_miss(monkeypatch):
    from app.config import settings

    def embed(chunks):
        return [[1.0] for _ in chunks]

    er._remember_vector(embed, (), "zz-phrase", [1.0])
    monkeypatch.setattr(settings, "embedding_model_name", "zz-other-model")

    assert er._known_vector(embed, (), "zz-phrase") is None
