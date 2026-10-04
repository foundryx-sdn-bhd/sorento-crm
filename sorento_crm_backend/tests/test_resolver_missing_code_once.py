"""A missing product code is looked up once per resolve request, not once per pass.

CHAT-SLOW-MISS (4 Oct 2026): "srtwc286 , srtwc6022  srt5764  stock" spent 25-34s in
the `routed` stage, 19s of it between two lookups of the one code that does not exist.
`resolve_gate` asks the resolver with `fallback_to_all_types=True`, so an unresolved
token runs the whole pipeline twice: the primary pass under the parser's `product` hint,
then the all-types fallback (`references._resolve_input`). The fallback re-ran every
probe the primary pass had already run for that token, embedded the same string a second
time (an OpenAI round trip each), and its cross-type expansion then ran every Tier-1 and
Tier-2 probe a THIRD time over a token they had just found nothing for.

Measured on a seeded copy (25k products, 150k orders, embedding stubbed at 0.5s): one
resolve took 4.8s, 131 statements, 2 embeddings; see the PR for the after numbers.
"""
from __future__ import annotations

import collections
import time
import uuid

import pytest
from sqlalchemy import text

from app.api.v1.system.references import _resolve_input
from app.models.base import set_company_scope
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services import embedding_worker
from app.services import entity_resolver as er
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners

from ._pg_fixture import blank_session, unique_code

QUERY = "srtwc286 , srtwc6022  srt5764  stock"
TOKENS = ["SRTWC286", "SRTWC6022", "SRT5764"]
MISSING = "SRTWC6022"


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope_listeners()


@pytest.fixture
def db():
    with blank_session() as session:
        # pg_trgm lives in `public`; the "did you mean" probe needs it reachable.
        current = session.execute(text("SHOW search_path")).scalar()
        session.execute(text(f"SET LOCAL search_path TO {current}, public"))
        set_company_scope(session, None)
        cat, uom = str(uuid.uuid4()), str(uuid.uuid4())
        session.add(ProductCategory(id=cat, category_code=unique_code("C")[:50], category_name="C"))
        session.add(UnitOfMeasure(id=uom, uom_code=unique_code("U")[:20], uom_name="Each"))
        session.flush()
        for code in ("SRTWC286", "SRT5764", "SRTWC6028"):
            session.add(
                Product(
                    id=str(uuid.uuid4()), product_code=code, product_name=code,
                    category_id=cat, base_uom_id=uom, list_price=10, is_active=True,
                    company_id=DEFAULT_COMPANY_ID,
                )
            )
        session.flush()
        yield session


@pytest.fixture
def counted(monkeypatch):
    """Count every probe call per (probe, token), and every embedding per text."""
    probe_calls: collections.Counter = collections.Counter()
    embeds: collections.Counter = collections.Counter()

    def _counting(probe, batched):
        def run(db, arg, **kw):
            for tok in (arg if batched else [arg]):
                probe_calls[(probe.__name__, tok)] += 1
            return probe(db, arg, **kw)

        run.__name__ = probe.__name__
        return run

    monkeypatch.setattr(
        er, "_TIER1_PROBES", tuple((_counting(p, True), t) for p, t in er._TIER1_PROBES)
    )
    monkeypatch.setattr(
        er, "_TIER2_PROBES", tuple((_counting(p, False), t) for p, t in er._TIER2_PROBES)
    )
    delay = {"s": 0.0}

    def fake_embed(chunks):
        for c in chunks:
            embeds[c] += 1
        time.sleep(delay["s"])
        return [[0.0] * 1536 for _ in chunks]

    monkeypatch.setattr(embedding_worker, "_embed_text_chunks", fake_embed)
    return probe_calls, embeds, delay


def _resolve(db):
    return _resolve_input(
        db, QUERY, list(TOKENS), allowed_entity_types=["product"] * len(TOKENS),
        fallback_to_all_types=True,
    )


def _by_token(result):
    return {r["token"]: r for r in result["resolutions"]}


def test_the_answer_is_unchanged(db, counted):
    rows = _by_token(_resolve(db))

    assert [m["canonical_code"] for m in rows["SRTWC286"]["matches"]] == ["SRTWC286"]
    assert [m["canonical_code"] for m in rows["SRT5764"]["matches"]] == ["SRT5764"]
    assert rows[MISSING]["matches"] == []
    assert "SRTWC6028" in [a["canonical_code"] for a in rows[MISSING]["alternatives"]]


def test_the_missing_code_is_embedded_once(db, counted):
    _, embeds, _ = counted
    _resolve(db)

    assert embeds[MISSING] == 1, dict(embeds)


def test_no_probe_runs_twice_for_the_same_token(db, counted):
    probe_calls, _, _ = counted
    _resolve(db)

    repeats = {k: n for k, n in probe_calls.items() if n > 1}
    assert repeats == {}, repeats


def test_a_missing_code_costs_one_embedding_round_trip_not_two(db, counted):
    """The timing face of the same fix: the embedding round trip is the expensive
    step a repeat pays again (an OpenAI call in production)."""
    _, _, delay = counted
    delay["s"] = 2.0

    started = time.perf_counter()
    _resolve(db)
    elapsed = time.perf_counter() - started

    # Before: two round trips, so never under 4.0s. After: one, plus the SQL.
    assert elapsed < 3.9, f"{elapsed:.2f}s"


def test_two_spellings_of_one_code_both_resolve(db, monkeypatch):
    """Tier-1 probes map a batch by normalised code (`dict(zip(normalized, tokens))`),
    so of two tokens that normalise alike only the last gets the row from the BATCHED
    call. The cross-type pass re-asks each alone and finds it; a remembered empty
    answer for the other spelling must not stand in for that. Product sets, projects
    and users have no Tier-2 probe to rescue them, so this is the probe in the raw."""

    def probe(db, tokens):
        norm = {t.replace("-", "").lower(): t for t in tokens}
        out = {t: [] for t in tokens}
        if "ab1" in norm:
            out[norm["ab1"]] = [
                er.ResolvedEntity(
                    entity_type="product_set", canonical_code="AB-1",
                    uuid=str(uuid.uuid4()), match_field="code",
                )
            ]
        return out

    monkeypatch.setattr(er, "_TIER1_PROBES", ((probe, frozenset({"product_set"})),))
    monkeypatch.setattr(er, "_TIER2_PROBES", ())

    rows = er.resolve_references(
        db, ["AB-1", "ab1"], cross_type_expand=True, enable_embedding_fallback=False
    )

    found = {r.token: [m.canonical_code for m in r.matches] for r in rows.resolutions}
    assert found == {"AB-1": ["AB-1"], "ab1": ["AB-1"]}, found
