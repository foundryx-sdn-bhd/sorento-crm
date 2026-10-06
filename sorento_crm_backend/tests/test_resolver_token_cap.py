"""RESOLVER-TOKEN-CAP (owner report, 6 Oct 2026, prod): a catalogue photo read 19 product
codes, the parser carried all 19, and the stock reply listed the FIRST 8 with no word
about the other 11. `resolve_references` cut a pre-extracted token list to
`max_candidates` (default 8) and the cut tokens appeared in neither `resolutions` nor
`unresolved_tokens`, so nothing downstream could name them.

The resolver half of the fix, pinned on real Postgres rows:

- a pre-extracted list is answered up to `PRE_EXTRACTED_TOKEN_CEILING` (50, the stock
  balance page's own default `limit` and the chatbot's `_LOOKUP_LIMIT`), never 8;
- a list past the ceiling reports the cut in `truncated_tokens`, in order, and never
  mislabels a cut token as unresolved;
- one and three tokens, and free-text extraction, are unchanged (controls).

Fake fixture ids only (public repo): every code is ZZT-prefixed.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners

from ._pg_fixture import blank_session, unique_code

# Zero-padded so no code is a prefix of another: the exact tier answers each one alone.
CODES = [f"ZZTCAP{i:02d}" for i in range(1, 61)]
NINETEEN = CODES[:19]


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
        for code in CODES:
            session.add(
                Product(
                    id=str(uuid.uuid4()), product_code=code, product_name=code, category_id=cat,
                    base_uom_id=uom, list_price=10, is_active=True, company_id=DEFAULT_COMPANY_ID,
                )
            )
        session.commit()
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


def _chatbot_resolve(db, tokens: list[str]) -> dict:
    """`_resolve_input` with the keys `resolve_gate.resolve_entity_body` sends for N
    product codes: OR mode, one `product` hint per token, the lane's own limit."""
    from app.api.v1.system.references import _resolve_input

    return _resolve_input(
        db,
        "",
        tokens,
        match_mode="or",
        allowed_entity_types=["product"] * len(tokens),
        fallback_to_all_types=True,
        limit=50,
    )


def _matched_codes(result: dict) -> dict[str, set[str]]:
    return {
        r["token"]: {str(m.get("canonical_code") or "").upper() for m in (r.get("matches") or [])}
        for r in result.get("resolutions") or []
    }


# --------------------------------------------------------------------------- #
# The owner's turn: 19 pre-extracted codes
# --------------------------------------------------------------------------- #


def test_nineteen_pre_extracted_tokens_are_all_answered(db):
    result = _chatbot_resolve(db, NINETEEN)

    by_token = _matched_codes(result)
    assert list(by_token) == NINETEEN, f"expected one resolution per token, in order: {list(by_token)}"
    for code in NINETEEN:
        assert code in by_token[code], f"{code} did not resolve to itself: {by_token[code]}"
    assert result["unresolved_tokens"] == []
    assert result.get("truncated_tokens", []) == []


# --------------------------------------------------------------------------- #
# Past the ceiling: the cut is reported, never silent
# --------------------------------------------------------------------------- #


def test_the_ceiling_is_the_stock_pages_own_size():
    from app.services.entity_resolver import PRE_EXTRACTED_TOKEN_CEILING

    assert PRE_EXTRACTED_TOKEN_CEILING == 50


def test_sixty_tokens_answer_fifty_and_name_the_cut(db):
    result = _chatbot_resolve(db, CODES)

    by_token = _matched_codes(result)
    assert list(by_token) == CODES[:50], f"the first fifty are answered, in order: {list(by_token)}"
    assert all(code in by_token[code] for code in CODES[:50])
    assert result["truncated_tokens"] == CODES[50:]
    # A cut token was never looked up, so it is not "not found".
    assert not set(CODES[50:]) & set(result["unresolved_tokens"]), result["unresolved_tokens"]


def test_the_resolver_itself_reports_the_cut(db):
    from app.services.entity_resolver import resolve_references

    res = resolve_references(db, CODES, allowed_entity_types=["product"])

    assert [r.token for r in res.resolutions] == CODES[:50]
    assert res.truncated_tokens == CODES[50:]
    assert res.as_dict()["truncated_tokens"] == CODES[50:]
    assert not set(CODES[50:]) & set(res.unresolved_tokens)


def test_a_caller_raising_the_cap_past_the_ceiling_is_honoured(db):
    """`ai_extract.extract_service` resolves chunks of up to 100 with
    `max_candidates=len(chunk)`; an explicit cap stays the caller's."""
    from app.services.entity_resolver import resolve_references

    res = resolve_references(db, CODES, allowed_entity_types=["product"], max_candidates=len(CODES))

    assert [r.token for r in res.resolutions] == CODES
    assert res.truncated_tokens == []


def test_a_caller_lowering_the_cap_still_sees_the_cut(db):
    from app.services.entity_resolver import resolve_references

    res = resolve_references(db, CODES[:5], allowed_entity_types=["product"], max_candidates=3)

    assert [r.token for r in res.resolutions] == CODES[:3]
    assert res.truncated_tokens == CODES[3:5]


# --------------------------------------------------------------------------- #
# Controls: the flows that already worked
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("count", [1, 3, 8])
def test_small_lists_are_unchanged(db, count):
    result = _chatbot_resolve(db, CODES[:count])

    by_token = _matched_codes(result)
    assert list(by_token) == CODES[:count]
    assert all(code in by_token[code] for code in CODES[:count])
    assert result["unresolved_tokens"] == []
    assert result.get("truncated_tokens", []) == []


def test_free_text_extraction_keeps_its_own_count(db):
    """`max_candidates` still governs how many tokens are EXTRACTED from free text (8 by
    default); only a pre-extracted list has a ceiling to report."""
    from app.services.entity_resolver import resolve_references

    res = resolve_references(db, " ".join(NINETEEN), allowed_entity_types=["product"])

    assert 0 < len(res.resolutions) <= 8
    assert res.truncated_tokens == []
