"""PV2-RESOLVE review round 1: scope and the cases the first table missed. No LLM, no network.

B1: spec ranges and closest rows read only products the contact can see (company scope).
S4: clarify candidates for a null brand key come from visible products.
S7: inactive customers never resolve. S8: clear/remove ops, enum value with no attribute,
enum attribute with no value, product_type null key + phrase.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.base import company_scope
from app.models.company import Company
from app.models.product import Product
from app.services.chatbot.pv2.resolve import DbResolver, Entity, Held, ResolvedRef, Spec

from tests._pg_fixture import unique_code
from tests.chatbot.pv2 import _resolve_seed as S


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def held(**refs) -> Held:
    return Held(refs={k: v for k, v in refs.items()})


def resolver(db, **pinned) -> DbResolver:
    base = {"brand": set(), "product_type": set(), "attachment_type": set(), "spec_attribute": set()}
    base.update({k: set(v) for k, v in pinned.items()})
    return DbResolver(db, pinned=base)


def _other_company_product(db, code, cat):
    other = Company(id=str(uuid.uuid4()), name="Other Co", code=unique_code("OC")[:50])
    db.add(other)
    db.flush()
    with company_scope(db, None):
        row = Product(
            id=str(uuid.uuid4()), product_code=code, product_name=f"Name of {code}", category_id=cat.id,
            base_uom_id=S._uom(db), list_price=0, is_active=True, company_id=other.id,
        )
        db.add(row)
        db.flush()
    return row


# --------------------------------------------------------------------------- B1
def test_closest_rows_and_ranges_never_read_another_companys_products(db):
    S.registry(db, "dim_length", "Length", "numeric", unit="mm")
    cat = S.category(db, class_label="Water Closet")
    mine = S.product(db, "MINE-1", cat=cat)
    S.specs(db, mine, klass="Water Closet", dim_length=600)
    theirs = _other_company_product(db, "THEIRS-1", cat)
    S.specs(db, theirs, klass="Water Closet", dim_length=4900)
    r = resolver(db, spec_attribute={"dim_length"})

    far = r.resolve_specs([Spec(number=5000, unit="mm")], held=held())[0]
    assert far.status == "none"
    assert [o.key for o in far.closest] == ["MINE-1"]

    # 4900 is inside the range only if the other company's row widens it.
    inside_theirs = r.resolve_specs([Spec(number=4900, unit="mm")], held=held())[0]
    assert inside_theirs.status == "none"


def test_closest_rows_skip_products_in_an_unsearchable_category(db):
    S.registry(db, "dim_length", "Length", "numeric", unit="mm")
    hidden = S.category(db, class_label="Water Closet", searchable=False)
    shown = S.category(db, class_label="Water Closet")
    S.specs(db, S.product(db, "HIDE-1", cat=hidden), klass="Water Closet", dim_length=990)
    S.specs(db, S.product(db, "SHOW-1", cat=shown), klass="Water Closet", dim_length=500)
    out = resolver(db, spec_attribute={"dim_length"}).resolve_specs([Spec(number=1000)], held=held())[0]
    assert [o.key for o in out.closest] == ["SHOW-1"]


def test_closest_uses_each_attribute_in_its_own_unit(db):
    S.registry(db, "dim_length", "Length", "numeric", unit="mm")
    S.registry(db, "hose_length", "Hose length", "numeric", unit="cm")
    cat = S.category(db, class_label="Water Closet")
    p = S.product(db, "HOSE-1", cat=cat)
    S.specs(db, p, klass="Water Closet", hose_length=50, dim_length=500)
    # 9 m: neither range holds it; both attributes offer their real row, in their own unit.
    out = resolver(db, spec_attribute={"dim_length", "hose_length"}).resolve_specs(
        [Spec(number=9, unit="m")], held=held()
    )[0]
    assert out.status == "none"
    assert out.closest and out.closest[0].key == "HOSE-1"


def test_number_in_cm_on_a_cm_attribute_asked_in_mm_converts(db):
    S.registry(db, "hose_length", "Hose length", "numeric", unit="cm")
    out = resolver(db, spec_attribute={"hose_length"}).resolve_specs(
        [Spec(attribute="hose_length", number=500, unit="mm")], held=held()
    )[0]
    assert out.status == "exact" and out.specs[0].value == 50 and out.specs[0].unit == "cm"


# --------------------------------------------------------------------------- S4
def test_null_brand_key_offers_only_brands_with_visible_products(db):
    sold = S.brand(db, "Sorento Brand", code="SORENTO")
    S.brand(db, "Ghost Brand", code="GHOST")  # live, pinned, but no product the contact can see
    S.product(db, "BR-1", brand_row=sold)
    out = resolver(db, brand={"SORENTO", "GHOST"}).resolve_entities(
        [Entity(kind="brand", key=None, phrase="that brand", op="set", refers_to_held=False)], held=held()
    )[0]
    assert [o.label for o in out.options] == ["Sorento Brand"]
    assert out.unrecognised is True


def test_null_product_type_key_with_phrase_is_never_resolved_from_the_phrase(db):
    cat = S.category(db, class_label="Water Closet")
    S.product(db, "WC-1", cat=cat)
    out = resolver(db, product_type={"Water Closet"}).resolve_entities(
        [Entity(kind="product_type", key=None, phrase="Water Closet", op="set", refers_to_held=False)], held=held()
    )[0]
    assert out.status == "ambiguous" and out.refs == []
    assert [o.key for o in out.options] == ["Water Closet"]
    assert out.unrecognised is True


# --------------------------------------------------------------------------- S7
def test_an_inactive_customer_never_resolves(db):
    gone = S.customer(db, "C-OLD", "Old Trading")
    gone.is_active = False
    db.flush()
    r = resolver(db)
    assert r.resolve_entities([Entity(kind="customer", phrase="C-OLD")], held=held())[0].status == "none"
    assert r.resolve_entities([Entity(kind="customer", phrase="old")], held=held())[0].status == "none"


# --------------------------------------------------------------------------- S8
def test_clear_needs_no_lookup_and_remove_resolves_like_set(db):
    S.product(db, "SRTWC6003")
    r = resolver(db)
    cleared = r.resolve_entities([Entity(kind="product", phrase=None, op="clear")], held=held())[0]
    assert cleared.status == "exact" and cleared.refs == []
    removed = r.resolve_entities([Entity(kind="product", phrase="srtwc6003", op="remove")], held=held())[0]
    assert removed.status == "exact" and removed.refs[0].key == "SRTWC6003"


def test_remove_refers_to_held_picks_from_held(db):
    ref = ResolvedRef(kind="brand", key="SORENTO", label="Sorento Brand")
    out = resolver(db).resolve_entities(
        [Entity(kind="brand", op="remove", refers_to_held=True)], held=held(brand=[ref])
    )[0]
    assert out.status == "exact" and out.refs == [ref]


def test_enum_value_with_no_attribute_places_on_its_only_owner(db):
    S.registry(db, "trap_type", "Trap", "enum", allowed=["p_trap", "s_trap"])
    S.registry(db, "finish", "Finish", "enum", allowed=["matte"])
    out = resolver(db, spec_attribute={"trap_type", "finish"}).resolve_specs(
        [Spec(value_key="p_trap")], held=held()
    )[0]
    assert out.status == "exact" and out.inferred is True
    assert out.specs[0].attribute == "trap_type" and out.specs[0].value == "p_trap"


def test_enum_value_with_no_attribute_and_two_owners_asks(db):
    S.registry(db, "trap_type", "Trap", "enum", allowed=["round"])
    S.registry(db, "shape", "Shape", "enum", allowed=["round"])
    out = resolver(db, spec_attribute={"trap_type", "shape"}).resolve_specs(
        [Spec(value_key="round")], held=held()
    )[0]
    assert out.status == "ambiguous"
    assert sorted(o.key for o in out.options) == ["shape", "trap_type"]
    for o in out.options:
        S.assert_clean_label(o.label)
        assert "_" not in o.label


def test_enum_attribute_with_no_value_offers_its_values(db):
    S.registry(db, "trap_type", "Trap", "enum", allowed=["p_trap", "s_trap"])
    out = resolver(db, spec_attribute={"trap_type"}).resolve_specs([Spec(attribute="trap_type")], held=held())[0]
    assert out.status == "ambiguous"
    assert [o.label for o in out.options] == ["P trap", "S trap"]
    assert [o.payload["value"] for o in out.options] == ["p_trap", "s_trap"]


def test_boolean_value_keys_are_true_false_only(db):
    S.registry(db, "has_overflow", "Has an overflow", "boolean")
    r = resolver(db, spec_attribute={"has_overflow"})
    assert r.resolve_specs([Spec(attribute="has_overflow", value_key="false")], held=held())[0].specs[0].value is False
    assert r.resolve_specs([Spec(attribute="has_overflow")], held=held())[0].specs[0].value is True
    odd = r.resolve_specs([Spec(attribute="has_overflow", value_key="no")], held=held())[0]
    assert odd.status == "none" and odd.unrecognised is True


def test_null_brand_key_never_offers_a_brand_only_another_company_sells(db):
    mine = S.brand(db, "Sorento Brand", code="SORENTO")
    cat = S.category(db, class_label="Water Closet")
    theirs = _other_company_product(db, "THEIRS-2", cat)
    with company_scope(db, None):
        theirs.brand_id = mine.id
        db.flush()
    out = resolver(db, brand={"SORENTO"}).resolve_entities(
        [Entity(kind="brand", key=None, phrase="x", op="set", refers_to_held=False)], held=held()
    )[0]
    assert out.options == [] and out.status == "none"
