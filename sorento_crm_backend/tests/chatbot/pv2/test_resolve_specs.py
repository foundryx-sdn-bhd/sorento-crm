"""PV2-RESOLVE spec resolution (CONTRACT-PV2-13oct section 4, Numbers). No LLM, no network."""
from __future__ import annotations

import pytest

from app.services.chatbot.pv2.resolve import DbResolver, Held, ResolvedRef, Spec

from tests.chatbot.pv2 import _resolve_seed as S


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def spec(attribute=None, *, value_key=None, number=None, number_to=None, unit=None, comparator="eq", op="set", phrase=None):
    return Spec(
        attribute=attribute,
        value_key=value_key,
        number=number,
        number_to=number_to,
        unit=unit,
        comparator=comparator,
        op=op,
        phrase=phrase,
    )


def held_class(label="Water Closet") -> Held:
    return Held(
        domain=None,
        refs={"product_type": [ResolvedRef(kind="product_type", key=label, label=label, phrase=None)]},
        specs=[],
        attachment_type=None,
        period=None,
        report=None,
        idea_draft_id=None,
    )


def empty_held() -> Held:
    return Held(domain=None, refs={}, specs=[], attachment_type=None, period=None, report=None, idea_draft_id=None)


def resolver(db, attrs=(), values=()) -> DbResolver:
    return DbResolver(
        db,
        pinned={
            "brand": set(),
            "product_type": {"Water Closet"},
            "attachment_type": set(),
            "spec_attribute": set(attrs),
            "spec_value": set(values),
        },
    )


def run(db, s, *, attrs=(), values=(), held=None):
    out = resolver(db, attrs, values).resolve_specs([s], held=held or empty_held())
    assert len(out) == 1
    return out[0]


# --- 8: attribute given, enum --------------------------------------------------
def test_enum_value_in_allowed_values_is_exact(db):
    S.registry(db, "trap_type", "Trap Type", "enum", allowed=["p_trap", "s_trap"])
    res = run(db, spec("trap_type", value_key="p_trap"), attrs={"trap_type"}, values={"trap_type.p_trap"})
    assert res.status == "exact"
    assert res.unrecognised is False
    assert len(res.specs) == 1
    got = res.specs[0]
    assert (got.attribute, got.value, got.comparator) == ("trap_type", "p_trap", "eq")


def test_enum_value_not_allowed_is_none_unrecognised(db):
    S.registry(db, "trap_type", "Trap Type", "enum", allowed=["p_trap", "s_trap"])
    res = run(db, spec("trap_type", value_key="x_trap"), attrs={"trap_type"}, values={"trap_type.x_trap"})
    assert res.status == "none"
    assert res.unrecognised is True
    assert res.specs == []


# --- 9: unit conversion ------------------------------------------------------
@pytest.mark.parametrize(
    "number,unit,expected",
    [(60, "cm", 600), (600, "mm", 600), (0.6, "m", 600)],
)
def test_numeric_value_converted_to_registry_unit(db, number, unit, expected):
    S.registry(db, "dim_length", "Length", "numeric", unit="mm")
    res = run(db, spec("dim_length", number=number, unit=unit), attrs={"dim_length"})
    assert res.status == "exact"
    got = res.specs[0]
    assert got.attribute == "dim_length"
    assert got.value == pytest.approx(expected)
    assert got.unit == "mm"


# --- 10: attribute null + number ---------------------------------------------
def _water_closets(db, *, length, height):
    """Two registry keys + products of class Water Closet with the given (low, high) ranges."""
    S.registry(db, "dim_length", "Length", "numeric", unit="mm")
    S.registry(db, "dim_height", "Height", "numeric", unit="mm")
    cat = S.category(db, class_label="Water Closet")
    for i, (ln, ht) in enumerate(zip(length, height)):
        p = S.product(db, f"SRTWC{i}-A", cat=cat)
        S.specs(db, p, klass="Water Closet", dim_length=ln, dim_height=ht)


def test_null_attribute_one_range_holds_number_infers_attribute(db):
    _water_closets(db, length=(500, 700), height=(700, 800))
    res = run(db, spec(None, number=600, unit="mm"), attrs={"dim_length", "dim_height"}, held=held_class())
    assert res.status == "exact"
    assert res.inferred is True
    assert [s.attribute for s in res.specs] == ["dim_length"]
    assert res.specs[0].value == pytest.approx(600)


def test_null_attribute_two_ranges_hold_number_is_ambiguous_with_registry_labels(db):
    _water_closets(db, length=(500, 700), height=(550, 650))
    res = run(db, spec(None, number=600, unit="mm"), attrs={"dim_length", "dim_height"}, held=held_class())
    assert res.status == "ambiguous"
    assert {o.label for o in res.options} == {"Length", "Height"}
    assert {o.key for o in res.options} == {"dim_length", "dim_height"}
    assert res.specs == []
    for o in res.options:
        S.assert_clean_label(o.label)


def test_null_attribute_no_range_holds_number_is_none_with_closest_real_rows(db):
    _water_closets(db, length=(500, 700), height=(700, 800))
    res = run(db, spec(None, number=5000, unit="mm"), attrs={"dim_length", "dim_height"}, held=held_class())
    assert res.status == "none"
    assert res.specs == []  # never an invented spec
    assert len(res.closest) > 0
    for o in res.closest:
        S.assert_clean_label(o.label)


# --- 11: pinning -------------------------------------------------------------
def test_attribute_not_pinned_is_none_unrecognised_even_if_live(db):
    S.registry(db, "dim_length", "Length", "numeric", unit="mm")
    res = run(db, spec("dim_length", number=600, unit="mm", phrase="length"), attrs=set())
    assert res.status == "none"
    assert res.unrecognised is True
    assert res.specs == []


def test_attribute_pinned_but_registry_row_inactive_is_none(db):
    S.registry(db, "dim_length", "Length", "numeric", unit="mm", active=False)
    res = run(db, spec("dim_length", number=600, unit="mm"), attrs={"dim_length"})
    assert res.status == "none"
    assert res.specs == []


def test_results_one_per_spec_in_order(db):
    S.registry(db, "dim_length", "Length", "numeric", unit="mm")
    S.registry(db, "trap_type", "Trap Type", "enum", allowed=["p_trap"])
    out = resolver(db, {"dim_length", "trap_type"}, {"trap_type.p_trap"}).resolve_specs(
        [spec("trap_type", value_key="p_trap"), spec("nope", number=1), spec("dim_length", number=60, unit="cm")],
        held=empty_held(),
    )
    assert [r.status for r in out] == ["exact", "none", "exact"]
