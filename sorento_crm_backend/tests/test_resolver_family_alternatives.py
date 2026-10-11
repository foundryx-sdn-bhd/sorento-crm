"""RED tests for CODE-VARIANTS resolver behaviour (AC-6 .. AC-9).

UAC: documentation/plans/chatbot/code-variants-acceptance-criteria.md

A product token that ends with NO match gets its own model's family (products.family_code)
ahead of the trigram neighbours, flagged `display["same_family"]`. Real Postgres, own seed
chain, no embedding tier. Family codes are written literally on the seeded rows so these
tests do not depend on the write paths (those are in test_product_family_code.py).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.base import company_scope, set_company_scope
from app.models.company import Company
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.entity_resolver import ENTITY_MISS_SUGGEST_FLOOR, resolve_references
from tests._pg_fixture import blank_session, unique_code

SORENTO = "00000000-0000-0000-0000-000000000001"
MOCHA = "00000000-0000-0000-0000-000000000002"

FAMILY = ["SRTWC8357-RL", "SRTWC8357-RL-P", "SRTWC8357-200-RL", "SRTWC8357-300-RL"]
OTHERS = ["SRTWC8354-SH-150", "SRTWC8036-SH-150", "SRTWC8152-SH-150", "SRTWC8356-SH-150"]
FOLDED, RAW = "SRTWC8357SH150", "SRTWC8357-SH-150"


@pytest.fixture()
def db():
    with blank_session() as session:
        yield session


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def refs(db):
    cat, uom = _uid(), _uid()
    db.add(ProductCategory(id=cat, category_code=unique_code("c")[:50], category_name="ZZT"))
    db.add(UnitOfMeasure(id=uom, uom_code=unique_code("u")[:20], uom_name="Each"))
    if db.query(Company).filter(Company.id == MOCHA).first() is None:
        db.add(Company(id=MOCHA, name="ZZT Mocha", code=unique_code("MCH")[:20], is_active=True))
    db.flush()
    return cat, uom


def _p(db, refs, code, *, family=None, company=SORENTO, active=True) -> str:
    pid = _uid()
    db.add(
        Product(
            id=pid, company_id=company, product_code=code, product_name=f"ZZT {code}",
            category_id=refs[0], base_uom_id=refs[1], list_price=1, is_active=active,
            family_code=family,
        )
    )
    db.flush()
    return pid


def _seed_world(db, refs):
    for c in FAMILY:
        _p(db, refs, c, family="SRTWC8357")
    for c in OTHERS:
        _p(db, refs, c, family=c.split("-")[0])


def _resolve(db, token, raw=None, *, types=("product",)):
    with company_scope(db, frozenset({SORENTO})):
        result = resolve_references(
            db, [token], allowed_entity_types=list(types),
            raw_tokens=[raw] if raw is not None else None,
            alternatives_active_only=True, enable_embedding_fallback=False,
        )
    return result.resolutions[0]


def _codes(tr):
    return [a.canonical_code for a in tr.alternatives]


# --------------------------------------------------------------------------- AC-6 / AC-7
@pytest.mark.parametrize("token, raw", [(FOLDED, RAW), (RAW, None)], ids=["chatbot-shape", "raw-less"])
def test_family_members_lead_the_alternatives_flagged(db, refs, token, raw):
    _seed_world(db, refs)
    tr = _resolve(db, token, raw)
    assert not tr.matches
    codes = _codes(tr)
    assert set(codes[:4]) == set(FAMILY), codes
    assert all(a.display.get("same_family") is True for a in tr.alternatives[:4])
    rest = tr.alternatives[4:]
    assert rest, "other models still fill the remaining slot"
    assert all(not a.display.get("same_family") for a in rest)
    assert set(_codes(tr)[4:]) <= set(OTHERS)


def test_family_block_ordered_by_similarity_desc_then_code(db, refs):
    _seed_world(db, refs)
    tr = _resolve(db, FOLDED, RAW)
    block = [a for a in tr.alternatives if a.display.get("same_family")]
    keys = [(-(a.similarity or 0.0), a.canonical_code) for a in block]
    assert len(block) == 4 and keys == sorted(keys), keys
    assert all(a.similarity is not None for a in block)  # the real similarity(), not a constant


def test_cap_stays_five_and_family_fills_it_first(db, refs):
    _seed_world(db, refs)
    for extra in ("SRTWC8357-A", "SRTWC8357-B", "SRTWC8357-C"):
        _p(db, refs, extra, family="SRTWC8357")
    tr = _resolve(db, FOLDED, RAW)
    assert len(tr.alternatives) == 5
    assert all(a.display.get("same_family") is True for a in tr.alternatives)


def test_a_member_below_the_suggest_floor_is_still_offered(db, refs):
    _seed_world(db, refs)
    far = "SRTWC8357-" + "-".join(["ZQXJVKWYPLMNB", "QPLZMXNCBVKJ", "WRTYUIOPASDF", "GHJKLZXCVBNM", "POIUYTREWQLK"])
    _p(db, refs, far, family="SRTWC8357")
    sim = db.execute(
        text("SELECT similarity(lower(:a), lower(:b))"), {"a": far, "b": RAW}
    ).scalar()
    assert sim < ENTITY_MISS_SUGGEST_FLOOR, "premise: the far member is below the floor"
    tr = _resolve(db, FOLDED, RAW)
    assert far in _codes(tr)
    assert next(a for a in tr.alternatives if a.canonical_code == far).display.get("same_family") is True


def test_inactive_member_is_dropped_when_active_only(db, refs):
    _seed_world(db, refs)
    _p(db, refs, "SRTWC8357-OLD", family="SRTWC8357", active=False)
    assert "SRTWC8357-OLD" not in _codes(_resolve(db, FOLDED, RAW))


def test_another_company_member_is_never_shown(db, refs):
    _seed_world(db, refs)
    _p(db, refs, "SRTWC8357-MOCHA-ONLY", family="SRTWC8357", company=MOCHA)
    assert "SRTWC8357-MOCHA-ONLY" not in _codes(_resolve(db, FOLDED, RAW))


def test_family_key_comes_from_the_raw_text_not_the_folded_token(db, refs):
    """Folded 'SRTWC8357SH150' alone has no dash, so its key would be the whole token and
    match nothing. Only the raw text names the family."""
    _seed_world(db, refs)
    with_raw = _resolve(db, FOLDED, RAW)
    without_raw = _resolve(db, FOLDED, None)
    assert any(a.display.get("same_family") for a in with_raw.alternatives)
    assert not any(a.display.get("same_family") for a in without_raw.alternatives)


# --------------------------------------------------------------------------- AC-8 unchanged
def _baseline_vs_family(db, token, raw=None):
    with_family = _resolve(db, token, raw)
    db.execute(text("UPDATE products SET family_code = NULL"))
    db.expire_all()
    baseline = _resolve(db, token, raw)
    return with_family, baseline


def test_no_dash_token_with_no_such_family_is_unchanged(db, refs):
    _seed_world(db, refs)
    for c in ("SRTKS8153-RL", "SRTKS8153-BL", "SRTKS8151-RL"):
        _p(db, refs, c, family=c.split("-")[0])
    got, base = _baseline_vs_family(db, "SRTKS8152")
    assert _codes(got) == _codes(base) and _codes(got)
    assert not any(a.display.get("same_family") for a in got.alternatives)


def test_key_without_digit_is_unchanged(db, refs):
    for c in ("TPE-9201", "TPE-9202", "TPE-9203"):
        _p(db, refs, c, family="TPE")
    got, base = _baseline_vs_family(db, "TPE-9299")
    assert _codes(got) == _codes(base) and _codes(got)
    assert not any(a.display.get("same_family") for a in got.alternatives)


def test_a_matched_token_carries_no_family_alternatives(db, refs):
    _seed_world(db, refs)
    tr = _resolve(db, "SRTWC8357-RL-P", "SRTWC8357-RL-P")
    assert [m.canonical_code for m in tr.matches] == ["SRTWC8357-RL-P"]
    assert not any(a.display.get("same_family") for a in tr.alternatives)


def test_a_one_product_family_still_leads(db, refs):
    """Lead ruling (UAC AC-8): a family of one still leads; the rest keep today's order."""
    _p(db, refs, "SRTLONE9100-RL", family="SRTLONE9100")
    for c in ("SRTLONE9101-SH-150", "SRTLONE9102-SH-150"):
        _p(db, refs, c, family=c.split("-")[0])
    got, base = _baseline_vs_family(db, "SRTLONE9100SH150", "SRTLONE9100-SH-150")
    assert got.alternatives[0].canonical_code == "SRTLONE9100-RL"
    assert got.alternatives[0].display.get("same_family") is True
    assert _codes(got)[1:] == [c for c in _codes(base) if c != "SRTLONE9100-RL"]


# --------------------------------------------------------------------------- AC-9 route
_PATH = "/api/v1/system/references/resolve"


@pytest.fixture()
def api(db, refs):
    from app.main import app
    from app.database import get_db
    from app.dependencies import (
        get_current_user, get_current_user_or_api_key, get_external_api_user,
    )
    from app.services.company_scope_resolver import apply_company_scope

    principal = {"id": _uid(), "email": "zzt-family@test.com"}

    async def _scope():
        scope = frozenset({SORENTO})
        set_company_scope(db, scope)
        return scope

    def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    app.dependency_overrides[get_external_api_user] = lambda: principal
    app.dependency_overrides[apply_company_scope] = _scope
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def test_route_chatbot_shape_returns_family_first(api, db, refs):
    _seed_world(db, refs)
    r = api.post(
        _PATH,
        json={
            "query": RAW, "tokens": [FOLDED], "raw_tokens": [RAW],
            "allowed_entity_types": ["product"], "match_mode": "or",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    res = next(x for x in body["resolutions"] if x["token"] == FOLDED)
    alts = [a for a in res["alternatives"] if a["entity_type"] == "product"]
    assert {a["canonical_code"] for a in alts[:4]} == set(FAMILY), alts
    assert all(a["display"].get("same_family") is True for a in alts[:4])


def test_route_fallback_rerun_without_raw_tokens_still_family_first(api, db, refs):
    """The route's fallback pass (tokens_override, no raw_tokens) re-runs a missed token.
    Sent with a dashed token and no raw_tokens, the family key comes from the token itself."""
    _seed_world(db, refs)
    r = api.post(
        _PATH,
        json={
            "query": RAW, "tokens": [RAW], "allowed_entity_types": ["product"],
            "match_mode": "or", "fallback_to_all_types": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert RAW in body["unresolved_tokens"]
    res = next(x for x in body["resolutions"] if x["token"] == RAW)
    alts = [a for a in res["alternatives"] if a["entity_type"] == "product"]
    assert {a["canonical_code"] for a in alts[:4]} == set(FAMILY), alts
    assert all(a["display"].get("same_family") is True for a in alts[:4])
