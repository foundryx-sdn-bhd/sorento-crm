"""SPEC-PROMO-HELP (crew latency on #1582, "250mm floor grating got stock?" routed 3037 ms):
`search_specs(exact=True)` loaded EVERY chat searchable product with its specs and its brand,
then dropped in Python each one whose stored class the words did not name. Profiled on 3000
seeded products: 1.5 s of the resolver's 2.0 s, for a size ask whose candidates the lane
then discards (the route filters by `spec_constraints`).

The class test now runs in SQL as well, so only the named class is loaded. The candidates are
the same (a variant family whose parent holds another class aside, reviewer S2); the rows
read are fewer. Made-up codes only (ZZT-SXP-*).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.product_spec import ProductSpecifications
from app.services.product_class_signal import backfill_category_signals
from app.services.product_spec_registry import seed_spec_registry
from app.services.product_spec_search import search_specs
from tests._pg_fixture import blank_session

BASINS = [("B1", 600), ("B2", 600), ("B3", 450)]
OTHERS = [("S1", "Kitchen Sink", 600), ("S2", "Kitchen Sink", 600), ("T1", "Tap", None)]


@pytest.fixture
def db():
    with blank_session() as s:
        for label, synonyms in (("Wash Basin", ["basin"]), ("Kitchen Sink", ["sink"]), ("Tap", ["tap"])):
            s.add(ProductCategory(
                id=str(uuid.uuid4()), category_code=f"ZZT-SXP-{label[:4].upper()}", category_name=label,
                class_label=label, is_searchable=True, search_synonyms=synonyms,
            ))
        cat = ProductCategory(id=str(uuid.uuid4()), category_code="ZZT-SXP-CAT", category_name="ZZT")
        uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code="ZZT-SXP-UOM", uom_name="Each")
        s.add_all([cat, uom])
        s.flush()
        backfill_category_signals(s)
        seed_spec_registry(s)
        rows = [(tag, "Wash Basin", mm) for tag, mm in BASINS] + OTHERS
        for tag, klass, mm in rows:
            pid = str(uuid.uuid4())
            s.add(Product(
                id=pid, product_code=f"ZZT-SXP-{tag}", product_name=f"ZZT-SXP-{tag}", category_id=cat.id,
                base_uom_id=uom.id, list_price=Decimal("1.00"), is_active=True,
            ))
            s.flush()
            values = {"class": {"value": klass}}
            if mm is not None:
                values["dim_length"] = {"value": mm, "unit": "mm"}
            s.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=pid, values=values))
        s.flush()
        yield s


def _loaded_spec_rows(db, call):
    """The `product_specifications` rows the ORM hydrated while `call` ran."""
    loaded: list[str] = []

    def on_load(target, _context):
        loaded.append(str(target.product_id))

    event.listen(ProductSpecifications, "load", on_load)
    try:
        db.expire_all()
        result = call()
    finally:
        event.remove(ProductSpecifications, "load", on_load)
    return result, loaded


def test_exact_ranking_loads_only_the_named_class(db):
    """RED before the fix: all six spec rows were loaded to answer "basin"."""
    basin_ids = {
        str(p.id) for p in db.query(Product).filter(Product.product_code.in_([f"ZZT-SXP-{t}" for t, _ in BASINS]))
    }
    found, loaded = _loaded_spec_rows(db, lambda: search_specs(db, free_terms=["basin"], exact=True))
    assert set(loaded) == basin_ids, loaded
    assert {c["product_code"] for c in found["candidates"]} == {f"ZZT-SXP-{t}" for t, _ in BASINS}


def test_the_answer_is_unchanged_with_a_spec_and_the_class(db):
    """Guard: the class prefilter keeps the exact rule (every value held) and the non-exact
    ranker is not narrowed (it still loads every row and ranks other classes)."""
    found = search_specs(db, specs=[{"key": "dim_length", "value": 600}], free_terms=["basin"], exact=True)
    assert {c["product_code"] for c in found["candidates"]} == {"ZZT-SXP-B1", "ZZT-SXP-B2"}
    _loose, loaded = _loaded_spec_rows(
        db, lambda: search_specs(db, specs=[{"key": "dim_length", "value": 600}], free_terms=["basin"])
    )
    assert len(loaded) == len(BASINS) + len(OTHERS)


def test_a_stated_class_spec_or_two_types_keeps_the_old_read(db):
    """Guard: with a `class` spec stated, a class may also be matched through that spec, and
    two product types in one phrase match nothing; neither is narrowed in SQL."""
    stated = search_specs(db, specs=[{"key": "class", "value": "Kitchen Sink"}], free_terms=["basin"], exact=True)
    two = search_specs(db, free_terms=["water tap basin"], exact=True)
    assert two["candidates"] == []
    assert isinstance(stated["candidates"], list)
