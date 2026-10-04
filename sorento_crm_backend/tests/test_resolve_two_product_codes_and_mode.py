"""CHATBOT-QUEUE-FIX, crew browser pass on PR #1415: the resolver half of prod turn f0a2.

"Srtswt3001 / Srtswt3001-gm stock" resolved, in AND mode (the chatbot's default before
this lane), to the INTERSECTION of the two tokens: the prefix probe for `srtswt3001`
also matches SRTSWT3001-GM, so the intersection is SRTSWT3001-GM alone and SRTSWT3001
vanishes. In OR mode each token keeps its own answer. The chatbot now sends OR for two
product codes (`resolve_gate.resolve_entity_body`, `test_two_product_codes_resolve_or`).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners

from ._pg_fixture import blank_session, unique_code


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
        for code in ("SRTSWT3001", "SRTSWT3001-GM"):
            session.add(
                Product(
                    id=str(uuid.uuid4()), product_code=code, product_name=code, category_id=cat,
                    base_uom_id=uom, list_price=10, is_active=True, company_id=DEFAULT_COMPANY_ID,
                )
            )
        session.commit()
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


# What `resolve_gate.resolve_entity_body` sends: product tokens fold `[-\s]+` away.
TOKENS = ["Srtswt3001", "Srtswt3001gm"]


def _codes(payload: dict) -> set[str]:
    rows = list(payload.get("intersection") or [])
    for resolution in payload.get("resolutions") or []:
        rows.extend(resolution.get("matches") or [])
    return {str(r.get("canonical_code") or "").upper() for r in rows}


def test_and_mode_folds_the_first_code_onto_the_seconds_variant(db):
    """The prod mechanism, pinned: AND drops SRTSWT3001 with nothing unresolved."""
    from app.api.v1.system.references import _resolve_input

    result = _resolve_input(db, "", TOKENS, match_mode="and", allowed_entity_types=["product", "product"])
    assert "SRTSWT3001" not in _codes(result), _codes(result)
    assert not result.get("unresolved_tokens")


def test_or_mode_answers_both_codes(db):
    from app.api.v1.system.references import _resolve_input

    result = _resolve_input(db, "", TOKENS, match_mode="or", allowed_entity_types=["product", "product"])
    assert {"SRTSWT3001", "SRTSWT3001-GM"} <= _codes(result), _codes(result)
