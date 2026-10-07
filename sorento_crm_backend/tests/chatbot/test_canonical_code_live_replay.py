"""RESOLVE-SEMANTIC-FALLBACK, the live replay table: the shared resolver with the REAL
code reader (the real provider, through whatever egress the machine has), over seeded ZZT
codes, in the shapes the owner's card named.

Each case prints one line `CANONICAL-CODE-REPLAY case=<name> outcome=<...>` for the PR's
replay table. Opt-in, never in CI: `CHATBOT_LIVE_LLM=1`; key `OPENAI_API_KEY` (or a
placeholder where a proxy injects it). The reader's provider ladder ends at the
environment key when no assistant config row exists, which is the blank test schema's
state. Same pattern as `test_canonical_code_live_parser.py`.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners

from tests._pg_fixture import blank_session, unique_code

pytestmark = pytest.mark.live_external

TAP = "ZZTWT5608"
FAMILY = ["ZZTWC8066-RL", "ZZTWC8066-SH", "ZZTWC8066-SH-150"]
CODES = [TAP, *FAMILY, "ZZTWC286-SH"]


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope_listeners()


@pytest.fixture(autouse=True)
def _live_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY") or "sk-proxy-injected")
    from app.config import settings

    monkeypatch.setattr(settings, "openai_api_key", os.environ.get("OPENAI_API_KEY") or "sk-proxy-injected", raising=False)


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


def _route(db, message: str, tokens: list[str], raws: list[str]) -> dict:
    from app.api.v1.system import references

    payload = references.ResolveReferenceRequest(
        query=message, tokens=tokens, raw_tokens=raws, allowed_entity_types=["product"] * len(tokens),
        match_mode="or", fallback_to_all_types=True, limit=50, dry_run=True,
    )
    return references.resolve_reference_post(payload, current_user={"id": None}, db=db)


def _report(name: str, result: dict) -> dict:
    row = {r["token"]: r for r in result["resolutions"]}
    outcome = {
        t: {
            "codes": [m["canonical_code"] for m in r["matches"]],
            "ambiguous": r["ambiguous"],
            "fallback": r.get("fallback"),
        }
        for t, r in row.items()
    }
    print(f"CANONICAL-CODE-REPLAY case={name!r} outcome={outcome}")
    return row


def test_the_owners_line_resolves_through_the_live_reader(db):
    """"TAP ZZTWT5608 x 5", parser not promoted: the folded token misses, the reader
    reads the code out, the exact tier answers."""
    row = _report("TAP ZZTWT5608 x 5", _route(db, "TAP ZZTWT5608 x 5", ["TAPZZTWT5608"], ["TAP ZZTWT5608"]))["TAPZZTWT5608"]
    assert row["fallback"]["kind"] == "code", row["fallback"]
    assert [m["canonical_code"] for m in row["matches"]] == [TAP]
    assert row["matches"][0]["match_tier"] == "exact"


def test_a_three_line_stock_check_answers_every_line_in_one_call(db):
    message = "stock check\nTAP ZZTWT5608 x 5\nWC ZZTWC8066-SH x 2\nZZTWC286-SH x 1"
    result = _route(
        db, message,
        ["TAPZZTWT5608", "WCZZTWC8066SH", "ZZTWC286SH"],
        ["TAP ZZTWT5608", "WC ZZTWC8066-SH", "ZZTWC286-SH"],
    )
    row = _report("three-line stock check", result)
    assert [m["canonical_code"] for m in row["TAPZZTWT5608"]["matches"]] == [TAP]
    # The exact row alone, never the -SH-150 sibling: the gate settles a typed code to its
    # exact row by comparing the TYPED token, which this folded token is not.
    assert [m["canonical_code"] for m in row["WCZZTWC8066SH"]["matches"]] == ["ZZTWC8066-SH"]
    assert [m["canonical_code"] for m in row["ZZTWC286SH"]["matches"]] == ["ZZTWC286-SH"]
    assert "fallback" not in row["ZZTWC286SH"], "the clean token was never asked about"
    assert result["unresolved_tokens"] == []


def test_a_bare_fragment_beside_a_type_word_is_the_family_picker(db):
    row = _report("TOILET 8066", _route(db, "TOILET 8066 stock", ["TOILET8066"], ["TOILET 8066"]))["TOILET8066"]
    assert row["fallback"]["kind"] in ("family", "code"), row["fallback"]
    assert sorted(m["canonical_code"] for m in row["matches"]) == FAMILY
    assert row["ambiguous"] is True


def test_a_word_with_no_code_stays_not_found(db):
    row = _report("bidet", _route(db, "bidet stock", ["BIDET"], ["bidet"]))["BIDET"]
    assert row["fallback"]["kind"] == "none", row["fallback"]
    assert row["matches"] == []


def test_a_near_miss_is_never_corrected(db):
    """"zztw5608" (a T missing): the catalogue has ZZTWT5608, and the reply still says
    not found - the owner's rule, enforced by the guard whatever the model answers."""
    row = _report("zztw5608", _route(db, "zztw5608 stock", ["zztw5608"], ["zztw5608"]))["zztw5608"]
    assert row["matches"] == []
    assert row["fallback"]["kind"] in ("none", "code", "family")
    assert all(m["canonical_code"] != TAP for m in row["matches"])
