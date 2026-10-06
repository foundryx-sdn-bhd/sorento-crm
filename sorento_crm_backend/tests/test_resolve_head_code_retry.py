"""Server-side head-code retry for product tokens.

WhatsApp users type "code + description" ("SRTWB8004 BASIN TAP"), and n8n folds it
into ONE token ("SRTWB8004BASINTAP") before calling resolve - the folded token
exact-misses even though SRTWB8004 exists in the catalogue. This is the retry: a
caller that also sends the pre-fold raw text (`raw_tokens`, positionally parallel
to `tokens`) gets one more exact probe, against the leading code-shaped word of
that raw text, PRODUCT ONLY, before the resolver gives up on the token.

Contract: `documentation/plans/PLAN-resolver-head-code-retry.md`, AC-1..AC-8 in
`documentation/plans/resolver-head-code-retry-acceptance-criteria.md`.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.models.base import set_company_scope
from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.entity_resolver import resolve_references

from ._pg_fixture import blank_session, unique_code

ENDPOINT = "/api/v1/system/references/resolve"
_USER = {"id": str(uuid.uuid4()), "email": "n8n@example.com"}


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope_listeners()


@pytest.fixture
def db():
    with blank_session() as session:
        set_company_scope(session, None)
        yield session


@pytest.fixture()
def client(db):
    from app.main import app
    from app.database import get_db
    from app.dependencies import (
        get_current_user,
        get_current_user_or_api_key,
        get_external_api_user,
    )
    from app.services.company_scope_resolver import apply_company_scope

    app.dependency_overrides[get_db] = lambda: db
    # The route resolves get_current_user_or_api_key underneath
    # get_external_api_user, so overriding only the outer one leaves the
    # request 401ing - which would make every "no match" assertion below pass
    # for the wrong reason.
    app.dependency_overrides[get_external_api_user] = lambda: _USER
    app.dependency_overrides[get_current_user_or_api_key] = lambda: _USER
    app.dependency_overrides[get_current_user] = lambda: _USER
    app.dependency_overrides[apply_company_scope] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _refs(db):
    if not hasattr(db, "_refs"):
        cat, uom = str(uuid.uuid4()), str(uuid.uuid4())
        db.add(ProductCategory(id=cat, category_code=unique_code("C")[:50], category_name="C"))
        db.add(UnitOfMeasure(id=uom, uom_code=unique_code("U")[:20], uom_name="Each"))
        db.flush()
        db._refs = (cat, uom)
    return db._refs


def _brand(db, *, code: str, name: str) -> str:
    bid = str(uuid.uuid4())
    db.add(
        Brand(
            id=bid,
            brand_code=unique_code(code)[:50],
            brand_name=name,
            company_id=DEFAULT_COMPANY_ID,
        )
    )
    db.flush()
    return bid


def _product(
    db,
    *,
    code: str,
    brand_id: str | None = None,
    company_id: str | None = DEFAULT_COMPANY_ID,
) -> str:
    cat, uom = _refs(db)
    pid = str(uuid.uuid4())
    db.add(
        Product(
            id=pid,
            product_code=code,
            product_name=code,
            category_id=cat,
            base_uom_id=uom,
            list_price=10,
            is_active=True,
            company_id=company_id,
            brand_id=brand_id,
        )
    )
    db.flush()
    return pid


def test_the_fixture_is_actually_authenticated(client):
    """Guard the guards: a 401 body has no `resolutions` either, so every
    "no head_code match" assertion below would pass for the wrong reason
    against an unauthenticated response."""
    response = client.post(ENDPOINT, json={"query": "anything"})
    assert response.status_code == 200
    assert "resolutions" in response.json()


# AC-1: head-code resolve, full row fields the n8n routing depends on.
def test_ac1_head_code_resolve(db, client):
    bid = _brand(db, code="ZZTBR1", name="ZZT Brand One")
    _product(db, code="ZZTWB8004", brand_id=bid)
    db.flush()

    body = client.post(
        ENDPOINT,
        json={"tokens": ["ZZTWB8004BASINTAP"], "raw_tokens": ["ZZTWB8004 BASIN TAP"]},
    ).json()

    tr = next(r for r in body["resolutions"] if r["token"] == "ZZTWB8004BASINTAP")
    assert len(tr["matches"]) == 1
    match = tr["matches"][0]
    assert match["entity_type"] == "product"
    assert match["canonical_code"] == "ZZTWB8004"
    assert match["match_tier"] == "head_code"
    assert match["company_id"] is not None
    assert (match.get("display") or {}).get("brand") is not None


# AC-2: a SPACED catalogue code still exact-hits Tier 1 - the retry never fires.
def test_ac2_spaced_codes_still_exact_hit(db, client):
    _product(db, code="ZZTWB7299-WALL HUNG")
    _product(db, code="ZZT86CR-HEAD ONLY")
    db.flush()

    body = client.post(
        ENDPOINT,
        json={
            "tokens": ["ZZTWB7299WALLHUNG"],
            "raw_tokens": ["ZZTWB7299-WALL HUNG"],
        },
    ).json()

    tr = next(r for r in body["resolutions"] if r["token"] == "ZZTWB7299WALLHUNG")
    assert len(tr["matches"]) == 1
    assert tr["matches"][0]["match_tier"] == "exact"
    assert tr["matches"][0]["canonical_code"] == "ZZTWB7299-WALL HUNG"


# AC-3: split codes unchanged - both the happy path and the no-digit-head miss.
def test_ac3_split_code_exact_hit_unchanged(db, client):
    _product(db, code="ZZB6201")
    db.flush()

    body = client.post(ENDPOINT, json={"tokens": ["ZZB6201"]}).json()

    tr = next(r for r in body["resolutions"] if r["token"] == "ZZB6201")
    assert len(tr["matches"]) == 1
    assert tr["matches"][0]["match_tier"] == "exact"


def test_ac3_head_with_no_digit_gains_no_match(client):
    # "ZZB 6201" folded to "ZZB6201X" (a code that does not exist) - the head
    # candidate "ZZB" carries no digit, so the pattern rejects it outright.
    body = client.post(
        ENDPOINT,
        json={"tokens": ["ZZB6201X"], "raw_tokens": ["ZZB 6201"]},
    ).json()

    tr = next(r for r in body["resolutions"] if r["token"] == "ZZB6201X")
    assert tr["matches"] == []


# AC-4: no code-like head at all - the retry does not fire, response matches
# the no-raw_tokens response for the same token.
#
# Since RESOLVE-SEMANTIC-FALLBACK the raw text has a SECOND reader on the route, the
# code reader (`semantic_fallback`, on by default): with raw text it is asked about the
# miss and its verdict rides the `fallback` trace key; with no raw text it is not asked
# (`not_asked / no_typed_text`). So the retry's own invariant is pinned with that reader
# opted out (byte-identical), and with it on, the trace key is the ONLY difference.
def test_ac4_no_code_like_head_unchanged(client):
    with_raw = client.post(
        ENDPOINT,
        json={
            "tokens": ["BASINTAPCHROME"],
            "raw_tokens": ["BASIN TAP CHROME"],
            "semantic_fallback": False,
        },
    ).json()
    without_raw = client.post(
        ENDPOINT, json={"tokens": ["BASINTAPCHROME"], "semantic_fallback": False}
    ).json()

    tr_with = next(r for r in with_raw["resolutions"] if r["token"] == "BASINTAPCHROME")
    tr_without = next(r for r in without_raw["resolutions"] if r["token"] == "BASINTAPCHROME")
    assert tr_with["matches"] == []
    assert tr_with == tr_without
    assert "fallback" not in tr_with


def test_ac4_with_the_code_reader_on_only_its_trace_key_differs(client):
    with_raw = client.post(
        ENDPOINT,
        json={"tokens": ["BASINTAPCHROME"], "raw_tokens": ["BASIN TAP CHROME"]},
    ).json()
    without_raw = client.post(ENDPOINT, json={"tokens": ["BASINTAPCHROME"]}).json()

    tr_with = next(r for r in with_raw["resolutions"] if r["token"] == "BASINTAPCHROME")
    tr_without = next(r for r in without_raw["resolutions"] if r["token"] == "BASINTAPCHROME")
    assert tr_with["matches"] == []
    assert not any(m.get("match_tier") == "head_code" for m in tr_with["matches"])
    # The reader was asked only where there was typed text to read; the retry itself
    # produced nothing either way.
    assert tr_with.pop("fallback")["asked"] is True
    assert tr_without.pop("fallback") == {"asked": False, "kind": "not_asked", "reason": "no_typed_text"}
    assert tr_with == tr_without


# AC-5: inert without raw_tokens - byte-identical to today.
def test_ac5_inert_without_raw_tokens(db, client):
    _product(db, code="ZZTWB8005")
    db.flush()

    body = client.post(ENDPOINT, json={"tokens": ["ZZTWB8005BASINTAP"]}).json()

    tr = next(r for r in body["resolutions"] if r["token"] == "ZZTWB8005BASINTAP")
    assert tr["matches"] == []
    assert not any(m.get("match_tier") == "head_code" for m in tr["matches"])


# AC-6: retry beats spec-search - a head-resolvable token counts as resolved,
# so spec_candidates are not produced for it.
def test_ac6_retry_beats_spec_search(db, client):
    _product(db, code="ZZTWB8006")
    db.flush()

    body = client.post(
        ENDPOINT,
        json={
            "tokens": ["ZZTWB8006BASINTAP"],
            "raw_tokens": ["ZZTWB8006 BASIN TAP"],
            "spec_fallback": True,
            "free_terms": ["basin tap"],
        },
    ).json()

    tr = next(r for r in body["resolutions"] if r["token"] == "ZZTWB8006BASINTAP")
    assert len(tr["matches"]) == 1
    assert tr["matches"][0]["match_tier"] == "head_code"
    assert "spec_candidates" not in body


# AC-7: head exact-miss is a clean miss, no error.
def test_ac7_head_exact_miss_is_a_clean_miss(db, client):
    # Seed the family so a matcher bug that answers with the WRONG code (rather
    # than nothing) is caught, not just a raised exception.
    _product(db, code="ZZTWB7299-WALL HUNG")
    db.flush()

    body = client.post(
        ENDPOINT,
        json={
            "tokens": ["ZZTWB7299WALLHUNGEXTRA"],
            "raw_tokens": ["ZZTWB7299-WALL HUNG EXTRA"],
        },
    ).json()

    tr = next(r for r in body["resolutions"] if r["token"] == "ZZTWB7299WALLHUNGEXTRA")
    assert tr["matches"] == []


# AC-8: length-mismatch ignored - field ignored, behaviour as AC-5.
def test_ac8_length_mismatch_ignored(db, client):
    _product(db, code="ZZTWB8008")
    db.flush()

    shorter = client.post(
        ENDPOINT,
        json={"tokens": ["ZZTWB8008BASINTAP", "OTHER"], "raw_tokens": ["ZZTWB8008 BASIN TAP"]},
    ).json()
    longer = client.post(
        ENDPOINT,
        json={
            "tokens": ["ZZTWB8008BASINTAP"],
            "raw_tokens": ["ZZTWB8008 BASIN TAP", "EXTRA"],
        },
    ).json()
    baseline = client.post(ENDPOINT, json={"tokens": ["ZZTWB8008BASINTAP"]}).json()

    tr_shorter = next(r for r in shorter["resolutions"] if r["token"] == "ZZTWB8008BASINTAP")
    tr_longer = next(r for r in longer["resolutions"] if r["token"] == "ZZTWB8008BASINTAP")
    tr_baseline = next(r for r in baseline["resolutions"] if r["token"] == "ZZTWB8008BASINTAP")
    assert tr_shorter["matches"] == []
    assert tr_longer["matches"] == []
    assert tr_shorter == tr_baseline
    assert tr_longer == tr_baseline


# --------------------------------------------------------------------------- #
# Service-level coverage: same behaviour reached through resolve_references()
# directly, without the HTTP layer in between.
# --------------------------------------------------------------------------- #
def test_service_level_head_code_hit_stamps_tier(db):
    pid = _product(db, code="ZZTWB8009")
    db.commit()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))

    result = resolve_references(
        db,
        ["ZZTWB8009BASINTAP"],
        allowed_entity_types=["product"],
        raw_tokens=["ZZTWB8009 BASIN TAP"],
    )

    tr = next(r for r in result.resolutions if r.token == "ZZTWB8009BASINTAP")
    assert len(tr.matches) == 1
    assert tr.matches[0].uuid == pid
    assert tr.matches[0].match_tier == "head_code"


def test_service_level_ambiguous_when_multiple_products_share_a_head(db):
    """Two different products both carrying the exact head code is a real
    disambiguation, same as any other multi-hit tier."""
    _product(db, code="ZZTWB8010")
    cat, uom = _refs(db)
    # A second company owns an identically-coded row - both are visible under
    # the unrestricted (None) company scope this test runs under.
    from app.models.company import Company

    other = str(uuid.uuid4())
    db.add(Company(id=other, name="ZZT Other Co", code=unique_code("OTH")[:20]))
    db.flush()
    _product(db, code="ZZTWB8010", company_id=other)
    db.commit()

    result = resolve_references(
        db,
        ["ZZTWB8010BASINTAP"],
        allowed_entity_types=["product"],
        raw_tokens=["ZZTWB8010 BASIN TAP"],
    )

    tr = next(r for r in result.resolutions if r.token == "ZZTWB8010BASINTAP")
    assert len(tr.matches) == 2
    assert tr.ambiguous is True
    assert all(m.match_tier == "head_code" for m in tr.matches)


def test_service_level_non_product_type_does_not_retry(db):
    """A caller whitelisting only a non-product type must never get a product
    back from the retry - `_types_for` gates it the same as every other probe."""
    _product(db, code="ZZTWB8011")
    db.commit()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))

    result = resolve_references(
        db,
        ["ZZTWB8011BASINTAP"],
        allowed_entity_types=["customer"],
        raw_tokens=["ZZTWB8011 BASIN TAP"],
    )

    tr = next(r for r in result.resolutions if r.token == "ZZTWB8011BASINTAP")
    assert tr.matches == []


def test_service_level_to_prompt_block_carries_the_tier_note(db):
    _product(db, code="ZZTWB8012")
    db.commit()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))

    result = resolve_references(
        db,
        ["ZZTWB8012BASINTAP"],
        allowed_entity_types=["product"],
        raw_tokens=["ZZTWB8012 BASIN TAP"],
    )

    block = result.to_prompt_block()
    assert "matched by leading code; trailing words ignored" in block
