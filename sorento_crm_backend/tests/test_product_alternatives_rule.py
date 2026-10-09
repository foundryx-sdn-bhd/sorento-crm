"""PRODUCT-ALTERNATIVES: the -NEW rule and the stored alternative links.

Card: documentation/plans/chatbot/CARD-product-alternatives-08oct.md.

Owner (8 Oct 2026): "we need to have a function to always set -NEW is the alternative for
its counterpart, be it during product sync, product creation or what not". The rule is a
data rule on PRODUCT CODES (never user text): product Y is the alternative of product X,
same company, when removing exactly one whole `NEW` segment (split on `-`, case-insensitive)
from Y's code gives X's code. It writes explicit `product_alternatives` rows with
`source = auto_new`; a product whose alternatives a person edited by hand
(`products.alternatives_manual`) is never touched by the rule again.

Every product here is seeded from a blank chain with synthetic `ZZALT*` codes, inside a
rolled-back transaction (`tests/_pg_fixture`-style, Postgres only).
"""
from __future__ import annotations

import uuid

import pytest
from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.models.product import Product, ProductAlternative
from app.models.base import set_company_scope
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.master_ingest_service import MasterIngestService
from app.services.product_alternative_service import (
    SOURCE_AUTO_NEW,
    SOURCE_MANUAL,
    alternatives_for,
    backfill_new_alternatives,
    new_base_codes,
    reconcile_new_alternatives,
)

from tests._mc_lookup_seed import product, seed_mocha, MOCHA_ID
from tests._pg_fixture import blank_session


@pytest.fixture()
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
        yield session


def _code(stem: str) -> str:
    """A unique synthetic stem so parallel runs and the real catalogue never collide."""
    return f"ZZALT{uuid.uuid4().hex[:6].upper()}{stem}"


def _links(db, product_id: str) -> list[tuple[str, str]]:
    rows = (
        db.query(ProductAlternative)
        .filter(ProductAlternative.product_id == product_id)
        .all()
    )
    return sorted((r.alternative_product_id, r.source) for r in rows)


# --------------------------------------------------------------------------- #
# The code transform (pure)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "code, expected",
    [
        ("SRTBF11821-NEW", {"SRTBF11821"}),             # suffix
        ("srtbf11821-new", {"SRTBF11821"}),             # case-insensitive
        ("SRTWC286-SH-NEW-200", {"SRTWC286-SH-200"}),   # mid-code
        ("SRTWC286-SH-NEW-P", {"SRTWC286-SH-P"}),
        ("SRTWC286-SH-NEW", {"SRTWC286-SH"}),
        ("X1-NEW-NEW", {"X1-NEW"}),                     # chain: one hop
        ("NEW-X1", {"X1"}),                             # leading segment
        ("SRT1000-CR - NEW", {"SRT1000-CR"}),           # spaces around a segment (crew)
        ("SRTUB6503-BACK INLET - NEW", {"SRTUB6503-BACK INLET"}),
        ("**NEW", set()),
        ("SRTBF11821NEW", set()),                       # not a whole segment
        ("NEWTON-1", set()),
        ("SRTBF11821", set()),
        ("NEW", set()),                                 # nothing left
        ("", set()),
        (None, set()),
    ],
)
def test_new_base_codes(code, expected):
    assert new_base_codes(code) == expected


# --------------------------------------------------------------------------- #
# reconcile: one product at a time, either side of the pair, idempotent
# --------------------------------------------------------------------------- #
def test_new_product_links_to_existing_base(db):
    base_code = _code("BF")
    base = product(db, company_id=DEFAULT_COMPANY_ID, code=base_code)
    new = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{base_code}-NEW")

    added = reconcile_new_alternatives(db, new)

    assert added == 1
    assert _links(db, base.id) == [(new.id, SOURCE_AUTO_NEW)]
    assert _links(db, new.id) == []  # one direction only: old -> new


def test_base_created_after_its_new_code_is_linked_too(db):
    base_code = _code("BF")
    new = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{base_code}-NEW")
    base = product(db, company_id=DEFAULT_COMPANY_ID, code=base_code)

    reconcile_new_alternatives(db, base)

    assert _links(db, base.id) == [(new.id, SOURCE_AUTO_NEW)]


def test_mid_code_new_links(db):
    stem = _code("WC")
    base = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{stem}-SH-200")
    new = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{stem}-SH-NEW-200")
    # Sibling noise that must NOT link to the -200 base.
    other = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{stem}-SH-NEW-150")

    reconcile_new_alternatives(db, new)
    reconcile_new_alternatives(db, other)

    assert _links(db, base.id) == [(new.id, SOURCE_AUTO_NEW)]


def test_reconcile_is_idempotent(db):
    base_code = _code("BF")
    base = product(db, company_id=DEFAULT_COMPANY_ID, code=base_code)
    new = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{base_code}-NEW")

    reconcile_new_alternatives(db, new)
    second = reconcile_new_alternatives(db, new)
    reconcile_new_alternatives(db, base)

    assert second == 0
    assert _links(db, base.id) == [(new.id, SOURCE_AUTO_NEW)]


def test_no_base_no_link(db):
    orphan = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{_code('OR')}-NEW")
    assert reconcile_new_alternatives(db, orphan) == 0
    assert db.query(ProductAlternative).filter(
        ProductAlternative.alternative_product_id == orphan.id
    ).count() == 0


def test_spaced_new_code_links_to_its_base(db):
    stem = _code("CR")
    base = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{stem}-CR")
    new = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{stem}-CR - NEW")

    reconcile_new_alternatives(db, new)

    assert _links(db, base.id) == [(new.id, SOURCE_AUTO_NEW)]


def test_never_links_across_companies(db):
    seed_mocha(db)
    base_code = _code("BF")
    base = product(db, company_id=DEFAULT_COMPANY_ID, code=base_code)
    new_other_co = product(db, company_id=MOCHA_ID, code=f"{base_code}-NEW")

    reconcile_new_alternatives(db, new_other_co)
    reconcile_new_alternatives(db, base)

    assert _links(db, base.id) == []


def test_manual_product_is_never_touched(db):
    base_code = _code("BF")
    base = product(db, company_id=DEFAULT_COMPANY_ID, code=base_code)
    base.alternatives_manual = True
    db.flush()
    new = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{base_code}-NEW")

    assert reconcile_new_alternatives(db, new) == 0
    assert reconcile_new_alternatives(db, base) == 0
    assert _links(db, base.id) == []


def test_rename_drops_the_stale_auto_link_but_keeps_manual(db):
    base_code = _code("BF")
    base = product(db, company_id=DEFAULT_COMPANY_ID, code=base_code)
    new = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{base_code}-NEW")
    hand = product(db, company_id=DEFAULT_COMPANY_ID, code=_code("HAND"))
    reconcile_new_alternatives(db, new)
    db.add(
        ProductAlternative(
            product_id=base.id,
            alternative_product_id=hand.id,
            source=SOURCE_MANUAL,
            company_id=DEFAULT_COMPANY_ID,
        )
    )
    db.flush()

    new.product_code = f"{_code('ELSE')}-NEW"
    db.flush()
    reconcile_new_alternatives(db, new)

    assert _links(db, base.id) == [(hand.id, SOURCE_MANUAL)]


# --------------------------------------------------------------------------- #
# lookup: one hop, active alternatives only, ordered by code
# --------------------------------------------------------------------------- #
def test_alternatives_for_returns_one_hop_active_only(db):
    base_code = _code("BF")
    base = product(db, company_id=DEFAULT_COMPANY_ID, code=base_code)
    new = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{base_code}-NEW")
    newer = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{base_code}-NEW-NEW")
    for p in (base, new, newer):
        reconcile_new_alternatives(db, p)

    got = alternatives_for(db, [base.id, new.id])
    assert [p.product_code for p in got[base.id]] == [new.product_code]
    assert [p.product_code for p in got[new.id]] == [newer.product_code]

    new.is_active = False
    db.flush()
    assert alternatives_for(db, [base.id]).get(base.id, []) == []


def test_alternatives_for_empty_input(db):
    assert alternatives_for(db, []) == {}


# --------------------------------------------------------------------------- #
# backfill: one-off pass over the catalogue, dry run by default
# --------------------------------------------------------------------------- #
def test_backfill_dry_run_writes_nothing_then_apply_writes(db):
    stem = _code("WC")
    base = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{stem}-SH-200")
    new = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{stem}-SH-NEW-200")

    report = backfill_new_alternatives(db, apply=False)
    assert report["would_add"] >= 1
    assert (base.product_code, new.product_code) in report["pairs"]
    assert _links(db, base.id) == []

    report = backfill_new_alternatives(db, apply=True)
    assert report["added"] >= 1
    assert _links(db, base.id) == [(new.id, SOURCE_AUTO_NEW)]

    again = backfill_new_alternatives(db, apply=True)
    assert again["added"] == 0


# --------------------------------------------------------------------------- #
# write paths: AutoCount ingest and the product API create
# --------------------------------------------------------------------------- #
def test_autocount_ingest_links_new_code(db):
    svc = MasterIngestService(db, integration_id=None, company_id=DEFAULT_COMPANY_ID)
    stem = _code("ING")
    svc.ingest("product_categories", [{"source_ref": f"DK-{stem}-C", "code": f"{stem}-C", "name": "Cat"}])
    svc.ingest("units_of_measure", [{"source_ref": f"DK-{stem}-U", "code": f"{stem}-U", "name": "Each"}])
    common = {"category_code": f"{stem}-C", "uom_code": f"{stem}-U"}
    svc.ingest("products", [{"source_ref": f"DK-{stem}-1", "code": stem, "name": "Old", **common}])
    svc.ingest("products", [{"source_ref": f"DK-{stem}-2", "code": f"{stem}-NEW", "name": "New", **common}])

    base = db.query(Product).filter(Product.product_code == stem).one()
    new = db.query(Product).filter(Product.product_code == f"{stem}-NEW").one()
    assert _links(db, base.id) == [(new.id, SOURCE_AUTO_NEW)]


def test_product_api_create_links_new_code(db):
    from app.schemas.product import ProductCreate
    from app.services.product_service import ProductService

    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))  # the route's one-company scope
    base_code = _code("API")
    base = product(db, company_id=DEFAULT_COMPANY_ID, code=base_code)
    created = ProductService(db).create_product(
        ProductCreate(
            product_code=f"{base_code}-NEW",
            product_name="New",
            category_id=base.category_id,
            base_uom_id=base.base_uom_id,
            list_price=0,
        ),
        created_by=None,
    )

    assert _links(db, base.id) == [(created.id, SOURCE_AUTO_NEW)]


# --------------------------------------------------------------------------- reviewer round 1


def test_normalize_code_is_the_twin_of_the_sql_normaliser(db):
    from sqlalchemy import literal, select

    from app.services.product_alternative_service import _norm_sql, normalize_code

    for raw in ["srt1000-cr - new", "  SRTUB6503-BACK INLET - NEW ", "A -\tNEW", "x--new", "AB", "AB\t", "\nAB"]:
        sql = db.execute(select(_norm_sql(literal(raw)))).scalar()
        assert normalize_code(raw) == sql, raw


def test_manual_base_keeps_its_auto_link_when_the_new_code_is_renamed(db):
    base_code = _code("BF")
    base = product(db, company_id=DEFAULT_COMPANY_ID, code=base_code)
    new = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{base_code}-NEW")
    reconcile_new_alternatives(db, new)
    base.alternatives_manual = True
    db.flush()

    new.product_code = f"{_code('ELSE')}-NEW"
    db.flush()
    reconcile_new_alternatives(db, new)

    assert _links(db, base.id) == [(new.id, SOURCE_AUTO_NEW)]


def test_renamed_base_drops_its_stale_auto_link(db):
    base_code = _code("BF")
    base = product(db, company_id=DEFAULT_COMPANY_ID, code=base_code)
    new = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{base_code}-NEW")
    reconcile_new_alternatives(db, new)

    base.product_code = _code("RENAMED")
    db.flush()
    reconcile_new_alternatives(db, base)

    assert _links(db, base.id) == []


def test_excel_bulk_import_links_new_code(db, monkeypatch):
    from app.models.product import ProductCategory, UnitOfMeasure
    from app.services import product_service as product_service_mod

    for name in ("_bulk_publish_product_embedding_events",):
        monkeypatch.setattr(product_service_mod.ProductService, name, lambda self, *a, **kw: None)
    monkeypatch.setattr(
        product_service_mod.ProductService, "_resolve_default_supplier_for_new_product", lambda self: None
    )
    monkeypatch.setattr(
        product_service_mod.ProductService, "_default_standard_lead_time_days", lambda self: None
    )
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    cat_code = _code("CAT")[:40]
    db.add(ProductCategory(id=str(uuid.uuid4()), category_code=cat_code, category_name="Cat"))
    db.add(UnitOfMeasure(id=str(uuid.uuid4()), uom_code=_code("U")[:40], uom_name="Each"))
    db.commit()
    base_code = _code("XL")
    rows = [
        {"product_code": base_code, "product_name": "Old", "item_group": cat_code, "list_price": "1"},
        {"product_code": f"{base_code}-NEW", "product_name": "New", "item_group": cat_code, "list_price": "1"},
    ]

    product_service_mod.ProductService(db).bulk_import_products(rows, user_id=None)

    base = db.query(Product).filter(Product.product_code == base_code).one()
    new = db.query(Product).filter(Product.product_code == f"{base_code}-NEW").one()
    assert _links(db, base.id) == [(new.id, SOURCE_AUTO_NEW)]


def test_alternatives_for_excludes_a_non_searchable_alternative(db):
    base_code = _code("BF")
    base = product(db, company_id=DEFAULT_COMPANY_ID, code=base_code)
    new = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{base_code}-NEW")
    reconcile_new_alternatives(db, new)
    new.is_searchable = False
    db.flush()
    assert alternatives_for(db, [base.id]) == {}
